"""
core/freshness.py — Signal freshness check

Problem this solves (the NVDA case):
    The bot scans every 30 minutes, so by the time a signal reaches
    Discord, the analyzed candles can be 30+ minutes old. On fast days
    the entry trigger may already be blown through — e.g. the AI says
    "enter on close below $196" while the stock already trades at
    $195.26, most of the way to the $194.50 target. Entering there is
    chasing, not entering.

How it works:
    At alert time (NOT scan time) we re-fetch the live price and measure
    how much of the trigger→target move has already been consumed:

        consumed = (trigger - live) / (trigger - target)   # PUT
        consumed = (live - trigger) / (target - trigger)   # CALL

    consumed < 0     → price hasn't reached the trigger yet   → FRESH
    0 – STALE_PCT    → trigger hit, most of move still ahead  → FRESH (note added)
    > STALE_PCT      → move mostly over before alert arrived  → STALE

Default STALE_PCT = 0.40 (40% of the move already consumed).
Override with env var FRESHNESS_STALE_PCT.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.logger import get_logger

log = get_logger("Freshness")

STALE_PCT = float(os.getenv("FRESHNESS_STALE_PCT", "0.40"))


def _get_live_price(ticker: str) -> Optional[float]:
    """Fetch the freshest available price. Returns None on failure."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # fast_info.last_price is the cheapest near-real-time quote
        price = t.fast_info.last_price
        if price and price > 0:
            return float(price)
    except Exception as e:
        log.warning(f"{ticker}: live price fetch failed — {e}")
    return None


def check_signal_freshness(
    ticker: str,
    contract_type: str,
    entry_trigger: Optional[float],
    stock_target: Optional[float],
    snapshot_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Evaluate whether a signal is still actionable at alert time.

    Returns:
        {
            "checked":       bool,   # False if we couldn't evaluate
            "is_stale":      bool,
            "live_price":    float | None,
            "consumed_pct":  float | None,  # 0.0–1.0+ of move already done
            "note":          str,           # human-readable, shown in alerts
            "checked_at":    ISO timestamp (UTC),
        }
    """
    result: Dict[str, Any] = {
        "checked": False,
        "is_stale": False,
        "live_price": None,
        "consumed_pct": None,
        "note": "",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    # Nothing to check for NO-TRADE signals or missing levels
    if contract_type not in ("CALL", "PUT"):
        return result
    if not entry_trigger or not stock_target:
        result["note"] = "No numeric trigger/target — freshness not evaluated"
        return result

    move_range = abs(entry_trigger - stock_target)
    if move_range < 0.01:
        result["note"] = "Trigger ≈ target — freshness not evaluated"
        return result

    live = _get_live_price(ticker)
    if live is None:
        result["note"] = "Live price unavailable — verify chart before entering"
        return result

    result["checked"] = True
    result["live_price"] = round(live, 2)

    if contract_type == "PUT":
        consumed = (entry_trigger - live) / move_range
    else:  # CALL
        consumed = (live - entry_trigger) / move_range

    result["consumed_pct"] = round(consumed, 3)

    if consumed < 0:
        result["note"] = (
            f"FRESH — price ${live:.2f} has not reached trigger "
            f"${entry_trigger:.2f} yet"
        )
    elif consumed <= STALE_PCT:
        result["note"] = (
            f"Trigger already hit — ${live:.2f} is {consumed:.0%} of the way "
            f"to target ${stock_target:.2f}. Reduced edge; confirm momentum "
            f"before entering."
        )
    else:
        result["is_stale"] = True
        result["note"] = (
            f"STALE — move already happened. Price ${live:.2f} has consumed "
            f"{consumed:.0%} of the trigger→target range "
            f"(${entry_trigger:.2f} → ${stock_target:.2f}). Do not chase."
        )
        log.warning(f"⏱ {ticker}: {result['note']}")

    # Bonus context: drift since the scan snapshot
    if snapshot_price and snapshot_price > 0:
        drift = (live - snapshot_price) / snapshot_price * 100
        if abs(drift) >= 0.5:
            result["note"] += f" (moved {drift:+.1f}% since scan snapshot)"

    return result
