"""
core/trigger_watch.py — Real-time entry-trigger confirmation

WHY THIS EXISTS (member request):
    Every signal's entry condition is "enter on a 15-minute candle CLOSE
    through $X with volume above Nx". Until now members had to sit and
    watch the chart to know when that happened — so people either entered
    early on anticipation, or noticed late and chased. Both are the
    documented causes of losses.

    This module watches posted signals and fires a follow-up alert the
    moment a COMPLETED 15m candle actually closes through the trigger.

WHAT IT CHECKS (and why it's stricter than the paper tracker):
    The paper tracker compares the latest PRICE against the trigger. That
    is not the same as the entry rule. A wick through the level, or an
    intraday touch, does not satisfy "candle close". This module only
    looks at COMPLETED 15m bars and their CLOSE, which is what the card
    actually asks for.

    It also checks the volume multiple stated in that signal's entry
    condition, and reports it either way — a close on thin volume is
    still reported, but explicitly flagged as failing the volume gate, so
    members are never pinged into an entry the card itself would reject.

STATE: backtest/trigger_watch.json — only signals actually POSTED to
    Discord are registered, so members never get a trigger alert for a
    signal they never saw.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("TriggerWatch")
ET = ZoneInfo("America/New_York")

STATE_FILE = Path("backtest/trigger_watch.json")

# A registered signal stops being watched after this long — a trigger that
# fires hours later is a different setup, not this one.
WATCH_HOURS = 4


def _now_et() -> datetime:
    return datetime.now(ET)


def _expire_stale(rows: List[dict], now: Optional[datetime] = None) -> bool:
    """
    Mark PENDING watches EXPIRED once they are older than WATCH_HOURS *or*
    were registered on an earlier ET day. Returns True if anything changed.

    Fixed 2026-08-30. Expiry used to live only inside check_triggers(), and
    WATCH_HOURS is 4 while the scan window ends early afternoon — so a
    late-morning signal was never swept and survived the night as PENDING.
    register() skips a ticker+direction that already has a PENDING row, so
    the stale entry silently blocked the next day's signal from ever being
    watched (TSLA PUT, 2026-08-26: posted to members, never monitored).
    """
    now = now or _now_et()
    changed = False
    for r in rows:
        if r.get("status") != "PENDING":
            continue
        try:
            sig_t = datetime.fromisoformat(r["signal_time"])
        except Exception:
            sig_t = now
        stale_age = (now - sig_t) > timedelta(hours=WATCH_HOURS)
        stale_day = sig_t.date() != now.date()
        if stale_age or stale_day:
            r["status"] = "EXPIRED"
            changed = True
            log.info(f"Expired stale watch: {r.get('ticker')} "
                     f"{r.get('direction')} from {r.get('signal_time')}")
    return changed


def _load() -> List[dict]:
    if not STATE_FILE.exists():
        return []
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []


def _save(rows: List[dict]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
    except Exception as e:
        log.warning(f"Could not save trigger watch state: {e}")


def _parse_volume_multiple(entry_condition: str) -> Optional[float]:
    """
    Pull the required MINIMUM volume multiple out of the entry condition,
    e.g. "...with volume at or above 0.8x the 15m average" -> 0.8.

    Fixed 2026-08-30. The old version grabbed the first "<number>x" in the
    string regardless of meaning, so a condition phrased as a CEILING —
    "no volume expansion above 1.5x avg", "no volume reversal above 0.5x"
    — was stored as a FLOOR. The watcher then told members "volume short,
    entry not met" when the card actually wanted volume to stay low, and
    vice versa. Roughly a third of the Aug 24-28 week's parsed multiples
    had inverted meaning.

    Now: the match must be volume-related, and any negated phrasing
    ("no ... above Nx", "without ... above Nx", "unless") returns None so
    the alert reports the observed ratio as information instead of
    rendering a verdict it cannot justify. None is the honest answer for
    a condition this parser does not model.
    """
    if not entry_condition:
        return None

    text = entry_condition.lower()
    for m in re.finditer(r"([\d.]+)\s*x\b", text):
        # Window before the number decides whether it is a floor at all.
        start = max(0, m.start() - 90)
        before = text[start:m.start()]
        after = text[m.end():m.end() + 40]
        context = before + " " + after

        if "volume" not in context and "vol " not in context:
            continue  # "1.5x ATR", "2x the range" — not a volume gate
        if re.search(r"\b(no|not|without|unless|avoid|fails? to|"
                     r"does ?n[o']t)\b", before):
            return None  # ceiling or negated condition — not a minimum
        if not re.search(r"\b(above|over|at least|minimum|exceed|"
                         r"greater|>=?|at or above)\b", before):
            return None  # cannot confirm it is a floor; do not guess

        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def register(ticker: str, ai: Dict[str, Any], message_id: str = "") -> None:
    """
    Called ONLY after a signal successfully posts to Discord. Registers it
    to be watched for its entry trigger.
    """
    ts = (ai.get("trade_setup") or {})
    direction = ts.get("contract_type", "NONE")
    trigger = ts.get("entry_trigger")
    if direction not in ("CALL", "PUT") or trigger in (None, ""):
        return
    try:
        trigger = float(trigger)
    except (ValueError, TypeError):
        return

    rows = _load()
    # Sweep stale watches BEFORE the pending check, or yesterday's row
    # blocks today's signal from ever being registered.
    if _expire_stale(rows):
        _save(rows)
    # Don't double-register the same ticker+direction while one is pending
    for r in rows:
        if (r.get("ticker") == ticker and r.get("direction") == direction
                and r.get("status") == "PENDING"):
            return

    rows.append({
        "ticker":          ticker,
        "direction":       direction,
        "trigger":         trigger,
        "strike":          ts.get("strike"),
        "expiry_date":     ts.get("expiry_date") or "",
        "vol_multiple":    _parse_volume_multiple(ts.get("entry_condition", "")),
        "entry_condition": (ts.get("entry_condition") or "")[:300],
        "score":           ai.get("confluence_score"),
        "verdict":         ts.get("verdict", ""),
        "message_id":      message_id or "",
        "signal_time":     _now_et().isoformat(timespec="seconds"),
        "status":          "PENDING",
    })
    _save(rows)
    log.info(f"👁 Watching {ticker} {direction} for 15m close through ${trigger}")


def _get_15m_bars(ticker: str, limit: int = 24) -> Optional[List[dict]]:
    """Completed 15m bars, oldest→newest. Alpaca primary, yfinance fallback."""
    try:
        from core import alpaca_data
        if alpaca_data.is_enabled():
            bars = alpaca_data.get_bars(ticker, "15m", limit=limit)
            if bars:
                return bars
    except Exception as e:
        log.debug(f"{ticker}: Alpaca 15m bars failed — {e}")
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period="2d", interval="15m")
        if df is None or df.empty:
            return None
        out = []
        for idx, row in df.tail(limit).iterrows():
            out.append({
                "t": idx.isoformat(),
                "o": float(row["Open"]), "h": float(row["High"]),
                "l": float(row["Low"]),  "c": float(row["Close"]),
                "v": float(row["Volume"]),
            })
        return out
    except Exception as e:
        log.debug(f"{ticker}: yfinance 15m bars failed — {e}")
    return None


def _bar_time(bar: dict) -> Optional[datetime]:
    t = bar.get("t")
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET)
    except Exception:
        return None


def check_and_alert(webhook_url: str = "") -> None:
    """
    Called each scan. For every PENDING signal, look for a COMPLETED 15m
    bar (after the signal fired) that closed through the trigger level.
    On the first such bar, post a confirmation alert and mark it done.
    """
    rows = _load()
    if not rows:
        return

    now = _now_et()
    changed = False

    if _expire_stale(rows, now):
        changed = True

    for r in rows:
        if r.get("status") != "PENDING":
            continue


        ticker    = r["ticker"]
        direction = r["direction"]
        trigger   = float(r["trigger"])

        bars = _get_15m_bars(ticker)
        if not bars:
            continue

        # Average volume across the window, for the volume-gate comparison
        vols = [float(b.get("v", 0) or 0) for b in bars if b.get("v")]
        avg_vol = (sum(vols) / len(vols)) if vols else 0

        for b in bars:
            bt = _bar_time(b)
            if bt is None or bt < sig_t:
                continue                      # bar predates the signal
            if bt + timedelta(minutes=15) > now:
                continue                      # bar still in progress

            close = float(b.get("c", 0) or 0)
            if close <= 0:
                continue

            crossed = (close > trigger) if direction == "CALL" else (close < trigger)
            if not crossed:
                continue

            # Volume gate on THAT candle
            bar_vol = float(b.get("v", 0) or 0)
            vol_ratio = (bar_vol / avg_vol) if avg_vol else None
            required = r.get("vol_multiple")
            vol_ok = None
            if required is not None and vol_ratio is not None:
                vol_ok = vol_ratio >= required

            _post_trigger_alert(webhook_url, r, close, bt, vol_ratio, required, vol_ok)
            r["status"]       = "TRIGGERED"
            r["trigger_time"] = bt.isoformat(timespec="seconds")
            r["trigger_close"] = round(close, 2)
            changed = True
            break

    if changed:
        _save(rows)


def _post_trigger_alert(webhook_url: str, r: dict, close: float,
                        bar_time: datetime, vol_ratio: Optional[float],
                        required: Optional[float], vol_ok: Optional[bool]) -> None:
    """
    Post the confirmation: a channel message (webhook) and, when the bot
    token is configured, a reply inside the signal's thread.
    Wrapped so a failure here can never affect scanning.
    """
    import os
    import requests

    ticker    = r["ticker"]
    direction = r["direction"]
    trigger   = r["trigger"]
    strike    = r.get("strike")

    # Live premium so members see what it costs NOW, not at signal time
    premium = None
    try:
        from core import alpaca_data
        if alpaca_data.is_enabled() and strike and r.get("expiry_date"):
            premium = alpaca_data.get_option_mid(
                ticker, direction, float(strike), r["expiry_date"])
    except Exception:
        pass

    hhmm = bar_time.strftime("%-I:%M %p") if hasattr(bar_time, "strftime") else ""
    arrow = "above" if direction == "CALL" else "below"

    if vol_ok is True:
        vol_line = (f"✅ **Volume confirmed** — {vol_ratio:.2f}x vs "
                    f"{required:.2f}x required")
        headline = "🎯 **ENTRY TRIGGER HIT**"
        colour   = 0x3FB950
    elif vol_ok is False:
        vol_line = (f"⚠️ **Volume short** — {vol_ratio:.2f}x vs "
                    f"{required:.2f}x required. The card's entry condition "
                    f"is NOT fully met.")
        headline = "⚠️ **PRICE TRIGGER HIT — VOLUME FAILED**"
        colour   = 0xE0B53C
    else:
        # No volume floor could be read from the card, so nothing was
        # verified. This used to render with the same green headline as a
        # confirmed entry, which made "checked and passed" and "never
        # checked" indistinguishable to a member skimming the channel.
        vr = f"{vol_ratio:.2f}x" if vol_ratio is not None else "n/a"
        vol_line = (f"ℹ️ Volume on that candle: {vr} — the card states no "
                    f"volume floor, so nothing was verified here. Check the "
                    f"card's avoid-if conditions yourself.")
        headline = "🔎 **PRICE TRIGGER HIT — VOLUME NOT CHECKED**"
        colour   = 0x4A90E2

    prem_line = f"\n💵 Premium now: **${premium}**" if premium is not None else ""
    strike_line = f" ${strike}" if strike else ""

    body = (
        f"{headline}\n"
        f"**{ticker} {direction}{strike_line}** — 15m candle closed {arrow} "
        f"${trigger} at **${close:.2f}** ({hhmm} ET)\n"
        f"{vol_line}{prem_line}\n"
        f"*Scans run every ~10 min, so this confirmation may lag the close. "
        f"Re-check the avoid-if conditions before entering.*"
    )

    # 1. Channel ping via webhook
    if webhook_url:
        try:
            requests.post(webhook_url, json={"embeds": [{
                "description": body, "color": colour,
            }]}, timeout=12)
            log.info(f"🎯 {ticker} {direction}: trigger alert posted")
        except Exception as e:
            log.warning(f"Trigger alert webhook failed for {ticker}: {e}")

    # 2. Thread reply (only if bot token + the original message id exist)
    bot_token  = os.environ.get("DISCORD_BOT_TOKEN", "")
    message_id = r.get("message_id", "")
    channel_id = os.environ.get("DISCORD_SIGNAL_CHANNEL_ID", "")
    if bot_token and message_id and channel_id:
        try:
            requests.post(
                f"https://discord.com/api/v10/channels/{message_id}/messages",
                headers={"Authorization": f"Bot {bot_token}",
                         "Content-Type": "application/json"},
                json={"content": body}, timeout=12)
        except Exception as e:
            log.debug(f"Thread reply failed for {ticker}: {e}")
