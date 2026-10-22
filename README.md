# 🎁 Personalised Offer Engine

**Hackathon 2 · Punjab Jobs AI Bootcamp · J-PAL South Asia** · built with Claude Code

An online store shouldn't send everyone the same discount. A coupon that wins back a customer who is about
to leave is money well spent; the same coupon sent to someone who would have bought anyway is lost margin.

This project is the **brain that decides which customer gets which offer, or no offer**, for a made-up Indian
online store called **Bazaarly**. It delivers each offer through a **Telegram bot**:

1. **2,000 synthetic customers** with 18 months of realistic shopping behaviour.
2. **Rules** that a marketing team edits without touching code: budget, discount limits, who may get what.
3. An **AI agent (Claude)** that picks the best allowed offer for each customer and writes a personal message.
4. A **safety check** in plain Python that verifies every AI choice against the rules **before** it is sent.
5. A **control group** and a **dashboard** that show whether the offers actually work.

**Try the bot:** [t.me/simran12345_newbot](https://t.me/simran12345_newbot) → press **Start** → pick a shopper type.

---

## How it works

```
 data/generate_data.py ─► SQLite: customers, orders, browsing sessions, carts, reward points
                                   │
 engine/segments.py ──────► features + customer type (from behaviour only)
                                   │
 rules.yaml ─► engine/eligibility.py ─► the "menu": offers this customer may get, with max values
                                   │
               engine/agent.py ─► Claude picks ONE offer from the menu + writes the message (JSON)
                                   │
            engine/guardrail.py ─► checks every rule ── fail ─► Claude retries once with the reasons
                                   │                            fail again / AI down ─► safe default offer
                                   ▼
                     bot.py ─► Telegram offer card with code, expiry, Claim / Not interested
                                   │
                  dashboard.py ─► results, blocked AI answers, rule editor, data checks
```

**The AI chooses; the rules decide.** Claude only ever sees the offers the rules allow for that customer, and
its answer is still checked again in code. Nothing the AI says is trusted without that check.

## What a classmate sees (example)

> 👤 **You are playing: Charan Gill** · 📍 Ludhiana · 🟡 At risk
> 🛍️ 9 orders in 18 months, average ₹1,850 · 🕒 Last order 84 days ago
>
> Hi Charan! 💛 We've missed you. Here's 15% off fashion on orders above ₹499.
> 🏷️ **15% off fashion** · min order ₹499 · save up to ₹400
> 🔑 Code: `SAVE15-C4FC` · ⏳ Valid till 25 Oct
> 🤖 *Why (AI): at-risk fashion buyer, gone quiet for nearly 3 months, so a strong win-back offer is worth it.*
> **[ Claim 🎁 ] [ Not interested ]**

Bot commands: `/start` pick a shopper · `/offer` your offer · `/profile` who you're playing · `/switch` play someone else.
Admin only: `/send_all` push this week's offers to everyone (blocked during quiet hours) · `/stats` quick numbers · `/myid` find your Telegram ID.

---

## Setup

Needs **Python 3.11+**.

```bash
git clone https://github.com/SimranjitKaur543/hackathon2.git
cd hackathon2
pip install -r requirements.txt
```

**1. Secrets.** Copy `.env.example` to `.env` and fill it in. `.env` is in `.gitignore`, so it never reaches GitHub.

| Variable | Where to get it | Needed? |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` | yes |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys (needs credits) | optional: without it the bot sends rule-based default offers |
| `ADMIN_TELEGRAM_ID` | send `/myid` to your bot | optional: unlocks `/send_all` and `/stats` |
| `CLAUDE_MODEL` | e.g. `claude-haiku-5-5` for a cheaper model | optional, default `claude-opus-5-5` |

**2. Create the customer data** (about 10 seconds; anyone can rebuild it, so it isn't committed):

```bash
python data/generate_data.py
python data/check_data.py        # prints realism checks
```

**3. Run the bot and the dashboard** (two terminals):

```bash
python bot.py                    # Telegram bot
python dashboard.py              # dashboard at http://localhost:8501
```

**4. Run the tests:**

```bash
python -m pytest
```

---

## For the marketing team: changing the rules

Everything the engine may do is in **[`rules.yaml`](rules.yaml)**, and every line has a plain-English comment.
You can edit it in two ways:

- **Dashboard → ⚙️ Rules** (easiest): change numbers and tick offers, press **👀 Preview** to see the effect on all
  2,000 customers (cost, how many get discounts), then **💾 Save rules**. Mistakes are caught with a clear message
  before anything is saved.
- **Edit the file directly**, then save it.

Either way, **the very next offer uses the new rules**; no restart is needed.

| Rule | Current setting |
|---|---|
| Campaign budget | ₹50,000 |
| Control group | 10% of customers never get an offer, to measure the effect |
| Frequency | max 1 offer per customer per week |
| Quiet hours | no pushed offers 21:30–09:00 |
| Loyal customers | **no discounts**, bonus points only (they buy anyway) |
| At risk / lapsed | up to 20% / 25% off, or ₹250 / ₹300 flat |
| Deal hunters | at most 10% off (protect margin) |
| Electronics, grocery | at most 8% off (thin margins) |
| A % offer | can never save more than ₹400 |
| Messages | ≤ 350 characters; never "guaranteed", "cashback", "free gift", "lowest price ever"… |

## The safety check (guardrail)

[`engine/guardrail.py`](engine/guardrail.py) blocks an offer if:

- the offer type isn't allowed for that customer type, or targets a category that isn't theirs
- the value is above the limit (taking the lowest of the offer, customer-type and category caps)
- the customer already had an offer this week, is in the control group, or the budget would be exceeded
- **the message says something different from the offer:** a different %, a ₹ amount or points that aren't part of
  it, "free delivery" when that isn't the offer, or no mention of the actual value
- the message uses a banned phrase or is too long

When Claude's answer is blocked, Claude is told exactly which rules it broke and gets one more try. If that also
fails, or Claude is unavailable, a **rule-based default** is sent: the first offer in that customer type's list,
at half the allowed maximum. Tests prove that this default passes the guardrail for every customer type.
Every blocked answer is logged and shown in **Dashboard → 🛡️ Safety check**, which also has a
**"Try to break the rules"** box for demos.

## Synthetic data: why it behaves like real customers

Each customer is given a hidden **true persona** (loyal, regular, deal hunter, at risk, lapsed, new, window shopper)
that drives their behaviour. The engine **never reads it**: it works out the customer type from behaviour alone,
as a real platform would, and the hidden persona is used only to measure that sorting.

| Check | This data | Real e-commerce |
|---|---|---|
| Customers sorted into the right type | **94%** | – |
| Cart abandonment | 67% | ~70% |
| Revenue from the top 10% of buyers | 40% | 40–50% |
| Order value | median ₹1,249, mean ₹2,024 (long tail) | right-skewed |
| Cash on delivery: tier-1 / tier-2 / tier-3 cities | 17% / 29% / 48% | COD much higher in small towns |
| Returns: fashion vs grocery | 23% vs 1% | fashion highest |
| Orders per month | spikes at festive sale/Diwali, Republic Day, end of season | festive spikes |

The data also includes Indian-style prices (₹799, ₹1,299), Punjab and other tier-1/2/3 cities, UPI/card/COD
payments, browsing sessions, open carts with real product names, and reward points that **expire after 12 months**
(535 customers have points expiring this month, a natural reason to send them an offer).

## Does it work? Measuring the offers

- **Control group:** a stable 10% of customers (by hashed customer ID) get a plain "new arrivals" message with a
  *Shop now* button instead of an offer. The dashboard compares the **offer claim rate with the control group's
  click rate**; the difference is the effect of the offers.
- Also tracked: claim rate per customer type and offer type, budget committed, and how many offers the AI wrote
  versus the safe default.

## Project structure

| Path | What it does |
|---|---|
| `bot.py` | Telegram bot: shopper picker, offer cards, Claim buttons, admin commands |
| `dashboard.py` + `dashboard/index.html` | Dashboard: a small built-in Python server plus an HTML/JS page |
| `rules.yaml` | All business rules, editable by marketing |
| `engine/segments.py` | Features (recency, frequency, coupon use, churn risk…) and customer type |
| `engine/eligibility.py` | Offer menu per customer, control group, budget/frequency checks, cost, safe default |
| `engine/guardrail.py` | The safety check every offer must pass |
| `engine/agent.py` | Claude prompt, structured JSON output, retry with feedback, fallback |
| `engine/pipeline.py` | One call the bot uses: decide, check, record |
| `engine/store.py` | Players, offers sent, blocked-answer log (SQLite) |
| `engine/rules.py` | Loads, validates and saves `rules.yaml` (comments kept) |
| `data/generate_data.py`, `data/check_data.py` | Synthetic data and its realism report |
| `tests/` | 52 tests: guardrail, eligibility, rules, segments, agent retry/fallback, rule saving |

## Limitations and next steps

- Claims are a stand-in for purchases: classmates can't actually buy, so "claimed" measures interest, not revenue.
- The control group is small at classroom scale; real conclusions need hundreds of people per group.
- Offer costs are estimates (expected discount on the customer's usual basket), counted when an offer is sent.
- Next: learn which offer works best per customer type from claim data (e.g. a bandit), A/B-test message styles,
  and add send-time optimisation.

## Privacy

All customer data is synthetic. The bot stores only each classmate's Telegram ID and first name. API keys and the
bot token live in `.env`, which is never committed.
