"""The agent must never let a rule-breaking AI answer through, and must recover with a safe default."""
from engine import agent


def run(monkeypatch, rules, f, answers):
    """Run agent.decide with a fake Claude that returns `answers` one after another."""
    calls = []

    def fake_ask(client, f, menu, feedback):
        calls.append(feedback)
        answer = answers[len(calls) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(agent, "get_client", lambda: object())
    monkeypatch.setattr(agent, "ask_claude", fake_ask)
    decision, blocked = agent.decide(f, rules, 0, 0)
    return decision, blocked, calls


GOOD = dict(offer_type="percent_off", category="fashion", value=15, reason="win back",
            message="Hi Riya! 💛 We miss you. Enjoy 15% off fashion on orders above ₹499.")
TOO_BIG = {**GOOD, "value": 40, "message": "Hi Riya! 40% off fashion!"}


def test_good_ai_answer_is_used(monkeypatch, rules, make_customer):
    decision, blocked, calls = run(monkeypatch, rules, make_customer(), [GOOD])
    assert decision["source"] == "ai" and decision["value"] == 15
    assert blocked == [] and len(calls) == 1


def test_bad_answer_is_blocked_and_ai_retries_with_feedback(monkeypatch, rules, make_customer):
    decision, blocked, calls = run(monkeypatch, rules, make_customer(), [TOO_BIG, GOOD])
    assert decision["source"] == "ai" and decision["value"] == 15
    assert len(blocked) == 1
    assert any("above the limit" in v for v in calls[1])  # the retry was told what went wrong


def test_two_bad_answers_fall_back_to_safe_default(monkeypatch, rules, make_customer):
    decision, blocked, _ = run(monkeypatch, rules, make_customer(), [TOO_BIG, TOO_BIG])
    assert decision["source"] == "fallback"
    assert len(blocked) == 2


def test_ai_error_falls_back(monkeypatch, rules, make_customer):
    decision, blocked, _ = run(monkeypatch, rules, make_customer(), [RuntimeError("down")])
    assert decision["source"] == "fallback"
    assert "AI error" in blocked[0]["violations"][0]


def test_no_ai_call_when_customer_cannot_get_an_offer(monkeypatch, rules, make_customer):
    def must_not_call(*args):
        raise AssertionError("Claude should not be asked")

    monkeypatch.setattr(agent, "get_client", lambda: object())
    monkeypatch.setattr(agent, "ask_claude", must_not_call)
    decision, _ = agent.decide(make_customer(), rules, offers_last_7d=1, budget_used=0)
    assert decision["offer_type"] == "no_offer"
    assert "this week" in decision["reason"]
