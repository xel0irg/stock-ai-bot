"""
core/expected_move.py — Options-implied expected move per expiry

Why this exists (backtest finding, week 27):
    Median AI stock target sat 2.22% from spot, but the median best
    favorable excursion on losing trades was only 0.78%. 71% of losses
    moved the right direction first and died short of an unrealistic
    target. The market tells you every day how far it expects a stock
    to move — the ATM straddle price — and targets beyond that are
    structurally unwinnable on 0-2 DTE timeframes.

Method:
    1. PRIMARY — options chain: expected move ≈ ATM straddle mid price
       / spot, computed from the nearest listed expiry, then scaled to
       0/1/2 DTE by sqrt(time).
    2. FALLBACK — ATR: if the chain is unavailable/illiquid, derive
       from daily ATR% (≈ a 1-day expected move).

Returns percentages, e.g. 1.15 means ±1.15%.
"""
from __future__ import annotations

import math
from datetime import datetime, date
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("ExpectedMove")

ET = ZoneInfo("America/New_York")


def _mid(row) -> Optional[float]:
    try:
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            return (bid + ask) / 2
        last = float(row.get("lastPrice") or 0)
        return last if last > 0 else None
    except Exception:
        return None


def _straddle_em_pct(ticker_obj, expiry: str, spot: float) -> Optional[float]:
    try:
        chain = ticker_obj.option_chain(expiry)
        calls, puts = chain.calls, chain.puts
        if calls.empty or puts.empty:
            return None
        c = calls.iloc[(calls["strike"] - spot).abs().argsort()].iloc[0]
        p = puts.iloc[(puts["strike"] - spot).abs().argsort()].iloc[0]
        c_mid, p_mid = _mid(c), _mid(p)
        if c_mid is None or p_mid is None:
            return None
        em_pct = (c_mid + p_mid) / spot * 100
        if not (0.1 <= em_pct <= 25):
            return None
        return round(em_pct, 2)
    except Exception as e:
        log.debug(f"straddle calc failed for {expiry}: {e}")
        return None


def get_expected_move(
    ticker: str,
    spot: Optional[float],
    atr_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute expected move (±%) for 0DTE / 1DTE / 2DTE horizons.

    Returns:
        {
            "method":  "options" | "atr" | None,
            "0DTE":    float | None,
            "1DTE":    float | None,
            "2DTE":    float | None,
            "summary": str,
        }
    """
    out: Dict[str, Any] = {"method": None, "0DTE": None, "1DTE": None,
                           "2DTE": None, "summary": "Expected move unavailable"}
    if not spot or spot <= 0:
        return out

    anchor_pct, anchor_days = None, None
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        expiries = list(t.options or [])[:3]
        today = datetime.now(ET).date()
        for exp in expiries:
            exp_date = date.fromisoformat(exp)
            days = (exp_date - today).days
            if days < 0 or days > 7:
                continue
            em = _straddle_em_pct(t, exp, spot)
            if em is not None:
                anchor_pct, anchor_days = em, days
                break
    except Exception as e:
        log.debug(f"{ticker}: option chain unavailable — {e}")

    if anchor_pct is not None:
        out["method"] = "options"
        a = max(anchor_days, 0.5)
        for label, d in (("0DTE", 0.5), ("1DTE", 1.0), ("2DTE", 2.0)):
            out[label] = round(anchor_pct * math.sqrt(d / a), 2)
    elif atr_pct and atr_pct > 0:
        out["method"] = "atr"
        out["0DTE"] = round(atr_pct * 0.7, 2)
        out["1DTE"] = round(atr_pct, 2)
        out["2DTE"] = round(atr_pct * 1.41, 2)

    if out["method"]:
        dollars = {k: f"±${spot * out[k] / 100:.2f}" for k in ("0DTE", "1DTE", "2DTE")}
        src = "options-implied (ATM straddle)" if out["method"] == "options" else "ATR-derived (chain unavailable)"
        out["summary"] = (
            f"EXPECTED MOVE ({src}): "
            f"0DTE ±{out['0DTE']}% ({dollars['0DTE']}) | "
            f"1DTE ±{out['1DTE']}% ({dollars['1DTE']}) | "
            f"2DTE ±{out['2DTE']}% ({dollars['2DTE']})"
        )
        log.info(f"{ticker}: {out['summary']}")

    return out


def validate_target(
    contract_type: str,
    expiry: Optional[str],
    spot: Optional[float],
    stock_target: Optional[float],
    expected_move: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Check AI stock target against the expected move for the chosen expiry.
    Clamps targets beyond ±1.0x EM to 0.9x EM in the trade direction.
    """
    res = {"target": stock_target, "target_original": None, "em_pct": None,
           "target_em_ratio": None, "adjusted": False, "note": ""}

    if (contract_type not in ("CALL", "PUT") or not spot or not stock_target
            or not expected_move or not expected_move.get("method")):
        return res

    em_pct = expected_move.get(expiry or "1DTE") or expected_move.get("1DTE")
    if not em_pct:
        return res

    res["em_pct"] = em_pct
    target_dist_pct = abs(stock_target - spot) / spot * 100
    ratio = round(target_dist_pct / em_pct, 2)
    res["target_em_ratio"] = ratio

    if ratio <= 1.0:
        res["note"] = f"Target {target_dist_pct:.1f}% away = {ratio:.1f}x expected move — realistic"
        return res

    clamp_dist = spot * (0.9 * em_pct / 100)
    new_target = spot - clamp_dist if contract_type == "PUT" else spot + clamp_dist
    res["target_original"] = stock_target
    res["target"] = round(new_target, 2)
    res["adjusted"] = True
    res["note"] = (
        f"Target adjusted: AI wanted ${stock_target:.2f} "
        f"({target_dist_pct:.1f}% = {ratio:.1f}x the ±{em_pct}% expected move) "
        f"→ clamped to ${new_target:.2f} (0.9x EM)"
    )
    log.info(f"🎯 {res['note']}")
    return res
