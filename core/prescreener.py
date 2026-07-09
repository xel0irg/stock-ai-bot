"""
core/prescreener.py — Tier 1 lightweight pre-screener

Architecture:
    Tier 1 (this module) — runs every 5 min, pure Python, zero Claude cost
    Tier 2 (main.py)     — full AI synthesis, only when Tier 1 flags a ticker

What Tier 1 checks (any ONE condition triggers):
    1. Price movement ≥ MOVE_PCT% since last scan snapshot
    2. Volume ≥ VOLUME_MULT × 3-month average (intraday spike)
    3. RSI crossing into overbought (>70) or oversold (<30) territory
    4. Price crossed VWAP since last snapshot (intraday structure break)

Thresholds are conservative — we'd rather over-flag and let the AI
decide than miss a real setup. The cost of a false positive is one
Claude call (~$0.012). The cost of a miss is a skipped signal.

Each ticker's last snapshot is stored in memory so we can detect
*changes* between scans, not just absolute levels.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("PreScreener")

ET = ZoneInfo("America/New_York")

# ── Thresholds ────────────────────────────────────────────
MOVE_PCT      = 0.30   # % price move since last scan to trigger
VOLUME_MULT   = 1.20   # current volume vs 3-month avg to trigger
RSI_OB        = 70.0   # RSI overbought threshold
RSI_OS        = 30.0   # RSI oversold threshold
VWAP_CROSS    = True   # flag on VWAP cross

# In-memory snapshot store: {ticker: {price, volume_ratio, rsi, above_vwap, ts}}
_SNAPSHOTS: Dict[str, Dict[str, Any]] = {}


def _fast_quote(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Cheapest possible quote — yf.fast_info only.
    Returns price, volume, avg_volume. Takes ~0.1-0.3 sec.
    """
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        price      = float(fi.last_price      or 0)
        volume     = float(fi.three_month_average_volume or 0)
        # day_volume isn't directly in fast_info — use regular_market_volume
        day_vol    = float(getattr(fi, "regular_market_volume", 0) or 0)
        prev_close = float(fi.previous_close  or 0)
        if price <= 0:
            return None
        return {
            "price":       price,
            "prev_close":  prev_close,
            "day_volume":  day_vol,
            "avg_volume":  volume,
            "volume_ratio": round(day_vol / volume, 2) if volume > 0 else 0,
        }
    except Exception as e:
        log.debug(f"{ticker}: fast_quote failed — {e}")
        return None


