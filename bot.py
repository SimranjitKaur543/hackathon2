"""Telegram bot for the Personalised Offer Engine.

Each classmate who starts the bot plays one synthetic customer and receives the offer the engine picks
for that customer. Run with:  python bot.py
"""
import asyncio
import html
import logging
import os
from contextlib import closing
from datetime import date, datetime

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

load_dotenv()

from engine import store  # noqa: E402  (needs .env loaded first)
from engine.agent import STORE_NAME, get_client  # noqa: E402
from engine.db import connect  # noqa: E402
from engine.eligibility import is_quiet_hours  # noqa: E402
from engine.pipeline import offer_for  # noqa: E402
from engine.rules import load_rules  # noqa: E402
from engine.segments import SEGMENT_LABELS, SEGMENT_ORDER  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # hides the token from request logs
log = logging.getLogger("bot")

ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID") or 0)
CATEGORY_NAMES = {"fashion": "fashion", "electronics": "electronics", "beauty": "beauty", "home": "home & kitchen",
                  "footwear": "footwear", "grocery": "grocery"}


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def rs(amount) -> str:
    return f"₹{int(amount):,}"


# --- Message builders ------------------------------------------------------------------------
def persona_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(SEGMENT_LABELS[s], callback_data=f"seg:{s}") for s in SEGMENT_ORDER]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("🎲 Surprise me", callback_data="seg:random")])
    return InlineKeyboardMarkup(rows)


def profile_text(f: dict) -> str:
    lines = [f"👤 <b>You are playing: {esc(f['name'])}</b>",
             f"📍 {esc(f['city'])} · {SEGMENT_LABELS[f['segment']]}"]
    if f["orders_total"]:
        lines.append(f"🛍️ {f['orders_total']} orders in 18 months, average {rs(f['avg_order_value'])}")
        lines.append(f"🕒 Last order {f['recency_days']} days ago")
        lines.append(f"🏷️ Used a coupon on {round(f['coupon_rate'] * 100)}% of orders")
    else:
        lines.append(f"🛍️ No orders yet · {f['sessions_30d']} visits in the last 30 days")
    lines.append(f"❤️ Favourite: {CATEGORY_NAMES.get(f['top_category'], f['top_category'])}")
    if f["cart_items"]:
        items = ", ".join(esc(i["product"]) for i in f["cart_items"])
        lines.append(f"🛒 In cart: {items} ({rs(f['open_cart_value'])})")
    if f["reward_points"]:
        expiring = f" ({f['points_expiring_30d']} expire this month)" if f["points_expiring_30d"] else ""
        lines.append(f"⭐ {f['reward_points']} reward points{expiring}")
    return "\n".join(lines)


def offer_headline(o, rules: dict) -> str:
    t, v, cat = o["offer_type"], o["value"], CATEGORY_NAMES.get(o["category"], o["category"])
    offers = rules["offers"]
    if t == "percent_off":
        cap = offers["percent_off"].get("max_discount_rs")
        return f"🏷️ <b>{v}% off {esc(cat)}</b> · min order {rs(offers['percent_off']['min_order_value_rs'])}" + (f" · save up to {rs(cap)}" if cap else "")
    if t == "flat_off":
        return f"🏷️ <b>{rs(v)} off</b> · min order {rs(offers['flat_off']['min_order_value_rs'])}"
    if t == "free_delivery":
        return "🚚 <b>Free delivery</b> on your next order"
    if t == "bonus_points":
        return f"⭐ <b>{v} bonus points</b> on your next order above {rs(offers['bonus_points']['min_order_value_rs'])}"
    if t == "cart_reminder":
        return "🛒 <b>Your cart is waiting</b>"
    return ""


