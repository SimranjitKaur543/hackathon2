# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personalised offer engine for a fictional Indian online store ("Bazaarly"), built for J-PAL Hackathon 2.
Synthetic customers → customer type → rules decide the allowed offers → Claude picks one and writes the
message → a Python guardrail checks it → the Telegram bot delivers it. A dashboard shows the results and
lets marketing edit the rules. See README.md for the full picture.

## Commands

```bash
pip install -r requirements.txt
python data/generate_data.py     # (re)build data/shop.db: 2,000 customers, fixed seed 42
python data/check_data.py        # realism report
python -m pytest                 # all tests; must pass before every commit
python bot.py                    # Telegram bot (needs TELEGRAM_BOT_TOKEN in .env)
python dashboard.py              # dashboard at http://localhost:8501
```

Platform: Windows with Git Bash. Use `python`, not `python3`. Scripts that print ₹ or emoji call
`sys.stdout.reconfigure(encoding="utf-8")`.

## Layout

- `engine/segments.py`: features and `assign_segment()` (pure function, ordered by `SEGMENT_ORDER`)
- `engine/rules.py`: `load_rules()` / `validate_rules()` / `save_rules()` (uses ruamel to keep YAML comments)
- `engine/eligibility.py`: `build_menu()`, `can_send()`, `is_holdout()`, `estimate_cost()`, `fallback_decision()`
- `engine/guardrail.py`: `check_decision()` returns `GuardrailResult(ok, violations, cost)`
- `engine/agent.py`: Claude call (structured JSON output), retry with feedback, fallback
- `engine/pipeline.py`: `offer_for(telegram_id)`, the single entry point the bot uses
- `engine/store.py`: bot tables (`telegram_users`, `offers_sent`, `guardrail_log`) and customer loading
- `bot.py`, `dashboard.py` + `dashboard/index.html` (vanilla HTML/CSS/JS, no build step, no framework)

## Rules that must not be broken

1. **Nothing reaches a customer without passing `check_decision()`.** The AI's output is never trusted on
   its own, and that includes the fallback (`tests/test_guardrail.py` checks the default for every segment).
2. **All business limits live in `rules.yaml`**, never hard-coded. New limits get a plain-English comment
   there, validation in `validate_rules()` with an error message a non-programmer can act on, a field in the
   dashboard's Rules tab, and a test.
3. **Segmentation uses behaviour only.** `customers.true_persona` is hidden ground truth for measuring accuracy
   and must never be read by `engine/` decision code.
4. **Secrets stay in `.env`** (gitignored). Never print, log or commit the bot token or API key. `bot.py`
   sets the `httpx` logger to WARNING so the token never appears in request logs.
5. **Synthetic data only.** The bot stores just the Telegram ID and first name.
6. Rules are re-read on every offer, so dashboard edits apply without a restart. Keep it that way.

## Claude API usage

- Model from `CLAUDE_MODEL` (default `claude-opus-5-5`), `output_config.effort: "low"`, JSON schema in
  `DECISION_SCHEMA`, server-side fallback (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`).
- If the key is missing or has no credits, `decide()` logs the error and uses the rule-based default, so the
  bot keeps working without the AI.
- When changing the prompt, keep "pick only from allowed_offers" and "don't invent offers" wording. The
  guardrail is the real enforcement, but a clear prompt reduces blocked answers.

## Conventions

- Python 3.11, type hints, small pure functions where possible; comments explain *why*.
- Money is in ₹ (integers in data, floats for costs). Offer types: `percent_off`, `flat_off`,
  `free_delivery`, `bonus_points`, `cart_reminder`, `no_offer` (plus `holdout` rows for the control group).
- Commit small working steps; end commit messages with the Co-Authored-By line.
- `data/shop.db` is generated and not committed. Bot tables are `CREATE IF NOT EXISTS`, so regenerating the
  customer data keeps the offer history.

## How this was built with Claude Code

Planned in plan mode from the hackathon brief, then built step by step with a commit after each:
bot skeleton → synthetic data + segmentation → rules, eligibility and guardrail + tests → Claude agent and offer
cards → HTML dashboard → docs. Each step was checked by running it: tests, an end-to-end offer flow without
Telegram, a guardrail sweep over all 2,000 customers, and a browser check of every dashboard tab.
