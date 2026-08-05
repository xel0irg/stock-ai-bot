"""
core/alpaca_data.py — Alpaca market-data layer (replaces yfinance fragility)

WHY: yfinance has been the single point of failure in this project —
rate limits, inflated option premiums, phantom strikes, stale quotes,
missing signal cards. Alpaca's free tier returns clean stock bars and
(crucially) tight, reliable option quotes with a `tradable` flag.

DESIGN:
  - Every function is a drop-in for the yfinance equivalent: same inputs,
    same output shape, so callers don't change.
  - Fully gated: if ALPACA_KEY / ALPACA_SECRET aren't set, every function
    returns None and the caller falls back to yfinance. Nothing activates
    until the keys are configured in GitHub Actions secrets.
  - IEX-feed caveat: the free stock QUOTE has unreliable wide spreads, so
    we derive current price from the latest BAR close, never the raw quote
    bid/ask midpoint. Option quotes on this tier are tight and trusted.
"""
from __future__ import annotations

import os
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.logger import get_logger

log = get_logger("AlpacaData")

_KEY = os.environ.get("ALPACA_KEY", "")
_SECRET = os.environ.get("ALPACA_SECRET", "")

DATA_BASE = "https://data.alpaca.markets"

def is_enabled() -> bool:
    """Alpaca is only active when both credentials are present."""
    return bool(_KEY and _SECRET)


def _headers() -> Dict[str, str]:
    return {
        "APCA-API-KEY-ID": _KEY,
        "APCA-API-SECRET-KEY": _SECRET,
        "accept": "application/json",
    }


def _get(url: str) -> Optional[dict]:
    """GET with Alpaca auth. Returns parsed JSON or None on any failure."""
    if not is_enabled():
        return None
    try:
        req = urllib.request.Request(url, headers=_headers())
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        log.debug(f"Alpaca HTTP {e.code} for {url[:80]}")
    except Exception as e:
        log.debug(f"Alpaca request failed: {e}")
    return None


# ── Stock bars ────────────────────────────────────────────────────────
_TF_MAP = {"1d": "1Day", "1h": "1Hour", "1H": "1Hour",
           "15m": "15Min", "5m": "5Min", "1m": "1Min"}


def get_bars(ticker: str, timeframe: str = "1d",
             limit: int = 90) -> Optional[List[dict]]:
    """
    Return a list of OHLCV bars (oldest→newest) or None.
    Each bar: {t, o, h, l, c, v, vw}. Mirrors what callers need from yfinance.
    """
    tf = _TF_MAP.get(timeframe, timeframe)
    url = (f"{DATA_BASE}/v2/stocks/{ticker}/bars"
           f"?timeframe={tf}&limit={limit}&adjustment=raw&feed=iex")
    data = _get(url)
    if not data or "bars" not in data or not data["bars"]:
        return None
    return data["bars"]


def get_latest_price(ticker: str) -> Optional[float]:
    """
    Current price from the latest BAR close — NOT the raw quote.
    The free IEX quote has unreliable wide spreads; the bar close is clean.
    """
    bars = get_bars(ticker, timeframe="1m", limit=1)
    if bars:
        return round(float(bars[-1]["c"]), 2)
    # fall back to a daily bar if 1-min unavailable (off-hours)
    bars = get_bars(ticker, timeframe="1d", limit=1)
    if bars:
        return round(float(bars[-1]["c"]), 2)
    return None


# ── Options ───────────────────────────────────────────────────────────
def _occ_symbol(ticker: str, expiry_date: str, contract_type: str,
                strike: float) -> str:
    """
    Build the OCC option symbol Alpaca uses, e.g. AAPL260805C00210000.
    expiry_date: 'YYYY-MM-DD'. contract_type: CALL/PUT. strike: float.
    """
    d = datetime.fromisoformat(expiry_date)
    yy = d.strftime("%y")
    mm = d.strftime("%m")
    dd = d.strftime("%d")
    cp = "C" if contract_type.upper() == "CALL" else "P"
    strike_int = int(round(strike * 1000))
    return f"{ticker}{yy}{mm}{dd}{cp}{strike_int:08d}"


def get_option_mid(ticker: str, contract_type: str, strike: float,
                   expiry_date: str) -> Optional[float]:
    """
    Live option mid price (bid+ask)/2 for a specific contract.
    Drop-in replacement for paper_tracker._option_mid.
    Option quotes on the free tier are tight/reliable (unlike stock quotes).
    """
    sym = _occ_symbol(ticker, expiry_date, contract_type, strike)
    url = f"{DATA_BASE}/v1beta1/options/quotes/latest?symbols={sym}"
    data = _get(url)
    if not data or "quotes" not in data or sym not in data["quotes"]:
        return None
    q = data["quotes"][sym]
    bid = float(q.get("bp", 0) or 0)
    ask = float(q.get("ap", 0) or 0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    return None


def get_tradable_contracts(ticker: str, expiry_date: str,
                           contract_type: str) -> Optional[List[dict]]:
    """
    List real, tradable contracts for a ticker+expiry+type from the
    trading API. Each has strike_price, expiration_date, tradable.
    This is what lets us snap to strikes that ACTUALLY EXIST — solving
    the 'AI invents phantom strikes' problem at the source.
    """
    ct = "call" if contract_type.upper() == "CALL" else "put"
    url = (f"https://paper-api.alpaca.markets/v2/options/contracts"
           f"?underlying_symbols={ticker}&expiration_date={expiry_date}"
           f"&type={ct}&status=active&limit=200")
    data = _get(url)
    if not data or "option_contracts" not in data:
        return None
    return data["option_contracts"]


def nearest_tradable_strike(ticker: str, contract_type: str,
                            strike: float, expiry_date: str) -> Optional[float]:
    """Snap a requested strike to the nearest real tradable contract."""
    contracts = get_tradable_contracts(ticker, expiry_date, contract_type)
    if not contracts:
        return None
    strikes = []
    for c in contracts:
        try:
            if c.get("tradable", True):
                strikes.append(float(c["strike_price"]))
        except (ValueError, TypeError, KeyError):
            continue
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - strike))
