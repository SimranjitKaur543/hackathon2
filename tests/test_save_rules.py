"""Saving from the dashboard must keep rules.yaml readable: same values back, comments kept."""
import shutil

from engine.rules import RULES_PATH, load_rules, save_rules


def test_save_round_trip_keeps_comments_and_values(tmp_path):
    path = tmp_path / "rules.yaml"
    shutil.copy(RULES_PATH, path)
    rules = load_rules(path)
    rules["campaign"]["total_budget_rs"] = 75000
    rules["campaign"]["quiet_hours"]["start"] = "22:00"
    rules["segments"]["deal_hunter"]["allowed_offers"] = ["free_delivery", "bonus_points"]
    rules["segments"]["deal_hunter"].pop("max_percent_off")
    save_rules(rules, path)

    again = load_rules(path)
    assert again == rules
    assert again["campaign"]["quiet_hours"]["start"] == "22:00"  # still a string, not a number
    text = path.read_text(encoding="utf-8")
    assert "# Total discount money" in text
    assert "allowed_offers: [free_delivery, bonus_points]" in text
