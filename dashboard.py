"""Marketing dashboard: serves dashboard/index.html plus the JSON data it shows.

Uses only Python's built-in web server, so there is nothing extra to install.
Run with:  python dashboard.py   then open http://localhost:8501
"""
import json
import sqlite3
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import store
from engine.db import connect
from engine.eligibility import build_menu, can_send, fallback_decision, is_holdout
from engine.guardrail import check_decision
from engine.rules import OFFER_TYPES, RulesError, load_rules, save_rules, validate_rules
from engine.segments import SEGMENT_LABELS, SEGMENT_ORDER

PORT = 8501
PAGE = Path(__file__).resolve().parent / "dashboard" / "index.html"


def rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


# --- Data for each tab ------------------------------------------------------------------------
def results(conn) -> dict:
    rules = load_rules()
    offers = rows(conn, """SELECT o.*, u.first_name FROM offers_sent o
                           LEFT JOIN telegram_users u USING(telegram_id) ORDER BY offer_id DESC""")
    treated = [o for o in offers if not o["is_holdout"] and o["offer_type"] != "no_offer"]
    control = [o for o in offers if o["is_holdout"]]
    claimed = sum(o["status"] == "claimed" for o in treated)
    control_clicked = sum(o["status"] == "claimed" for o in control)

    by_segment = []
    for seg in SEGMENT_ORDER:
        sent = [o for o in treated if o["segment"] == seg]
        if sent:
            c = sum(o["status"] == "claimed" for o in sent)
            by_segment.append(dict(label=SEGMENT_LABELS[seg], sent=len(sent), claimed=c, value=c / len(sent)))
    mix = Counter(o["offer_type"] for o in offers if not o["is_holdout"])
    return dict(
        players=conn.execute("SELECT COUNT(*) FROM telegram_users").fetchone()[0],
        sent=len(treated), claimed=claimed, claim_rate=rate(claimed, len(treated)),
        control=len(control), control_clicked=control_clicked, control_rate=rate(control_clicked, len(control)),
        ai_share=rate(sum(o["source"] == "ai" for o in treated), len(treated)),
        budget=rules["campaign"]["total_budget_rs"], spent=sum(o["cost"] or 0 for o in offers),
        by_segment=by_segment,
        by_offer=[dict(label=k, value=v) for k, v in mix.most_common()],
        latest=[{**o, "segment_label": SEGMENT_LABELS.get(o["segment"], o["segment"])} for o in offers[:50]],
    )


def safety(conn) -> dict:
    log = rows(conn, "SELECT * FROM guardrail_log ORDER BY id DESC")
    for r in log:
        r["violations"] = json.loads(r["violations"])
        r["decision"] = json.loads(r["decision"]) if r["decision"] else None
        r["segment_label"] = SEGMENT_LABELS.get(r["segment"], r["segment"])
    errors = [r for r in log if r["violations"] and r["violations"][0].startswith("AI error")]
    blocked = [r for r in log if r not in errors]
    return dict(
        passed=conn.execute("SELECT COUNT(*) FROM offers_sent WHERE source = 'ai'").fetchone()[0],
        blocked=blocked[:50], blocked_count=len(blocked),
        errors=errors[:20], errors_count=len(errors),
    )


def sample_customer(conn, segment: str) -> dict:
    rules = load_rules()
    ids = [r[0] for r in conn.execute("SELECT customer_id FROM customer_features WHERE segment = ?", (segment,))]
    cid = next(i for i in ids if not is_holdout(i, rules["campaign"]["holdout_percent"]))
    f = store.load_customer(conn, cid)
    return dict(customer=f, menu=build_menu(f, rules))


def customer(conn, cid: int) -> dict:
    rules = load_rules()
    f = store.load_customer(conn, cid)
    return dict(customer=f, menu=build_menu(f, rules), segment_label=SEGMENT_LABELS[f["segment"]],
                holdout=is_holdout(cid, rules["campaign"]["holdout_percent"]))


