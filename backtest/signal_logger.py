"""
backtest/signal_logger.py — Persistent signal logging for backtesting

Every time the bot generates a signal (regardless of score/threshold),
we append a row to backtest/signals_log.csv. This builds a permanent,
git-committed dataset of every prediction the bot has ever made —
independent of GitHub Actions' 30-day artifact retention.

This is intentionally lightweight: we log the signal at generation time,
and a SEPARATE script (outcome_checker.py) fills in what actually
happened later, once enough time has passed to know the outcome.
"""
from __future__ import annotations
import csv
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

from core.logger import get_logger

log = get_logger("BacktestLogger")

LOG_DIR  = Path(__file__).resolve().parent
LOG_FILE = LOG_DIR / "signals_log.csv"

FIELDNAMES = [
    "log_id",            # unique id: ticker_timestamp
    "timestamp",         # when the signal was generated (ISO)
    "ticker",
    "contract_type",     # CALL / PUT / NONE
    "setup_quality",     # HIGH CONVICTION / MODERATE / LOW CONVICTION / NO TRADE
    "confluence_score",
    "bias",              # BULLISH / BEARISH / NEUTRAL
    "price_at_scan",     # stock price when signal was generated
    "strike",
    "stock_target",      # where AI predicted price needs to go
    "stop_level",        # AI's invalidation level (parsed from stop_rule text)
    "expiry",            # 0DTE / 1DTE / 2DTE
    # Filled in later by outcome_checker.py:
    "checked",           # "yes" once outcome has been evaluated
    "checked_at",
    "outcome_price",     # price at check time
    "max_favorable_pct", # best % move in predicted direction within window
    "max_adverse_pct",   # worst % move against prediction within window
    "hit_target",        # yes/no — did price reach stock_target
    "hit_stop",          # yes/no — did price reach stop_level first
    "result",            # WIN / LOSS / NO_TRIGGER / PENDING
]


def _ensure_log_exists():
    """Create the CSV with headers if it doesn't exist yet."""
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        log.info(f"Created new backtest log at {LOG_FILE}")


def _extract_stop_level(stop_rule_text: str, contract_type: str) -> float | None:
    """
    Best-effort extraction of a numeric stop level from the AI's
    free-text stop_rule field, e.g. "Exit if SPY closes 15m candle
    above $734.00" -> 734.00
    """
    import re
    if not stop_rule_text:
        return None
    matches = re.findall(r"\$?(\d{1,6}(?:\.\d{1,2})?)", stop_rule_text)
    if not matches:
        return None
    # Heuristic: the stop level is usually the first dollar figure mentioned
    try:
        return float(matches[0])
    except (ValueError, IndexError):
        return None


def log_signal(ticker: str, tech: Dict[str, Any], ai: Dict[str, Any]) -> None:
    """
    Append one row to the backtest signal log for this ticker's scan.
    Called automatically after every analysis, regardless of score —
    we want NO TRADE / low-conviction signals in the dataset too, so
    we can later validate whether the threshold itself is well-calibrated.
    """
    try:
        _ensure_log_exists()

        ts           = ai.get("timestamp") or datetime.now().isoformat()
        trade_setup  = ai.get("trade_setup", {}) or {}
        price        = tech.get("technicals", {}).get("last_price")

        row = {
            "log_id":            f"{ticker}_{ts.replace(':', '').replace('-', '')}",
            "timestamp":         ts,
            "ticker":            ticker,
            "contract_type":     trade_setup.get("contract_type", "NONE"),
            "setup_quality":     trade_setup.get("setup_quality", "NO TRADE"),
            "confluence_score":  ai.get("confluence_score"),
            "bias":              ai.get("suggested_bias", "NEUTRAL"),
            "price_at_scan":     price,
            "strike":            trade_setup.get("strike"),
            "stock_target":      trade_setup.get("stock_target"),
            "stop_level":        _extract_stop_level(
                                     trade_setup.get("stop_rule", ""),
                                     trade_setup.get("contract_type", "")
                                 ),
            "expiry":            trade_setup.get("expiry"),
            "checked":           "",
            "checked_at":        "",
            "outcome_price":     "",
            "max_favorable_pct": "",
            "max_adverse_pct":   "",
            "hit_target":        "",
            "hit_stop":          "",
            "result":            "",
        }

        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)

        log.info(f"Backtest log: recorded {ticker} | {row['contract_type']} | score={row['confluence_score']}")

    except Exception as e:
        # Never let logging failures break the main pipeline
        log.warning(f"Backtest signal logging failed for {ticker}: {e}")
