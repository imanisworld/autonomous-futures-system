#!/usr/bin/env python3
"""
scripts/corpus_v1_apply_orphan_correction.py

Folds scripts/corpus_v1_orphan_resolution.py's carry-forward resolutions back
into the canonical Corpus v1 evidence and recomputes the closure-record
totals -- the operator's required correction before Corpus v1 can be
re-closed: "the complete Corpus v1 totals must now show 747 attempts and 747
resolved, not the previous 724 resolved."

Inputs (both already-committed evidence, unmodified):
    scripts/corpus_v1_raw_trades.jsonl       747 rows, 23 with result=null
    scripts/corpus_v1_orphan_resolution.json 23 carry-forward-resolved rows

Outputs (new files, originals left untouched -- the original 724-resolved
numbers remain the historical record of what the raw per-day replay run
itself produced; these corrected files are the closure-record amendment):
    scripts/corpus_v1_raw_trades_corrected.jsonl
    scripts/corpus_v1_results_corrected.json
    stdout: BEFORE/AFTER delta for combined + per-instrument full-period stats

Reuses corpus_v1_report.py's _stats/_period_block/_splits/_load_journal_entries
/_load_no_trade directly (no reimplementation, no drift risk) -- this script
only supplies a corrected trade list in place of a fresh _load_trades() call.
Does not touch replay_engine.py, execution code, strategy configuration, or
any gate -- pure post-hoc aggregation over already-generated evidence files.

Fails loud (raises) rather than guessing on any of:
  - a null-result raw_trades row with no matching resolution
  - a resolution that doesn't match any null-result raw_trades row
  - more than one candidate on either side for the same (date, instrument,
    strategy) key
This mirrors the project's existing no-FIFO, no-guessing identity-join
philosophy (#327/#332) applied one layer up, at the correction-merge step.

Usage:
    python3 scripts/corpus_v1_apply_orphan_correction.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_v1_report import (  # noqa: E402
    FULL_START, FULL_END, INSTRUMENTS,
    _load_journal_entries, _load_no_trade, _splits, _stats,
)

RAW_TRADES_PATH = Path("scripts/corpus_v1_raw_trades.jsonl")
ORPHAN_RESOLUTION_PATH = Path("scripts/corpus_v1_orphan_resolution.json")
CORRECTED_RAW_TRADES_PATH = Path("scripts/corpus_v1_raw_trades_corrected.jsonl")
CORRECTED_RESULTS_PATH = Path("scripts/corpus_v1_results_corrected.json")
LOGS_BASE = Path("logs/replay_corpus_v1")


def _load_raw_trades() -> list[dict]:
    return [json.loads(line) for line in RAW_TRADES_PATH.read_text().splitlines() if line.strip()]


def _load_resolutions() -> dict[tuple, dict]:
    resolutions = json.loads(ORPHAN_RESOLUTION_PATH.read_text())
    by_key: dict[tuple, dict] = {}
    for r in resolutions:
        key = (r["date"], r["instrument"], r["strategy"])
        if key in by_key:
            raise AssertionError(f"Ambiguous orphan resolution: duplicate key {key}")
        if r["resolution"] != "RESOLVED":
            raise AssertionError(f"Unresolved orphan present in resolution file: {key} -> {r['resolution']}")
        by_key[key] = r
    return by_key


def apply_correction(raw_trades: list[dict], resolutions: dict[tuple, dict]) -> list[dict]:
    corrected = []
    consumed: set[tuple] = set()
    for t in raw_trades:
        if t["result"] is not None or t["unjoinable_legacy"]:
            corrected.append(t)
            continue
        key = (t["date"], t["instrument"], t["strategy"])
        if key not in resolutions:
            raise AssertionError(f"No orphan resolution found for open trade {key} -- refusing to guess")
        if key in consumed:
            raise AssertionError(f"Multiple open raw_trades rows share key {key} -- resolution join is ambiguous")
        consumed.add(key)
        r = resolutions[key]
        corrected.append({
            **t,
            "result": r["result"],
            "pnl": r["pnl_dollars"],
            "corrected_from_orphan": True,
            "corrected_resolved_on": r["resolved_on"],
        })

    unused = set(resolutions.keys()) - consumed
    if unused:
        raise AssertionError(f"Orphan resolutions present with no matching open raw_trades row: {unused}")

    return corrected


def _recompute_splits(trades: list[dict]) -> dict:
    by_instr: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_instr[t["instrument"]].append(t)

    per_instrument = {}
    all_no_trade: list[dict] = []
    for instr in INSTRUMENTS:
        log_dir = LOGS_BASE / instr
        entries = _load_journal_entries(log_dir) if log_dir.exists() else []
        no_trade_rows = _load_no_trade(entries)
        all_no_trade.extend(no_trade_rows)
        per_instrument[instr] = _splits(by_instr.get(instr, []), no_trade_rows)

    combined = _splits(trades, all_no_trade)
    return {"per_instrument": per_instrument, "combined": combined}


def _fmt(v, money=False, pct=False):
    if v is None:
        return "-"
    if v == float("inf"):
        return "inf"
    if money:
        return f"${v:,.2f}"
    if pct:
        return f"{v * 100:.2f}%"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main() -> int:
    raw_trades = _load_raw_trades()
    resolutions = _load_resolutions()

    original_combined = _stats(raw_trades)
    corrected_trades = apply_correction(raw_trades, resolutions)
    corrected_combined = _stats(corrected_trades)

    if corrected_combined["attempts"] != 747 or corrected_combined["resolved"] != 747:
        raise AssertionError(
            f"Expected 747/747 after correction, got "
            f"{corrected_combined['attempts']}/{corrected_combined['resolved']}"
        )
    if corrected_combined["open_with_identity"] != 0 or corrected_combined["unjoinable_legacy"] != 0:
        raise AssertionError("Expected 0 open and 0 unjoinable after correction")

    print("=== Corpus v1 closure-record correction: full period, combined ===")
    print(f"{'Metric':<14} {'Before':>14} {'After':>14} {'Delta':>14}")
    for label, key, money, pct in [
        ("Attempts", "attempts", False, False),
        ("Resolved", "resolved", False, False),
        ("Wins", "wins", False, False),
        ("Losses", "losses", False, False),
        ("Win rate", "win_rate", False, True),
        ("Gross win", "gross_win", True, False),
        ("Gross loss", "gross_loss", True, False),
        ("Profit factor", "profit_factor", False, False),
        ("Net P&L", "net_pnl", True, False),
        ("Expectancy", "expectancy", True, False),
    ]:
        before = original_combined[key]
        after = corrected_combined[key]
        delta = (after - before) if isinstance(before, (int, float)) and isinstance(after, (int, float)) else "-"
        print(f"{label:<14} {_fmt(before, money, pct):>14} {_fmt(after, money, pct):>14} {_fmt(delta, money, pct):>14}")

    splits = _recompute_splits(corrected_trades)
    results = {
        "meta": {
            "main_sha": "a5434794e471137af83f6e5886b535fb9e3cfcd5",
            "instruments": list(INSTRUMENTS),
            "range": [FULL_START, FULL_END],
            "correction": "2026-07-25: 23 day-boundary orphans (all orb_reclaim, "
                           "22 MES/1 MNQ) carry-forward resolved per operator HOLD "
                           "verdict on the strategy-validation pass -- see "
                           "docs/strategy-validation-pass-2026-07-24.md and "
                           "scripts/corpus_v1_orphan_resolution.py. Pre-correction "
                           "totals (724 resolved) remain in scripts/corpus_v1_results.json "
                           "as the historical record of the raw per-day replay run.",
        },
        "per_instrument": splits["per_instrument"],
        "combined": splits["combined"],
        "raw_trade_count": len(corrected_trades),
    }

    CORRECTED_RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\n[correction] wrote {CORRECTED_RESULTS_PATH}")

    with CORRECTED_RAW_TRADES_PATH.open("w") as f:
        for t in sorted(corrected_trades, key=lambda x: (x["date"], x["instrument"])):
            f.write(json.dumps(t) + "\n")
    print(f"[correction] wrote {CORRECTED_RAW_TRADES_PATH}")

    print("\n=== Per instrument (full period) ===")
    print(f"{'Instr':<6} {'Resolved before':>16} {'Resolved after':>16} {'Net before':>14} {'Net after':>14}")
    for instr in INSTRUMENTS:
        before_trades = [t for t in raw_trades if t["instrument"] == instr]
        after_trades = [t for t in corrected_trades if t["instrument"] == instr]
        b = _stats(before_trades)
        a = _stats(after_trades)
        print(f"{instr:<6} {b['resolved']:>16} {a['resolved']:>16} "
              f"{_fmt(b['net_pnl'], True):>14} {_fmt(a['net_pnl'], True):>14}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
