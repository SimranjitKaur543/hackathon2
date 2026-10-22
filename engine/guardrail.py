"""The safety check. Every offer — from the AI or anywhere else — passes through `check_decision`
before it is sent. It is plain Python and never trusts the AI's own claims."""
import re
from dataclasses import dataclass, field

from engine.eligibility import build_menu, can_send, estimate_cost
from engine.rules import OFFER_TYPES, VALUE_OFFERS

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
RUPEE_RE = re.compile(r"(?:₹|\brs\.?|\binr)\s*(\d[\d,]*)", re.I)
POINTS_RE = re.compile(r"(\d[\d,]*)\s+(?:(?:bonus|reward|extra)\s+)*(?:points|pts)\b", re.I)
FREE_DELIVERY_RE = re.compile(r"free\s+(?:delivery|shipping)", re.I)


@dataclass
class GuardrailResult:
    ok: bool
    violations: list[str] = field(default_factory=list)
    cost: float = 0.0


def _numbers(pattern: re.Pattern, text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in pattern.findall(text)]


def check_message(decision: dict, entry: dict, f: dict, rules: dict) -> list[str]:
    """The text must match the approved offer exactly and make no promises we don't offer."""
    t, value, msg = decision["offer_type"], decision["value"], decision.get("message") or ""
    rules_msg = rules["message"]
    problems = []
    if not msg.strip():
        return ["message is empty"]
    if len(msg) > rules_msg["max_length"]:
        problems.append(f"message is {len(msg)} characters (limit {rules_msg['max_length']})")
    lower = msg.lower()
    for phrase in rules_msg.get("banned_phrases", []):
        if phrase.lower() in lower:
            problems.append(f"message uses banned phrase '{phrase}'")

    percents = _numbers(PERCENT_RE, msg)
    if t == "percent_off":
        if value not in percents:
            problems.append(f"message does not state the approved {value}% off")
        if any(p != value for p in percents):
            problems.append(f"message mentions {percents}% but the approved offer is {value}%")
    elif percents:
        problems.append(f"message mentions a % discount ({percents}) but the offer is {t}")

    allowed_rs = {entry.get("min_order_value_rs", 0), f.get("open_cart_value") or 0}
    allowed_rs |= {i["price"] for i in f.get("cart_items") or []}
    if t == "flat_off":
        allowed_rs.add(value)
        if value not in _numbers(RUPEE_RE, msg):
            problems.append(f"message does not state the approved ₹{value} off")
    if t == "percent_off" and entry.get("max_discount_rs"):
        allowed_rs.add(entry["max_discount_rs"])
    bad_rs = [r for r in _numbers(RUPEE_RE, msg) if r not in allowed_rs]
    if bad_rs:
        problems.append(f"message mentions ₹ amounts that are not part of the offer: {bad_rs}")

    allowed_pts = {f.get("reward_points") or 0, f.get("points_expiring_30d") or 0}
    if t == "bonus_points":
        allowed_pts.add(value)
        if value not in _numbers(POINTS_RE, msg):
            problems.append(f"message does not state the approved {value} bonus points")
    bad_pts = [p for p in _numbers(POINTS_RE, msg) if p not in allowed_pts]
    if bad_pts:
        problems.append(f"message mentions points that are not part of the offer: {bad_pts}")

    if FREE_DELIVERY_RE.search(msg) and t != "free_delivery":
        problems.append("message promises free delivery, which is not part of this offer")
    if t == "free_delivery" and not FREE_DELIVERY_RE.search(msg):
        problems.append("message does not mention the free delivery")
    return problems


def check_decision(decision: dict, f: dict, rules: dict, offers_last_7d: int = 0, budget_used: float = 0.0) -> GuardrailResult:
    """Check one proposed offer against every rule. Returns all problems found, not just the first."""
    t = decision.get("offer_type")
    if t not in OFFER_TYPES:
        return GuardrailResult(False, [f"'{t}' is not a known offer type"])
    if t == "no_offer":
        return GuardrailResult(True)

    problems = []
    allowed, why = can_send(f, rules, offers_last_7d, budget_used)
    if not allowed:
        problems.append(f"customer cannot get an offer: {why}")

    menu = build_menu(f, rules)
    entry = next((m for m in menu if m["offer_type"] == t and m["category"] == decision.get("category")), None)
    if entry is None:
        options = ", ".join(f"{m['offer_type']}({m['category']})" for m in menu) or "none"
        problems.append(f"{t} for '{decision.get('category')}' is not allowed for a {f['segment']} customer "
                        f"(allowed: {options})")
        return GuardrailResult(False, problems)

    value = decision.get("value")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        problems.append(f"value must be a number (got {value!r})")
        return GuardrailResult(False, problems)
    if t in VALUE_OFFERS:
        if value != int(value) or value < 1:
            problems.append(f"value must be a whole number of 1 or more (got {value})")
        if value > entry["max_value"]:
            problems.append(f"{t} of {value} is above the limit of {entry['max_value']} for this customer")
    elif value != 0:
        problems.append(f"{t} must have value 0 (got {value})")

    cost = estimate_cost(t, value, f, rules)
    budget = rules["campaign"]["total_budget_rs"]
    if budget_used + cost > budget:
        problems.append(f"offer would cost ₹{cost:.0f} but only ₹{max(budget - budget_used, 0):.0f} of budget is left")

    problems += check_message(decision, entry, f, rules)
    return GuardrailResult(not problems, problems, cost)
