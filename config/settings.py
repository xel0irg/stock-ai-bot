"""
config/settings.py — Centralized config loader
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── Anthropic ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Reddit ────────────────────────────────────────────────
REDDIT_CLIENT_ID     = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT    = os.getenv("REDDIT_USER_AGENT", "Degenic$/1.0")

# ── StockTwits ────────────────────────────────────────────
STOCKTWITS_TOKEN = os.getenv("STOCKTWITS_ACCESS_TOKEN", "")

# ── Twitter/X ─────────────────────────────────────────────
TWITTER_BEARER = os.getenv("TWITTER_BEARER_TOKEN", "")

def _safe_float(env_var: str, default: float) -> float:
    """Parse an env var as float, falling back to default on blank/invalid values.
    Guards against GitHub Secrets that exist but are set to an empty string —
    os.getenv()'s own default only applies when the var is unset entirely."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _safe_int(env_var: str, default: int) -> int:
    """Same as _safe_float but for int settings."""
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ── Bot Behaviour ─────────────────────────────────────────
WATCHLIST = [t.strip().upper() for t in os.getenv("WATCHLIST", "AAPL,TSLA,NVDA,SPY").split(",")]
SCAN_INTERVAL_MINUTES = _safe_int("SCAN_INTERVAL_MINUTES", 15)
SENTIMENT_THRESHOLD   = _safe_float("SENTIMENT_THRESHOLD", 0.6)
CONFLUENCE_THRESHOLD  = _safe_int("CONFLUENCE_THRESHOLD", 65)

# Custom daily Anthropic API spend cap, enforced in code since Anthropic
# only supports monthly limits natively. Defaults to $5/day — generous
# enough for normal scanning + a handful of /scan commands, but caps a
# runaway loop or unexpected spike well before it becomes a real bill.
DAILY_SPEND_LIMIT_USD = _safe_float("DAILY_SPEND_LIMIT_USD", 5.0)
LOG_LEVEL             = os.getenv("LOG_LEVEL", "INFO")

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Discord ───────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# ── Paths ─────────────────────────────────────────────────
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ── Reddit Subreddits to scrape ───────────────────────────
REDDIT_SUBS = [
    "wallstreetbets",
    "stocks",
    "investing",
    "options",
    "StockMarket",
    "pennystocks",
    "Daytrading",
]

# ── News sources (RSS) ────────────────────────────────────
NEWS_RSS_FEEDS = {
    "Reuters":   "https://feeds.reuters.com/reuters/businessNews",
    "Bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "MarketWatch": "http://feeds.marketwatch.com/marketwatch/topstories",
    "SeekingAlpha": "https://seekingalpha.com/market_currents.xml",
    "Benzinga":  "https://www.benzinga.com/feed",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

# ── Technical indicator settings ─────────────────────────
TA_SETTINGS = {
    "rsi_period":         14,
    "macd_fast":          12,
    "macd_slow":          26,
    "macd_signal":        9,
    "bb_period":          20,
    "bb_std":             2,
    "volume_ma_period":   20,
    "atr_period":         14,
    "ema_short":          9,
    "ema_medium":         21,
    "ema_long":           50,
    "ema_very_long":      200,
}

# ── Strategy versioning ──────────────────────────────────
# Stamped onto every signals_log.csv row so directional accuracy and
# P&L can be attributed to a pipeline version. BUMP THIS whenever the
# scoring rubric, AI prompt, data source, or feed gating changes —
# otherwise old-code and new-code signals are indistinguishable forever
# (we could not tell whether June-July's 36% directional accuracy was
# old code or old regime; this ends that).
STRATEGY_VERSION = "2026.09.01-score-decomposed"

# ── Entry-trigger geometry ───────────────────────────────
# A trigger sitting at (or through) spot is not a trigger — the entry
# condition is satisfied the instant the card posts, so the "wait for
# confirmation" discipline that the cards teach does nothing. On
# 2026-08-31 three of six signals had a trigger exactly equal to spot
# and the median trigger distance was 0.009% vs 0.107% historically.
# Rather than reject those signals, we repair the geometry: push the
# trigger to a real confirmation distance derived from ATR.
MIN_TRIGGER_DIST_PCT = _safe_float("MIN_TRIGGER_DIST_PCT", 0.10)  # percent
TRIGGER_ATR_FRACTION = _safe_float("TRIGGER_ATR_FRACTION", 0.15)

# ── Correlated-cluster cap ───────────────────────────────
# Eight tickers that move together produce one macro observation, not
# eight independent signals. Aug 31 posted six PUTs in five minutes
# across six tickers — a single bet, sized six times.
MAX_SAME_DIRECTION_PER_WINDOW = _safe_int("MAX_SAME_DIRECTION_PER_WINDOW", 3)
CLUSTER_WINDOW_MINUTES        = _safe_int("CLUSTER_WINDOW_MINUTES", 15)

# ── Reversal / exhaustion handling ───────────────────────
# Penalty applied when the AI picks the direction that reversal analysis
# says is already exhausted — i.e. chasing a move that has run. The size
# is a CHOSEN value, not one fitted to historical P&L; it is deliberately
# large enough to matter and small enough that strong confluence can
# still clear the feed cutoff. Version-stamped so its effect is testable.
REVERSAL_RISK_THRESHOLD = _safe_int("REVERSAL_RISK_THRESHOLD", 45)
REVERSAL_MAX_PENALTY    = _safe_int("REVERSAL_MAX_PENALTY", 15)