def customers_overview(conn) -> dict:
    feats = rows(conn, "SELECT f.segment, f.total_spend, f.orders_total, c.true_persona FROM customer_features f "
                       "JOIN customers c USING(customer_id)")
    match = sum(f["segment"] == f["true_persona"]
                or (f["true_persona"] == "regular" and f["segment"] in ("regular", "cart_abandoner")) for f in feats)
    spend = sorted((f["total_spend"] for f in feats if f["orders_total"]), reverse=True)
    seg_counts = Counter(f["segment"] for f in feats)

    step = 10000  # spend histogram in ₹10k buckets, everything above ₹1.5 lakh in one bar
    buckets = Counter(min(s // step, 15) for s in spend)
    histogram = [dict(label=f"₹{b * 10}k" + ("+" if b == 15 else ""), value=buckets.get(b, 0)) for b in range(16)]

    monthly = rows(conn, "SELECT substr(order_date, 1, 7) AS label, COUNT(*) AS value FROM orders GROUP BY label")
    return dict(
        customers=len(feats), match_rate=match / len(feats),
        abandonment=conn.execute("SELECT AVG(status = 'abandoned') FROM carts WHERE status != 'open'").fetchone()[0],
        top10_share=sum(spend[:len(spend) // 10]) / sum(spend),
        by_segment=[dict(label=SEGMENT_LABELS[s], value=seg_counts[s]) for s in SEGMENT_ORDER],
        cod_by_tier=rows(conn, """SELECT 'Tier ' || c.city_tier AS label, AVG(o.payment_method = 'COD') AS value
                                  FROM orders o JOIN customers c USING(customer_id) GROUP BY c.city_tier"""),
        monthly=monthly[1:-1],  # first and last months are only partly covered
        spend_histogram=histogram,
    )


def preview(conn, new_rules: dict) -> dict:
    """Default offer for every customer under the current rules and the edited rules."""
    everyone = store.load_all_customers(conn)

    def simulate(r: dict) -> dict:
        cost, mix = 0.0, Counter()
        for f in everyone:
            if not can_send(f, r, 0, 0)[0]:
                mix[(f["segment"], "holdout")] += 1
                continue
            d = fallback_decision(f, build_menu(f, r), r)
            cost += check_decision(d, f, r).cost
            mix[(f["segment"], d["offer_type"])] += 1
        discounts = sum(v for (_, t), v in mix.items() if t in ("percent_off", "flat_off"))
        table = [dict(segment=SEGMENT_LABELS[s], **{t: mix.get((s, t), 0) for t in list(OFFER_TYPES) + ["holdout"]})
                 for s in SEGMENT_ORDER]
        return dict(cost=round(cost), discounts=discounts, table=table)

    return dict(before=simulate(load_rules()), after=simulate(new_rules), budget=new_rules["campaign"]["total_budget_rs"])


# --- Web server -------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        args = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        with closing(connect()) as conn:
            store.init(conn)
            if url.path == "/api/results":
                return self.send_json(results(conn))
            if url.path == "/api/safety":
                return self.send_json(safety(conn))
            if url.path == "/api/customers":
                return self.send_json(customers_overview(conn))
            if url.path == "/api/customer":
                return self.send_json(customer(conn, int(args.get("id", 1))))
            if url.path == "/api/sample":
                return self.send_json(sample_customer(conn, args.get("segment", "at_risk")))
            if url.path == "/api/rules":
                return self.send_json(dict(rules=load_rules(), segments=SEGMENT_ORDER,
                                           segment_labels=SEGMENT_LABELS, offer_types=OFFER_TYPES))
        self.send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        data = self.read_json()
        if url.path in ("/api/rules", "/api/preview"):
            try:
                validate_rules(data)
            except RulesError as e:
                return self.send_json({"error": str(e)}, 400)
            if url.path == "/api/rules":
                save_rules(data)
                return self.send_json({"ok": True})
            with closing(connect()) as conn:
                return self.send_json(preview(conn, data))
        if url.path == "/api/check":
            with closing(connect()) as conn:
                f = store.load_customer(conn, int(data["customer_id"]))
            decision = dict(offer_type=data["offer_type"], category=data["category"],
                            value=int(data["value"]), message=data["message"])
            r = check_decision(decision, f, load_rules())
            return self.send_json(dict(ok=r.ok, violations=r.violations, cost=r.cost))
        self.send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args) -> None:  # keep the terminal quiet
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)  # only reachable from this laptop
    print(f"Dashboard running at http://localhost:{PORT}  (Ctrl+C to stop)")
    server.serve_forever()