def offer_card(result: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    o, f, rules, kind = result["offer"], result["customer"], result["rules"], result["kind"]
    first = esc(f["name"].split(" ")[0])

    if kind == "holdout" or (o is not None and o["offer_type"] == "holdout"):
        text = (f"👋 Hi {first}! Thanks for shopping with {STORE_NAME}.\n"
                f"New arrivals in {esc(CATEGORY_NAMES.get(o['category'], o['category']))} just dropped. Take a look!\n\n"
                "<i>🧪 You're in the control group: no offer this time. Comparing your clicks with offer "
                "receivers shows whether offers really work.</i>")
        keyboard = None if o["status"] != "sent" else InlineKeyboardMarkup([[
            InlineKeyboardButton("Shop now 🛍️", callback_data=f"claim:{o['offer_id']}"),
            InlineKeyboardButton("Not now", callback_data=f"decline:{o['offer_id']}")]])
        return text, keyboard

    if kind == "no_offer":
        return (f"🙏 Thanks for being with {STORE_NAME}, {first}!\n\n"
                f"<i>🤖 No offer this time. Why: {esc(o['reason'])}</i>"), None

    lines = []
    if kind == "already":
        lines.append("📬 <b>You already have this week's offer:</b>\n")
    lines += [esc(o["message"]), "", offer_headline(o, rules)]
    if o["code"]:
        lines.append(f"🔑 Code: <code>{esc(o['code'])}</code>")
    if o["offer_type"] != "cart_reminder":
        lines.append(f"⏳ Valid till {date.fromisoformat(o['valid_until']).strftime('%d %b')}")
    source = "AI" if o["source"] == "ai" else "rule-based default"
    lines.append(f"\n<i>🤖 Why ({source}): {esc(o['reason'])}</i>")

    keyboard = None
    if o["status"] == "sent":
        claim = "Go to cart 🛒" if o["offer_type"] == "cart_reminder" else "Claim 🎁"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(claim, callback_data=f"claim:{o['offer_id']}"),
                                          InlineKeyboardButton("Not interested", callback_data=f"decline:{o['offer_id']}")]])
    else:
        lines.append(f"<i>Status: {esc(o['status'])}</i>")
    return "\n".join(lines), keyboard


# --- Handlers -------------------------------------------------------------------------------
async def send_offer(chat_id: int, telegram_id: int, context: ContextTypes.DEFAULT_TYPE) -> dict:
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    result = await asyncio.to_thread(offer_for, telegram_id)  # Claude call runs off the event loop
    if result["kind"] == "not_linked":
        await context.bot.send_message(chat_id, "Send /start first to pick your shopper.")
        return result
    text, keyboard = offer_card(result)
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    return result


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    with closing(connect()) as conn:
        store.init(conn)
        cid = store.linked_customer(conn, user.id)
    if cid:
        await update.message.reply_text(
            f"👋 Welcome back, {user.first_name}!\n\n/offer – see your offer\n/profile – who you're playing\n"
            "/switch – play a different shopper")
        return
    await update.message.reply_text(
        f"👋 Hi {user.first_name}! Welcome to <b>{STORE_NAME}</b>'s Offer Engine.\n\n"
        "You'll play one of our (made-up) shoppers. Based on their shopping history, an AI picks the offer "
        "that makes the most business sense, and a rules check makes sure it never breaks the store's limits.\n\n"
        "<b>Pick a shopper type:</b>",
        parse_mode=ParseMode.HTML, reply_markup=persona_keyboard())


async def switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Which kind of shopper do you want to play?", reply_markup=persona_keyboard())


