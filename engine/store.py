"""Everything the bot remembers: which classmate plays which customer, offers sent, and blocked attempts.
These tables survive regenerating the customer data."""
import json
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta

from engine.segments import SEGMENT_ORDER

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_users (
    telegram_id INTEGER PRIMARY KEY, first_name TEXT, customer_id INTEGER, joined_at TEXT
);
CREATE TABLE IF NOT EXISTS offers_sent (
    offer_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, telegram_id INTEGER, segment TEXT,
    sent_at TEXT, offer_type TEXT, category TEXT, value INTEGER, cost REAL, code TEXT, valid_until TEXT,
    message TEXT, reason TEXT, source TEXT, is_holdout INTEGER DEFAULT 0,
    status TEXT DEFAULT 'sent', responded_at TEXT
);
CREATE TABLE IF NOT EXISTS guardrail_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, customer_id INTEGER, segment TEXT,
    attempt INTEGER, source TEXT, decision TEXT, violations TEXT
);
"""

CODE_PREFIX = {"percent_off": "SAVE", "flat_off": "FLAT", "free_delivery": "SHIPFREE"}


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- Customers ---------------------------------------------------------------------------------
def load_customer(conn: sqlite3.Connection, customer_id: int) -> dict:
    row = conn.execute("""SELECT f.*, c.name, c.city, c.city_tier, c.preferred_payment
                          FROM customer_features f JOIN customers c USING(customer_id)
                          WHERE customer_id = ?""", (customer_id,)).fetchone()
    f = dict(row)
    f["cart_items"] = [dict(r) for r in conn.execute(
        "SELECT product, category, price FROM cart_items WHERE customer_id = ?", (customer_id,))]
    f["cart_categories"] = [i["category"] for i in f["cart_items"]]
    f["recent_orders"] = [dict(r) for r in conn.execute(
        "SELECT order_date, category, amount, coupon_used FROM orders WHERE customer_id = ? "
        "ORDER BY order_date DESC LIMIT 3", (customer_id,))]
    return f


def pick_customer(conn: sqlite3.Connection, segment: str | None) -> int:
    """A random customer of the chosen type that no classmate is playing yet."""
    segment = segment if segment in SEGMENT_ORDER else None
    sql = ("SELECT customer_id FROM customer_features WHERE customer_id NOT IN "
           "(SELECT customer_id FROM telegram_users WHERE customer_id IS NOT NULL)")
    ids = [r[0] for r in conn.execute(sql + (" AND segment = ?" if segment else ""), (segment,) if segment else ())]
    return random.choice(ids)


def link_user(conn: sqlite3.Connection, telegram_id: int, first_name: str, customer_id: int) -> None:
    conn.execute("INSERT INTO telegram_users VALUES (?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET "
                 "first_name = excluded.first_name, customer_id = excluded.customer_id",
                 (telegram_id, first_name, customer_id, now()))
    conn.commit()


def linked_customer(conn: sqlite3.Connection, telegram_id: int) -> int | None:
    row = conn.execute("SELECT customer_id FROM telegram_users WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return row[0] if row else None


def all_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM telegram_users WHERE customer_id IS NOT NULL").fetchall()


# --- Offers ------------------------------------------------------------------------------------
def offers_last_7d(conn: sqlite3.Connection, customer_id: int) -> int:
    return conn.execute("""SELECT COUNT(*) FROM offers_sent WHERE customer_id = ? AND is_holdout = 0
                           AND offer_type != 'no_offer' AND sent_at >= ?""",
                        (customer_id, (datetime.now() - timedelta(days=7)).isoformat())).fetchone()[0]


def latest_offer(conn: sqlite3.Connection, customer_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM offers_sent WHERE customer_id = ? AND offer_type != 'no_offer' "
                        "ORDER BY offer_id DESC LIMIT 1", (customer_id,)).fetchone()


def budget_used(conn: sqlite3.Connection) -> float:
    return conn.execute("SELECT COALESCE(SUM(cost), 0) FROM offers_sent").fetchone()[0]


def record_offer(conn: sqlite3.Connection, f: dict, telegram_id: int, decision: dict, valid_days: int,
                 is_holdout: bool = False) -> int:
    t = decision["offer_type"]
    code = f"{CODE_PREFIX[t]}{decision['value'] or ''}-{secrets.token_hex(2).upper()}" if t in CODE_PREFIX else None
    cur = conn.execute(
        """INSERT INTO offers_sent (customer_id, telegram_id, segment, sent_at, offer_type, category, value, cost,
           code, valid_until, message, reason, source, is_holdout, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f["customer_id"], telegram_id, f["segment"], now(), t, decision.get("category"), decision.get("value", 0),
         decision.get("cost", 0.0), code, (date.today() + timedelta(days=valid_days)).isoformat(),
         decision.get("message", ""), decision.get("reason", ""), decision.get("source", ""), int(is_holdout),
         "no_offer" if t == "no_offer" else "sent"))
    conn.commit()
    return cur.lastrowid


def set_status(conn: sqlite3.Connection, offer_id: int, telegram_id: int, status: str) -> sqlite3.Row | None:
    """Record Claim / Not interested. Only the person the offer was sent to can respond, and only once."""
    row = conn.execute("SELECT * FROM offers_sent WHERE offer_id = ? AND telegram_id = ?", (offer_id, telegram_id)).fetchone()
    if row and row["status"] == "sent":
        conn.execute("UPDATE offers_sent SET status = ?, responded_at = ? WHERE offer_id = ?", (status, now(), offer_id))
        conn.commit()
    return row


def log_blocked(conn: sqlite3.Connection, f: dict, blocked: list[dict]) -> None:
    conn.executemany(
        "INSERT INTO guardrail_log (at, customer_id, segment, attempt, source, decision, violations) VALUES (?,?,?,?,?,?,?)",
        [(now(), f["customer_id"], f["segment"], b["attempt"], b["source"],
          json.dumps(b["decision"], ensure_ascii=False), json.dumps(b["violations"], ensure_ascii=False)) for b in blocked])
    conn.commit()
