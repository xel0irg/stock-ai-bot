"""
core/spend_tracker.py — Daily Anthropic API spend cap

Anthropic only supports MONTHLY spend limits natively (Console > Settings
> Limits). This module adds a custom DAILY cap on top of that, enforced
entirely in this bot's own code — independent of and in addition to
whatever monthly limit you've set in the Anthropic Console.

How it works:
  1. Every successful Claude API call records its actual cost (computed
     from message.usage.input_tokens / output_tokens) into a small
     persistent log: core/spend_log.csv
  2. Before making a new API call, check_daily_limit() sums today's
     recorded spend and compares it against DAILY_SPEND_LIMIT_USD
     (set in config/settings.py, defaults to a generous fallback).
  3. If today's spend already meets/exceeds the limit, the caller
     (run_ai_synthesis) skips the API call entirely and returns a
     clear "skipped — daily limit reached" result instead.

This applies everywhere run_ai_synthesis() is called from: scheduled
scans, manual local scans, AND the Discord /scan command — they all
funnel through the same function, so the cap is enforced consistently
no matter which trigger initiated the call.
"""
from __future__ import annotations
import csv
from pathlib import Path
from datetime import datetime, date

from core.logger import get_logger

log = get_logger("SpendTracker")

LOG_FILE = Path(__file__).resolve().parent.parent / "spend_log.csv"

FIELDNAMES = ["timestamp", "date", "ticker", "model", "input_tokens", "output_tokens", "cost_usd"]

# Current Claude Opus 4.5 pricing (per Anthropic's pricing page, June 2026):
# $5 / MTok input, $25 / MTok output. Update these if the model or
# pricing changes.
PRICE_PER_MTOK_INPUT  = 5.00
PRICE_PER_MTOK_OUTPUT = 25.00


def _ensure_log_exists():
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def calculate_cost(input_tokens: int, output_tokens: int) -> float:
    """Compute the USD cost of a single API call from its token counts."""
    input_cost  = (input_tokens  / 1_000_000) * PRICE_PER_MTOK_INPUT
    output_cost = (output_tokens / 1_000_000) * PRICE_PER_MTOK_OUTPUT
    return round(input_cost + output_cost, 6)


def record_spend(ticker: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Append one row recording the cost of a completed API call.
    Returns the cost in USD for this specific call.
    """
    _ensure_log_exists()
    cost = calculate_cost(input_tokens, output_tokens)
    now  = datetime.now()

    row = {
        "timestamp":     now.isoformat(),
        "date":          now.date().isoformat(),
        "ticker":        ticker,
        "model":         model,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      cost,
    }

    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)

    log.info(f"Spend recorded: {ticker} | ${cost:.4f} (in={input_tokens}, out={output_tokens})")
    return cost


def get_today_spend() -> float:
    """Sum all recorded spend for today's calendar date."""
    if not LOG_FILE.exists():
        return 0.0

    today = date.today().isoformat()
    total = 0.0

    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("date") == today:
                try:
                    total += float(row.get("cost_usd", 0))
                except (ValueError, TypeError):
                    continue

    return round(total, 4)


def check_daily_limit(daily_limit_usd: float) -> tuple[bool, float]:
    """
    Check whether today's spend is already at or above the daily limit.
    Returns (is_allowed, current_spend_today).
    """
    current = get_today_spend()
    is_allowed = current < daily_limit_usd

    if not is_allowed:
        log.warning(
            f"⛔ Daily spend limit reached: ${current:.2f} / ${daily_limit_usd:.2f} — "
            f"skipping further AI calls today"
        )

    return is_allowed, current
