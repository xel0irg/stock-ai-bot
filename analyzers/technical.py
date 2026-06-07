"""
analyzers/technical.py — Price action, volume, technicals, options flow
"""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice
from datetime import datetime, timedelta
from typing import Dict, Any
import requests
from bs4 import BeautifulSoup

from core.logger import get_logger
from config.settings import TA_SETTINGS

log = get_logger("TechAnalyzer")


def fetch_ohlcv(ticker: str, period: str = "3mo", interval: str = "1d") -> pd.DataFrame:
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         progress=False, auto_adjust=True)
        if df.empty:
            log.warning(f"No OHLCV data for {ticker}")
            return pd.DataFrame()
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        log.error(f"OHLCV fetch error for {ticker}: {e}")
        return pd.DataFrame()


def compute_technicals(df: pd.DataFrame) -> Dict[str, Any]:
    """Run full technical indicator suite on OHLCV dataframe."""
    if df.empty or len(df) < 30:
        return {}

    s = TA_SETTINGS
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    results: Dict[str, Any] = {}

    # ── RSI ───────────────────────────────────────────────
    rsi_ind = RSIIndicator(close, window=s["rsi_period"])
    rsi     = float(rsi_ind.rsi().iloc[-1])
    results["rsi"] = round(rsi, 2)
    results["rsi_signal"] = (
        "oversold"       if rsi < 30 else
        "near_oversold"  if rsi < 40 else
        "neutral"        if rsi < 60 else
        "near_overbought"if rsi < 70 else
        "overbought"
    )

    # ── MACD ──────────────────────────────────────────────
    macd_ind = MACD(close,
                    window_fast=s["macd_fast"],
                    window_slow=s["macd_slow"],
                    window_sign=s["macd_signal"])
    macd_val  = float(macd_ind.macd().iloc[-1])
    macd_sig  = float(macd_ind.macd_signal().iloc[-1])
    macd_hist = float(macd_ind.macd_diff().iloc[-1])
    macd_hist_prev = float(macd_ind.macd_diff().iloc[-2])
    results["macd"]          = round(macd_val, 4)
    results["macd_signal"]   = round(macd_sig, 4)
    results["macd_hist"]     = round(macd_hist, 4)
    results["macd_crossover"] = (
        "bullish_cross" if macd_val > macd_sig and macd_hist > 0 and macd_hist_prev <= 0 else
        "bearish_cross" if macd_val < macd_sig and macd_hist < 0 and macd_hist_prev >= 0 else
        "bullish"       if macd_val > macd_sig else
        "bearish"
    )

    # ── Bollinger Bands ───────────────────────────────────
    bb = BollingerBands(close, window=s["bb_period"], window_dev=s["bb_std"])
    bb_upper = float(bb.bollinger_hband().iloc[-1])
    bb_lower = float(bb.bollinger_lband().iloc[-1])
    bb_mid   = float(bb.bollinger_mavg().iloc[-1])
    last_close = float(close.iloc[-1])
    bb_pct = (last_close - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) != 0 else 0.5
    results["bb_upper"]  = round(bb_upper, 2)
    results["bb_lower"]  = round(bb_lower, 2)
    results["bb_mid"]    = round(bb_mid, 2)
    results["bb_pct"]    = round(bb_pct, 3)
    results["bb_signal"] = (
        "at_lower_band" if bb_pct < 0.1 else
        "lower_half"    if bb_pct < 0.5 else
        "upper_half"    if bb_pct < 0.9 else
        "at_upper_band"
    )

    # ── EMAs ──────────────────────────────────────────────
    ema9   = float(EMAIndicator(close, window=s["ema_short"]).ema_indicator().iloc[-1])
    ema21  = float(EMAIndicator(close, window=s["ema_medium"]).ema_indicator().iloc[-1])
    ema50  = float(EMAIndicator(close, window=s["ema_long"]).ema_indicator().iloc[-1])
    ema200 = float(EMAIndicator(close, window=s["ema_very_long"]).ema_indicator().iloc[-1])
    results["ema9"]   = round(ema9, 2)
    results["ema21"]  = round(ema21, 2)
    results["ema50"]  = round(ema50, 2)
    results["ema200"] = round(ema200, 2)
    results["ema_trend"] = (
        "strong_bull" if ema9 > ema21 > ema50 > ema200 else
        "strong_bear" if ema9 < ema21 < ema50 < ema200 else
        "bull_above_200" if last_close > ema200 else
        "bear_below_200"
    )

    # ── ATR (volatility) ──────────────────────────────────
    atr = float(AverageTrueRange(high, low, close, window=s["atr_period"]).average_true_range().iloc[-1])
    atr_pct = (atr / last_close) * 100
    results["atr"]     = round(atr, 2)
    results["atr_pct"] = round(atr_pct, 2)

    # ── Volume analysis ───────────────────────────────────
    vol_ma    = float(volume.rolling(s["volume_ma_period"]).mean().iloc[-1])
    last_vol  = float(volume.iloc[-1])
    vol_ratio = last_vol / vol_ma if vol_ma > 0 else 1.0
    results["volume_last"]   = int(last_vol)
    results["volume_ma20"]   = int(vol_ma)
    results["volume_ratio"]  = round(vol_ratio, 2)
    results["volume_signal"] = (
        "extreme_surge" if vol_ratio > 3.0 else
        "high"          if vol_ratio > 2.0 else
        "elevated"      if vol_ratio > 1.5 else
        "normal"        if vol_ratio > 0.7 else
        "low"
    )

    # ── OBV ───────────────────────────────────────────────
    obv = OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    obv_trend = "rising" if float(obv.iloc[-1]) > float(obv.iloc[-5]) else "falling"
    results["obv_trend"] = obv_trend

    # ── Price momentum ────────────────────────────────────
    ret_1d  = float((close.iloc[-1] / close.iloc[-2] - 1) * 100) if len(close) > 1 else 0
    ret_5d  = float((close.iloc[-1] / close.iloc[-5] - 1) * 100) if len(close) > 5 else 0
    ret_20d = float((close.iloc[-1] / close.iloc[-20] - 1) * 100) if len(close) > 20 else 0
    results["return_1d"]  = round(ret_1d, 2)
    results["return_5d"]  = round(ret_5d, 2)
    results["return_20d"] = round(ret_20d, 2)
    results["last_price"] = round(last_close, 2)

    # ── Support/Resistance (simple swing points) ──────────
    recent = close.iloc[-20:]
    results["resistance_20d"] = round(float(recent.max()), 2)
    results["support_20d"]    = round(float(recent.min()), 2)

    # ── Stochastic ────────────────────────────────────────
    stoch = StochasticOscillator(high, low, close, window=14, smooth_window=3)
    stoch_k = float(stoch.stoch().iloc[-1])
    stoch_d = float(stoch.stoch_signal().iloc[-1])
    results["stoch_k"]      = round(stoch_k, 2)
    results["stoch_d"]      = round(stoch_d, 2)
    results["stoch_signal"] = (
        "oversold"  if stoch_k < 20 else
        "overbought"if stoch_k > 80 else
        "neutral"
    )

    return results


