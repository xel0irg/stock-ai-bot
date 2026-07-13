"""
core/prescreener.py — Tier 1 lightweight pre-screener

Architecture:
    Tier 1 (this module) — runs every scan, pure Python, zero Claude cost
    Tier 2 (main.py)     — full AI synthesis, only when Tier 1 flags a ticker

What Tier 1 checks (any ONE condition triggers):
    1. Price movement ≥ MOVE_PCT% since last scan snapshot
    2. Volume ≥ VOLUME_MULT × 3-month average (intraday spike)
    3. RSI crossing into overbought (>70) or oversold (<30) territory
    4. Price crossed VWAP since last snapshot

PERSISTENCE FIX:
    GitHub Actions runners are ephemeral — every run is a fresh process.
    Snapshots are now persisted to backtest/prescreener_state.json and
    committed back to the repo alongside signals_log.csv. On the next
    run the state is loaded first, so quiet tickers actually get skipped
    instead of all 8 going to Claude every time.

Option 4 — AI RESULT CACHE:
    If a ticker scored NONE last scan AND Tier 1 didn't flag it this
    scan, skip the Claude call and reuse the last result. Only call
    Claude when something has actually changed.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
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
FORCE_SCAN_MINUTES = 60  # force full AI scan after this many minutes regardless

# State file — committed back to repo so it persists across GitHub Actions runs
STATE_FILE = Path("backtest/prescreener_state.json")

# In-memory store (loaded from STATE_FILE on first use)
_SNAPSHOTS: Dict[str, Dict[str, Any]] = {}
_AI_CACHE:  Dict[str, Dict[str, Any]] = {}  # Option 4: last AI result per ticker
_STATE_LOADED = False


# ── Persistence ───────────────────────────────────────────

def _load_state() -> None:
    """Load persisted snapshots + AI cache from disk on first call."""
    global _STATE_LOADED, _SNAPSHOTS, _AI_CACHE
    if _STATE_LOADED:
        return
    _STATE_LOADED = True
    if not STATE_FILE.exists():
        log.info("prescreener_state.json not found — starting fresh (first run)")
        return
    try:
        data = json.loads(STATE_FILE.read_text())
        _SNAPSHOTS = data.get("snapshots", {})
        _AI_CACHE  = data.get("ai_cache", {})
        log.info(f"Loaded pre-screener state: {len(_SNAPSHOTS)} snapshots, "
                 f"{len(_AI_CACHE)} cached AI results")
    except Exception as e:
        log.warning(f"Could not load prescreener_state.json — starting fresh: {e}")


def save_state() -> None:
    """
    Persist snapshots + AI cache to disk so the next GitHub Actions
    run picks up where this one left off.
    Called from main.py after each scan completes.
    """
    _load_state()
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({
            "snapshots": _SNAPSHOTS,
            "ai_cache":  _AI_CACHE,
            "saved_at":  datetime.now(ET).isoformat(timespec="seconds"),
        }, indent=2))
        log.info(f"Pre-screener state saved ({len(_SNAPSHOTS)} snapshots, "
                 f"{len(_AI_CACHE)} AI cache entries)")
    except Exception as e:
        log.warning(f"Could not save prescreener_state.json: {e}")


# ── AI result cache (Option 4) ────────────────────────────

def cache_ai_result(ticker: str, ai_result: Dict[str, Any],
                    was_flagged: bool) -> None:
    """
    Store the AI synthesis result for a ticker.
    Only cache NONE/NO_TRADE results — actionable signals should always
    be re-evaluated fresh to catch changing conditions.
    """
    _load_state()
    contract = (ai_result.get("trade_setup") or {}).get("contract_type", "NONE")
    if contract in ("CALL", "PUT"):
        # Never cache actionable signals
        _AI_CACHE.pop(ticker, None)
        return
    _AI_CACHE[ticker] = {
        "result":      ai_result,
        "was_flagged": was_flagged,
        "cached_at":   time.time(),
        "score":       ai_result.get("confluence_score", 0),
    }


def get_cached_ai_result(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Return cached AI result if:
    - Ticker was NOT flagged by Tier 1 this scan
    - Last result was NONE (no trade)
    - Cache is less than FORCE_SCAN_MINUTES old

    Returns None if any condition fails → caller does fresh Claude call.
    """
    _load_state()
    entry = _AI_CACHE.get(ticker)
    if not entry:
        return None
    age_min = (time.time() - entry.get("cached_at", 0)) / 60
    if age_min > FORCE_SCAN_MINUTES:
        log.debug(f"{ticker}: AI cache expired ({age_min:.0f}min old)")
        return None
    log.info(f"{ticker}: using cached AI result (NONE, {age_min:.0f}min old, "
             f"score={entry['score']}) — skipping Claude call")
    return entry["result"]


# ── Quote fetching ────────────────────────────────────────

