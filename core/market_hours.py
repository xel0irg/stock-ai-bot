"""
core/market_hours.py — NYSE trading calendar & session check

Zero external dependencies (stdlib only, uses zoneinfo — Python 3.9+).
Computes NYSE holidays algorithmically for any year, so there is no
hardcoded list to keep updated.

Rules implemented:
  Holidays (full-day closures):
    - New Year's Day        (Jan 1)
    - Martin Luther King Jr (3rd Monday of January)
    - Washington's Birthday (3rd Monday of February)
    - Good Friday           (2 days before Easter Sunday)
    - Memorial Day          (last Monday of May)
    - Juneteenth            (June 19)
    - Independence Day      (July 4)
    - Labor Day             (1st Monday of September)
    - Thanksgiving          (4th Thursday of November)
    - Christmas             (Dec 25)

  Observed-date rules:
    - Holiday on Saturday → observed the Friday before
      (EXCEPT New Year's: if Jan 1 falls on Saturday, NYSE does NOT
       close on Dec 31 — per NYSE Rule 7.2)
    - Holiday on Sunday   → observed the Monday after

  Early closes (1:00 PM ET):
    - July 3 (when it's a weekday and July 4 is not a Saturday-observed Friday)
    - Day after Thanksgiving
    - Christmas Eve (when Dec 24 is a weekday and not itself an observed holiday)

Session: 9:30 AM – 4:00 PM ET (1:00 PM on early-close days).

Usage:
    from core.market_hours import market_status, is_market_open

    status = market_status()
    if not status["is_open"]:
        print(f"Market closed: {status['reason']}")
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

MARKET_OPEN        = time(9, 30)
MARKET_CLOSE       = time(16, 0)
EARLY_MARKET_CLOSE = time(13, 0)

# Scans are blocked until this time ET — volume normalizes around 10:30 AM.
# The first hour is high-noise/low-volume; setups generated before 10:30
# consistently score 8-12 points lower due to volume penalty and produce
# false signals that reverse once real participation arrives.
SCAN_START_TIME    = time(10, 30)

# Scans stop at this time ET — 0DTE theta too destructive after 2 PM,
# and 1DTE signals this late in session have no time to set up properly.
SCAN_END_TIME      = time(14, 0)


# ── Date helpers ──────────────────────────────────────────────────────

def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th occurrence of `weekday` (Mon=0..Sun=6) in a month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` in a month."""
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - timedelta(days=offset)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous/Meeus algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(holiday: date, *, is_new_years: bool = False) -> date | None:
    """
    Apply NYSE observed-date rules.
    Returns None if the holiday is not observed at all
    (New Year's falling on a Saturday).
    """
    if holiday.weekday() == 5:                     # Saturday
        if is_new_years:
            return None                            # NYSE Rule 7.2 exception
        return holiday - timedelta(days=1)         # observed Friday
    if holiday.weekday() == 6:                     # Sunday
        return holiday + timedelta(days=1)         # observed Monday
    return holiday


# ── Holiday calendar ──────────────────────────────────────────────────

def nyse_holidays(year: int) -> set[date]:
    """All full-day NYSE closures for a given year (observed dates)."""
    raw: list[tuple[date, bool]] = [
        (date(year, 1, 1),   True),   # New Year's Day (special Sat rule)
        (date(year, 6, 19),  False),  # Juneteenth
        (date(year, 7, 4),   False),  # Independence Day
        (date(year, 12, 25), False),  # Christmas
    ]
    holidays: set[date] = set()
    for d, is_ny in raw:
        obs = _observed(d, is_new_years=is_ny)
        if obs is not None:
            holidays.add(obs)

    holidays.add(_nth_weekday(year, 1, 0, 3))        # MLK Day
    holidays.add(_nth_weekday(year, 2, 0, 3))        # Washington's Birthday
    holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))          # Memorial Day
    holidays.add(_nth_weekday(year, 9, 0, 1))        # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))       # Thanksgiving

    # Next year's New Year's Day can be observed in *this* year
    # (Jan 1 on Sunday → observed Mon Jan 2 next year; that stays in
    # next year. But Jan 1 on Saturday is NOT observed — nothing to add.)
    return holidays


