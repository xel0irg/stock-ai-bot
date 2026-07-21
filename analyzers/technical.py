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
        # Fix for yfinance multi-level column tuples (newer versions)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() for col in df.columns]
        else:
            df.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df.columns]
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

    # ── Data quality guard ────────────────────────────────
    # Drop any trailing NaN rows that yfinance sometimes appends
    # outside market hours or during data feed hiccups
    close  = close.dropna()
    high   = high.dropna()
    low    = low.dropna()
    volume = volume.dropna()

    if len(close) < 30:
        log.warning("Insufficient clean price data after NaN removal")
        return {}

    last_close = float(close.iloc[-1])
    if np.isnan(last_close) or last_close <= 0:
        log.warning(f"last_close is invalid ({last_close}) — skipping technicals")
        return {}

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


def score_technicals(ta: Dict[str, Any],
                     intraday: Dict[str, Any] | None = None) -> int:
    """
    Convert technical signals into a 0-100 bullish confluence score.

    `ta` carries DAILY indicators. `intraday` carries the 5m/15m/1H
    structure. For 0-2 DTE trades the intraday tape matters more than
    the daily structure — a stock can be below its 200 EMA (true for
    nearly every ticker on this watchlist) while being cleanly bullish
    on the session. Scoring on daily indicators alone is what produced
    a flood of PUT signals against rising tickers.
    """
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
    elif trend == "bull_above_200": score += 3   # mirrors bear_below_200
    elif trend == "strong_bear":  score -= 12
    elif trend == "bear_below_200": score -= 3   # was -6; weak evidence for 0-2 DTE

    # Volume surge (smart money signal)
    # Thresholds rescaled: the old bands required vol_ratio > 2.0 for any
    # positive contribution, which essentially never happens on this
    # watchlist intraday (typical readings are 0.3-0.8x). That made the
    # volume block a permanent -4 penalty and part of a structural
    # bearish floor. Bands now reflect realistic intraday ranges.
    vol_ratio = ta.get("volume_ratio")
    if vol_ratio is None:
        pass                                    # no data -> neutral
    elif vol_ratio >= 2.0:  score += 12         # genuine surge
    elif vol_ratio >= 1.5:  score += 8
    elif vol_ratio >= 1.0:  score += 4          # at or above average
    elif vol_ratio >= 0.5:  pass                # normal intraday -> neutral
    else:                   score -= 4          # genuinely thin

    # OBV
    # The old logic was `if rising: +5 else: -5`, so ANY non-"rising"
    # value — including "flat", missing keys, or None — scored as
    # bearish. Every other indicator here treats unknown as neutral.
    obv = ta.get("obv_trend")
    if obv == "rising":    score += 5
    elif obv == "falling": score -= 5
    # anything else (flat / unknown / missing) -> neutral

    # Stochastic
    stoch = ta.get("stoch_signal", "neutral")
    if stoch == "oversold":   score += 8
    elif stoch == "overbought": score -= 8

    # BB position
    bb_pct = ta.get("bb_pct", 0.5)
    if bb_pct < 0.1:   score += 8
    elif bb_pct > 0.9: score -= 8

    # ── INTRADAY STRUCTURE (0-2 DTE weighting) ───────────────────────
    # Weighted deliberately heavily: this is the timeframe the trade
    # actually lives on. Previously contributed nothing at all — it was
    # fetched AFTER scoring ran, so the scorer never saw it.
    if intraday and intraday.get("has_data"):
        tf5  = intraday.get("tf_5m")  or {}
        tf15 = intraday.get("tf_15m") or {}
        tf1h = intraday.get("tf_1h")  or {}

        # VWAP position across the three timeframes (+4 each way, max ±12).
        # Being above VWAP on all three is the cleanest bullish structure
        # available intraday; below all three is the cleanest bearish.
        for tf in (tf5, tf15, tf1h):
            pos = (tf.get("vwap_position") or "").lower()
            if "above" in pos:   score += 4
            elif "below" in pos: score -= 4

        # Combined intraday bias
        ib = (intraday.get("intraday_bias") or "").upper()
        if ib == "BULLISH":            score += 10
        elif ib == "LEANING_BULLISH":  score += 5
        elif ib == "BEARISH":          score -= 10
        elif ib == "LEANING_BEARISH":  score -= 5

        # 3-candle momentum on the 15m — short-term directional thrust
        mom = tf15.get("momentum_3c")
        if isinstance(mom, (int, float)):
            if mom >= 1.0:    score += 6
            elif mom >= 0.3:  score += 3
            elif mom <= -1.0: score -= 6
            elif mom <= -0.3: score -= 3

        # Intraday overextension guard — a parabolic 5m RSI is a poor
        # entry in EITHER direction. Pull the score back toward neutral
        # rather than rewarding a chase.
        rsi5 = tf5.get("rsi")
        if isinstance(rsi5, (int, float)):
            if rsi5 >= 85 and score > 50:   score -= 8
            elif rsi5 <= 15 and score < 50: score += 8

    return max(0, min(100, score))


