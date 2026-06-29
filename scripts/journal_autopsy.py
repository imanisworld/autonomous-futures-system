#!/usr/bin/env python3
"""Read-only WIN/LOSS autopsy over journal JSONL files.

This script pairs approved TRADE decisions with their resolving OUTCOME records
and reports only what the journal can prove. It does not fetch bars, start the
app, submit orders, or mutate journal files.

Primary questions:
  - Per instrument, how often did a LOSS have journal evidence that price first
    reached the planned target? ("lost winner")
  - On WIN outcomes, how much MFE/exit did the journal show past the planned
    target?

If the journal lacks MFE/max-favorable fields, the report says so. Use
scripts/mfe_study.py when you need bar-based MFE reconstruction.

Usage:
    python3 scripts/journal_autopsy.py [logs/journal_2026-06-*.jsonl ...]
    python3 scripts/journal_autopsy.py --json logs/journal_2026-06-*.jsonl
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass
class AutopsyTrade:
    ts: str
    outcome_ts: Optional[str]
    instrument: str
    session: str
    strategy: str
    direction: str
    entry: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    result: Optional[str]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    pnl_dollars: Optional[float]
    mfe_r: Optional[float]
    target_r: Optional[float]
    lost_winner: Optional[bool]
    past_target_r: Optional[float]


def journal_paths(args: list[str]) -> list[Path]:
    paths = [Path(p) for p in args] if args else [Path(p) for p in glob.glob("logs/journal_*.jsonl")]
    return sorted(path for path in paths if path.exists())


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def pair_journal(paths: Iterable[Path]) -> list[AutopsyTrade]:
    pending: dict[str, deque[dict]] = defaultdict(deque)
    trades: list[AutopsyTrade] = []

    for path in sorted(paths):
        for row in read_jsonl(path):
            if _is_approved_trade(row):
                inline = row.get("outcome") or {}
                if _is_resolved_outcome(inline):
                    trades.append(_build_trade(row, row, inline))
                else:
                    pending[str(row.get("instrument") or "")].append(row)
                continue

            if row.get("type") != "OUTCOME":
                continue
            outcome = row.get("outcome") or {}
            if not isinstance(outcome, dict):
                continue
            instr = str(row.get("instrument") or "")
            if pending[instr]:
                trades.append(_build_trade(pending[instr].popleft(), row, outcome))

    for queue in pending.values():
        for row in queue:
            trades.append(_build_trade(row, None, {}))

    return trades


def summarize(trades: list[AutopsyTrade]) -> dict[str, dict]:
    by_inst: dict[str, list[AutopsyTrade]] = defaultdict(list)
    for trade in trades:
        by_inst[trade.instrument or "UNKNOWN"].append(trade)

    summary: dict[str, dict] = {}
    for inst, rows in sorted(by_inst.items()):
        resolved = [t for t in rows if t.result in {"WIN", "LOSS", "BREAKEVEN"}]
        wins = [t for t in resolved if t.result == "WIN"]
        losses = [t for t in resolved if t.result == "LOSS"]
        losses_with_mfe = [t for t in losses if t.lost_winner is not None]
        lost_winners = [t for t in losses_with_mfe if t.lost_winner]
        wins_with_past = [t for t in wins if t.past_target_r is not None]
        past_vals = [float(t.past_target_r) for t in wins_with_past]
        mfe_vals = [float(t.mfe_r) for t in resolved if t.mfe_r is not None]

        summary[inst] = {
            "trades": len(rows),
            "resolved": len(resolved),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": sum(1 for t in resolved if t.result == "BREAKEVEN"),
            "open": sum(1 for t in rows if t.result is None),
            "pnl_dollars": round(sum(float(t.pnl_dollars or 0.0) for t in resolved), 2),
            "losses_with_mfe_evidence": len(losses_with_mfe),
            "lost_winners": len(lost_winners),
            "lost_winner_pct_of_losses_with_evidence": _pct(len(lost_winners), len(losses_with_mfe)),
            "losses_without_mfe_evidence": len(losses) - len(losses_with_mfe),
            "wins_with_past_target_evidence": len(wins_with_past),
            "median_past_target_r_on_wins": _median(past_vals),
            "max_past_target_r_on_wins": round(max(past_vals), 3) if past_vals else None,
            "median_mfe_r": _median(mfe_vals),
        }
    return summary


def print_report(summary: dict[str, dict]) -> None:
    if not summary:
        print("No approved trades found.")
        return

    print("\nJournal WIN/LOSS Autopsy")
    print("Read-only. Uses journaled TRADE/OUTCOME fields only; no bar replay.")
    print()
    header = (
        f"{'instrument':<10} {'res':>4} {'W':>3} {'L':>3} {'BE':>3} "
        f"{'P&L':>10} {'lostW':>7} {'loss no-MFE':>11} {'win past tgt R':>14}"
    )
    print(header)
    print("-" * len(header))
    for inst, row in summary.items():
        lost = _fmt_pct_count(
            row["lost_winners"],
            row["losses_with_mfe_evidence"],
            row["lost_winner_pct_of_losses_with_evidence"],
        )
        past = _fmt_float(row["median_past_target_r_on_wins"])
        print(
            f"{inst:<10} {row['resolved']:>4} {row['wins']:>3} {row['losses']:>3} "
            f"{row['breakeven']:>3} ${row['pnl_dollars']:>9.2f} "
            f"{lost:>7} {row['losses_without_mfe_evidence']:>11} {past:>14}"
        )
    print()
    print("lostW denominator is losses with journaled MFE/max-favorable evidence.")
    print("win past tgt R is median R beyond planned target on wins when journal evidence exists.")


def _is_approved_trade(row: dict) -> bool:
    return (
        row.get("decision") == "TRADE"
        and isinstance(row.get("setup"), dict)
        and (row.get("risk_check") or {}).get("result") == "APPROVED"
    )


def _is_resolved_outcome(outcome: Any) -> bool:
    return isinstance(outcome, dict) and outcome.get("result") in {"WIN", "LOSS", "BREAKEVEN", "CANCELLED"}


def _build_trade(decision: dict, outcome_row: Optional[dict], outcome: dict) -> AutopsyTrade:
    setup = decision.get("setup") or {}
    direction = str(setup.get("direction") or "").upper()
    entry = _num(setup.get("entry"))
    stop = _num(setup.get("stop"))
    target = _num(setup.get("target"))
    exit_price = _num(outcome.get("exit_price"))
    mfe_r = _mfe_r(direction=direction, entry=entry, stop=stop, outcome=outcome)
    target_r = _target_r(entry, stop, target)
    result = outcome.get("result")

    lost_winner: Optional[bool] = None
    if result == "LOSS" and mfe_r is not None and target_r is not None:
        lost_winner = mfe_r >= target_r

    past_target_r = _past_target_r(
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        outcome=outcome,
        exit_price=exit_price,
    )

    return AutopsyTrade(
        ts=str(decision.get("ts") or ""),
        outcome_ts=str(outcome_row.get("ts")) if outcome_row else None,
        instrument=str(decision.get("instrument") or ""),
        session=str(decision.get("session") or ""),
        strategy=str(setup.get("strategy") or "unknown"),
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        result=str(result) if result else None,
        exit_price=exit_price,
        exit_reason=str(outcome.get("exit_reason")) if outcome.get("exit_reason") is not None else None,
        pnl_dollars=_num(outcome.get("pnl_dollars")),
        mfe_r=mfe_r,
        target_r=target_r,
        lost_winner=lost_winner,
        past_target_r=past_target_r,
    )


def _mfe_r(*, direction: str, entry: Optional[float], stop: Optional[float], outcome: dict) -> Optional[float]:
    for key in ("mfe_R", "mfe_r", "max_favorable_r", "favorable_r"):
        value = _num(outcome.get(key))
        if value is not None:
            return value

    max_fav = _num(outcome.get("max_favorable"))
    if max_fav is None:
        max_fav = _num(outcome.get("max_favorable_price"))
    risk = _risk(entry, stop)
    if max_fav is None or entry is None or risk is None:
        return None
    if direction == "LONG":
        return max(0.0, (max_fav - entry) / risk)
    if direction == "SHORT":
        return max(0.0, (entry - max_fav) / risk)
    return None


def _past_target_r(
    *,
    direction: str,
    entry: Optional[float],
    stop: Optional[float],
    target: Optional[float],
    outcome: dict,
    exit_price: Optional[float],
) -> Optional[float]:
    explicit = _num(outcome.get("past_target_r"))
    if explicit is not None:
        return max(0.0, explicit)

    mfe_r = _mfe_r(direction=direction, entry=entry, stop=stop, outcome=outcome)
    target_r = _target_r(entry, stop, target)
    if mfe_r is not None and target_r is not None:
        return max(0.0, mfe_r - target_r)

    risk = _risk(entry, stop)
    if exit_price is None or target is None or risk is None:
        return None
    if direction == "LONG":
        return max(0.0, (exit_price - target) / risk)
    if direction == "SHORT":
        return max(0.0, (target - exit_price) / risk)
    return None


def _target_r(entry: Optional[float], stop: Optional[float], target: Optional[float]) -> Optional[float]:
    risk = _risk(entry, stop)
    if entry is None or target is None or risk is None:
        return None
    return abs(target - entry) / risk


def _risk(entry: Optional[float], stop: Optional[float]) -> Optional[float]:
    if entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    return risk if risk > 0 else None


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _median(values: list[float]) -> Optional[float]:
    return round(statistics.median(values), 3) if values else None


def _fmt_float(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_pct_count(n: int, d: int, pct: Optional[float]) -> str:
    return "n/a" if pct is None else f"{n}/{d} {pct:.0f}%"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("journals", nargs="*", help="journal_*.jsonl files; defaults to logs/journal_*.jsonl")
    parser.add_argument("--json", action="store_true", help="emit JSON summary instead of text")
    args = parser.parse_args(argv)

    paths = journal_paths(args.journals)
    if not paths:
        print("no journal files found", file=sys.stderr)
        return 1
    summary = summarize(pair_journal(paths))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
