"""
backtest/paper_tracker.py — Forward paper-trading tracker

Why this exists:
    Historical option premiums cannot be reconstructed from free data —
    yfinance only exposes the LIVE chain, not intraday history. And the
    stock-path backtest can't reliably tell whether profit or stop came
    first inside a 15-minute bar. So the only trustworthy performance
    measure is FORWARD tracking: snapshot the real option premium when a
    signal fires, snapshot it again at close, and record the actual P&L.

    This produces zero data on day one and accumulates real, unambiguous
    results over the following weeks — premium-based, not inferred.

What it records, per signal (in backtest/paper_trades.csv):
    At signal time:
        - contract (ticker, strike, expiry date, CALL/PUT)
        - live option premium (mid of bid/ask)
        - stock price, entry trigger, target, stop
        - entered_at_signal = yes  (this cohort always "enters")
    Through the day (each scan updates the open row):
        - entered_on_trigger: flips to yes the first scan the stock
          crosses the entry trigger; stamps the premium at that moment
        - hit_target / hit_stop: whichever the stock touches first
    At close (~3:55 PM ET):
        - final premium snapshot
        - P&L computed four ways:
            {entry@signal, entry@trigger} x {hold-to-close, target/stop exit}

Nothing here touches signals_log.csv or the existing backtest.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("PaperTracker")
ET = ZoneInfo("America/New_York")

TRADES_FILE = Path("backtest/paper_trades.csv")

FIELDS = [
    "signal_id",            # ticker + ISO timestamp, unique per signal
    "signal_time",          # ET ISO timestamp
    "ticker",
    "contract_type",        # CALL / PUT
    "strike",
    "expiry_date",          # actual calendar date of the contract (YYYY-MM-DD)
    "dte_label",            # 0DTE / 1DTE / 2DTE as the AI chose
    "confluence_score",
    # ── entry snapshots ──
    "stock_at_signal",
    "premium_at_signal",    # mid price when the signal fired
    "entry_trigger",        # stock level that "arms" the trade
    "stock_target",
    "stop_level",
    # ── trigger tracking ──
    "entered_on_trigger",   # yes/no — did stock cross the entry trigger
    "premium_at_trigger",   # option mid when trigger first crossed
    "stock_at_trigger",
    # ── intraday path ──
    "hit_target",           # yes/no — stock reached target before stop
    "hit_stop",             # yes/no — stock reached stop before target
    "premium_at_exit_ts",   # option mid at the moment target/stop hit
    "premium_path",          # JSON list of [HH:MM, premium] sampled each scan
                             # — builds the intraday premium curve so stop-loss
                             # and take-profit rules can be backtested against
                             # real paths instead of just entry/close endpoints
    # ── close ──
    "premium_at_close",
    "stock_at_close",
    # ── computed P&L (% of premium) ──
    "pnl_signal_hold",      # entry@signal  -> close
    "pnl_signal_exit",      # entry@signal  -> target/stop exit
    "pnl_trigger_hold",     # entry@trigger -> close
    "pnl_trigger_exit",     # entry@trigger -> target/stop exit
    # ── bookkeeping ──
    "status",               # OPEN / DONE
    "last_update",
]


def _now_et() -> datetime:
    return datetime.now(ET)


def _signal_id(ticker: str, signal_time: str) -> str:
    return f"{ticker}_{signal_time}"


def _option_mid(ticker: str, contract_type: str, strike: float,
                expiry_date: str) -> Optional[float]:
    """
    Fetch the current mid price (bid+ask)/2 for a specific contract.

    Alpaca is primary — its option quotes are tight and accurate (verified
    to within the bid/ask spread of live broker prices), which fixes the
    yfinance premium inflation that corrupted paper P&L (e.g. yfinance
    recording $5.22 when the real contract was $2.07). yfinance remains an
    automatic fallback if Alpaca is unconfigured or returns nothing.
    """
    # Primary: Alpaca
    try:
        from core import alpaca_data
        if alpaca_data.is_enabled():
            mid = alpaca_data.get_option_mid(ticker, contract_type, strike, expiry_date)
            if mid is not None:
                return mid
    except Exception as e:
        log.debug(f"{ticker}: Alpaca option mid failed, falling back — {e}")

    # Fallback: yfinance
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        chain = t.option_chain(expiry_date)
        table = chain.calls if contract_type == "CALL" else chain.puts
        row = table[table["strike"] == strike]
        if row.empty:
            # nearest strike fallback
            row = table.iloc[(table["strike"] - strike).abs().argsort()].iloc[:1]
        if row.empty:
            return None
        bid = float(row["bid"].iloc[0] or 0)
        ask = float(row["ask"].iloc[0] or 0)
        last = float(row["lastPrice"].iloc[0] or 0)
        if bid > 0 and ask > 0:
            return round((bid + ask) / 2, 2)
        return round(last, 2) if last > 0 else None
    except Exception as e:
        log.debug(f"{ticker}: option mid fetch failed — {e}")
        return None


def _resolve_expiry_date(ticker: str, dte_label: str) -> Optional[str]:
    """
    Map a 0/1/2DTE label to the real expiry DATE.

    BUG THIS FIXES: the old version used list POSITIONS —
    expiries[1] for "1DTE". But yfinance's expiry list is just the
    available expiries in order, which for most tickers jumps from
    today straight to the next weekly. So "1DTE" resolved to a
    contract 4+ days out, and every recorded premium was inflated
    because it carried far more time value than the real 1DTE.

    Now we compute the actual target calendar date and pick the
    closest available expiry ON OR AFTER it.
    """
    try:
        import yfinance as yf
        from datetime import date, timedelta

        expiries = list(yf.Ticker(ticker).options or [])
        if not expiries:
            return None

        days = {"0DTE": 0, "1DTE": 1, "2DTE": 2}.get(dte_label, 0)
        today = _now_et().date()

        # Walk forward `days` TRADING days (skip weekends)
        target = today
        added = 0
        while added < days:
            target += timedelta(days=1)
            if target.weekday() < 5:      # Mon-Fri
                added += 1

        # Pick the first available expiry on or after the target date
        for e in expiries:
            try:
                ed = date.fromisoformat(e)
            except ValueError:
                continue
            if ed >= target:
                return e

        return expiries[-1]   # nothing that far out; use the longest available
    except Exception as e:
        log.debug(f"{ticker}: expiry resolution failed — {e}")
        return None


def _nearest_valid_strike(ticker: str, contract_type: str,
                          strike: float, expiry_date: str) -> Optional[float]:
    """
    Snap the AI's requested strike to one that ACTUALLY EXISTS on the chain.

    BUG THIS FIXES: the AI invents strikes like $321 for AAPL or $234 for
    AMZN, but those tickers trade in $2.50 increments at those levels — the
    contracts don't exist. Anyone following the card literally could not
    place the trade. We snap to the closest real strike and record that.

    Alpaca primary — it exposes the true tradable-contracts list with a
    `tradable` flag, which is authoritative. yfinance is the fallback.
    """
    # Primary: Alpaca's real tradable-contracts list
    try:
        from core import alpaca_data
        if alpaca_data.is_enabled():
            snapped = alpaca_data.nearest_tradable_strike(
                ticker, contract_type, strike, expiry_date)
            if snapped is not None:
                if abs(snapped - strike) > 0.01:
                    log.info(f"{ticker}: strike ${strike} not tradable "
                             f"— snapped to ${snapped} (Alpaca)")
                return snapped
    except Exception as e:
        log.debug(f"{ticker}: Alpaca strike snap failed, falling back — {e}")

    # Fallback: yfinance
    try:
        import yfinance as yf
        chain = yf.Ticker(ticker).option_chain(expiry_date)
        table = chain.calls if contract_type == "CALL" else chain.puts
        if table.empty:
            return None
        strikes = table["strike"].tolist()
        nearest = min(strikes, key=lambda s: abs(s - strike))
        if abs(nearest - strike) > 0.01:
            log.info(f"{ticker}: strike ${strike} not on chain — "
                     f"snapped to ${nearest}")
        return float(nearest)
    except Exception as e:
        log.debug(f"{ticker}: strike validation failed — {e}")
        return None


def _load() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    with open(TRADES_FILE, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _save(rows: list[dict]) -> None:
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def open_paper_trade(ticker: str, ai: Dict[str, Any]) -> None:
    """
    Called when a signal fires. Snapshots the live contract premium and
    opens a paper trade. Only acts on real CALL/PUT signals.
    """
    ts = ai.get("trade_setup", {}) or {}
    ct = ts.get("contract_type", "NONE")
    if ct not in ("CALL", "PUT"):
        return

    strike = ts.get("strike")
    if strike is None:
        return
    try:
        strike = float(strike)
    except (ValueError, TypeError):
        return

    dte_label = ts.get("expiry", "0DTE")
    expiry_date = _resolve_expiry_date(ticker, dte_label)
    if not expiry_date:
        log.warning(f"{ticker}: no expiry date resolved — paper trade skipped")
        return

    signal_time = _now_et().isoformat(timespec="seconds")
    sid = _signal_id(ticker, signal_time)

    rows = _load()
    # De-dup: one open paper trade per ticker+direction at a time
    for r in rows:
        if (r["ticker"] == ticker and r["contract_type"] == ct
                and r["status"] == "OPEN"):
            log.info(f"{ticker} {ct}: paper trade already open — not duplicating")
            return

    # Snap to a strike that actually exists on the chain before pricing
    valid_strike = _nearest_valid_strike(ticker, ct, strike, expiry_date)
    if valid_strike is not None:
        strike = valid_strike

    premium = _option_mid(ticker, ct, strike, expiry_date)
    if premium is None:
        log.warning(f"{ticker} {ct} ${strike}: no live premium — paper trade skipped")
        return

    stock = ts.get("stock_price_at_signal") or ts.get("entry_trigger")
    row = {f: "" for f in FIELDS}
    row.update({
        "signal_id":         sid,
        "signal_time":       signal_time,
        "ticker":            ticker,
        "contract_type":     ct,
        "strike":            strike,
        "expiry_date":       expiry_date,
        "dte_label":         dte_label,
        "confluence_score":  ai.get("confluence_score", ""),
        "stock_at_signal":   stock or "",
        "premium_at_signal": premium,
        "entry_trigger":     ts.get("entry_trigger", ""),
        "stock_target":      ts.get("stock_target", ""),
        "stop_level":        ts.get("stop_level", ""),
        "entered_on_trigger": "no",
        "hit_target":        "no",
        "hit_stop":          "no",
        "status":            "OPEN",
        "last_update":       signal_time,
    })
    rows.append(row)
    _save(rows)
    log.info(f"📝 Paper trade opened: {ticker} {ct} ${strike} @ ${premium} "
             f"({dte_label}, exp {expiry_date})")


def update_open_trades(current_prices: Dict[str, float]) -> None:
    """
    Called each scan. For every OPEN paper trade, check whether the stock
    crossed its entry trigger, target, or stop, and snapshot the option
    premium at those moments.

    current_prices: {ticker: latest_stock_price}
    """
    rows = _load()
    if not rows:
        return
    changed = False
    now = _now_et().isoformat(timespec="seconds")

    for r in rows:
        if r["status"] != "OPEN":
            continue
        tk = r["ticker"]
        px = current_prices.get(tk)
        if px is None:
            continue
        ct = r["contract_type"]

        def _f(key):
            try:
                return float(r[key]) if r[key] not in ("", None) else None
            except (ValueError, TypeError):
                return None

        trigger = _f("entry_trigger")
        target  = _f("stock_target")
        stop    = _f("stop_level")
        strike  = _f("strike")

        # ── Per-scan premium sample ──────────────────────────────────
        # Record the live option premium every scan so we can later
        # reconstruct the intraday premium curve and backtest exit rules
        # (stop-loss / take-profit) against real paths. Purely additive —
        # this never changes trigger/target/stop behaviour below.
        try:
            import json as _json
            _p = _option_mid(tk, ct, strike, r["expiry_date"])
            if _p is not None:
                _hhmm = _now_et().strftime("%H:%M")
                try:
                    _path = _json.loads(r.get("premium_path") or "[]")
                except (ValueError, TypeError):
                    _path = []
                # Avoid duplicate samples for the same minute
                if not _path or _path[-1][0] != _hhmm:
                    _path.append([_hhmm, _p])
                    r["premium_path"] = _json.dumps(_path)
                    changed = True
        except Exception as _e:
            log.debug(f"{tk}: premium path sample skipped — {_e}")

        # Trigger crossing
        if r["entered_on_trigger"] == "no" and trigger is not None:
            crossed = (px >= trigger) if ct == "CALL" else (px <= trigger)
            if crossed:
                prem = _option_mid(tk, ct, strike, r["expiry_date"])
                r["entered_on_trigger"] = "yes"
                r["premium_at_trigger"] = prem if prem is not None else ""
                r["stock_at_trigger"]   = round(px, 2)
                changed = True
                log.info(f"📝 {tk} {ct}: entry trigger hit @ ${px:.2f} "
                         f"(premium ${prem})")

        # Target / stop — first touch wins
        if r["hit_target"] == "no" and r["hit_stop"] == "no":
            hit_t = target is not None and ((px >= target) if ct == "CALL" else (px <= target))
            hit_s = stop   is not None and ((px <= stop)   if ct == "CALL" else (px >= stop))
            if hit_t or hit_s:
                prem = _option_mid(tk, ct, strike, r["expiry_date"])
                r["premium_at_exit_ts"] = prem if prem is not None else ""
                if hit_t and not hit_s:
                    r["hit_target"] = "yes"
                elif hit_s and not hit_t:
                    r["hit_stop"] = "yes"
                else:
                    # both in same scan interval — conservative: stop first
                    r["hit_stop"] = "yes"
                changed = True
                log.info(f"📝 {tk} {ct}: {'TARGET' if r['hit_target']=='yes' else 'STOP'} "
                         f"hit @ ${px:.2f} (premium ${prem})")

        r["last_update"] = now

    if changed:
        _save(rows)


def close_out_trades() -> None:
    """
    Called once near market close (~3:55 PM ET). Snapshots the final
    premium for every OPEN trade, computes P&L four ways, marks DONE.
    """
    rows = _load()
    if not rows:
        return
    changed = False
    now = _now_et().isoformat(timespec="seconds")

    for r in rows:
        if r["status"] != "OPEN":
            continue
        tk, ct = r["ticker"], r["contract_type"]

        def _f(key):
            try:
                return float(r[key]) if r[key] not in ("", None) else None
            except (ValueError, TypeError):
                return None

        strike = _f("strike")
        close_prem = _option_mid(tk, ct, strike, r["expiry_date"])
        r["premium_at_close"] = close_prem if close_prem is not None else ""

        p_signal  = _f("premium_at_signal")
        p_trigger = _f("premium_at_trigger")
        p_exit    = _f("premium_at_exit_ts")

        def pnl(entry, exit_):
            if entry and exit_ and entry > 0:
                return round((exit_ - entry) / entry * 100, 1)
            return ""

        # hold-to-close uses close premium; exit uses target/stop premium
        r["pnl_signal_hold"]  = pnl(p_signal,  close_prem)
        r["pnl_signal_exit"]  = pnl(p_signal,  p_exit) if p_exit else ""
        r["pnl_trigger_hold"] = pnl(p_trigger, close_prem) if p_trigger else ""
        r["pnl_trigger_exit"] = pnl(p_trigger, p_exit) if (p_trigger and p_exit) else ""

        r["status"] = "DONE"
        r["last_update"] = now
        changed = True
        log.info(f"📝 Paper trade closed: {tk} {ct} | "
                 f"signal->close {r['pnl_signal_hold']}% | "
                 f"trigger->exit {r['pnl_trigger_exit']}%")

    if changed:
        _save(rows)
