"""Turn raw customer behaviour into features and a segment (customer type).

Only observable behaviour is used (orders, browsing, carts). The hidden `true_persona` column is never
read here; it exists only so we can measure how accurate these rules are.
"""
import math
import sqlite3
from datetime import date

# Checked in order: the first segment whose test passes wins.
SEGMENT_ORDER = ["new", "window_shopper", "lapsed", "at_risk", "loyal", "deal_hunter", "cart_abandoner", "regular"]

SEGMENT_LABELS = {
    "new": "🆕 New customer",
    "window_shopper": "👀 Window shopper",
    "lapsed": "🔴 Lapsed",
    "at_risk": "🟡 At risk",
    "loyal": "🟢 Loyal",
    "deal_hunter": "🏷️ Deal hunter",
    "cart_abandoner": "🛒 Cart abandoner",
    "regular": "🔵 Regular",
}


def assign_segment(f: dict) -> str:
    """Pick a segment from a customer's features. Pure function so it is easy to test."""
    recency = f["recency_days"]
    if f["days_since_signup"] <= 45 and f["orders_total"] <= 1:
        return "new"
    if f["orders_total"] == 0:
        return "window_shopper"
    if recency > 150:
        return "lapsed"
    if recency >= 60:
        return "at_risk"
    if f["orders_180d"] >= 6 and recency <= 30 and f["coupon_rate"] < 0.5:
        return "loyal"
    if f["coupon_rate"] >= 0.6 and f["orders_total"] >= 3:
        return "deal_hunter"
    if f["open_cart_value"] > 0 and f["open_cart_age_days"] <= 7:
        return "cart_abandoner"
    return "regular"


def churn_risk(recency_days: int | None, avg_gap_days: float | None) -> float:
    """0 = active as usual, 1 = almost certainly gone. Compares time since last order with the
    customer's own usual gap between orders."""
    if recency_days is None:
        return 0.0
    expected_gap = max(avg_gap_days or 60.0, 14.0)
    return round(1 - math.exp(-recency_days / (2.5 * expected_gap)), 2)


FEATURES_SCHEMA = """
DROP TABLE IF EXISTS customer_features;
CREATE TABLE customer_features (
    customer_id INTEGER PRIMARY KEY, segment TEXT, churn_risk REAL,
    orders_total INTEGER, orders_180d INTEGER, total_spend INTEGER, avg_order_value INTEGER,
    recency_days INTEGER, avg_gap_days REAL, coupon_rate REAL, return_rate REAL,
    sessions_30d INTEGER, top_category TEXT, open_cart_value INTEGER, open_cart_age_days INTEGER,
    cart_abandon_rate REAL, days_since_signup INTEGER, reward_points INTEGER, points_expiring_30d INTEGER
);
"""


def _days(today: date, iso: str | None) -> int | None:
    return None if iso is None else (today - date.fromisoformat(iso)).days


def build_features(conn: sqlite3.Connection, today: date | None = None) -> int:
    """Compute features + segment for every customer and store them in `customer_features`."""
    today = today or date.today()
    orders = {r["customer_id"]: r for r in conn.execute("""
        SELECT customer_id, COUNT(*) n, MIN(order_date) first, MAX(order_date) last,
               SUM(CASE WHEN julianday(?) - julianday(order_date) <= 180 THEN 1 ELSE 0 END) n180,
               SUM(CASE WHEN returned = 0 THEN amount - discount ELSE 0 END) spend,
               AVG(amount) aov, AVG(coupon_used) coupon_rate, AVG(returned) return_rate
        FROM orders GROUP BY customer_id""", (today.isoformat(),))}
    sessions = {r["customer_id"]: r["n"] for r in conn.execute(
        "SELECT customer_id, COUNT(*) n FROM sessions WHERE julianday(?) - julianday(session_date) <= 30 GROUP BY customer_id",
        (today.isoformat(),))}
    open_carts = {r["customer_id"]: r for r in conn.execute(
        "SELECT customer_id, cart_value, created_date FROM carts WHERE status = 'open'")}
    abandon = {r["customer_id"]: r["rate"] for r in conn.execute(
        "SELECT customer_id, AVG(status = 'abandoned') rate FROM carts WHERE status != 'open' GROUP BY customer_id")}
    # Top category = most orders, with browsing as a tie-breaker / fallback for people with no orders.
    interest: dict[int, dict[str, float]] = {}
    for r in conn.execute("SELECT customer_id, category, COUNT(*) n FROM orders GROUP BY 1, 2"):
        interest.setdefault(r["customer_id"], {})[r["category"]] = r["n"] * 3
    for r in conn.execute("SELECT customer_id, category_viewed c, COUNT(*) n FROM sessions GROUP BY 1, 2"):
        d = interest.setdefault(r["customer_id"], {})
        d[r["c"]] = d.get(r["c"], 0) + r["n"]

    rows = []
    for c in conn.execute("SELECT customer_id, signup_date, favourite_category, reward_points, points_expiring_30d FROM customers"):
        cid = c["customer_id"]
        o = orders.get(cid)
        n = o["n"] if o else 0
        cart = open_carts.get(cid)
        avg_gap = None
        if o and n >= 2:
            avg_gap = round((date.fromisoformat(o["last"]) - date.fromisoformat(o["first"])).days / (n - 1), 1)
        f = dict(
            customer_id=cid,
            orders_total=n,
            orders_180d=o["n180"] if o else 0,
            total_spend=int(o["spend"]) if o else 0,
            avg_order_value=int(o["aov"]) if o else 0,
            recency_days=_days(today, o["last"]) if o else None,
            avg_gap_days=avg_gap,
            coupon_rate=round(o["coupon_rate"], 2) if o else 0.0,
            return_rate=round(o["return_rate"], 2) if o else 0.0,
            sessions_30d=sessions.get(cid, 0),
            top_category=max(interest[cid], key=interest[cid].get) if cid in interest else c["favourite_category"],
            open_cart_value=cart["cart_value"] if cart else 0,
            open_cart_age_days=_days(today, cart["created_date"]) if cart else None,
            cart_abandon_rate=round(abandon.get(cid, 0.0), 2),
            days_since_signup=_days(today, c["signup_date"]),
            reward_points=c["reward_points"],
            points_expiring_30d=c["points_expiring_30d"],
        )
        f["churn_risk"] = churn_risk(f["recency_days"], avg_gap)
        f["segment"] = assign_segment({**f, "recency_days": f["recency_days"] if f["recency_days"] is not None else 10**6})
        rows.append(f)

    conn.executescript(FEATURES_SCHEMA)
    cols = list(rows[0])
    conn.executemany(f"INSERT INTO customer_features ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                     [[r[k] for k in cols] for r in rows])
    conn.commit()
    return len(rows)
