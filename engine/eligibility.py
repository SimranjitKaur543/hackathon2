"""Work out which offers the rules allow for a customer (the "menu" the AI may choose from),
whether we may message them at all right now, what an offer costs, and a safe rule-based default.

`f` is a customer dict: the columns of `customer_features` plus `name`, and optionally
`cart_categories` and `cart_items` (list of {product, category, price}).
"""
import hashlib
from datetime import time

# Offers that apply to the whole order rather than one product category.
NO_CATEGORY = {"flat_off": "all", "free_delivery": "all", "bonus_points": "all", "cart_reminder": "cart", "no_offer": "none"}


def offer_categories(f: dict) -> list[str]:
    """Categories an offer may target: the customer's favourite, then anything sitting in their cart."""
    cats = [f["top_category"]] + list(f.get("cart_categories") or [])
    return list(dict.fromkeys(c for c in cats if c))


def build_menu(f: dict, rules: dict) -> list[dict]:
    """Every offer this customer may receive, with the highest value the rules allow for it."""
    seg = rules["segments"][f["segment"]]
    offers = rules["offers"]
    menu = []
    for offer_type in seg["allowed_offers"]:
        o = offers[offer_type]
        entry = dict(offer_type=offer_type, label=o.get("label", offer_type),
                     min_order_value_rs=o.get("min_order_value_rs", 0), max_value=0)
        if offer_type == "percent_off":
            for cat in offer_categories(f):
                cap = min(o["max_value"],
                          seg.get("max_percent_off", o["max_value"]),
                          rules["categories"].get(cat, {}).get("max_percent_off", o["max_value"]))
                if cap >= 5:  # anything smaller is not worth sending
                    menu.append({**entry, "category": cat, "max_value": int(cap),
                                 "max_discount_rs": o.get("max_discount_rs")})
            continue
        if offer_type == "flat_off":
            entry["max_value"] = int(min(o["max_value"], seg.get("max_flat_off_rs", o["max_value"])))
        elif offer_type == "bonus_points":
            entry["max_value"] = int(min(o["max_value"], seg.get("max_bonus_points", o["max_value"])))
        elif offer_type == "cart_reminder" and not f.get("open_cart_value"):
            continue  # nothing in the cart to remind them about
        if offer_type in ("flat_off", "bonus_points") and entry["max_value"] <= 0:
            continue
        menu.append({**entry, "category": NO_CATEGORY[offer_type]})
    return menu


def is_holdout(customer_id: int, holdout_percent: float) -> bool:
    """Stable random split: the same customer is always in (or out of) the holdout group."""
    bucket = int(hashlib.md5(f"holdout-{customer_id}".encode()).hexdigest(), 16) % 100
    return bucket < holdout_percent


def can_send(f: dict, rules: dict, offers_last_7d: int, budget_used: float) -> tuple[bool, str]:
    c = rules["campaign"]
    if is_holdout(f["customer_id"], c["holdout_percent"]):
        return False, "holdout group (gets no offer so we can measure the effect of offers)"
    if offers_last_7d >= c["max_offers_per_customer_per_week"]:
        return False, f"already received {offers_last_7d} offer(s) this week"
    if budget_used >= c["total_budget_rs"]:
        return False, "campaign budget used up"
    return True, "ok"


def is_quiet_hours(now: time, rules: dict) -> bool:
    q = rules["campaign"]["quiet_hours"]
    start, end = time.fromisoformat(q["start"]), time.fromisoformat(q["end"])
    if start <= end:
        return start <= now < end
    return now >= start or now < end  # window crosses midnight, e.g. 21:30–09:00


def estimate_cost(offer_type: str, value: float, f: dict, rules: dict) -> float:
    """Expected ₹ cost of an offer if it is used — counted against the budget when sent."""
    o = rules["offers"].get(offer_type, {})
    if offer_type == "percent_off":
        basket = max(f.get("avg_order_value") or 0, f.get("open_cart_value") or 0, o.get("min_order_value_rs", 0))
        return round(min(basket * value / 100, o.get("max_discount_rs", float("inf"))), 2)
    if offer_type == "flat_off":
        return float(value)
    if offer_type == "free_delivery":
        return float(o.get("cost_rs", 0))
    if offer_type == "bonus_points":
        return round(value * o.get("cost_per_point_rs", 0), 2)
    return 0.0


def _first_name(f: dict) -> str:
    return (f.get("name") or "").split(" ")[0] or "there"


def fallback_decision(f: dict, menu: list[dict], rules: dict) -> dict:
    """Safe, rule-based choice used when the AI is unavailable or its answer is blocked.
    Takes the segment's first-choice offer at a moderate value (half the allowed maximum)."""
    days = rules["campaign"]["offer_valid_days"]
    if not menu:
        return dict(offer_type="no_offer", category="none", value=0, message="", reason="no offers allowed", source="fallback")
    entry = menu[0]
    t, cap, min_rs, name = entry["offer_type"], entry["max_value"], entry["min_order_value_rs"], _first_name(f)
    if t == "percent_off":
        value = max(5, cap // 2 // 5 * 5)
        msg = f"Hi {name}! 🎉 Here's {value}% off {entry['category']} just for you, on orders above ₹{min_rs}. Valid for {days} days."
    elif t == "flat_off":
        value = max(50, cap // 2 // 50 * 50)
        msg = f"Hi {name}! 🎁 Get ₹{value} off your next order above ₹{min_rs}. Valid for {days} days."
    elif t == "bonus_points":
        value = max(10, cap // 2 // 10 * 10)
        msg = f"Hi {name}! ⭐ Earn {value} bonus reward points on your next order above ₹{min_rs}. Thank you for shopping with us!"
    elif t == "free_delivery":
        value = 0
        msg = f"Hi {name}! 🚚 Enjoy free delivery on your next order. Valid for {days} days."
    elif t == "cart_reminder":
        value = 0
        msg = f"Hi {name}! 🛒 Your cart is still saved. Pick up where you left off whenever you're ready."
    else:
        value, msg = 0, ""
    return dict(offer_type=t, category=entry["category"], value=value, message=msg,
                reason=f"rule-based default for {f['segment']}", source="fallback")