def _fast_quote(ticker: str) -> Optional[Dict[str, Any]]:
    """Cheapest possible quote — yf.fast_info only. ~0.1-0.3 sec."""
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        price      = float(fi.last_price      or 0)
        volume     = float(fi.three_month_average_volume or 0)
        day_vol    = float(getattr(fi, "regular_market_volume", 0) or 0)
        prev_close = float(fi.previous_close  or 0)
        if price <= 0:
            return None
        return {
            "price":        price,
            "prev_close":   prev_close,
            "day_volume":   day_vol,
            "avg_volume":   volume,
            "volume_ratio": round(day_vol / volume, 2) if volume > 0 else 0,
        }
    except Exception as e:
        log.debug(f"{ticker}: fast_quote failed — {e}")
        return None


# ── Tier 1 checks ─────────────────────────────────────────

def _check_ticker(ticker: str, quote: Dict[str, Any],
                  cached_tech: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run all Tier 1 checks for one ticker."""
    _load_state()
    result = {
        "ticker":       ticker,
        "should_scan":  False,
        "reasons":      [],
        "price":        quote["price"],
        "volume_ratio": quote["volume_ratio"],
        "checked_at":   datetime.now(ET).isoformat(timespec="seconds"),
    }

    snap = _SNAPSHOTS.get(ticker, {})

    # Check 1: Price movement since last snapshot
    last_price = snap.get("price")
    if last_price and last_price > 0:
        move_pct = abs(quote["price"] - last_price) / last_price * 100
        if move_pct >= MOVE_PCT:
            direction = "▲" if quote["price"] > last_price else "▼"
            result["reasons"].append(
                f"Price moved {direction}{move_pct:.2f}% "
                f"(${last_price:.2f} → ${quote['price']:.2f})"
            )

    # Check 2: Volume spike
    if quote["volume_ratio"] >= VOLUME_MULT:
        result["reasons"].append(f"Volume spike {quote['volume_ratio']:.1f}x avg")

    # Check 3: RSI extremes (from cached technicals)
    if cached_tech:
        ta = cached_tech.get("technicals", {})
        rsi = ta.get("rsi")
        if rsi is not None:
            last_rsi = snap.get("rsi")
            if last_rsi is not None:
                if last_rsi < RSI_OB <= rsi:
                    result["reasons"].append(f"RSI crossed overbought ({rsi:.1f})")
                elif last_rsi > RSI_OS >= rsi:
                    result["reasons"].append(f"RSI crossed oversold ({rsi:.1f})")
            elif rsi >= RSI_OB or rsi <= RSI_OS:
                label = "overbought" if rsi >= RSI_OB else "oversold"
                result["reasons"].append(f"RSI at {label} ({rsi:.1f})")

        # Check 4: VWAP cross
        intraday = cached_tech.get("intraday", {})
        tf15 = intraday.get("tf_15m", {})
        vwap = tf15.get("vwap")
        if vwap and vwap > 0:
            above_vwap = quote["price"] > vwap
            last_above = snap.get("above_vwap")
            if last_above is not None and above_vwap != last_above:
                cross = "above" if above_vwap else "below"
                result["reasons"].append(f"Price crossed {cross} VWAP (${vwap:.2f})")

    if result["reasons"]:
        result["should_scan"] = True
        log.info(f"🔍 {ticker} FLAGGED: {' | '.join(result['reasons'])}")
    else:
        log.debug(f"  {ticker}: no trigger "
                  f"(price=${quote['price']:.2f}, vol={quote['volume_ratio']:.1f}x)")

    return result


def update_snapshot(ticker: str, quote: Dict[str, Any],
                    cached_tech: Optional[Dict[str, Any]] = None) -> None:
    """Update the snapshot store after a check."""
    _load_state()
    snap: Dict[str, Any] = {
        "price":        quote["price"],
        "volume_ratio": quote["volume_ratio"],
        "ts":           time.time(),
    }
    if ticker in _SNAPSHOTS:
        snap["last_full_scan_ts"] = _SNAPSHOTS[ticker].get("last_full_scan_ts", 0)
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
    Returns (flagged_tickers, results_by_ticker).
    """
    _load_state()
    flagged = []
    results = {}
    ct = cached_tech or {}

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


def force_full_scan_interval(tickers: List[str]) -> List[str]:
    """
    Force a full AI scan for tickers not scanned in FORCE_SCAN_MINUTES.
    Uses persisted last_full_scan_ts so this works across GitHub Actions runs.
    """
    _load_state()
    now   = time.time()
    stale = []
    for ticker in tickers:
        snap    = _SNAPSHOTS.get(ticker, {})
        last_ts = snap.get("last_full_scan_ts", 0)
        age_min = (now - last_ts) / 60
        if age_min >= FORCE_SCAN_MINUTES:
            stale.append(ticker)
    if stale:
        log.info(f"Force-scan (>{FORCE_SCAN_MINUTES}min since last full scan): {stale}")
    return stale


def mark_full_scan(ticker: str) -> None:
    """Record that a full AI scan just ran for this ticker."""
    _load_state()
    if ticker not in _SNAPSHOTS:
        _SNAPSHOTS[ticker] = {}
    _SNAPSHOTS[ticker]["last_full_scan_ts"] = time.time()
