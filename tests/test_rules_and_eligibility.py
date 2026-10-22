import copy
from datetime import time

import pytest

from engine.eligibility import build_menu, estimate_cost, is_holdout, is_quiet_hours
from engine.rules import RulesError, validate_rules
from engine.segments import assign_segment


def test_rules_file_is_valid(rules):
    validate_rules(rules)


@pytest.mark.parametrize("break_it, message", [
    (lambda r: r["segments"]["loyal"].update(allowed_offers=["mystery_box"]), "mystery_box"),
    (lambda r: r["campaign"].update(total_budget_rs=-5), "total_budget_rs"),
    (lambda r: r["segments"].pop("at_risk"), "at_risk"),
    (lambda r: r["campaign"].update(holdout_percent=80), "holdout_percent"),
])
def test_bad_rules_give_clear_errors(rules, break_it, message):
    bad = copy.deepcopy(rules)
    break_it(bad)
    with pytest.raises(RulesError, match=message):
        validate_rules(bad)


def test_menu_respects_segment_and_category_limits(rules, make_customer):
    f = make_customer(segment="at_risk", top_category="fashion", cart_categories=["electronics"])
    menu = {(m["offer_type"], m["category"]): m["max_value"] for m in build_menu(f, rules)}
    assert menu[("percent_off", "fashion")] == 20       # segment cap (20) < fashion cap (25)
    assert menu[("percent_off", "electronics")] == 8    # category cap wins
    assert menu[("flat_off", "all")] == 250


def test_loyal_menu_has_no_discounts(rules, make_customer):
    types = {m["offer_type"] for m in build_menu(make_customer(segment="loyal"), rules)}
    assert types == {"bonus_points", "no_offer"}


def test_cart_reminder_only_with_items_in_cart(rules, make_customer):
    empty = build_menu(make_customer(segment="cart_abandoner", open_cart_value=0), rules)
    full = build_menu(make_customer(segment="cart_abandoner", open_cart_value=999), rules)
    assert "cart_reminder" not in {m["offer_type"] for m in empty}
    assert "cart_reminder" in {m["offer_type"] for m in full}


def test_percent_cost_is_capped(rules, make_customer):
    f = make_customer(avg_order_value=10000)
    assert estimate_cost("percent_off", 20, f, rules) == rules["offers"]["percent_off"]["max_discount_rs"]


def test_holdout_is_about_ten_percent_and_stable():
    share = sum(is_holdout(i, 10) for i in range(1, 5001)) / 5000
    assert 0.08 < share < 0.12
    assert is_holdout(42, 10) == is_holdout(42, 10)


def test_quiet_hours_cross_midnight(rules):
    assert is_quiet_hours(time(23, 0), rules)
    assert is_quiet_hours(time(7, 0), rules)
    assert not is_quiet_hours(time(13, 0), rules)


def base_features(**kw):
    f = dict(days_since_signup=400, orders_total=10, orders_180d=4, recency_days=20,
             coupon_rate=0.2, open_cart_value=0, open_cart_age_days=None)
    f.update(kw)
    return f


@pytest.mark.parametrize("features, segment", [
    (base_features(days_since_signup=10, orders_total=0), "new"),
    (base_features(orders_total=0, recency_days=10**6), "window_shopper"),
    (base_features(recency_days=200), "lapsed"),
    (base_features(recency_days=90), "at_risk"),
    (base_features(orders_180d=8, recency_days=5), "loyal"),
    (base_features(coupon_rate=0.9), "deal_hunter"),
    (base_features(open_cart_value=1500, open_cart_age_days=2), "cart_abandoner"),
    (base_features(), "regular"),
])
def test_segments(features, segment):
    assert assign_segment(features) == segment
