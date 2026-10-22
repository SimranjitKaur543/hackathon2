"""One call that the bot uses: work out (and record) the offer for one Telegram user."""
from contextlib import closing
from datetime import datetime, timedelta

from engine import store
from engine.agent import decide
from engine.db import connect
from engine.eligibility import is_holdout
from engine.rules import load_rules


def offer_for(telegram_id: int) -> dict:
    """Returns {"kind": ..., "offer": row or None, "customer": dict, "rules": dict}.

    kind is one of: "offer" (new offer sent), "holdout" (control group, no offer), "no_offer" (the engine
    decided not to send one), "already" (weekly limit reached; offer = the last one), "not_linked".
    Rules are re-read every time, so edits from the dashboard apply to the very next offer.
    """
    rules = load_rules()
    with closing(connect()) as conn:
        store.init(conn)
        cid = store.linked_customer(conn, telegram_id)
        if cid is None:
            return dict(kind="not_linked", offer=None, customer=None, rules=rules)
        f = store.load_customer(conn, cid)
        last = store.latest_offer(conn, cid)
        recent = last and datetime.fromisoformat(last["sent_at"]) > datetime.now() - timedelta(days=7)

        if is_holdout(cid, rules["campaign"]["holdout_percent"]):
            if recent:
                return dict(kind="already", offer=last, customer=f, rules=rules)
            decision = dict(offer_type="holdout", category=f["top_category"], value=0, message="",
                            reason="holdout group: no offer, used to measure whether offers work", source="rules")
            oid = store.record_offer(conn, f, telegram_id, decision, 0, is_holdout=True)
            return dict(kind="holdout", offer=_row(conn, oid), customer=f, rules=rules)

        sent_this_week = store.offers_last_7d(conn, cid)
        if sent_this_week >= rules["campaign"]["max_offers_per_customer_per_week"] and last:
            return dict(kind="already", offer=last, customer=f, rules=rules)

        decision, blocked = decide(f, rules, sent_this_week, store.budget_used(conn))
        store.log_blocked(conn, f, blocked)
        oid = store.record_offer(conn, f, telegram_id, decision, rules["campaign"]["offer_valid_days"])
        kind = "no_offer" if decision["offer_type"] == "no_offer" else "offer"
        return dict(kind=kind, offer=_row(conn, oid), customer=f, rules=rules, blocked=blocked)


def _row(conn, offer_id: int):
    return conn.execute("SELECT * FROM offers_sent WHERE offer_id = ?", (offer_id,)).fetchone()
