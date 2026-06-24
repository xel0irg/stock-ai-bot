"""
backtest/outcome_checker.py — Fills in real outcomes for logged signals

Run this periodically (end of day, or next morning before market open)
to check what actually happened to each PENDING signal in signals_log.csv.

For 0-2 DTE signals, the outcome window is short — we check price action
within roughly 1-2 trading days of the signal timestamp. This script:
  1. Finds rows where checked == "" (not yet evaluated) and the signal
     is old enough to have a knowable outcome (>= 1 trading day old)
  2. Pulls intraday price history for that ticker covering the window
  3. Determines whether stock_target was hit before stop_level, in the
     predicted direction
  4. Writes the result back into the CSV

Usage:
    python -m backtest.outcome_checker
"""
from __future__ import annotations
import csv
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf
import pandas as pd

from core.logger import get_logger
from backtest.signal_logger import LOG_FILE, FIELDNAMES

log = get_logger("OutcomeChecker")

MIN_AGE_HOURS = 20  # only check signals at least ~1 trading day old


def _load_rows() -> list[dict]:
    if not LOG_FILE.exists():
        log.warning(f"No signal log found at {LOG_FILE}")
        return []
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save_rows(rows: list[dict]) -> None:
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _get_price_window(ticker: str, start: datetime, end: datetime) -> pd.DataFrame | None:
    """Fetch 15-min OHLCV data covering the signal's outcome window."""
    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
            interval="15m",
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
        return df
    except Exception as e:
        log.warning(f"Price fetch failed for {ticker}: {e}")
        return None


def _evaluate_signal(row: dict, price_df: pd.DataFrame) -> dict:
    """
    Determine the outcome for one signal given the price window.
    Returns the fields to update on the row.
    """
    contract_type = row.get("contract_type", "NONE")
    update = {
        "checked":    "yes",
        "checked_at": datetime.now().isoformat(),
        "result":     "NO_TRIGGER",
    }

    if contract_type not in ("CALL", "PUT") or price_df is None or price_df.empty:
        update["result"] = "NO_TRADE" if contract_type == "NONE" else "NO_DATA"
        return update

    try:
        entry_price  = float(row["price_at_scan"]) if row.get("price_at_scan") else None
        stock_target = float(row["stock_target"])  if row.get("stock_target")  else None
        stop_level   = float(row["stop_level"])    if row.get("stop_level")    else None
    except (ValueError, TypeError):
        update["result"] = "BAD_DATA"
        return update

    if entry_price is None or stock_target is None:
        update["result"] = "BAD_DATA"
        return update

    closes = price_df["close"]
    final_price = float(closes.iloc[-1])
    update["outcome_price"] = round(final_price, 2)

    if contract_type == "PUT":
        # Predicted direction is DOWN — favorable move is price falling
        favorable_move = entry_price - float(closes.min())
        adverse_move   = float(closes.max()) - entry_price
        hit_target = bool((closes <= stock_target).any())
        hit_stop   = bool((closes >= stop_level).any()) if stop_level else False
    else:  # CALL
        favorable_move = float(closes.max()) - entry_price
        adverse_move   = entry_price - float(closes.min())
        hit_target = bool((closes >= stock_target).any())
        hit_stop   = bool((closes <= stop_level).any()) if stop_level else False

    update["max_favorable_pct"] = round((favorable_move / entry_price) * 100, 2)
    update["max_adverse_pct"]   = round((adverse_move   / entry_price) * 100, 2)
    update["hit_target"] = "yes" if hit_target else "no"
    update["hit_stop"]   = "yes" if hit_stop   else "no"

    # Determine win/loss — did target get hit, and did stop NOT get hit
    # first? We approximate "first" by checking which one occurred at an
    # earlier index in the series, since we don't have tick-level ordering.
    if hit_target and not hit_stop:
        update["result"] = "WIN"
    elif hit_target and hit_stop:
        # Both happened — determine which came first chronologically
        if contract_type == "PUT":
            target_idx = closes[closes <= stock_target].index.min() if hit_target else None
            stop_idx   = closes[closes >= stop_level].index.min()   if hit_stop and stop_level else None
        else:
            target_idx = closes[closes >= stock_target].index.min() if hit_target else None
            stop_idx   = closes[closes <= stop_level].index.min()   if hit_stop and stop_level else None
        if target_idx is not None and (stop_idx is None or target_idx < stop_idx):
            update["result"] = "WIN"
        else:
            update["result"] = "LOSS"
    elif hit_stop:
        update["result"] = "LOSS"
    else:
        update["result"] = "NO_TRIGGER"  # neither target nor stop hit in window

    return update


def run_outcome_check() -> dict:
    """Main entry point — check all eligible pending signals and update the log."""
    rows = _load_rows()
    if not rows:
        return {"checked": 0, "updated": 0}

    now = datetime.now()
    updated_count = 0

    for row in rows:
        if row.get("checked") == "yes":
            continue

        try:
            signal_time = datetime.fromisoformat(row["timestamp"])
        except (ValueError, KeyError):
            continue

        age_hours = (now - signal_time).total_seconds() / 3600
        if age_hours < MIN_AGE_HOURS:
            continue  # too recent — outcome not knowable yet

        ticker = row["ticker"]
        window_start = signal_time
        window_end   = min(signal_time + timedelta(days=2), now)

        log.info(f"Checking outcome for {ticker} (signal from {row['timestamp']})...")
        price_df = _get_price_window(ticker, window_start, window_end)
        update = _evaluate_signal(row, price_df)
        row.update(update)
        updated_count += 1

        log.info(f"  -> {ticker} {row.get('contract_type')} | result={update['result']}")

    _save_rows(rows)
    log.info(f"Outcome check complete: {updated_count} signals evaluated")
    return {"checked": len(rows), "updated": updated_count}


if __name__ == "__main__":
    run_outcome_check()
