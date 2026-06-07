"""
core/logger.py — Color-coded terminal + rotating file logger
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from colorama import Fore, Style, init

init(autoreset=True)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG:    Fore.CYAN,
        logging.INFO:     Fore.GREEN,
        logging.WARNING:  Fore.YELLOW,
        logging.ERROR:    Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }
    LEVEL_ICONS = {
        logging.DEBUG:    "🔍",
        logging.INFO:     "✅",
        logging.WARNING:  "⚠️ ",
        logging.ERROR:    "❌",
        logging.CRITICAL: "🚨",
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        icon  = self.LEVEL_ICONS.get(record.levelno, "")
        ts    = datetime.now().strftime("%H:%M:%S")
        msg   = super().format(record)
        return f"{Style.DIM}[{ts}]{Style.RESET_ALL} {color}{icon} {msg}{Style.RESET_ALL}"


def get_logger(name: str = "StockAIBot") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # ── Terminal handler (color) ──────────────────────────
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(ColorFormatter())
    logger.addHandler(ch)

    # ── File handler (plain, rotating 10MB × 5 files) ────
    today     = datetime.now().strftime("%Y-%m-%d")
    log_file  = LOG_DIR / f"stockbot_{today}.log"
    fh = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    return logger


def log_banner(logger: logging.Logger, ticker: str, score: int):
    """Print a visual banner for high-conviction signals."""
    bar = "█" * (score // 5) + "░" * (20 - score // 5)
    stars = "★" * min(5, score // 20)
    logger.info(f"\n{'='*60}")
    logger.info(f"  🎯  HIGH-CONVICTION SIGNAL: {ticker}")
    logger.info(f"  Confluence Score: [{bar}] {score}/100  {stars}")
    logger.info(f"{'='*60}")