def fetch_intraday(ticker: str) -> Dict[str, Any]:
    """
    Fetch 5-min, 15-min and 1-hour intraday data and compute key signals
    for entry timing on 0-2 DTE options trades.

    Three timeframes: 5m (entry precision) + 15m (setup confirmation)
    + 1H (trend direction). All three aligned = highest conviction.
    """
    result = {
        "has_data":       False,
        "tf_5m":          {},
        "tf_15m":         {},
        "tf_1h":          {},
        "intraday_bias":  "NEUTRAL",
        "confirms_daily": None,
        "summary":        "No intraday data",
    }

    def _compute_intraday(df: pd.DataFrame, label: str) -> Dict[str, Any]:
        """Compute intraday signals for one timeframe."""
        if df.empty or len(df) < 10:
            return {}

        close  = df["close"].dropna()
        high   = df["high"].dropna()
        low    = df["low"].dropna()
        volume = df["volume"].dropna()

        if len(close) < 10:
            return {}

        last = float(close.iloc[-1])
        if np.isnan(last) or last <= 0:
            return {}

        signals: Dict[str, Any] = {}
        signals["last_price"] = round(last, 2)
        signals["candles"]    = len(close)

        # RSI (shorter window for intraday)
        try:
            rsi = float(RSIIndicator(close, window=9).rsi().iloc[-1])
            signals["rsi"] = round(rsi, 2)
            signals["rsi_signal"] = (
                "oversold"   if rsi < 30 else
                "overbought" if rsi > 70 else
                "neutral"
            )
        except Exception:
            signals["rsi"] = None

        # MACD
        try:
            macd_ind  = MACD(close, window_fast=12, window_slow=26, window_sign=9)
            macd_hist = float(macd_ind.macd_diff().iloc[-1])
            macd_prev = float(macd_ind.macd_diff().iloc[-2])
            signals["macd_hist"] = round(macd_hist, 4)
            signals["macd_direction"] = (
                "bullish_cross" if macd_hist > 0 and macd_prev <= 0 else
                "bearish_cross" if macd_hist < 0 and macd_prev >= 0 else
                "bullish"       if macd_hist > 0 else
                "bearish"
            )
        except Exception:
            signals["macd_direction"] = None

        # EMA 9 and 21
        try:
            ema9  = float(EMAIndicator(close, window=9).ema_indicator().iloc[-1])
            ema21 = float(EMAIndicator(close, window=21).ema_indicator().iloc[-1])
            signals["ema9"]  = round(ema9, 2)
            signals["ema21"] = round(ema21, 2)
            signals["ema_trend"] = (
                "bullish" if last > ema9 > ema21 else
                "bearish" if last < ema9 < ema21 else
                "mixed"
            )
        except Exception:
            signals["ema_trend"] = None

        # VWAP (key intraday level — price above = bullish, below = bearish)
        try:
            vwap = float(VolumeWeightedAveragePrice(
                high, low, close, volume
            ).volume_weighted_average_price().iloc[-1])
            signals["vwap"] = round(vwap, 2)
            signals["vwap_position"] = (
                "above_vwap" if last > vwap else "below_vwap"
            )
            signals["vwap_distance_pct"] = round(((last - vwap) / vwap) * 100, 3)
        except Exception:
            signals["vwap"] = None
            signals["vwap_position"] = None

        # Volume surge on most recent candle
        try:
            vol_ma = float(volume.rolling(20).mean().iloc[-1])
            last_vol = float(volume.iloc[-1])
            vol_ratio = round(last_vol / vol_ma, 2) if vol_ma > 0 else 1.0
            signals["volume_ratio"] = vol_ratio
            signals["volume_signal"] = (
                "surge"    if vol_ratio > 2.0 else
                "elevated" if vol_ratio > 1.3 else
                "normal"   if vol_ratio > 0.7 else
                "low"
            )
        except Exception:
            signals["volume_ratio"] = None

        # Recent candle momentum (last 3 candles)
        try:
            recent_ret = float((close.iloc[-1] / close.iloc[-4] - 1) * 100)
            signals["momentum_3c"] = round(recent_ret, 3)
            signals["momentum_direction"] = "bullish" if recent_ret > 0 else "bearish"
        except Exception:
            signals["momentum_3c"] = None

        # Determine overall intraday bias for this timeframe
        bull_signals = 0
        bear_signals = 0
        if signals.get("macd_direction") in ("bullish", "bullish_cross"): bull_signals += 1
        if signals.get("macd_direction") in ("bearish", "bearish_cross"): bear_signals += 1
        if signals.get("ema_trend") == "bullish": bull_signals += 1
        if signals.get("ema_trend") == "bearish": bear_signals += 1
        if signals.get("vwap_position") == "above_vwap": bull_signals += 1
        if signals.get("vwap_position") == "below_vwap": bear_signals += 1
        if signals.get("momentum_direction") == "bullish": bull_signals += 1
        if signals.get("momentum_direction") == "bearish": bear_signals += 1

        signals["bias"] = (
            "BULLISH" if bull_signals >= 3 else
            "BEARISH" if bear_signals >= 3 else
            "MIXED"
        )
        signals["bull_signals"] = bull_signals
        signals["bear_signals"] = bear_signals

        return signals

    try:
        # 5-minute: last 1 day (entry precision)
        df_5m = yf.download(ticker, period="1d", interval="5m",
                            progress=False, auto_adjust=True)
        if isinstance(df_5m.columns, pd.MultiIndex):
            df_5m.columns = [col[0].lower() for col in df_5m.columns]
        else:
            df_5m.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df_5m.columns]

        # 15-minute: last 2 days of data
        df_15m = yf.download(ticker, period="2d", interval="15m",
                             progress=False, auto_adjust=True)
        if isinstance(df_15m.columns, pd.MultiIndex):
            df_15m.columns = [col[0].lower() for col in df_15m.columns]
        else:
            df_15m.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df_15m.columns]

        # 1-hour: last 5 days of data
        df_1h = yf.download(ticker, period="5d", interval="1h",
                            progress=False, auto_adjust=True)
        if isinstance(df_1h.columns, pd.MultiIndex):
            df_1h.columns = [col[0].lower() for col in df_1h.columns]
        else:
            df_1h.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in df_1h.columns]

        tf_5m  = _compute_intraday(df_5m,  "5m")
        tf_15m = _compute_intraday(df_15m, "15m")
        tf_1h  = _compute_intraday(df_1h,  "1h")

        if not tf_15m and not tf_1h:
            return result

        result["tf_5m"]    = tf_5m
        result["tf_15m"]   = tf_15m
        result["tf_1h"]    = tf_1h
        result["has_data"] = True

        # Three-timeframe confluence — all three aligned = highest conviction
        bias_5m  = tf_5m.get("bias",  "MIXED") if tf_5m else "MIXED"
        bias_15m = tf_15m.get("bias", "MIXED")
        bias_1h  = tf_1h.get("bias",  "MIXED")

        all_bull = all(b == "BULLISH" for b in [bias_5m, bias_15m, bias_1h])
        all_bear = all(b == "BEARISH" for b in [bias_5m, bias_15m, bias_1h])
        two_bull = [bias_5m, bias_15m, bias_1h].count("BULLISH") >= 2
        two_bear = [bias_5m, bias_15m, bias_1h].count("BEARISH") >= 2

        if all_bull:
            result["intraday_bias"] = "BULLISH"
        elif all_bear:
            result["intraday_bias"] = "BEARISH"
        elif two_bear:
            result["intraday_bias"] = "LEANING_BEARISH"
        elif two_bull:
            result["intraday_bias"] = "LEANING_BULLISH"
        else:
            result["intraday_bias"] = "MIXED"

        vwap_5m  = tf_5m.get("vwap_position",  "") if tf_5m else ""
        vwap_15m = tf_15m.get("vwap_position", "")
        vwap_1h  = tf_1h.get("vwap_position",  "")
        vwap_str = (
            f"5m: {vwap_5m.replace('_',' ').upper() if vwap_5m else 'N/A'} | "
            f"15m: {vwap_15m.replace('_',' ').upper() if vwap_15m else 'N/A'} | "
            f"1H: {vwap_1h.replace('_',' ').upper() if vwap_1h else 'N/A'}"
        )

        result["summary"] = (
            f"Intraday Bias: {result['intraday_bias']} | "
            f"VWAP: {vwap_str} | "
            f"5m: {bias_5m} ({tf_5m.get('bull_signals',0) if tf_5m else 0}B/{tf_5m.get('bear_signals',0) if tf_5m else 0}Br) | "
            f"15m: {bias_15m} ({tf_15m.get('bull_signals',0)}B/{tf_15m.get('bear_signals',0)}Br) | "
            f"1H: {bias_1h} ({tf_1h.get('bull_signals',0)}B/{tf_1h.get('bear_signals',0)}Br)"
        )
        log.info(f"Intraday signals for {ticker}: {result['summary']}")

    except Exception as e:
        log.warning(f"Intraday fetch error for {ticker}: {e}")

    return result
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

    ta = compute_technicals(df)

    options  = fetch_options_flow(ticker)
    short    = fetch_short_interest(ticker)
    intraday = fetch_intraday(ticker)

    # Score AFTER intraday is available — previously score_technicals()
    # ran before fetch_intraday(), so intraday structure could not
    # possibly influence the score.
    ta_score = score_technicals(ta, intraday)

    return {
        "ticker":         ticker,
        "timestamp":      datetime.now().isoformat(),
        "technicals":     ta,
        "ta_score":       ta_score,
        "options_flow":   options,
        "short_interest": short,
        "intraday":       intraday,
    }
