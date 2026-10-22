"""The AI agent: Claude picks one offer from the menu the rules allow and writes the message.

Claude never gets the final say. Its answer goes through the guardrail; if it breaks a rule, Claude is
told exactly what was wrong and gets one more try. If that also fails (or Claude is unreachable), the
safe rule-based default is used instead.
"""
import json
import logging
import os

import anthropic

from engine.eligibility import build_menu, can_send, fallback_decision
from engine.guardrail import check_decision
from engine.segments import SEGMENT_LABELS

log = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MAX_AI_ATTEMPTS = 2
STORE_NAME = "Bazaarly"

SYSTEM_PROMPT = f"""You are the offer engine for {STORE_NAME}, an Indian online store selling fashion, footwear,
beauty, home, electronics and grocery. For one customer you choose the single best offer and write the
Telegram message they will receive.

How to choose:
- You may ONLY pick from the "allowed_offers" menu. Copy offer_type and category exactly. For % off,
  flat ₹ off and bonus points, choose a whole-number value no higher than max_value; other offers use value 0.
- Spend as little as needed. A customer who would buy anyway should get a small or no-cost offer; a
  customer we are about to lose deserves a stronger one. Choosing no_offer is fine when it fits.
- Use what you know about them: favourite category, items left in the cart, points about to expire,
  how long since they last ordered.

How to write the message:
- Warm, short (under 280 characters), friendly Indian-English tone, 1–2 emojis, address them by first name.
- State the offer clearly with the exact number you chose (e.g. "15% off fashion" or "₹200 off").
- Mention the minimum order value if the offer has one, written as ₹ followed by the number.
- Do not invent anything: no other discounts, no free delivery unless that is the offer, no cashback, no
  gifts, no deadlines, no coupon codes (the system adds the code and expiry date itself).
- Leave message empty for no_offer.

In "reason", explain your choice in one sentence for the marketing team."""

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "offer_type": {"type": "string", "enum": ["percent_off", "flat_off", "free_delivery", "bonus_points", "cart_reminder", "no_offer"]},
        "category": {"type": "string"},
        "value": {"type": "integer"},
        "reason": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["offer_type", "category", "value", "reason", "message"],
    "additionalProperties": False,
}

_client = None


def get_client() -> anthropic.Anthropic | None:
    global _client
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key.startswith("sk-ant-"):
        return None
    if _client is None:
        _client = anthropic.Anthropic(api_key=key, timeout=60.0)
    return _client


def customer_summary(f: dict) -> dict:
    """What Claude is told about the customer: behaviour only, nothing it doesn't need."""
    return {
        "first_name": (f.get("name") or "").split(" ")[0],
        "customer_type": SEGMENT_LABELS[f["segment"]],
        "city": f.get("city"),
        "orders_last_18_months": f["orders_total"],
        "days_since_last_order": f["recency_days"],
        "usual_days_between_orders": f.get("avg_gap_days"),
        "average_order_value_rs": f["avg_order_value"],
        "share_of_orders_with_coupon": f["coupon_rate"],
        "favourite_category": f["top_category"],
        "visits_last_30_days": f.get("sessions_30d"),
        "items_in_cart": [f"{i['product']} (₹{i['price']})" for i in f.get("cart_items") or []],
        "cart_total_rs": f.get("open_cart_value") or 0,
        "reward_points": f.get("reward_points"),
        "points_expiring_in_30_days": f.get("points_expiring_30d"),
        "churn_risk_0_to_1": f.get("churn_risk"),
    }


def ask_claude(client: anthropic.Anthropic, f: dict, menu: list[dict], feedback: list[str]) -> dict:
    prompt = {"customer": customer_summary(f), "allowed_offers": menu}
    content = json.dumps(prompt, ensure_ascii=False, indent=1)
    if feedback:
        content += ("\n\nYour previous answer was rejected by the rules check for these reasons:\n- "
                    + "\n- ".join(feedback) + "\nFix every problem and answer again.")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # if this model declines, the API retries on a fallback model
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def decide(f: dict, rules: dict, offers_last_7d: int, budget_used: float) -> tuple[dict, list[dict]]:
    """Return (final decision, blocked attempts). The final decision has always passed the guardrail."""
    allowed, why = can_send(f, rules, offers_last_7d, budget_used)
    if not allowed:
        return dict(offer_type="no_offer", category="none", value=0, message="", reason=why, source="rules"), []

    menu = build_menu(f, rules)
    blocked = []
    client = get_client()
    if client:
        feedback: list[str] = []
        for attempt in range(1, MAX_AI_ATTEMPTS + 1):
            try:
                decision = ask_claude(client, f, menu, feedback)
            except (anthropic.APIError, RuntimeError, json.JSONDecodeError, StopIteration) as e:
                log.warning("Claude call failed (%s): %s", type(e).__name__, e)
                blocked.append(dict(attempt=attempt, decision=None, violations=[f"AI error: {e}"], source="ai"))
                break
            result = check_decision(decision, f, rules, offers_last_7d, budget_used)
            if result.ok:
                return {**decision, "source": "ai", "cost": result.cost}, blocked
            log.info("Guardrail blocked AI attempt %d: %s", attempt, result.violations)
            blocked.append(dict(attempt=attempt, decision=decision, violations=result.violations, source="ai"))
            feedback = result.violations

    decision = fallback_decision(f, menu, rules)
    result = check_decision(decision, f, rules, offers_last_7d, budget_used)
    if not result.ok:  # should never happen — the tests check every segment
        blocked.append(dict(attempt=0, decision=decision, violations=result.violations, source="fallback"))
        return dict(offer_type="no_offer", category="none", value=0, message="", reason="fallback blocked", source="rules"), blocked
    return {**decision, "cost": result.cost}, blocked
