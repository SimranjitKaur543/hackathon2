"""The safety check must block every offer that breaks a rule, and let good offers through."""
import pytest

from engine.eligibility import build_menu, fallback_decision, is_holdout
from engine.guardrail import check_decision
from engine.segments import SEGMENT_ORDER


def offer(**kw):
    d = dict(offer_type="percent_off", category="fashion", value=15,
             message="Hi Riya! We miss you 💛 Here's 15% off fashion on orders above ₹499. Valid for 3 days.")
    d.update(kw)
    return d


def test_good_offer_passes(rules, make_customer):
    result = check_decision(offer(), make_customer(), rules)
    assert result.ok, result.violations
    assert result.cost > 0


def test_discount_above_segment_limit_is_blocked(rules, make_customer):
    # at_risk is capped at 20%
    r = check_decision(offer(value=22, message="Hi Riya! 22% off fashion for you."), make_customer(), rules)
    assert not r.ok
    assert any("above the limit" in v for v in r.violations)


def test_category_margin_limit_is_enforced(rules, make_customer):
    # electronics is capped at 8% even though at_risk allows 20%
    f = make_customer(top_category="electronics")
    r = check_decision(offer(category="electronics", value=15, message="15% off electronics!"), f, rules)
    assert not r.ok
    assert any("above the limit of 8" in v for v in r.violations)


def test_loyal_customers_never_get_a_discount(rules, make_customer):
    f = make_customer(segment="loyal")
    r = check_decision(offer(value=10, message="Hi Riya! 10% off fashion."), f, rules)
    assert not r.ok
    assert any("not allowed for a loyal customer" in v for v in r.violations)


def test_category_not_relevant_to_customer_is_blocked(rules, make_customer):
    r = check_decision(offer(category="beauty", message="15% off beauty!"), make_customer(), rules)
    assert not r.ok


def test_weekly_frequency_cap(rules, make_customer):
    r = check_decision(offer(), make_customer(), rules, offers_last_7d=1)
    assert not r.ok
    assert any("this week" in v for v in r.violations)


def test_budget_cannot_be_exceeded(rules, make_customer):
    budget = rules["campaign"]["total_budget_rs"]
    r = check_decision(offer(), make_customer(), rules, budget_used=budget - 10)
    assert not r.ok
    assert any("budget" in v for v in r.violations)


def test_holdout_customers_get_nothing(rules, make_customer):
    cid = next(i for i in range(1, 1000) if is_holdout(i, rules["campaign"]["holdout_percent"]))
    f = make_customer()
    f["customer_id"] = cid
    r = check_decision(offer(), f, rules)
    assert not r.ok
    assert any("holdout" in v for v in r.violations)


@pytest.mark.parametrize("message, problem", [
    ("Hi Riya! Huge 50% off fashion just for you!", "approved offer is 15%"),
    ("Hi Riya! A special fashion deal just for you!", "does not state the approved 15%"),
    ("Hi Riya! 15% off fashion plus free delivery!", "free delivery"),
    ("Hi Riya! 15% off fashion and ₹200 cashback!", "cashback"),
    ("Hi Riya! 15% off fashion, guaranteed lowest price ever!", "guaranteed"),
    ("Hi Riya! 15% off fashion and 500 bonus points!", "points"),
    ("Hi Riya! 15% off fashion! " + "x" * 400, "characters"),
])
def test_message_cannot_misstate_the_offer(rules, make_customer, message, problem):
    r = check_decision(offer(message=message), make_customer(), rules)
    assert not r.ok
    assert any(problem in v for v in r.violations), r.violations


def test_flat_off_amount_must_match(rules, make_customer):
    f = make_customer()
    good = offer(offer_type="flat_off", category="all", value=200, message="Hi Riya! ₹200 off orders above ₹999.")
    bad = offer(offer_type="flat_off", category="all", value=200, message="Hi Riya! ₹300 off orders above ₹999.")
    assert check_decision(good, f, rules).ok
    assert not check_decision(bad, f, rules).ok


def test_free_delivery_must_have_zero_value(rules, make_customer):
    r = check_decision(offer(offer_type="free_delivery", category="all", value=50,
                             message="Free delivery on your next order!"), make_customer(), rules)
    assert not r.ok


def test_unknown_offer_type_is_blocked(rules, make_customer):
    r = check_decision(offer(offer_type="buy_one_get_one"), make_customer(), rules)
    assert not r.ok


def test_no_offer_is_always_allowed(rules, make_customer):
    assert check_decision(dict(offer_type="no_offer", value=0, message=""), make_customer(segment="loyal"), rules).ok


@pytest.mark.parametrize("segment", SEGMENT_ORDER)
def test_safe_default_always_passes_the_guardrail(rules, make_customer, segment):
    """The fallback is what we send when the AI fails, so it must never break a rule itself."""
    for cat in ("fashion", "electronics", "grocery", "beauty"):
        f = make_customer(segment=segment, top_category=cat, open_cart_value=1598,
                          cart_items=[{"product": "Sneakers", "category": "footwear", "price": 1598}],
                          cart_categories=["footwear"])
        decision = fallback_decision(f, build_menu(f, rules), rules)
        r = check_decision(decision, f, rules)
        assert r.ok, (segment, cat, decision, r.violations)
