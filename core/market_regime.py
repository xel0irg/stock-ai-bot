"""
core/market_regime.py — Market regime context for the AI prompt

Problem this solves (backtest finding, week 27):
    188 PUT signals vs 29 CALLs. CALLs won 13.8% vs PUTs 0.5%.
    The scanner had no awareness of the broad market trend.

Fetched once per scan, cached 30 min, shared across all tickers.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("MarketRegime")

ET = ZoneInfo("America/New_York")

_CACHE: Dict[str, Any] = {}
_CACHE_TTL = 1800  # 30 min

SECTOR_ETFS = {
    "XLK": "Tech",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLY": "Cons.Discretionary",
    "XLP": "Cons.Staples",
}

TICKER_SECTOR = {
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "META": "XLK",
    "AMZN": "XLY", "TSLA": "XLY",
    "SPY":  None,  "QQQ":  None,
}


def _fetch_quote(yf_ticker) -> Dict[str, Any]:
    try:
        fi    = yf_ticker.fast_info
        price = float(fi.last_price or 0)
        prev  = float(fi.previous_close or 0)
        ret1d = round((price - prev) / prev * 100, 2) if prev else None
        hist  = yf_ticker.history(period="1mo", interval="1d", auto_adjust=True)
        if len(hist) >= 21:
            ema9  = float(hist["Close"].ewm(span=9,  adjust=False).mean().iloc[-1])
            ema21 = float(hist["Close"].ewm(span=21, adjust=False).mean().iloc[-1])
            trend = "ABOVE" if price > ema9 > ema21 else (
                    "BELOW" if price < ema9 < ema21 else "MIXED")
        else:
            ema9 = ema21 = None
            trend = "UNKNOWN"
        return {"price": price, "ret1d": ret1d, "ema9": ema9, "ema21": ema21, "trend": trend}
    except Exception as e:
        log.debug(f"quote fetch failed: {e}")
        return {}


def _vix_regime(level: float) -> str:
    if level < 15:  return "CALM (low fear, trending conditions favor momentum)"
    if level < 20:  return "NORMAL"
    if level < 25:  return "ELEVATED (hedging increasing, wider intraday swings)"
    if level < 30:  return "HIGH (fear rising, consider smaller size)"
    return f"EXTREME FEAR ({level:.1f}) — avoid 0DTE, premium is richly priced"


def get_market_regime(force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch broad market context, cached for 30 min."""
    now = time.time()
    if not force_refresh and _CACHE.get("ts") and (now - _CACHE["ts"]) < _CACHE_TTL:
        return _CACHE["data"]

    log.info("Fetching market regime context (SPY/QQQ/VIX/sectors)...")
    result: Dict[str, Any] = {
        "spy": {}, "qqq": {}, "vix": None,
        "vix_regime": "", "sectors": {},
        "summary": "", "fetched_at": datetime.now(ET).isoformat(timespec="minutes"),
    }

    try:
        import yfinance as yf
        result["spy"] = _fetch_quote(yf.Ticker("SPY"))
        result["qqq"] = _fetch_quote(yf.Ticker("QQQ"))

        try:
            vix_hist = yf.Ticker("^VIX").history(period="2d", interval="1d")
            if not vix_hist.empty:
                result["vix"] = round(float(vix_hist["Close"].iloc[-1]), 2)
                result["vix_regime"] = _vix_regime(result["vix"])
        except Exception as e:
            log.debug(f"VIX fetch failed: {e}")

        for etf, label in SECTOR_ETFS.items():
            try:
                fi    = yf.Ticker(etf).fast_info
                price = float(fi.last_price or 0)
                prev  = float(fi.previous_close or 0)
                if price and prev:
                    result["sectors"][etf] = {
                        "label": label,
                        "ret1d": round((price - prev) / prev * 100, 2),
                    }
            except Exception:
                pass

    except Exception as e:
        log.warning(f"Market regime fetch failed: {e}")
        result["summary"] = "Market regime context unavailable"
        return result

    lines = [
        "\n═══════════════════════════════════════════════",
        "🌍 MARKET REGIME CONTEXT",
        "═══════════════════════════════════════════════",
    ]

    spy = result["spy"]
    qqq = result["qqq"]
    if spy:
        a = "▲" if spy.get("trend") == "ABOVE" else "▼" if spy.get("trend") == "BELOW" else "↔"
        lines.append(f"SPY: ${spy.get('price',0):.2f} ({spy.get('ret1d',0):+.2f}%) "
                     f"| EMA9={spy.get('ema9',0):.2f} EMA21={spy.get('ema21',0):.2f} "
                     f"| Trend: {a} {spy.get('trend','?')}")
    if qqq:
        a = "▲" if qqq.get("trend") == "ABOVE" else "▼" if qqq.get("trend") == "BELOW" else "↔"
        lines.append(f"QQQ: ${qqq.get('price',0):.2f} ({qqq.get('ret1d',0):+.2f}%) "
                     f"| EMA9={qqq.get('ema9',0):.2f} EMA21={qqq.get('ema21',0):.2f} "
                     f"| Trend: {a} {qqq.get('trend','?')}")
    if result["vix"]:
        lines.append(f"VIX: {result['vix']} — {result['vix_regime']}")

    if result["sectors"]:
        parts = []
        for etf, d in sorted(result["sectors"].items(), key=lambda x: x[1]["ret1d"], reverse=True):
            arrow = "▲" if d["ret1d"] > 0 else "▼"
            parts.append(f"{d['label']} {arrow}{d['ret1d']:+.1f}%")
        lines.append("Sectors: " + " | ".join(parts))

    spy_trend = spy.get("trend", "UNKNOWN")
    qqq_trend = qqq.get("trend", "UNKNOWN")
    vix = result["vix"] or 20
    if spy_trend == "ABOVE" and qqq_trend == "ABOVE" and vix < 20:
        lines.append(
            "⚠️  REGIME SIGNAL: Broad market trending BULLISH with low fear. "
            "Counter-trend PUT setups need significantly higher confluence. "
            "CALL setups get a tailwind."
        )
    elif spy_trend == "BELOW" and qqq_trend == "BELOW" and vix > 20:
        lines.append(
            "⚠️  REGIME SIGNAL: Broad market trending BEARISH with elevated fear. "
            "PUT setups get a tailwind. Counter-trend CALL setups need higher confluence."
        )
    elif spy_trend == "MIXED" or qqq_trend == "MIXED":
        lines.append(
            "ℹ️  REGIME SIGNAL: Mixed/choppy broad market — "
            "individual ticker setups carry more weight than market direction."
        )

    result["summary"] = "\n".join(lines)
    log.info(f"Market regime: SPY {spy.get('trend','?')} | QQQ {qqq.get('trend','?')} | VIX {result['vix']}")

    _CACHE["ts"]   = now
    _CACHE["data"] = result
    return result


def regime_for_ticker(ticker: str) -> str:
    """Return regime summary with ticker's sector highlighted."""
    regime = get_market_regime()
    base   = regime.get("summary", "")
    if not base:
        return ""
    sector_etf = TICKER_SECTOR.get(ticker.upper())
    if sector_etf and sector_etf in regime.get("sectors", {}):
        d     = regime["sectors"][sector_etf]
        arrow = "▲" if d["ret1d"] > 0 else "▼"
        spy_positive = (regime.get("spy") or {}).get("ret1d", 0) > 0
        acting = "with" if (d["ret1d"] > 0) == spy_positive else "against"
        base += (f"\n{ticker} Sector ({d['label']} / {sector_etf}): "
                 f"{arrow}{d['ret1d']:+.2f}% today — acting {acting} the broad market.")
    return base
