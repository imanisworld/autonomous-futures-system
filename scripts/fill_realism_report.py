"""Limit-entry fill-realism / miss-rate report from the live journal.

Motivation: the replay/paper_broker ASSUMES every entry fills, but live IOC-limit
entries at a price level MISS when price has already left the level (trend days). This
tool measures the REAL miss rate per setup type straight from the journal — which
records, for every TRADE decision, both the planned ``entry`` and the live ``close``
at signal time — and pairs each decision with its resolving OUTCOME (CANCELLED =
no-fill, WIN/LOSS/BE = filled).

Two outputs:
  1. GROUND TRUTH (trustworthy): actual no-fill rate per setup type, from outcomes.
     Use this to size the momentum-entry opportunity.
  2. NAIVE signal-close fill model (DIAGNOSTIC, do not trust): would the IOC limit be
     marketable to the signal-bar close? In practice this agrees with reality only
     ~half the time — because some limits fill on a brief retest the close can't see.
     Its low agreement is the POINT: it proves a faithful fill model needs next-bar /
     intrabar high-low, not the signal close. Keep this in mind before trusting any
     replay A/B of an entry change.

Read-only. Usage:
    python scripts/fill_realism_report.py [logs/journal_2026-06-*.jsonl ...]
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from ops.fill_realism import is_entry_nofill

TICK = 0.25
# ENTRY_SLIPPAGE_TOLERANCE_TICKS_<root> — the live IOC limit offset, in ticks.
TOL_TICKS = {
    "MES": float(os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "8") or 8),
    "MNQ": float(os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "16") or 16),
}
# Setups whose entry rests AT a level (miss when price leaves it) vs STOP/breakout
# setups whose entry is beyond price (fill on momentum).
LIMIT_SETUPS = {
    "vwap_reclaim", "vwap_hold", "vwap_rejection",
    "pdh_reclaim", "pdl_reclaim", "continuation_pullback",
}


def _root(instr: str) -> str:
    return (instr or "").upper().rstrip("!1234567890HMUZ")


def tol_points(instr: str) -> float:
    return TOL_TICKS.get(_root(instr), 8) * TICK


def naive_would_fill(direction, entry, close, tol):
    """Naive marketable-to-close model. Returns True/False, or None if undeterminable.
    LONG buy-limit fills iff close <= entry+tol; SHORT sell-limit iff close >= entry-tol."""
    if entry is None or close is None:
        return None
    if direction == "LONG":
        return close <= entry + tol
    if direction == "SHORT":
        return close >= entry - tol
    return None


def is_nofill(result: str, reason: str) -> bool:
    return is_entry_nofill(result, reason)


def pair_journal(files):
    """Pair each TRADE decision with its resolving OUTCOME (one position per instrument)."""
    pending: dict[str, dict] = {}
    rows: list[dict] = []
    for path in sorted(files):
        try:
            handle = open(path)
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                instr = rec.get("instrument")
                if rec.get("decision") == "TRADE" and isinstance(rec.get("setup"), dict):
                    setup = rec["setup"]
                    ctx = rec.get("context") or {}
                    pending[instr] = {
                        "strategy": setup.get("strategy"),
                        "direction": setup.get("direction"),
                        "entry": setup.get("entry"),
                        "close": ctx.get("close"),
                        "instrument": instr,
                    }
                elif rec.get("type") == "OUTCOME" and isinstance(rec.get("outcome"), dict):
                    dec = pending.pop(instr, None)
                    if not dec:
                        continue
                    out = rec["outcome"]
                    dec["actual_nofill"] = is_nofill(out.get("result"), out.get("exit_reason"))
                    rows.append(dec)
    return rows


def report(rows) -> None:
    by_strat = defaultdict(lambda: {"n": 0, "actual_nf": 0, "model_nf": 0, "agree": 0, "limit": False})
    lim = {"n": 0, "nf": 0}
    stop = {"n": 0, "nf": 0}
    misses = []
    for r in rows:
        wf = naive_would_fill(r["direction"], r["entry"], r["close"], tol_points(r["instrument"]))
        if wf is None:
            continue
        strat = r["strategy"] or "?"
        is_limit = strat in LIMIT_SETUPS
        model_nf, actual_nf = (wf is False), bool(r["actual_nofill"])
        b = by_strat[strat]
        b["limit"] = is_limit
        b["n"] += 1
        b["actual_nf"] += actual_nf
        b["model_nf"] += model_nf
        b["agree"] += (model_nf == actual_nf)
        bucket = lim if is_limit else stop
        bucket["n"] += 1
        bucket["nf"] += actual_nf
        if is_limit and actual_nf:
            misses.append(r)

    print(f"\n{'strategy':<22} {'type':<6} {'n':>4} {'no-fill':>8} {'miss%':>6} {'naive-model%':>13}")
    print("-" * 64)
    for strat in sorted(by_strat, key=lambda s: -by_strat[s]["n"]):
        b = by_strat[strat]
        miss = 100 * b["actual_nf"] / b["n"] if b["n"] else 0
        agree = 100 * b["agree"] / b["n"] if b["n"] else 0
        print(f"{strat:<22} {('LIMIT' if b['limit'] else 'stop'):<6} {b['n']:>4} "
              f"{b['actual_nf']:>8} {miss:>5.0f}% {agree:>12.0f}%")
    print("-" * 64)
    lim_miss = 100 * lim["nf"] / lim["n"] if lim["n"] else 0
    stop_miss = 100 * stop["nf"] / stop["n"] if stop["n"] else 0
    print(f"GROUND TRUTH: LIMIT setups {lim['nf']}/{lim['n']} miss ({lim_miss:.0f}%)  |  "
          f"STOP setups {stop['nf']}/{stop['n']} miss ({stop_miss:.0f}%)")
    print("(naive-model% = agreement of the signal-close fill model with reality — "
          "low on purpose; proves a faithful model needs next-bar high/low.)")
    if misses:
        print("\nTrend-day misses a momentum re-anchor would CATCH (enter at close):")
        for r in misses[:15]:
            gap = (r["close"] - r["entry"]) if r["entry"] and r["close"] else 0
            print(f"  {r['instrument']:<5} {r['strategy']:<16} {r['direction']:<5} "
                  f"entry={r['entry']} close={r['close']} (gap {gap:+.2f})")


def main(argv) -> int:
    files = argv or glob.glob("logs/journal_*.jsonl")
    if not files:
        print("no journal files found", file=sys.stderr)
        return 1
    report(pair_journal(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
