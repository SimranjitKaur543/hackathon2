"""Telegram bot for the Personalised Offer Engine.

Step 1 version: it only greets people on /start so we can confirm the bot works.
Run with:  python bot.py
"""
import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # hides the token from request logs


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "there"
    await update.message.reply_text(
        f"👋 Hi {name}! Welcome to the Offer Engine.\n\n"
        "Soon I'll send you personalised shopping offers. Stay tuned! 🎁"
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or token.startswith("paste-"):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and add your token.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    logging.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
