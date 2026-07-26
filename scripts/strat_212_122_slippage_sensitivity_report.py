#!/usr/bin/env python3
"""
scripts/strat_212_122_slippage_sensitivity_report.py

Aggregates the 1/2/3-tick adverse-slippage sensitivity sweep produced by
scripts/strat_212_122_slippage_sensitivity_run.py (2/3-tick) plus the
existing PR #337 FINAL baseline journals at
logs/replay_strat212_122_canonical/ (1-tick, config.fill_slippage_ticks
default -- see that script's docstring for why 1-tick is not rerun here).

Reuses the exact same trade-pairing (JournalReader._trades_for_day,
paper_order_id identity join) and _stats()/net-P&L logic as
scripts/strat_212_122_canonical_evidence_report.py -- imported directly,
not reimplemented, so there is exactly one definition of "net P&L" /
"profit factor" across both reports.

Usage:
    python3 scripts/strat_212_122_slippage_sensitivity_report.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.strat_212_122_canonical_evidence_report import (  # noqa: E402
    COMMISSION_RT,
    INSTRUMENTS,
    STRATEGIES,
    _load_trades,
    _stats,
)

SLIPPAGE_LOG_DIRS = {
    1: Path("logs/replay_strat212_122_canonical"),
    2: Path("logs/replay_strat212_122_slippage/slip_2"),
    3: Path("logs/replay_strat212_122_slippage/slip_3"),
}


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:,.2f}"
    if isinstance(v, float):
        return f"{v * 100:.1f}%"
    return str(v)


def main() -> int:
    trades_by_slip: dict[int, dict[str, list[dict]]] = {}
    for slip, log_base in SLIPPAGE_LOG_DIRS.items():
        per_instrument: dict[str, list[dict]] = {}
        for instr in INSTRUMENTS:
            log_dir = log_base / instr
            if not log_dir.exists():
                print(f"[report] MISSING log dir for slippage={slip}: {log_dir}", file=sys.stderr)
                return 1
            per_instrument[instr] = _load_trades(log_dir, instr)
        trades_by_slip[slip] = per_instrument

    results: dict = {
        "meta": {
            "description": "1/2/3-tick adverse-slippage sensitivity for strat_212/strat_122, "
                            "via PaperBroker's own slippage_ticks mechanism (real fill path: "
                            "applies to entry + stop-exit market fills, NOT target/limit exits) "
                            "wired through config.fill_slippage_ticks + ReplayEngine, unmodified. "
                            "1-tick IS the existing PR #337 FINAL baseline (config default); "
                            "2-tick and 3-tick are additional full 313-day/instrument reruns.",
            "commission_round_trip_usd": COMMISSION_RT,
            "slippage_ticks_tested": sorted(SLIPPAGE_LOG_DIRS.keys()),
        },
        "per_instrument_per_strategy": {},
        "per_strategy_combined": {},
    }

    print("\n=== Slippage sensitivity, per cell (instrument x strategy), full period ===")
    print("| Instrument | Strategy | Slippage | N resolved | Net P&L (raw) | Net P&L (comm-adj) | PF (raw) | PF (comm-adj) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for instr in INSTRUMENTS:
        results["per_instrument_per_strategy"].setdefault(instr, {})
        for strat in STRATEGIES:
            results["per_instrument_per_strategy"][instr].setdefault(strat, {})
            for slip in sorted(SLIPPAGE_LOG_DIRS.keys()):
                sub = [t for t in trades_by_slip[slip][instr] if t["strategy"] == strat]
                raw = _stats(sub, 0.0)
                adj = _stats(sub, COMMISSION_RT)
                results["per_instrument_per_strategy"][instr][strat][f"slippage_{slip}_tick"] = {
                    "raw": raw,
                    "commission_adjusted": adj,
                }
                print(f"| {instr} | {strat} | {slip} | {raw['resolved']} "
                      f"| {_fmt(raw['net_pnl'], True)} | {_fmt(adj['net_pnl'], True)} "
                      f"| {_fmt(raw['profit_factor'])} | {_fmt(adj['profit_factor'])} |")

    print("\n=== Slippage sensitivity, per-strategy combined (both instruments), full period ===")
    print("| Strategy | Slippage | N resolved | Net P&L (raw) | Net P&L (comm-adj) | PF (raw) | PF (comm-adj) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for strat in STRATEGIES:
        results["per_strategy_combined"].setdefault(strat, {})
        for slip in sorted(SLIPPAGE_LOG_DIRS.keys()):
            sub = [t for instr in INSTRUMENTS for t in trades_by_slip[slip][instr] if t["strategy"] == strat]
            raw = _stats(sub, 0.0)
            adj = _stats(sub, COMMISSION_RT)
            results["per_strategy_combined"][strat][f"slippage_{slip}_tick"] = {
                "raw": raw,
                "commission_adjusted": adj,
            }
            print(f"| {strat} | {slip} | {raw['resolved']} "
                  f"| {_fmt(raw['net_pnl'], True)} | {_fmt(adj['net_pnl'], True)} "
                  f"| {_fmt(raw['profit_factor'])} | {_fmt(adj['profit_factor'])} |")

    out_path = Path("scripts/strat_212_122_slippage_sensitivity_results.json")
    out_path.write_text(json.dumps(results, indent=2, default=str) + "\n")
    print(f"\n[report] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
