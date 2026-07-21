"""
core/alert_cooldown.py — Per-ticker alert de-duplication

Problem this solves (Jul 20):
    TSLA fired 7 near-identical PUT alerts between 10:37 and 12:51 —
    the same setup re-reported every ~10 minutes as the AI cache
    expired. That is one opportunity, not seven. It floods Discord,
    inflates the backtest log, and would be unusable for a subscriber.

Rule:
    Suppress an alert if the SAME ticker in the SAME direction already
    alerted within COOLDOWN_MINUTES — UNLESS the score improved by at
    least SCORE_ESCALATION points, which indicates the setup genuinely
    strengthened and is worth re-reporting.

    A direction FLIP (PUT -> CALL) always alerts: that is new
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

COOLDOWN_MINUTES  = 45   # same ticker + direction suppressed within this window
SCORE_ESCALATION  = 6    # ...unless score improved by at least this much

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

    age_min = (time.time() - prev.get("ts", 0)) / 60
    if age_min >= COOLDOWN_MINUTES:
        return True, f"cooldown expired ({age_min:.0f}m)"

    prev_score = prev.get("score", 0)
    if score >= prev_score + SCORE_ESCALATION:
        return True, (f"score escalated {prev_score} -> {score} "
                      f"(+{score - prev_score})")

    return False, (f"duplicate {direction} within {COOLDOWN_MINUTES}m "
                   f"(last {age_min:.0f}m ago at score {prev_score}, "
                   f"now {score} — needs +{SCORE_ESCALATION} to re-alert)")


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
