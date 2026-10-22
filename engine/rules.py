"""Load rules.yaml and check it for mistakes, with error messages a marketing person can act on."""
from pathlib import Path

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from engine.segments import SEGMENT_ORDER

RULES_PATH = Path(__file__).resolve().parent.parent / "rules.yaml"

OFFER_TYPES = ("percent_off", "flat_off", "free_delivery", "bonus_points", "cart_reminder", "no_offer")
VALUE_OFFERS = ("percent_off", "flat_off", "bonus_points")  # offers that carry a number (%, ₹ or points)


class RulesError(ValueError):
    pass


def load_rules(path: Path = RULES_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            rules = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise RulesError(f"rules.yaml could not be read — check the spacing near: {e}") from e
    validate_rules(rules)
    return rules


def save_rules(rules: dict, path: Path = RULES_PATH) -> None:
    """Used by the dashboard. Validates first so a bad edit never reaches the bot, and keeps the
    explanatory comments in rules.yaml intact."""
    validate_rules(rules)
    rt = YAML()
    rt.preserve_quotes = True
    rt.width = 120
    with open(path, encoding="utf-8") as f:
        doc = rt.load(f)
    _merge(doc, rules)
    with open(path, "w", encoding="utf-8") as f:
        rt.dump(doc, f)


def _merge(doc: CommentedMap, new: dict) -> None:
    """Copy `new` into the round-trip document, touching only values that changed."""
    for key in [k for k in doc if k not in new]:
        del doc[key]
    for key, value in new.items():
        old = doc.get(key)
        if isinstance(value, dict) and isinstance(old, dict):
            _merge(old, value)
        elif old == value:
            continue
        elif isinstance(value, list):
            seq = CommentedSeq(value)
            if isinstance(old, CommentedSeq) and old.fa.flow_style():
                seq.fa.set_flow_style()
            doc[key] = seq
        elif isinstance(value, str):
            doc[key] = DoubleQuotedScalarString(value)  # keeps "21:30" a string, not a number
        else:
            doc[key] = value


def validate_rules(r: dict) -> None:
    errors = []
    for section in ("campaign", "offers", "segments", "categories", "message"):
        if not isinstance(r.get(section), dict):
            errors.append(f"Section '{section}' is missing.")
    if errors:
        raise RulesError(" ".join(errors))

    def non_negative(where: str, value) -> None:
        if not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{where} must be a number of 0 or more (it is {value!r}).")

    c = r["campaign"]
    for key in ("total_budget_rs", "holdout_percent", "max_offers_per_customer_per_week", "offer_valid_days"):
        non_negative(f"campaign → {key}", c.get(key))
    if isinstance(c.get("holdout_percent"), (int, float)) and c["holdout_percent"] > 50:
        errors.append("campaign → holdout_percent should be 50 or less.")

    for name, o in r["offers"].items():
        if name not in OFFER_TYPES:
            errors.append(f"offers → '{name}' is not a known offer type ({', '.join(OFFER_TYPES)}).")
            continue
        for key, value in (o or {}).items():
            if key != "label":
                non_negative(f"offers → {name} → {key}", value)
    for name in VALUE_OFFERS:
        if name in r["offers"] and "max_value" not in r["offers"][name]:
            errors.append(f"offers → {name} needs a max_value.")

    for seg, s in r["segments"].items():
        if seg not in SEGMENT_ORDER:
            errors.append(f"segments → '{seg}' is not a known customer type ({', '.join(SEGMENT_ORDER)}).")
            continue
        allowed = (s or {}).get("allowed_offers") or []
        if not allowed:
            errors.append(f"segments → {seg} needs at least one allowed offer (use no_offer to send nothing).")
        for offer in allowed:
            if offer not in r["offers"]:
                errors.append(f"segments → {seg} allows '{offer}', but that offer is not in the offer menu.")
        for key, value in (s or {}).items():
            if key != "allowed_offers":
                non_negative(f"segments → {seg} → {key}", value)
    for seg in SEGMENT_ORDER:
        if seg not in r["segments"]:
            errors.append(f"segments → '{seg}' is missing; every customer type needs rules.")

    for cat, v in r["categories"].items():
        non_negative(f"categories → {cat} → max_percent_off", (v or {}).get("max_percent_off"))

    non_negative("message → max_length", r["message"].get("max_length"))
    if errors:
        raise RulesError("Problems in rules.yaml:\n- " + "\n- ".join(errors))
