"""
backtest/report.py — Aggregate backtest results into a win-rate summary

Run after outcome_checker.py has evaluated some signals to see how the
bot's directional calls have actually performed.

Usage:
    python -m backtest.report
"""
from __future__ import annotations
import csv
from pathlib import Path
from collections import defaultdict

from core.logger import get_logger
from backtest.signal_logger import LOG_FILE

log = get_logger("BacktestReport")


def _load_completed_rows() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # A trade is "resolved" if it actually triggered and produced an
    # outcome: WIN, LOSS, or FLAT (triggered but expired without reaching
    # profit — a real theta loss on an option). NOT_TRIGGERED means price
    # never reached the entry, so no trade would have been taken; those
    # are excluded from win rate but reported separately.
    return [r for r in rows
            if r.get("checked") == "yes"
            and r.get("result") in ("WIN", "LOSS", "FLAT")]


def _score_bucket(score: str) -> str:
    try:
        s = int(score)
    except (ValueError, TypeError):
        return "unknown"
    if s >= 70:
        return "70-100 (HIGH)"
    elif s >= 55:
        return "55-69 (MODERATE)"
    else:
        return "<55 (LOW)"


def _win_rate(rows: list[dict]) -> tuple[int, int, float]:
    # FLAT counts as a non-win (option expired without reaching profit).
    wins  = sum(1 for r in rows if r["result"] == "WIN")
    total = len(rows)   # WIN + LOSS + FLAT
    rate  = round((wins / total) * 100, 1) if total else 0.0
    return wins, total, rate


def generate_report() -> str:
    rows = _load_completed_rows()

    if not rows:
        return (
            "📊 BACKTEST REPORT\n"
            "No completed signals yet. Run outcome_checker.py after signals "
            "have had time to play out (signals need 20+ hours of age before "
            "they're checked)."
        )

    lines = ["📊 BACKTEST REPORT", "═" * 50, ""]

    # Overall
    wins, total, rate = _win_rate(rows)
    lines.append(f"OVERALL: {wins}/{total} wins ({rate}%)")
    lines.append("")

    # By score bucket
    lines.append("BY CONFLUENCE SCORE:")
    by_bucket = defaultdict(list)
    for r in rows:
        by_bucket[_score_bucket(r.get("confluence_score"))].append(r)
    for bucket in ["70-100 (HIGH)", "55-69 (MODERATE)", "<55 (LOW)", "unknown"]:
        if bucket in by_bucket:
            w, t, rt = _win_rate(by_bucket[bucket])
            lines.append(f"  {bucket}: {w}/{t} ({rt}%)")
    lines.append("")

    # By direction
    lines.append("BY DIRECTION:")
    by_direction = defaultdict(list)
    for r in rows:
        by_direction[r.get("contract_type", "UNKNOWN")].append(r)
    for direction in ["CALL", "PUT"]:
        if direction in by_direction:
            w, t, rt = _win_rate(by_direction[direction])
            lines.append(f"  {direction}: {w}/{t} ({rt}%)")
    lines.append("")

    # By ticker
    lines.append("BY TICKER:")
    by_ticker = defaultdict(list)
    for r in rows:
        by_ticker[r.get("ticker", "UNKNOWN")].append(r)
    for ticker in sorted(by_ticker.keys()):
        w, t, rt = _win_rate(by_ticker[ticker])
        lines.append(f"  {ticker}: {w}/{t} ({rt}%)")
    lines.append("")

    # By setup quality
    lines.append("BY SETUP QUALITY:")
    by_quality = defaultdict(list)
    for r in rows:
        by_quality[r.get("setup_quality", "UNKNOWN")].append(r)
    for quality in ["HIGH CONVICTION", "MODERATE", "LOW CONVICTION"]:
        if quality in by_quality:
            w, t, rt = _win_rate(by_quality[quality])
            lines.append(f"  {quality}: {w}/{t} ({rt}%)")
    lines.append("")

    lines.append("═" * 50)
    lines.append(f"Resolved trades: {total} (WIN/LOSS/FLAT; NOT_TRIGGERED excluded as no entry occurred)")

    report = "\n".join(lines)
    return report


if __name__ == "__main__":
    print(generate_report())
