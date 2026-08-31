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
    "entry_trigger",     # numeric entry trigger price from the AI
    "freshness_consumed_pct",  # % of trigger→target move consumed at alert time
    "freshness_stale",   # yes/no — was the signal flagged STALE at alert time
    "em_pct",            # expected move % for the chosen expiry
    "target_em_ratio",   # target distance / expected move (>1.0 = beyond EM)
    "target_adjusted",   # yes if target was clamped by validate_target()
    "strategy_version",  # pipeline version that produced this signal
    "volume_ratio_daily",# partial-day volume vs 20-day avg (feeds scoring)
    "volume_ratio_15m",  # last 15m bar vs rolling 20-bar avg
    "rvol_tod",          # 15m bar vs same-time-of-day avg, prior sessions
    # Filled in later by outcome_checker.py:
    "checked",           # "yes" once outcome has been evaluated
    "checked_at",
    "outcome_price",     # price at check time
    "max_favorable_pct", # best % move in predicted direction within window
    "max_adverse_pct",   # worst % move against prediction within window
    "hit_target",        # yes/no — did price reach stock_target
    "hit_stop",          # yes/no — did price reach stop_level first
    "entry_triggered",   # yes/no — did price reach the entry_trigger level
    "result",            # WIN / LOSS / FLAT / NOT_TRIGGERED / NO_DATA / PENDING
]


def _ensure_log_exists():
    """
    Create the CSV with headers if it doesn't exist yet.
    If it exists with an OLDER schema (fewer columns), migrate it in
    place: rewrite with the new header and pad old rows with blanks.
    This keeps the git-committed history intact when we add columns.
    """
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
        log.info(f"Created new backtest log at {LOG_FILE}")
        return

    # Schema migration check
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        if header == FIELDNAMES:
            return  # up to date
        old_rows = list(csv.DictReader(open(LOG_FILE, newline="", encoding="utf-8")))

    log.info(f"Migrating backtest log schema: {len(header)} → {len(FIELDNAMES)} columns")
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in old_rows:
            writer.writerow({k: row.get(k, "") for k in FIELDNAMES})


def _extract_stop_level(stop_rule_text: str, contract_type: str,
                        ref_price: float | None = None) -> float | None:
    """
    Extract a numeric STOCK stop level from the AI's free-text stop_rule,
    e.g. "Exit if SPY closes a 15m candle above $734.00" -> 734.00

    Fixed 2026-08-30. The previous version took the first number of any
    kind, so "exit if premium drops 15%" logged a stop level of 15.0 —
    797 of 1779 historical rows carry that value, and because
    paper_tracker compares the stock price against it, hit_stop could
    never fire (no stock is ever <= $15). Now:
      * only $-prefixed figures count, so percentages are ignored
      * a match immediately followed by '%' is rejected outright
      * when a reference price is known, the level must sit within
        +/-25% of it, which rules out strike counts, RSI values and
        candle counts that happen to carry a dollar sign
    Returns None rather than a wrong number — a blank is honest, a
    plausible-looking wrong value silently corrupts every downstream
    stop statistic.
    """
    import re
    if not stop_rule_text:
        return None

    candidates = []
    for m in re.finditer(r"\$\s?(\d{1,6}(?:\.\d{1,2})?)", stop_rule_text):
        tail = stop_rule_text[m.end():m.end() + 1]
        if tail == "%":
            continue
        try:
            candidates.append(float(m.group(1)))
        except ValueError:
            continue
    if not candidates:
        return None

    if ref_price:
        try:
            ref = float(ref_price)
            plausible = [c for c in candidates
                         if ref * 0.75 <= c <= ref * 1.25]
            if not plausible:
                return None
            candidates = plausible
        except (TypeError, ValueError):
            pass

    return candidates[0]


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

        try:
            from config.settings import STRATEGY_VERSION as _SV
        except Exception:
            _SV = ""

        row = {
            "log_id":            f"{ticker}_{ts.replace(':', '').replace('-', '')}",
            "strategy_version":  _SV,
            "volume_ratio_daily": tech.get("technicals", {}).get("volume_ratio", ""),
            "volume_ratio_15m":  (tech.get("intraday", {}) or {}).get("tf_15m", {}).get("volume_ratio", ""),
            "rvol_tod":          tech.get("rvol_tod", ""),
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
                                     trade_setup.get("contract_type", ""),
                                     price
                                 ),
            "expiry":            trade_setup.get("expiry"),
            "entry_trigger":     trade_setup.get("entry_trigger"),
            "freshness_consumed_pct": (ai.get("freshness") or {}).get("consumed_pct", ""),
            "freshness_stale":   ("yes" if (ai.get("freshness") or {}).get("is_stale") else
                                  "no" if (ai.get("freshness") or {}).get("checked") else ""),
            "em_pct":            trade_setup.get("em_pct", ""),
            "target_em_ratio":   trade_setup.get("target_em_ratio", ""),
            "target_adjusted":   "yes" if trade_setup.get("target_original") else "no",
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
