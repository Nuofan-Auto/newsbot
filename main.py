"""
Entry point for AI News Aggregator Telegram Bot.
"""
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing project modules
load_dotenv(Path(__file__).parent / ".env")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from news_aggregator.bot.telegram_bot import NewsBot

# ---------------------------------------------------------------------------
# Logging — rotating file, 10 MB per file, keep 5 backups (~60 MB total)
# ---------------------------------------------------------------------------
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
_file_handler = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "newsbot.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_file_handler])

logger = logging.getLogger(__name__)

_PID_FILE = Path(__file__).parent / "newsbot.pid"


def daemonize(pid_file: Path) -> None:
    """Double-fork to detach from the controlling terminal."""
    # First fork
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    # Second fork
    if os.fork() > 0:
        sys.exit(0)
    # Redirect stdin / stdout / stderr to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (sys.stdin.fileno(), sys.stdout.fileno(), sys.stderr.fileno()):
        os.dup2(devnull, fd)
    os.close(devnull)
    # Record PID so the process can be stopped with: kill $(cat newsbot.pid)
    pid_file.write_text(str(os.getpid()))


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set. Check your .env file.")
        sys.exit(1)

    daemonize(_PID_FILE)

    logger.info("Starting AI News Aggregator Bot (PID=%s)...", os.getpid())
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
    finally:
        _PID_FILE.unlink(missing_ok=True)
