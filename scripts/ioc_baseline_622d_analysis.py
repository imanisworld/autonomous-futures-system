#!/usr/bin/env python3
"""
scripts/ioc_baseline_622d_analysis.py

Workstream A Phase 0 measurement: aggregate the 2x2 replay matrix
({market, ioc_limit} entry fill x {static, runner_live} exit) produced by
scripts/run_replay_batch.py over the 622-day Polygon set, per instrument,
with the midpoint walk-forward split used by the #142 scorecard and
scripts/orb_market_entry_study.py.

Expects leg journals under:
    <logs-base>/replay_622d_<fill>_<exit>/<INSTR>/journal_YYYY-MM-DD.jsonl

Emits:
    scripts/ioc_baseline_622d_results.json
    markdown summary tables on stdout

Trade pairing mirrors run_replay_batch._strategy_breakdown: approved TRADE
decision rows are matched FIFO against type=OUTCOME rows within each day.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

FILLS = ("market", "ioc_limit")
EXITS = ("static", "runner")
INSTRUMENTS = ("MES", "MNQ")

RESOLVED = {"WIN", "LOSS"}


def _load_trades(log_dir: Path) -> list[dict]:
    """One dict per approved trade, in journal (date) order."""
    trades: list[dict] = []
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        date = path.stem.replace("journal_", "")
        entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

        outcome_map: dict[str, list[dict]] = defaultdict(list)
        for e in entries:
            if e.get("type") == "OUTCOME":
                outcome_map[e.get("instrument", "")].append(e)

        for e in entries:
            rc = e.get("risk_check") or {}
            if e.get("decision") == "TRADE" and rc.get("result") == "APPROVED":
                instr = e.get("instrument", "")
                out = outcome_map[instr].pop(0) if outcome_map[instr] else None
                oc = (out or {}).get("outcome") or {}
                trades.append(
                    {
                        "date": date,
                        "strategy": (e.get("setup") or {}).get("strategy", "unknown"),
                        "result": oc.get("result"),
                        "exit_reason": oc.get("exit_reason"),
                        "pnl": float(oc["pnl_dollars"]) if oc.get("pnl_dollars") is not None else None,
                    }
                )
    return trades


def _stats(trades: list[dict]) -> dict:
    attempts = len(trades)
    ioc_nofill = sum(1 for t in trades if t["exit_reason"] == "ENTRY_NOT_FILLED")
    day_nofill = sum(1 for t in trades if t["exit_reason"] == "NO_FILL")
    wins = sum(1 for t in trades if t["result"] == "WIN")
    losses = sum(1 for t in trades if t["result"] == "LOSS")
    resolved = wins + losses
    pnl = sum(t["pnl"] or 0.0 for t in trades)
    filled = attempts - ioc_nofill - day_nofill
    return {
        "attempts": attempts,
        "ioc_no_fill": ioc_nofill,
        "day_no_fill": day_nofill,
        "filled": filled,
        "fill_rate": round(filled / attempts, 4) if attempts else None,
        "wins": wins,
        "losses": losses,
        "resolved": resolved,
        "win_rate": round(wins / resolved, 4) if resolved else None,
        "net_pnl": round(pnl, 2),
        "expectancy": round(pnl / resolved, 2) if resolved else None,
    }


def _split_halves(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    """Midpoint split by trading DAY (not trade count) — #142 scorecard convention."""
    days = sorted({t["date"] for t in trades})
    if not days:
        return [], []
    mid_day = days[len(days) // 2]
    return [t for t in trades if t["date"] < mid_day], [t for t in trades if t["date"] >= mid_day]


def _leg_summary(trades: list[dict]) -> dict:
    h1, h2 = _split_halves(trades)
    per_strategy = {}
    for strat in sorted({t["strategy"] for t in trades}):
        st = [t for t in trades if t["strategy"] == strat]
        s1, s2 = _split_halves(st)
        per_strategy[strat] = {
            "all": _stats(st),
            "first_half": _stats(s1),
            "second_half": _stats(s2),
        }
    return {
        "all": _stats(trades),
        "first_half": _stats(h1),
        "second_half": _stats(h2),
        "per_strategy": per_strategy,
    }


def _fmt(v, money: bool = False) -> str:
    if v is None:
        return "—"
    if money:
        return f"${v:,.0f}"
    if isinstance(v, float):
        return f"{v * 100:.1f}%"
    return str(v)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate the 622d IOC-baseline replay matrix")
    parser.add_argument("--logs-base", default="logs", help="Directory holding replay_622d_* leg dirs")
    parser.add_argument(
        "--prefix",
        default="replay_622d",
        help="Leg dir prefix (e.g. replay_622d_nodd for the breaker-disabled pass)",
    )
    parser.add_argument(
        "--out",
        default="scripts/ioc_baseline_622d_results.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    base = Path(args.logs_base)
    results: dict = {}
    for fill in FILLS:
        for exitm in EXITS:
            for instr in INSTRUMENTS:
                leg_dir = base / f"{args.prefix}_{fill}_{exitm}" / instr
                if not leg_dir.exists():
                    print(f"[analysis] MISSING leg dir: {leg_dir}", file=sys.stderr)
                    continue
                trades = _load_trades(leg_dir)
                results.setdefault(f"{fill}_{exitm}", {})[instr] = _leg_summary(trades)

    Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    print(f"[analysis] wrote {args.out}\n")

    # Headline table: leg x instrument, overall + halves
    print("| Leg | Instr | Attempts | Fill% | Resolved | WR | Net P&L | H1 P&L | H2 P&L |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for leg, by_instr in results.items():
        for instr, s in by_instr.items():
            a, h1, h2 = s["all"], s["first_half"], s["second_half"]
            print(
                f"| {leg} | {instr} | {a['attempts']} | {_fmt(a['fill_rate'])} "
                f"| {a['resolved']} | {_fmt(a['win_rate'])} | {_fmt(a['net_pnl'], money=True)} "
                f"| {_fmt(h1['net_pnl'], money=True)} | {_fmt(h2['net_pnl'], money=True)} |"
            )

    # Per-strategy tables for the honest legs
    for leg in ("ioc_limit_static", "ioc_limit_runner"):
        by_instr = results.get(leg) or {}
        for instr, s in by_instr.items():
            print(f"\n### {leg} — {instr} (per strategy)\n")
            print("| Strategy | Attempts | Fill% | Resolved | WR | Net P&L | H1 P&L | H2 P&L |")
            print("|---|---:|---:|---:|---:|---:|---:|---:|")
            for strat, st in s["per_strategy"].items():
                a, h1, h2 = st["all"], st["first_half"], st["second_half"]
                print(
                    f"| {strat} | {a['attempts']} | {_fmt(a['fill_rate'])} | {a['resolved']} "
                    f"| {_fmt(a['win_rate'])} | {_fmt(a['net_pnl'], money=True)} "
                    f"| {_fmt(h1['net_pnl'], money=True)} | {_fmt(h2['net_pnl'], money=True)} |"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
