"""
Entry point for AI News Aggregator Telegram Bot.
"""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing project modules
load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from news_aggregator.bot.telegram_bot import NewsBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Check your .env file.")
        sys.exit(1)

    logger.info("Starting AI News Aggregator Bot...")
    bot = NewsBot(token)
    app = bot.build_app()

    app.run_polling(
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C).")