def score_technicals(ta: Dict[str, Any]) -> int:
    """Convert technical signals into a 0-100 bullish confluence score."""
    score = 50  # Start neutral

    # RSI
    rsi = ta.get("rsi", 50)
    if rsi < 30:   score += 15
    elif rsi < 40: score += 8
    elif rsi > 70: score -= 15
    elif rsi > 60: score -= 5

    # MACD
    cross = ta.get("macd_crossover", "")
    if cross == "bullish_cross": score += 15
    elif cross == "bullish":     score += 7
    elif cross == "bearish_cross": score -= 15
    elif cross == "bearish":     score -= 7

    # EMA trend
    trend = ta.get("ema_trend", "")
    if trend == "strong_bull":    score += 12
    elif trend == "bull_above_200": score += 6
    elif trend == "strong_bear":  score -= 12
    elif trend == "bear_below_200": score -= 6

    # Volume surge (smart money signal)
    vol_sig = ta.get("volume_signal", "normal")
    if vol_sig == "extreme_surge": score += 12
    elif vol_sig == "high":        score += 7
    elif vol_sig == "low":         score -= 4

    # OBV
    if ta.get("obv_trend") == "rising": score += 5
    else:                               score -= 5

    # Stochastic
    stoch = ta.get("stoch_signal", "neutral")
    if stoch == "oversold":   score += 8
    elif stoch == "overbought": score -= 8

    # BB position
    bb_pct = ta.get("bb_pct", 0.5)
    if bb_pct < 0.1:   score += 8
    elif bb_pct > 0.9: score -= 8

    return max(0, min(100, score))


