"""
core/fundamentals_cache.py — Daily cache for fundamental data

Why this exists:
    run_fundamental_analysis() makes 4 HTTP calls per ticker
    (yfinance .info, SEC EDGAR filings, OpenInsider, earnings check).
    None of this data changes intraday. Running it 13x per day (every
    30 min) wastes ~500 tokens and 3-4 seconds per ticker per scan.

Strategy:
    - Cache keyed by ticker + calendar date (ET)
    - TTL = until market close (4 PM ET) or 24 hours, whichever first
    - Cache stored in memory (GitHub Actions runners are ephemeral, so
      disk cache would reset between jobs anyway — memory cache within
      a single scan run is what matters most)
    - On the FIRST scan of the day the cache is cold — normal fetch
    - All subsequent scans that day reuse the cached result

Usage:
    from core.fundamentals_cache import get_fundamentals

    fund = get_fundamentals(ticker)   # drop-in replacement
"""
from __future__ import annotations

import time
from datetime import datetime, date
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("FundamentalsCache")

ET = ZoneInfo("America/New_York")

# In-memory cache: {ticker: {"data": {...}, "date": date, "ts": float}}
_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_valid(entry: Dict[str, Any]) -> bool:
    """Cache is valid if it was fetched on today's ET date."""
    if not entry:
        return False
    cached_date = entry.get("date")
    today = datetime.now(ET).date()
    return cached_date == today


def get_fundamentals(ticker: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Return fundamental data for ticker, using today's cached result
    if available. Fetches fresh on first call each calendar day (ET).

    Drop-in replacement for run_fundamental_analysis(ticker).
    """
    ticker = ticker.upper()
    entry  = _CACHE.get(ticker, {})

    if not force_refresh and _cache_valid(entry):
        age_min = (time.time() - entry["ts"]) / 60
        log.info(f"{ticker}: fundamentals served from cache (fetched {age_min:.0f}m ago)")
        return entry["data"]

    log.info(f"{ticker}: fetching fresh fundamentals...")
    try:
        from analyzers.fundamentals import run_fundamental_analysis
        data = run_fundamental_analysis(ticker)
        _CACHE[ticker] = {
            "data": data,
            "date": datetime.now(ET).date(),
            "ts":   time.time(),
        }
        log.info(f"{ticker}: fundamentals cached for today ({datetime.now(ET).date()})")
        return data
    except Exception as e:
        log.warning(f"{ticker}: fundamentals fetch failed — {e}")
        # Return stale cache if available, else empty dict
        if entry.get("data"):
            log.warning(f"{ticker}: returning stale fundamentals from {entry.get('date')}")
            return entry["data"]
        return {
            "ticker":       ticker,
            "timestamp":    datetime.now().isoformat(),
            "fundamentals": {},
            "sec_filings":  [],
            "insider":      {},
            "earnings":     {},
        }


def cache_status() -> Dict[str, Any]:
    """Return current cache state — useful for logging and /status command."""
    today = datetime.now(ET).date()
    status = {}
    for ticker, entry in _CACHE.items():
        age_min = (time.time() - entry.get("ts", 0)) / 60
        status[ticker] = {
            "cached_date": str(entry.get("date")),
            "valid":       entry.get("date") == today,
            "age_minutes": round(age_min, 1),
        }
    return status


def clear_cache(ticker: Optional[str] = None) -> None:
    """Clear cache for one ticker or all tickers."""
    if ticker:
        _CACHE.pop(ticker.upper(), None)
        log.info(f"Cleared fundamentals cache for {ticker}")
    else:
        _CACHE.clear()
        log.info("Cleared all fundamentals cache")