def early_close_days(year: int) -> set[date]:
    """Days the NYSE closes at 1:00 PM ET."""
    days: set[date] = set()
    holidays = nyse_holidays(year)

    # Day before Independence Day (July 3), if it's a weekday and not
    # itself a full holiday (i.e. not the observed Friday for a Sat July 4)
    jul3 = date(year, 7, 3)
    if jul3.weekday() < 5 and jul3 not in holidays:
        days.add(jul3)

    # Day after Thanksgiving (always a Friday)
    days.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))

    # Christmas Eve, if it's a weekday and not itself a full holiday
    # (i.e. not the observed Friday for a Sat Dec 25)
    dec24 = date(year, 12, 24)
    if dec24.weekday() < 5 and dec24 not in holidays:
        days.add(dec24)

    return days


# ── Public API ────────────────────────────────────────────────────────

def is_trading_day(d: date | None = None) -> bool:
    """True if the NYSE is open at all on this date."""
    d = d or datetime.now(ET).date()
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def market_status(now: datetime | None = None) -> dict:
    """
    Full market status for a given moment (defaults to now, ET).

    Returns:
        {
            "is_open":      bool,
            "reason":       str,   # human-readable explanation
            "session_open": "09:30" | None,
            "session_close":"16:00" | "13:00" | None,
            "is_early_close": bool,
        }
    """
    now = now.astimezone(ET) if now else datetime.now(ET)
    today = now.date()

    if today.weekday() >= 5:
        return {
            "is_open": False, "scan_open": False,
            "reason": f"Weekend ({today.strftime('%A')})",
            "scan_reason": f"Weekend ({today.strftime('%A')})",
            "session_open": None, "session_close": None,
            "scan_start": None, "scan_end": None,
            "is_early_close": False,
        }

    if today in nyse_holidays(today.year):
        return {
            "is_open": False, "scan_open": False,
            "reason": f"NYSE holiday ({today.isoformat()})",
            "scan_reason": f"NYSE holiday ({today.isoformat()})",
            "session_open": None, "session_close": None,
            "scan_start": None, "scan_end": None,
            "is_early_close": False,
        }

    early = today in early_close_days(today.year)
    close = EARLY_MARKET_CLOSE if early else MARKET_CLOSE
    scan_close = min(SCAN_END_TIME, close)
    t = now.time()

    if t < MARKET_OPEN:
        reason = f"Pre-market — opens 9:30 AM ET (now {now.strftime('%I:%M %p')} ET)"
        open_now = False
    elif t >= close:
        label = "1:00 PM (early close)" if early else "4:00 PM"
        reason = f"After hours — closed at {label} ET"
        open_now = False
    else:
        reason = "Market open" + (" (early close today at 1:00 PM ET)" if early else "")
        open_now = True

    # Scan window: 10:30 AM – 2:00 PM ET
    if open_now and t < SCAN_START_TIME:
        scan_open = False
        scan_reason = (f"⏳ Opening hour — scan window opens at "
                       f"{SCAN_START_TIME.strftime('%I:%M %p')} ET "
                       f"(now {now.strftime('%I:%M %p')} ET). "
                       f"Volume too low before 10:30 for reliable signals.")
    elif open_now and t >= scan_close:
        scan_open = False
        scan_reason = (f"⏸ Scan window closed at "
                       f"{scan_close.strftime('%I:%M %p')} ET — "
                       f"theta too destructive for new 0-2 DTE entries.")
    else:
        scan_open = open_now
        scan_reason = reason

    return {
        "is_open":      open_now,
        "scan_open":    scan_open,
        "reason":       reason,
        "scan_reason":  scan_reason,
        "session_open": "09:30",
        "session_close": close.strftime("%H:%M"),
        "scan_start":   SCAN_START_TIME.strftime("%H:%M"),
        "scan_end":     scan_close.strftime("%H:%M"),
        "is_early_close": early,
    }


def is_market_open(now: datetime | None = None) -> bool:
    """Convenience wrapper — True if the NYSE session is live right now."""
    return market_status(now)["is_open"]


def scan_window_open(now: datetime | None = None) -> bool:
    """True if within the high-quality scan window (10:30 AM – 2:00 PM ET)."""
    return market_status(now)["scan_open"]


if __name__ == "__main__":
    # Quick self-check
    s = market_status()
    print(f"Now (ET): {datetime.now(ET):%Y-%m-%d %I:%M %p}")
    print(f"Open: {s['is_open']} — {s['reason']}")
    y = datetime.now(ET).year
    print(f"\n{y} NYSE holidays:")
    for h in sorted(nyse_holidays(y)):
        print(f"  {h} ({h.strftime('%A')})")
    print(f"\n{y} early closes (1 PM ET):")
    for h in sorted(early_close_days(y)):
        print(f"  {h} ({h.strftime('%A')})")