def fetch_options_flow(ticker: str) -> Dict[str, Any]:
    """
    Fetch unusual options activity.
    Uses Yahoo Finance options chain (free, no key needed).
    """
    result = {
        "has_data": False,
        "unusual_calls": [],
        "unusual_puts": [],
        "put_call_ratio": None,
        "summary": "No options data available",
    }
    try:
        tk = yf.Ticker(ticker)
        exps = tk.options
        if not exps:
            return result

        # Grab nearest expiration
        exp = exps[0]
        chain = tk.option_chain(exp)
        calls = chain.calls
        puts  = chain.puts

        if calls.empty or puts.empty:
            return result

        # Put/Call ratio by volume
        total_call_vol = calls["volume"].fillna(0).sum()
        total_put_vol  = puts["volume"].fillna(0).sum()
        pc_ratio = round(total_put_vol / total_call_vol, 2) if total_call_vol > 0 else None
        result["put_call_ratio"] = pc_ratio

        # Find unusual options (high volume relative to open interest)
        def flag_unusual(df, kind):
            unusual = []
            for _, row in df.iterrows():
                vol = row.get("volume", 0) or 0
                oi  = row.get("openInterest", 1) or 1
                if vol > 500 and vol / oi > 0.5:
                    unusual.append({
                        "strike":  row.get("strike"),
                        "expiry":  exp,
                        "volume":  int(vol),
                        "oi":      int(oi),
                        "iv":      round(row.get("impliedVolatility", 0) * 100, 1),
                        "type":    kind,
                    })
            return sorted(unusual, key=lambda x: x["volume"], reverse=True)[:5]

        result["unusual_calls"] = flag_unusual(calls, "CALL")
        result["unusual_puts"]  = flag_unusual(puts, "PUT")

        # Build summary string
        unusual_count = len(result["unusual_calls"]) + len(result["unusual_puts"])
        sentiment = (
            "BULLISH OPTIONS FLOW" if len(result["unusual_calls"]) > len(result["unusual_puts"]) else
            "BEARISH OPTIONS FLOW" if len(result["unusual_puts"]) > len(result["unusual_calls"]) else
            "NEUTRAL OPTIONS FLOW"
        )
        result["has_data"] = True
        result["summary"]  = (
            f"{sentiment} | P/C Ratio: {pc_ratio} | "
            f"Unusual activity: {unusual_count} contracts flagged"
        )
        log.info(f"Options flow for {ticker}: {result['summary']}")

    except Exception as e:
        log.warning(f"Options flow error for {ticker}: {e}")

    return result


def fetch_short_interest(ticker: str) -> Dict[str, Any]:
    """Fetch short interest data from Yahoo Finance info."""
    result = {"short_ratio": None, "short_pct_float": None, "summary": "No data"}
    try:
        info = yf.Ticker(ticker).info
        short_ratio   = info.get("shortRatio")
        short_pct     = info.get("shortPercentOfFloat")
        shares_short  = info.get("sharesShort")
        float_shares  = info.get("floatShares")

        if short_pct:
            pct = round(short_pct * 100, 1)
            result["short_pct_float"] = pct
            result["short_ratio"]     = short_ratio
            result["shares_short"]    = shares_short
            result["float_shares"]    = float_shares
            result["squeeze_candidate"] = pct > 15
            result["summary"] = (
                f"Short % of Float: {pct}% | Days to Cover: {short_ratio} | "
                f"{'⚡ SHORT SQUEEZE CANDIDATE' if pct > 15 else 'Normal short interest'}"
            )
            log.info(f"Short interest for {ticker}: {result['summary']}")
    except Exception as e:
        log.warning(f"Short interest error for {ticker}: {e}")

    return result


def run_technical_analysis(ticker: str) -> Dict[str, Any]:
    """Full technical analysis pipeline for one ticker."""
    log.info(f"Running technical analysis for {ticker}...")

    df = fetch_ohlcv(ticker)
    if df.empty:
        return {"error": f"No data for {ticker}"}

    ta  = compute_technicals(df)
    ta_score = score_technicals(ta)

    options = fetch_options_flow(ticker)
    short   = fetch_short_interest(ticker)

    return {
        "ticker":         ticker,
        "timestamp":      datetime.now().isoformat(),
        "technicals":     ta,
        "ta_score":       ta_score,
        "options_flow":   options,
        "short_interest": short,
    }
