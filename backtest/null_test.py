"""
backtest/null_test.py — Does the bot have any directional skill?

The question this answers, before any further optimization:
does the bot's CALL/PUT choice beat (a) a coin flip and (b) the
naive momentum rule "buy the direction the ticker has already
moved today"? If it beats neither, no scoring, flow-data, or
exit tuning can save the current strategy — the directional
engine itself has no edge to amplify.

Uses only data already in signals_log.csv:
  price_at_scan  — stock at signal time
  outcome_price  — stock close at end of the signal's DTE window
                   (populated by the daily outcome checker)
  The momentum null anchors on the FIRST logged scan price of
  that ticker that day (any row, actionable or not).

Run:  python -m backtest.null_test
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

LOG = Path(__file__).parent / "signals_log.csv"


def _fl(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pval_vs_coinflip(k: int, n: int) -> float:
    """Two-sided p-value vs 50%, normal approximation."""
    if n == 0:
        return float("nan")
    z = (k - n / 2) / math.sqrt(n / 4)
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def _acc(xs) -> float:
    xs = list(xs)
    return 100 * sum(xs) / len(xs) if xs else float("nan")


def run() -> None:
    rows_raw = sorted(csv.DictReader(open(LOG)), key=lambda r: r["timestamp"])

    # First logged price per (date, ticker) — the intraday momentum anchor
    anchor: dict = {}
    for r in rows_raw:
        p = _fl(r.get("price_at_scan"))
        if p is not None:
            anchor.setdefault((r["timestamp"][:10], r["ticker"]), p)

    rows = []
    for r in rows_raw:
        if r.get("contract_type") not in ("CALL", "PUT"):
            continue
        p0, p1 = _fl(r.get("price_at_scan")), _fl(r.get("outcome_price"))
        if p0 is None or p1 is None or p1 == p0:
            continue
        a = anchor.get((r["timestamp"][:10], r["ticker"]))
        mom = None if (a is None or p0 == a) else ("CALL" if p0 > a else "PUT")
        up = p1 > p0
        rows.append({
            "sc":     _fl(r.get("confluence_score")),
            "bot":    r["contract_type"],
            "ok":     up == (r["contract_type"] == "CALL"),
            "mom":    mom,
            "mom_ok": None if mom is None else up == (mom == "CALL"),
            "trig":   r.get("entry_triggered") == "yes",
        })

    n = len(rows)
    k = sum(r["ok"] for r in rows)
    print(f"Actionable signals with a recorded outcome: n={n}")
    print(f"  BOT directional accuracy : {_acc(r['ok'] for r in rows):5.1f}%"
          f"   (p vs coin flip = {_pval_vs_coinflip(k, n):.3f})")

    m = [r for r in rows if r["mom"] is not None]
    print(f"  MOMENTUM-NULL accuracy   : {_acc(r['mom_ok'] for r in m):5.1f}%"
          f"   (n={len(m)}, 'buy the direction already moved today')")

    agree = [r for r in m if r["bot"] == r["mom"]]
    diff  = [r for r in m if r["bot"] != r["mom"]]
    print(f"  bot agrees with momentum : {100 * len(agree) / len(m):.0f}% of signals")
    print(f"    when agreeing    : {_acc(r['ok'] for r in agree):5.1f}%  (n={len(agree)})")
    kd = sum(r["ok"] for r in diff)
    print(f"    when disagreeing : {_acc(r['ok'] for r in diff):5.1f}%  (n={len(diff)},"
          f" p={_pval_vs_coinflip(kd, len(diff)):.3f})  <- the value-add test")

    print()
    for lo, hi, lab in [(0, 65, "sub-65"), (65, 101, "65+   ")]:
        b = [r for r in rows if r["sc"] is not None and lo <= r["sc"] < hi]
        print(f"  {lab} n={len(b):4}   bot accuracy {_acc(r['ok'] for r in b):5.1f}%")
    t = [r for r in rows if r["trig"]]
    print(f"  triggered-only n={len(t)}   bot accuracy {_acc(r['ok'] for r in t):5.1f}%"
          f"   (selection-biased upward: 'triggered' already means the"
          f" price moved the signal's way first)")

    print()
    print("Read: if 'when disagreeing' is ~50% with a large p-value, the bot has")
    print("shown no directional information beyond intraday momentum. Re-run as")
    print("data accumulates; regime matters and this is one sample period.")


if __name__ == "__main__":
    run()