async def choose_persona(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    segment = query.data.split(":", 1)[1]
    user = query.from_user
    with closing(connect()) as conn:
        store.init(conn)
        cid = store.pick_customer(conn, None if segment == "random" else segment)
        store.link_user(conn, user.id, user.first_name, cid)
        f = store.load_customer(conn, cid)
    await query.edit_message_reply_markup(None)
    await context.bot.send_message(query.message.chat_id, profile_text(f), parse_mode=ParseMode.HTML)
    await send_offer(query.message.chat_id, user.id, context)


async def offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_offer(update.effective_chat.id, update.effective_user.id, context)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with closing(connect()) as conn:
        store.init(conn)
        cid = store.linked_customer(conn, update.effective_user.id)
        if not cid:
            await update.message.reply_text("Send /start first to pick your shopper.")
            return
        f = store.load_customer(conn, cid)
    await update.message.reply_text(profile_text(f) + "\n\n/switch – play someone else", parse_mode=ParseMode.HTML)


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, offer_id = query.data.split(":", 1)
    status = "claimed" if action == "claim" else "declined"
    with closing(connect()) as conn:
        row = store.set_status(conn, int(offer_id), query.from_user.id, status)
    if row is None:
        await query.answer("This offer isn't yours.", show_alert=True)
        return
    if row["status"] != "sent":
        await query.answer("You already answered this one.")
        return
    await query.answer("🎉 Claimed! Use the code at checkout." if status == "claimed" else "Got it, thanks for telling us!")
    await query.edit_message_reply_markup(None)
    note = "✅ <b>Claimed</b> – see you at checkout!" if status == "claimed" else "👍 Noted. You won't be pushed this again."
    await context.bot.send_message(query.message.chat_id, note, parse_mode=ParseMode.HTML)


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Your Telegram ID is {update.effective_user.id}")


# --- Admin ----------------------------------------------------------------------------------
def is_admin(update: Update) -> bool:
    return ADMIN_ID and update.effective_user.id == ADMIN_ID


async def send_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin: push this week's offer to everyone who has started the bot."""
    if not is_admin(update):
        await update.message.reply_text("Only the campaign admin can do that.")
        return
    rules = load_rules()
    if is_quiet_hours(datetime.now().time(), rules):
        q = rules["campaign"]["quiet_hours"]
        await update.message.reply_text(f"🌙 Quiet hours ({q['start']}–{q['end']}): no campaign messages now.")
        return
    with closing(connect()) as conn:
        store.init(conn)
        users = store.all_users(conn)
    counts: dict[str, int] = {}
    for u in users:
        result = await asyncio.to_thread(offer_for, u["telegram_id"])
        counts[result["kind"]] = counts.get(result["kind"], 0) + 1
        if result["kind"] in ("offer", "holdout"):
            text, keyboard = offer_card(result)
            try:
                await context.bot.send_message(u["telegram_id"], text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            except Exception as e:  # user blocked the bot, etc.
                log.warning("Could not message %s: %s", u["telegram_id"], e)
    await update.message.reply_text(f"📣 Campaign run for {len(users)} people: {counts}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.message.reply_text("Only the campaign admin can do that.")
        return
    with closing(connect()) as conn:
        store.init(conn)
        q = lambda sql: conn.execute(sql).fetchone()  # noqa: E731
        users = q("SELECT COUNT(*) FROM telegram_users")[0]
        sent, claimed = q("SELECT COUNT(*), SUM(status='claimed') FROM offers_sent WHERE is_holdout=0 AND offer_type!='no_offer'")
        h_sent, h_claimed = q("SELECT COUNT(*), SUM(status='claimed') FROM offers_sent WHERE is_holdout=1")
        blocked = q("SELECT COUNT(*) FROM guardrail_log")[0]
        budget = store.budget_used(conn)
    total = load_rules()["campaign"]["total_budget_rs"]
    rate = lambda c, n: f"{(c or 0) / n:.0%}" if n else "–"  # noqa: E731
    await update.message.reply_text(
        f"📊 Players: {users}\nOffers sent: {sent}, claimed {claimed or 0} ({rate(claimed, sent)})\n"
        f"Control group: {h_sent}, clicked {h_claimed or 0} ({rate(h_claimed, h_sent)})\n"
        f"AI answers blocked by rules: {blocked}\nBudget used: {rs(budget)} of {rs(total)}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Error handling update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        await context.bot.send_message(update.effective_chat.id, "😕 Something went wrong. Please try again.")


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Pick your shopper"), BotCommand("offer", "See your personalised offer"),
        BotCommand("profile", "Who you are playing"), BotCommand("switch", "Play a different shopper"),
    ])


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token.startswith("paste-"):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add your token.")
    load_rules()  # fail fast with a clear message if rules.yaml has a mistake
    with closing(connect()) as conn:
        store.init(conn)
    log.info("Claude is %s", "connected" if get_client() else "NOT configured: using rule-based default offers")

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("offer", offer))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("switch", switch))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("send_all", send_all))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(choose_persona, pattern=r"^seg:"))
    app.add_handler(CallbackQueryHandler(respond, pattern=r"^(claim|decline):\d+$"))
    app.add_error_handler(on_error)
    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
