"""Generate realistic synthetic customers for an Indian e-commerce store.

Every customer is given a hidden "true persona" (loyal, deal hunter, at risk, ...) that drives how they
behave. The engine never reads the persona: it has to work out each customer's type from their
behaviour (orders, browsing, carts), just like a real platform would. The persona is kept only so we
can check how accurate that segmentation is.

Run with:  python data/generate_data.py
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from faker import Faker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.db import DB_PATH, connect  # noqa: E402
from engine.segments import build_features  # noqa: E402

SEED = 42
N_CUSTOMERS = 2000
HISTORY_DAYS = 540  # 18 months of order history
TODAY = date.today()

rng = np.random.default_rng(SEED)
random.seed(SEED)
fake = Faker("en_IN")
Faker.seed(SEED)

# --- Store catalogue -------------------------------------------------------------------------
CATEGORIES = ["fashion", "electronics", "beauty", "home", "footwear", "grocery"]
POPULARITY = np.array([0.32, 0.10, 0.15, 0.13, 0.13, 0.17])
MEDIAN_ITEM_PRICE = {"fashion": 799, "electronics": 2499, "beauty": 449, "home": 999, "footwear": 1299, "grocery": 599}
RETURN_RATE = {"fashion": 0.22, "electronics": 0.06, "beauty": 0.04, "home": 0.08, "footwear": 0.18, "grocery": 0.01}
PRODUCTS = {
    "fashion": ["Cotton Kurta", "Denim Jacket", "Printed Saree", "Slim Fit Jeans", "Oversized T-shirt", "Anarkali Suit"],
    "electronics": ["Wireless Earbuds", "Smartwatch", "Power Bank 20000mAh", "Bluetooth Speaker", "Phone Case"],
    "beauty": ["Vitamin C Serum", "Matte Lipstick", "Sunscreen SPF 50", "Hair Oil", "Face Wash"],
    "home": ["Bedsheet Set", "Non-stick Tawa", "Table Lamp", "Storage Boxes", "Wall Clock"],
    "footwear": ["Running Shoes", "Kolhapuri Chappals", "Sneakers", "Formal Shoes", "Juttis"],
    "grocery": ["Basmati Rice 5kg", "Dry Fruits Combo", "Green Tea", "Atta 10kg", "Masala Combo"],
}

CITIES = {
    1: ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Chennai", "Kolkata", "Pune", "Ahmedabad"],
    2: ["Jaipur", "Lucknow", "Chandigarh", "Ludhiana", "Amritsar", "Jalandhar", "Patiala", "Indore", "Kochi", "Surat"],
    3: ["Bathinda", "Moga", "Hoshiarpur", "Ambala", "Karnal", "Bareilly", "Siliguri", "Udaipur", "Gaya"],
}
TIER_SHARE = [0.40, 0.38, 0.22]
PAYMENT_MIX = {  # UPI, card, COD — smaller cities lean on cash on delivery
    1: [0.55, 0.30, 0.15],
    2: [0.50, 0.20, 0.30],
    3: [0.42, 0.10, 0.48],
}
PAYMENTS = ["UPI", "card", "COD"]

# --- Hidden personas ---------------------------------------------------------------------------
# share: fraction of customers | rate: orders per month while active | coupon: chance an order uses a coupon
# signup: days ago (min, max) | stop: days since their last order (min, max) | sessions: visits in last 30 days
# open_cart: chance of an item sitting in the cart right now | abandon: abandoned carts per completed order
PERSONAS = {
    "loyal":          dict(share=0.12, rate=2.2, coupon=0.12, signup=(180, 1000), stop=(0, 15),    sessions=(8, 20), open_cart=0.30, abandon=1.2),
    "regular":        dict(share=0.28, rate=0.6, coupon=0.35, signup=(90, 1000),  stop=(0, 55),    sessions=(2, 7),  open_cart=0.30, abandon=2.2),
    "deal_hunter":    dict(share=0.15, rate=1.0, coupon=0.85, signup=(90, 1000),  stop=(0, 45),    sessions=(3, 12), open_cart=0.50, abandon=3.0),
    "at_risk":        dict(share=0.15, rate=1.1, coupon=0.30, signup=(200, 1000), stop=(60, 150),  sessions=(0, 2),  open_cart=0.15, abandon=2.2),
    "lapsed":         dict(share=0.13, rate=0.6, coupon=0.35, signup=(300, 1000), stop=(151, 420), sessions=(0, 1),  open_cart=0.03, abandon=2.2),
    "new":            dict(share=0.09, rate=0.0, coupon=0.50, signup=(1, 40),     stop=(0, 40),    sessions=(2, 8),  open_cart=0.50, abandon=2.0),
    "window_shopper": dict(share=0.08, rate=0.0, coupon=0.00, signup=(30, 400),   stop=(0, 0),     sessions=(10, 25), open_cart=0.60, abandon=0.0),
}


# --- Festive / sale calendar (orders spike during these) -------------------------------------
DIWALI = {2024: date(2024, 11, 1), 2025: date(2025, 10, 21), 2026: date(2026, 11, 8), 2027: date(2027, 10, 29)}


def sale_periods(year: int):
    diwali = DIWALI.get(year, date(year, 11, 1))
    return [
        (date(year, 1, 19), date(year, 1, 26)),            # Republic Day sale
        (date(year, 6, 15), date(year, 7, 10)),            # End of season sale
        (date(year, 8, 8), date(year, 8, 15)),             # Independence Day sale
        (date(year, 9, 26), date(year, 10, 6)),            # Big festive sale
        (diwali - timedelta(days=12), diwali + timedelta(days=1)),  # Diwali sale
    ]


START = TODAY - timedelta(days=HISTORY_DAYS)
ALL_DAYS = [START + timedelta(days=i) for i in range(HISTORY_DAYS + 1)]
_in_sale = np.array([any(s <= d <= e for s, e in sale_periods(d.year)) for d in ALL_DAYS])
_weekend = np.array([d.weekday() >= 5 for d in ALL_DAYS])
DAY_WEIGHT = 1 + 0.25 * _weekend + 2.0 * _in_sale            # normal shoppers: ~3x during sales
DEAL_DAY_WEIGHT = 1 + 0.25 * _weekend + 7.0 * _in_sale       # deal hunters: ~8x during sales


def sample_dates(start: date, end: date, k: int, deal: bool) -> list[date]:
    lo, hi = (start - START).days, (end - START).days
    lo, hi = max(lo, 0), min(hi, HISTORY_DAYS)
    if k <= 0 or hi < lo:
        return []
    w = (DEAL_DAY_WEIGHT if deal else DAY_WEIGHT)[lo:hi + 1]
    idx = rng.choice(np.arange(lo, hi + 1), size=k, p=w / w.sum())
    return [START + timedelta(days=int(i)) for i in idx]


def round_price(p: float) -> int:
    """Indian retail style prices: 499, 799, 1299 ..."""
    return max(99, int(round(p / 50.0)) * 50 - 1)


def pick_category(pref: np.ndarray) -> str:
    return CATEGORIES[rng.choice(len(CATEGORIES), p=pref)]


# --- Builders ----------------------------------------------------------------------------------
def make_customer(cid: int, persona: str) -> dict:
    p = PERSONAS[persona]
    gender = rng.choice(["F", "M"], p=[0.52, 0.48])
    first = fake.first_name_female() if gender == "F" else fake.first_name_male()
    tier = int(rng.choice([1, 2, 3], p=TIER_SHARE))

    base = POPULARITY.copy()
    if gender == "F":
        base *= [1.15, 0.7, 1.6, 1.0, 1.0, 1.0]
    else:
        base *= [0.9, 1.8, 0.5, 1.0, 1.1, 1.0]
    pref = rng.dirichlet(base / base.sum() * 6)

    spend_level = float(rng.lognormal(0, 0.45)) * {1: 1.2, 2: 1.0, 3: 0.8}[tier]
    return dict(
        customer_id=cid,
        name=f"{first} {fake.last_name()}",
        gender=gender,
        age=int(min(65, 18 + rng.gamma(3.0, 4.0))),
        city=random.choice(CITIES[tier]),
        city_tier=tier,
        signup_date=TODAY - timedelta(days=int(rng.integers(*p["signup"]))),
        preferred_payment=PAYMENTS[rng.choice(3, p=PAYMENT_MIX[tier])],
        favourite_category=CATEGORIES[int(np.argmax(pref))],
        true_persona=persona,
        _pref=pref,
        _spend=spend_level,
    )


def make_orders(c: dict, persona: str) -> list[dict]:
    p = PERSONAS[persona]
    start = max(c["signup_date"], START)
    deal = persona == "deal_hunter"

    if persona == "window_shopper":
        return []
    if persona == "new":
        dates = sample_dates(c["signup_date"], TODAY, 1, deal) if rng.random() < 0.5 else []
    else:
        end = TODAY - timedelta(days=int(rng.integers(p["stop"][0], p["stop"][1] + 1)))
        end = max(end, start + timedelta(days=1))
        months = max((end - start).days / 30.0, 0.5)
        k = int(rng.poisson(p["rate"] * months))
        min_orders = {"at_risk": 3, "lapsed": 1, "regular": 1, "loyal": 6, "deal_hunter": 3}[persona]
        k = max(k, min_orders)
        # Pin the last order to `end` so recency matches the persona exactly.
        dates = sample_dates(start, end, k - 1, deal) + [end]

    orders = []
    for d in sorted(dates):
        cat = pick_category(c["_pref"])
        n_items = 1 + min(int(rng.poisson(0.5)), 3)
        amount = sum(round_price(rng.lognormal(np.log(MEDIAN_ITEM_PRICE[cat]), 0.55) * c["_spend"]) for _ in range(n_items))
        in_sale = bool(_in_sale[(d - START).days])
        coupon_p = min(0.98, p["coupon"] * (1.3 if in_sale else 1.0))
        coupon = rng.random() < coupon_p
        discount = int(amount * rng.uniform(0.05, 0.20)) if coupon else 0
        orders.append(dict(
            customer_id=c["customer_id"],
            order_date=d,
            category=cat,
            items=n_items,
            amount=amount,
            discount=discount,
            coupon_used=int(coupon),
            payment_method=c["preferred_payment"] if rng.random() < 0.8 else PAYMENTS[rng.choice(3, p=PAYMENT_MIX[c["city_tier"]])],
            returned=int(rng.random() < RETURN_RATE[cat]),
        ))
    return orders


def make_sessions(c: dict, persona: str) -> list[dict]:
    lo, hi = PERSONAS[persona]["sessions"]
    rows = []
    for _ in range(int(rng.integers(lo, hi + 1))):
        rows.append(dict(
            customer_id=c["customer_id"],
            session_date=TODAY - timedelta(days=int(rng.integers(0, 30))),
            category_viewed=pick_category(c["_pref"]),
            pages_viewed=int(1 + rng.geometric(0.25)),
            device=rng.choice(["app", "mobile_web", "desktop"], p=[0.60, 0.25, 0.15]),
        ))
    return rows


def make_carts(c: dict, persona: str, orders: list[dict]) -> tuple[list[dict], list[dict]]:
    p = PERSONAS[persona]
    carts, items = [], []
    for o in orders:
        carts.append(dict(customer_id=c["customer_id"], created_date=o["order_date"], cart_value=o["amount"], status="converted"))

    n_abandoned = int(rng.poisson(len(orders) * p["abandon"])) if orders else int(rng.poisson(4 if persona == "window_shopper" else 1))
    for d in sample_dates(max(c["signup_date"], START), TODAY - timedelta(days=15), n_abandoned, False):
        cat = pick_category(c["_pref"])
        carts.append(dict(customer_id=c["customer_id"], created_date=d,
                          cart_value=round_price(rng.lognormal(np.log(MEDIAN_ITEM_PRICE[cat]), 0.6) * c["_spend"]),
                          status="abandoned"))

    if rng.random() < p["open_cart"]:
        age = int(rng.integers(0, 14)) if persona not in ("at_risk", "lapsed") else int(rng.integers(5, 40))
        cdate = TODAY - timedelta(days=age)
        cart_items = []
        for _ in range(1 + min(int(rng.poisson(0.6)), 3)):
            cat = pick_category(c["_pref"])
            price = round_price(rng.lognormal(np.log(MEDIAN_ITEM_PRICE[cat]), 0.5) * c["_spend"])
            cart_items.append(dict(product=random.choice(PRODUCTS[cat]), category=cat, price=price))
        carts.append(dict(customer_id=c["customer_id"], created_date=cdate,
                          cart_value=sum(i["price"] for i in cart_items), status="open"))
        for it in cart_items:
            items.append(dict(customer_id=c["customer_id"], added_date=cdate, **it))
    return carts, items


def reward_points(persona: str, orders: list[dict]) -> tuple[int, int]:
    """2 points per ₹100 spent; points expire after 12 months. Returns (balance, expiring in next 30 days)."""
    redeem = {"deal_hunter": 0.7, "loyal": 0.3}.get(persona, 0.2)
    balance = expiring = 0.0
    for o in orders:
        if o["returned"]:
            continue
        age = (TODAY - o["order_date"]).days
        pts = (o["amount"] - o["discount"]) // 100 * 2 * (1 - redeem)
        if age <= 365:
            balance += pts
            if age >= 335:
                expiring += pts
    return int(balance), int(expiring)


# --- Database ----------------------------------------------------------------------------------
SCHEMA = """
DROP TABLE IF EXISTS customers; DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS carts; DROP TABLE IF EXISTS cart_items; DROP TABLE IF EXISTS customer_features;
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY, name TEXT, gender TEXT, age INTEGER, city TEXT, city_tier INTEGER,
    signup_date TEXT, preferred_payment TEXT, favourite_category TEXT,
    reward_points INTEGER, points_expiring_30d INTEGER,
    true_persona TEXT  -- hidden ground truth, used only to measure segmentation accuracy
);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER, order_date TEXT, category TEXT,
    items INTEGER, amount INTEGER, discount INTEGER, coupon_used INTEGER, payment_method TEXT, returned INTEGER
);
CREATE TABLE sessions (
    customer_id INTEGER, session_date TEXT, category_viewed TEXT, pages_viewed INTEGER, device TEXT
);
CREATE TABLE carts (customer_id INTEGER, created_date TEXT, cart_value INTEGER, status TEXT);
CREATE TABLE cart_items (customer_id INTEGER, added_date TEXT, product TEXT, category TEXT, price INTEGER);
CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_sessions_customer ON sessions(customer_id);
CREATE INDEX idx_carts_customer ON carts(customer_id);
"""


def insert(conn, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    conn.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        [[r[c].isoformat() if isinstance(r[c], date) else r[c] for c in cols] for r in rows],
    )


def main() -> None:
    names = list(PERSONAS)
    shares = np.array([PERSONAS[n]["share"] for n in names])
    personas = rng.choice(names, size=N_CUSTOMERS, p=shares / shares.sum())

    customers, orders, sessions, carts, items = [], [], [], [], []
    for cid, persona in enumerate(personas, start=1):
        c = make_customer(cid, persona)
        o = make_orders(c, persona)
        cr, it = make_carts(c, persona, o)
        c["reward_points"], c["points_expiring_30d"] = reward_points(persona, o)
        customers.append({k: v for k, v in c.items() if not k.startswith("_")})
        orders += o
        sessions += make_sessions(c, persona)
        carts += cr
        items += it

    DB_PATH.parent.mkdir(exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    insert(conn, "customers", customers)
    insert(conn, "orders", orders)
    insert(conn, "sessions", sessions)
    insert(conn, "carts", carts)
    insert(conn, "cart_items", items)
    conn.commit()
    n = build_features(conn, today=TODAY)
    conn.close()
    print(f"Created {len(customers)} customers, {len(orders)} orders, {len(sessions)} sessions, "
          f"{len(carts)} carts -> {DB_PATH}")
    print(f"Computed features and segments for {n} customers.")


if __name__ == "__main__":
    main()
