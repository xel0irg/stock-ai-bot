"""
backtest/clean_data.py — load signals with known-corrupt data excluded.

Some columns are unusable for stretches of history because of bugs that
have since been fixed. Filtering them out by hand every time invites
mistakes, so the exclusions live here, in one place, with the reason
attached to each.

Usage:
    from backtest.clean_data import load_signals, usable

    rows = load_signals()                       # everything, annotated
    rows = load_signals(require="stop_level")   # only rows whose stop is real

Run directly for a coverage report:
    python -m backtest.clean_data
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = Path(__file__).parent / "signals_log.csv"

# Column -> (first version whose values are trustworthy, why)
#
# strategy_version is blank for everything logged before 2026-08-22, so a
# blank stamp means "pre-versioning" and cannot be trusted for any of these.
CORRUPT_BEFORE: Dict[str, tuple] = {
    "stop_level": (
        "2026.08.30",
        "_extract_stop_level took the first number of any kind from the "
        "AI's free-text stop rule, so 'exit if premium drops 15%' logged a "
        "stop of 15.0. Roughly 1,000 rows carry that value. Because "
        "paper_tracker compared the stock price against it, hit_stop could "
        "never fire — so hit_stop is equally unusable over the same span.",
    ),
    "hit_stop": (
        "2026.08.30",
        "Downstream of the stop_level corruption: 'no' on every row ever "
        "recorded, which is an artifact rather than a result.",
    ),
    "volume_ratio_daily": (
        "2026.08.31",
        "Compared a partial day's volume against full-day averages, and "
        "the 15m variants included the still-forming bar, so every reading "
        "was biased low by however much of the period had not elapsed.",
    ),
    "volume_ratio_15m": ("2026.08.31", "Included the still-forming bar."),
    "rvol_tod":         ("2026.08.31", "Included the still-forming bar."),
}


def _version_ok(row: Dict[str, Any], min_version: str) -> bool:
    v = (row.get("strategy_version") or "").strip()
    if not v:
        return False          # pre-versioning era — never trustworthy
    return v >= min_version   # date-prefixed stamps sort correctly


def usable(row: Dict[str, Any], column: str) -> bool:
    """Is this row's value for `column` trustworthy?"""
    rule = CORRUPT_BEFORE.get(column)
    if rule is None:
        return True
    return _version_ok(row, rule[0])


def load_signals(path: Optional[Path] = None,
                 require: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load signals_log.csv. With `require`, keep only rows whose value for
    that column is trustworthy AND non-empty.
    """
    rows = list(csv.DictReader(open(path or LOG)))
    if require:
        rows = [r for r in rows
                if usable(r, require) and (r.get(require) or "").strip()]
    return rows


def report() -> None:
    rows = load_signals()
    print(f"signals_log.csv: {len(rows)} rows\n")
    print(f"{'column':22} {'usable':>8} {'unusable':>9}   trustworthy from")
    for col, (minv, _why) in CORRUPT_BEFORE.items():
        present = [r for r in rows if (r.get(col) or "").strip()]
        ok = [r for r in present if usable(r, col)]
        print(f"{col:22} {len(ok):>8} {len(present) - len(ok):>9}   {minv}")
    print("\nWhy each column is restricted:")
    for col, (_minv, why) in CORRUPT_BEFORE.items():
        print(f"\n  {col}\n    {why}")


if __name__ == "__main__":
    report()
