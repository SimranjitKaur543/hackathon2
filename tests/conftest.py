import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.eligibility import is_holdout  # noqa: E402
from engine.rules import load_rules  # noqa: E402


@pytest.fixture
def rules():
    return load_rules()


def _first_non_holdout(start: int) -> int:
    cid = start
    while is_holdout(cid, 10):
        cid += 1
    return cid


@pytest.fixture
def make_customer():
    """Build a customer dict; override any field with keyword arguments."""
    def _make(**kw):
        f = dict(
            customer_id=_first_non_holdout(kw.pop("customer_id", 1)),
            name="Riya Sharma", segment="at_risk", top_category="fashion",
            avg_order_value=1200, open_cart_value=0, cart_items=[], cart_categories=[],
            reward_points=120, points_expiring_30d=40,
        )
        f.update(kw)
        return f
    return _make