def _check_ticker(ticker: str, quote: Dict[str, Any],
                  cached_tech: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Run all Tier 1 checks for one ticker.

    Args:
        ticker:      ticker symbol
        quote:       from _fast_quote()
        cached_tech: last full technical analysis result (if available)
                     used for RSI and VWAP without an extra fetch

    Returns:
        {
            "should_scan": bool,
            "reasons":     [str],   # why it was flagged
            "price":       float,
            "volume_ratio": float,
        }
    """
    result = {
        "ticker":       ticker,
        "should_scan":  False,
        "reasons":      [],
        "price":        quote["price"],
        "volume_ratio": quote["volume_ratio"],
        "checked_at":   datetime.now(ET).isoformat(timespec="seconds"),
    }

    snap = _SNAPSHOTS.get(ticker, {})

    # ── Check 1: Price movement since last snapshot ───────
    last_price = snap.get("price")
    if last_price and last_price > 0:
        move_pct = abs(quote["price"] - last_price) / last_price * 100
        if move_pct >= MOVE_PCT:
            direction = "▲" if quote["price"] > last_price else "▼"
            result["reasons"].append(
                f"Price moved {direction}{move_pct:.2f}% "
                f"(${last_price:.2f} → ${quote['price']:.2f})"
            )

    # ── Check 2: Volume spike ─────────────────────────────
    if quote["volume_ratio"] >= VOLUME_MULT:
        result["reasons"].append(
            f"Volume spike {quote['volume_ratio']:.1f}x avg"
        )

    # ── Check 3: RSI extremes (from cached technicals) ───
    if cached_tech:
        ta = cached_tech.get("technicals", {})
        rsi = ta.get("rsi")
        if rsi is not None:
            last_rsi = snap.get("rsi")
            # Only flag if RSI crossed the threshold since last scan
            if last_rsi is not None:
                if last_rsi < RSI_OB <= rsi:
                    result["reasons"].append(f"RSI crossed overbought ({rsi:.1f})")
                elif last_rsi > RSI_OS >= rsi:
                    result["reasons"].append(f"RSI crossed oversold ({rsi:.1f})")
            elif rsi >= RSI_OB or rsi <= RSI_OS:
                # No prior snapshot — flag if at extreme
                label = "overbought" if rsi >= RSI_OB else "oversold"
                result["reasons"].append(f"RSI at {label} ({rsi:.1f})")

        # ── Check 4: VWAP cross ───────────────────────────
        if VWAP_CROSS:
            intraday = cached_tech.get("intraday", {})
            tf15 = intraday.get("tf_15m", {})
            vwap = tf15.get("vwap")
            if vwap and vwap > 0:
                above_vwap = quote["price"] > vwap
                last_above = snap.get("above_vwap")
                if last_above is not None and above_vwap != last_above:
                    cross = "above" if above_vwap else "below"
                    result["reasons"].append(
                        f"Price crossed {cross} VWAP (${vwap:.2f})"
                    )

    # ── Flag if any condition met ─────────────────────────
    if result["reasons"]:
        result["should_scan"] = True
        log.info(
            f"🔍 {ticker} FLAGGED: {' | '.join(result['reasons'])}"
        )
    else:
        log.debug(f"  {ticker}: no trigger "
                  f"(price=${quote['price']:.2f}, vol={quote['volume_ratio']:.1f}x)")

    return result


def update_snapshot(ticker: str, quote: Dict[str, Any],
                    cached_tech: Optional[Dict[str, Any]] = None) -> None:
    """Update the snapshot store after a check (whether flagged or not)."""
    snap: Dict[str, Any] = {
        "price": quote["price"],
        "volume_ratio": quote["volume_ratio"],
        "ts": time.time(),
    }
    if cached_tech:
        ta = cached_tech.get("technicals", {})
        snap["rsi"] = ta.get("rsi")
        intraday = cached_tech.get("intraday", {})
        tf15 = intraday.get("tf_15m", {})
        vwap = tf15.get("vwap")
        if vwap:
            snap["above_vwap"] = quote["price"] > vwap
    _SNAPSHOTS[ticker] = snap


def run_prescreener(
    tickers: List[str],
    cached_tech: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """
    Run Tier 1 pre-screen on all tickers.

    Args:
        tickers:     watchlist
        cached_tech: {ticker: last_tech_result} — from previous full scan
                     used for RSI/VWAP checks without extra fetches

    Returns:
        (flagged_tickers, results_by_ticker)
        flagged_tickers: list of tickers that need a full AI scan
    """
    flagged   = []
    results   = {}
    ct        = cached_tech or {}

    for ticker in tickers:
        quote = _fast_quote(ticker)
        if quote is None:
            log.warning(f"{ticker}: quote unavailable — adding to scan queue as fallback")
            flagged.append(ticker)
            continue

        check = _check_ticker(ticker, quote, ct.get(ticker))
        update_snapshot(ticker, quote, ct.get(ticker))
        results[ticker] = check

        if check["should_scan"]:
            flagged.append(ticker)

    log.info(
        f"Pre-screen complete: {len(flagged)}/{len(tickers)} tickers flagged "
        f"({', '.join(flagged) if flagged else 'none'})"
    )
    return flagged, results


def force_full_scan_interval(tickers: List[str], interval_minutes: int = 30) -> List[str]:
    """
    Even if no ticker triggers, force a full scan every N minutes
    so we never go more than interval_minutes without fresh AI analysis.
    Returns tickers that haven't been fully scanned in interval_minutes.
    """
    now = time.time()
    stale = []
    for ticker in tickers:
        snap = _SNAPSHOTS.get(ticker, {})
        last_ts = snap.get("last_full_scan_ts", 0)
        if (now - last_ts) >= interval_minutes * 60:
            stale.append(ticker)
    if stale:
        log.info(f"Force-scan (>{interval_minutes}min since last full scan): {stale}")
    return stale


def mark_full_scan(ticker: str) -> None:
    """Record that a full AI scan just ran for this ticker."""
    if ticker not in _SNAPSHOTS:
        _SNAPSHOTS[ticker] = {}
    _SNAPSHOTS[ticker]["last_full_scan_ts"] = time.time()
