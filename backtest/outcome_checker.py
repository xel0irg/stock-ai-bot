"""
backtest/outcome_checker.py — Fills in real outcomes for logged signals

Bugs fixed (July 2026):
    A. Naive timestamps — stored without timezone; datetime.now() vs
       naive ISO strings produced wrong age calculations across DST.
       Fix: parse timestamps as UTC, compare with UTC now().

    B. Calendar-day window — window_end = signal + 2 calendar days
       skipped straight past holiday weekends (Jul 4 + weekend = no
       trading data inside the window at all → NO_DATA).
       Fix: advance window_end by TRADING days using the NYSE calendar
       from core.market_hours, so the window always covers the right
       session regardless of holidays.

    C. Post-close signals — signals fired after an early close (e.g.
       July 3 at 1:01 PM ET, market closed at 1 PM) had their 1DTE
       outcome on the next trading day, which was July 7 — three
       calendar days later, outside the old +2-day window.
       Fix: window is now TRADING-days based, so it always reaches the
       correct session regardless of when in the session the signal fired.

    D. yfinance end= is EXCLUSIVE — the old code passed window_end
       directly; if window_end was a Saturday, yfinance returned nothing.
       Fix: always pass end = next_trading_day(window_end) + 1 buffer.

Usage:
    python -m backtest.outcome_checker        # normal run
    python -m backtest.outcome_checker --recheck   # re-evaluate NO_DATA rows
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from core.logger import get_logger
from core.market_hours import is_trading_day, nyse_holidays
from backtest.signal_logger import LOG_FILE, FIELDNAMES

log = get_logger("OutcomeChecker")

UTC = timezone.utc
MIN_AGE_HOURS = 20   # only check signals at least ~1 trading day old
OUTCOME_TRADING_DAYS = 2  # how many trading days form the outcome window


# ── Trading-day helpers ───────────────────────────────────────────────

def _next_trading_day(d: datetime) -> datetime:
    """Return the next calendar day that is a NYSE trading day."""
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt.date()):
        nxt += timedelta(days=1)
    return nxt


def _advance_trading_days(start: datetime, n: int) -> datetime:
    """Advance `start` by exactly n NYSE trading days."""
    d = start
    for _ in range(n):
        d = _next_trading_day(d)
    return d


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """
    Parse a timestamp string into a UTC-aware datetime.
    Handles both naive ISO strings (assumed UTC) and aware ones.
    """
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)   # treat naive as UTC
        return dt.astimezone(UTC)
    except ValueError:
        return None


# ── CSV I/O ───────────────────────────────────────────────────────────

def _load_rows() -> list[dict]:
    if not LOG_FILE.exists():
        log.warning(f"No signal log found at {LOG_FILE}")
        return []
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # Migrate schema if needed (new columns added since this row was written)
    migrated = []
    for row in rows:
        migrated.append({k: row.get(k, "") for k in FIELDNAMES})
    return migrated


def _save_rows(rows: list[dict]) -> None:
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ── Price data ────────────────────────────────────────────────────────

def _get_price_window(
    ticker: str,
    signal_time: datetime,  # UTC-aware
    trading_days: int = OUTCOME_TRADING_DAYS,
) -> Optional[pd.DataFrame]:
    """
    Fetch 15-min OHLCV covering `trading_days` NYSE sessions after the
    signal. Correctly skips weekends and holidays.

    yfinance `end=` is exclusive and date-based, so we pass one extra
    calendar day beyond window_end as the buffer.
    """
    # Determine the first trading session that can show outcome data.
    # If the signal fired during a session, that same session counts as
    # day 0; outcome starts the next session (1DTE = session after signal).
    window_end = _advance_trading_days(signal_time, trading_days)

    # yfinance needs a date string; end= is exclusive so add 1 calendar day
    fetch_start = signal_time.strftime("%Y-%m-%d")
    fetch_end   = (window_end + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        df = yf.download(
            ticker,
            start=fetch_start,
            end=fetch_end,
            interval="15m",
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            log.warning(f"{ticker}: no price data for {fetch_start}→{fetch_end}")
            return None

        # Flatten MultiIndex columns (yfinance quirk)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0].lower() for c in df.columns]
        else:
            df.columns = [
                c.lower() if isinstance(c, str) else c[0].lower()
                for c in df.columns
            ]

        # Trim to exactly the outcome window:
        # from signal_time → window_end (end of that trading day)
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        df.index = df.index.tz_convert(UTC)
        df = df[df.index >= signal_time]
        df = df[df.index.date <= window_end.date()]

        if df.empty:
            log.warning(f"{ticker}: price data fetched but nothing in outcome window "
                        f"({signal_time.date()} → {window_end.date()})")
            return None

        log.info(f"{ticker}: {len(df)} bars fetched for outcome window "
                 f"({signal_time.date()} → {window_end.date()})")
        return df

    except Exception as e:
        log.warning(f"Price fetch failed for {ticker}: {e}")
        return None


# ── Signal evaluation ─────────────────────────────────────────────────

def _evaluate_signal(row: dict, price_df: Optional[pd.DataFrame]) -> dict:
    """
    Determine WIN / LOSS / NO_TRIGGER / NO_DATA / NO_TRADE for one signal.
    Returns a dict of fields to update on the row.
    """
    contract_type = row.get("contract_type", "NONE")
    update = {
        "checked":    "yes",
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "result":     "NO_DATA",
    }

    if contract_type == "NONE":
        update["result"] = "NO_TRADE"
        return update

    if contract_type not in ("CALL", "PUT"):
        update["result"] = "NO_DATA"
        return update

    if price_df is None or price_df.empty:
        update["result"] = "NO_DATA"
        return update

    # Parse numeric fields
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
    highs  = price_df["high"] if "high" in price_df.columns else closes
    lows   = price_df["low"]  if "low"  in price_df.columns else closes
    final_price = float(closes.iloc[-1])
    update["outcome_price"] = round(final_price, 2)

    # ── Entry trigger ────────────────────────────────────────────────
    # Did price actually reach the entry trigger level? A signal whose
    # trigger was never reached is genuinely un-actionable — that is the
    # ONLY case that should be labelled NOT_TRIGGERED. Everything else is
    # a real outcome (win, loss, or a partial move that expired flat).
    try:
        entry_trigger = float(row["entry_trigger"]) if row.get("entry_trigger") else None
    except (ValueError, TypeError):
        entry_trigger = None

    if entry_trigger:
        if contract_type == "PUT":
            triggered = bool((lows <= entry_trigger).any())
        else:
            triggered = bool((highs >= entry_trigger).any())
    else:
        # No numeric trigger logged — treat as triggered so we still
        # measure the outcome rather than discarding the signal.
        triggered = True

    update["entry_triggered"] = "yes" if triggered else "no"

    if not triggered:
        # Price never reached the entry — no trade would have been taken.
        update["result"] = "NOT_TRIGGERED"
        update["max_favorable_pct"] = 0.0
        update["max_adverse_pct"]   = 0.0
        update["hit_target"] = "no"
        update["hit_stop"]   = "no"
        return update

    # ── Sequenced outcome: walk bars in order from the trigger ───────
    # The ONLY correct way to decide win vs loss is to replay the bars
    # chronologically from the moment the entry triggered and see which
    # comes FIRST — the profit level or the stop. Checking max-favorable
    # and max-adverse independently (the previous approach) counted a
    # trade that was stopped out and LATER rallied as a win, which
    # inflated the CALL win rate to an impossible 94%.
    ref = entry_trigger if entry_trigger else entry_price

    try:
        em_pct = float(row["em_pct"]) if row.get("em_pct") else None
    except (ValueError, TypeError):
        em_pct = None
    profit_threshold = max((em_pct or 0) * 0.5, 0.3)   # % move to count as a win

    if contract_type == "CALL":
        profit_level = ref * (1 + profit_threshold / 100)
        stop_price   = stop_level if stop_level else ref * (1 - profit_threshold / 100)
    else:  # PUT
        profit_level = ref * (1 - profit_threshold / 100)
        stop_price   = stop_level if stop_level else ref * (1 + profit_threshold / 100)

    # Restrict to bars AT OR AFTER the trigger was first reached
    if entry_trigger:
        if contract_type == "CALL":
            trig_mask = highs >= entry_trigger
        else:
            trig_mask = lows <= entry_trigger
        trig_hits = trig_mask[trig_mask].index
        trig_idx  = trig_hits.min() if len(trig_hits) else price_df.index[0]
    else:
        trig_idx = price_df.index[0]

    seq = price_df[price_df.index >= trig_idx]
    seq_high = seq["high"] if "high" in seq.columns else seq["close"]
    seq_low  = seq["low"]  if "low"  in seq.columns else seq["close"]

    result = "FLAT"
    max_fav = 0.0
    max_adv = 0.0
    hit_target = False
    hit_stop   = False

    for _, bar in seq.iterrows():
        hi = float(bar["high"]) if "high" in bar else float(bar["close"])
        lo = float(bar["low"])  if "low"  in bar else float(bar["close"])

        if contract_type == "CALL":
            fav = (hi - ref) / ref * 100
            adv = (ref - lo) / ref * 100
            profit_touched = hi >= profit_level
            stop_touched   = lo <= stop_price
            target_touched = hi >= stock_target
        else:
            fav = (ref - lo) / ref * 100
            adv = (hi - ref) / ref * 100
            profit_touched = lo <= profit_level
            stop_touched   = hi >= stop_price
            target_touched = lo <= stock_target

        max_fav = max(max_fav, fav)
        max_adv = max(max_adv, adv)
        if target_touched: hit_target = True

        # Within a single 15m bar we cannot know whether the high or the
        # low came first. Resolve conservatively: if BOTH profit and stop
        # are touched in the same bar, count it as a LOSS (assume the
        # adverse excursion hit first). This biases the win rate DOWN,
        # which is the honest direction for a backtest.
        if stop_touched and profit_touched:
            hit_stop = True
            result = "LOSS"
            break
        if stop_touched:
            hit_stop = True
            result = "LOSS"
            break
        if profit_touched:
            result = "WIN"
            break

    update["max_favorable_pct"] = round(max_fav, 2)
    update["max_adverse_pct"]   = round(max_adv, 2)
    update["hit_target"] = "yes" if hit_target else "no"
    update["hit_stop"]   = "yes" if hit_stop   else "no"
    update["result"]     = result   # WIN / LOSS / FLAT (never resolved in-window)

    return update


# ── Main entry point ──────────────────────────────────────────────────

def run_outcome_check(recheck_no_data: bool = False,
                      recheck_all: bool = False) -> dict:
    """
    Check all eligible pending signals and update the log.

    Args:
        recheck_no_data: if True, also re-evaluate rows previously
                         marked NO_DATA.
        recheck_all: if True, re-evaluate EVERY already-checked row.
                     Use after changing the evaluation logic itself
                     (e.g. the NO_TRIGGER -> WIN/LOSS/FLAT rework) so the
                     historical stats reflect the corrected definitions.
    """
    rows = _load_rows()
    if not rows:
        return {"checked": 0, "updated": 0}

    now_utc = datetime.now(UTC)
    updated_count = 0
    skipped_recent = 0
    recheck_count  = 0

    for row in rows:
        already_checked = row.get("checked") == "yes"
        is_no_data      = row.get("result") == "NO_DATA"

        # Decide whether to (re)evaluate this row
        if already_checked:
            if recheck_all:
                recheck_count += 1
            elif recheck_no_data and is_no_data:
                recheck_count += 1
            else:
                continue

        signal_time = _parse_ts(row.get("timestamp", ""))
        if signal_time is None:
            continue

        age_hours = (now_utc - signal_time).total_seconds() / 3600
        if age_hours < MIN_AGE_HOURS:
            skipped_recent += 1
            continue

        ticker = row.get("ticker", "")
        if not ticker:
            continue

        log.info(f"{'Re-checking' if is_no_data and recheck_no_data else 'Checking'} "
                 f"{ticker} | {row.get('contract_type')} | {row.get('expiry')} | "
                 f"signal={signal_time.date()}")

        price_df = _get_price_window(ticker, signal_time)
        update   = _evaluate_signal(row, price_df)
        row.update(update)
        updated_count += 1

        log.info(f"  → {ticker} result={update['result']} "
                 f"(favorable={update.get('max_favorable_pct','?')}% "
                 f"adverse={update.get('max_adverse_pct','?')}%)")

    _save_rows(rows)
    log.info(
        f"Outcome check complete: {updated_count} evaluated "
        f"({recheck_count} NO_DATA re-checked), {skipped_recent} too recent"
    )
    return {"checked": len(rows), "updated": updated_count}


if __name__ == "__main__":
    recheck     = "--recheck" in sys.argv
    recheck_all = "--recheck-all" in sys.argv
    if recheck_all:
        log.info("--recheck-all mode: re-evaluating EVERY checked row "
                 "against current logic")
    elif recheck:
        log.info("--recheck mode: will re-evaluate all NO_DATA rows")
    run_outcome_check(recheck_no_data=recheck, recheck_all=recheck_all)
