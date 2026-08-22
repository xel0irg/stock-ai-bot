"""
core/alert_cooldown.py — Per-ticker alert de-duplication

Problem this solves (Jul 20):
    TSLA fired 7 near-identical PUT alerts between 10:37 and 12:51 —
    the same setup re-reported every ~10 minutes as the AI cache
    expired. That is one opportunity, not seven. It floods Discord,
    inflates the backtest log, and would be unusable for a subscriber.

Rule (since 2026-08-22):
    ONE alert per ticker per direction per ET trading day. Same-day
    repeats are suppressed no matter how much the score moves.

    Why the change: measured on 951 logged signals, afternoon re-alerts
    of an already-posted setup ran ~6 pts worse directionally than the
    day's original alert (~45% vs ~52%) — they chase moves that are
    already extended. Suppressing them also makes the Discord feed
    match the paper tracker one-to-one (the tracker always held one
    position per ticker+direction per day), which ends the
    posted-but-never-measured gap in weekly recaps.

    A direction FLIP (PUT -> CALL) still always alerts: that is new
    information, not a repeat.

State is persisted alongside the prescreener state so it survives
GitHub Actions ephemeral runners.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from core.logger import get_logger

log = get_logger("AlertCooldown")

ET = ZoneInfo("America/New_York")

# (COOLDOWN_MINUTES / SCORE_ESCALATION removed 2026-08-22 — the window is
# now the ET calendar day and there is no score-escalation re-alert.)

STATE_FILE = Path("backtest/alert_cooldown.json")

_STATE: Dict[str, Dict[str, Any]] = {}
_LOADED = False


def _load() -> None:
    global _LOADED, _STATE
    if _LOADED:
        return
    _LOADED = True
    if not STATE_FILE.exists():
        return
    try:
        _STATE = json.loads(STATE_FILE.read_text())
        log.info(f"Loaded alert cooldown state ({len(_STATE)} entries)")
    except Exception as e:
        log.warning(f"Could not load alert cooldown state: {e}")
        _STATE = {}


def save_state() -> None:
    """Persist cooldown state. Called after each scan."""
    _load()
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(_STATE, indent=2))
    except Exception as e:
        log.warning(f"Could not save alert cooldown state: {e}")


def should_alert(ticker: str, direction: str, score: int) -> Tuple[bool, str]:
    """
    Decide whether this alert is a genuine new signal or a repeat.

    Returns (allowed, reason).
    """
    _load()
    if direction not in ("CALL", "PUT"):
        return True, ""

    key  = ticker.upper()
    prev = _STATE.get(key)
    if not prev:
        return True, ""

    # Direction flip is always new information
    if prev.get("direction") != direction:
        return True, f"direction flipped {prev.get('direction')} -> {direction}"

    # Same ticker + direction: suppress for the rest of the ET day.
    try:
        prev_day = datetime.fromisoformat(prev.get("at", "")).date()
    except (ValueError, TypeError):
        prev_day = datetime.fromtimestamp(prev.get("ts", 0), ET).date()
    today = datetime.now(ET).date()
    if prev_day != today:
        return True, f"new trading day (last {direction} alert {prev_day})"

    return False, (f"duplicate {direction} — already alerted today at "
                   f"{prev.get('at', '?')} (score {prev.get('score', '?')}, "
                   f"now {score}); one alert per setup per day")


def record_alert(ticker: str, direction: str, score: int) -> None:
    """Record that an alert was actually sent."""
    _load()
    if direction not in ("CALL", "PUT"):
        return
    _STATE[ticker.upper()] = {
        "direction": direction,
        "score":     score,
        "ts":        time.time(),
        "at":        datetime.now(ET).isoformat(timespec="seconds"),
    }
