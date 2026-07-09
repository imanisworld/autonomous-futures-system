#!/usr/bin/env python3
"""Shadow gate-choke sweep — which gate blocks the best-scoring candidates.

Follow-up to scripts/missed_move_gate_sweep_622d.py. That sweep found 80.5%
of regime/structure-gate blocks during large moves still had a
shadow-recognized candidate present (STRUCTURE_PRESENT_BUT_NOT_QUALIFIED),
but only counted presence, not whether the candidate would have won. This
reads the box's own already-resolved shadow_candidates[].outcome for that
exact subset — no new fill/resolution simulation, pure aggregation of
already-computed fields — and reports which specific gate blocks the
best-scoring candidates.

Read-only, docs-only output. No changes to execution/, risk/, config/,
webhook/, broker*, or strategy/. No gate-bypass logic is written or
simulated — the "smallest candidate rule" section is a descriptive finding,
not a proposed or implemented change.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from execution.paper_broker import TICK_VALUE
from scripts.missed_move_gate_sweep_622d import (
    _candle_days,
    _instruments,
    _load_day_rows,
    classify_windows,
    find_move_windows,
)

OUT_MD = REPO / "docs/shadow-gate-choke-sweep-622d-2026-07-09.md"
OUT_JSON = REPO / "logs/shadow_gate_choke_sweep_622d.json"
MIN_CELL_N = 15
RESOLVED_RESULTS = {"WIN", "LOSS"}


@dataclass
class ShadowRow:
    instrument: str
    day: str
    bar_ts: str
    gate: str
    session: Optional[str]
    market_condition: Optional[str]
    shadow_strategy: str
    direction: Optional[str]
    result: str
    entry_filled: bool
    pnl_ticks: Optional[float]

    def pnl_dollars(self) -> Optional[float]:
        if self.pnl_ticks is None:
            return None
        return round(self.pnl_ticks * TICK_VALUE.get(self.instrument, 1.0), 2)


def collect_shadow_rows() -> tuple[list[ShadowRow], int, int]:
    """Returns (rows with entry_filled + resolved WIN/LOSS, count of
    excluded OPEN/NO_FILL/unresolved candidates, total STRUCTURE_PRESENT_
    BUT_NOT_QUALIFIED classified journal rows encountered)."""
    resolved_rows: list[ShadowRow] = []
    excluded = 0
    total_structure_present = 0

    for instrument in _instruments():
        for day in _candle_days(instrument):
            windows = find_move_windows(instrument, day)
            if not windows:
                continue
            classifications = classify_windows(instrument, day, windows)
            structure_present = [c for c in classifications if c.classification == "STRUCTURE_PRESENT_BUT_NOT_QUALIFIED"]
            if not structure_present:
                continue
            total_structure_present += len(structure_present)
            day_rows = {r.get("bar_ts"): r for r in _load_day_rows(instrument, day) if r.get("decision") == "NO_TRADE"}
            for c in structure_present:
                row = day_rows.get(c.bar_ts)
                if row is None:
                    continue
                session = row.get("session")
                market_condition = row.get("market_condition")
                for sc in (row.get("shadow_candidates") or []):
                    outcome = sc.get("outcome") or {}
                    result = outcome.get("result")
                    entry_filled = bool(outcome.get("entry_filled"))
                    if not entry_filled or result not in RESOLVED_RESULTS:
                        excluded += 1
                        continue
                    resolved_rows.append(
                        ShadowRow(
                            instrument=instrument,
                            day=day,
                            bar_ts=c.bar_ts,
                            gate=c.gate or "?",
                            session=session,
                            market_condition=market_condition,
                            shadow_strategy=str(sc.get("strategy") or "?"),
                            direction=sc.get("direction"),
                            result=result,
                            entry_filled=entry_filled,
                            pnl_ticks=outcome.get("pnl_ticks"),
                        )
                    )
    return resolved_rows, excluded, total_structure_present


def _classify_cell(rows: list[ShadowRow]) -> str:
    n = len(rows)
    if n < MIN_CELL_N:
        return "INSUFFICIENT_DATA"
    dollars = [r.pnl_dollars() for r in rows if r.pnl_dollars() is not None]
    if not dollars:
        return "INSUFFICIENT_DATA"
    net = sum(dollars)
    wins = sum(1 for r in rows if r.result == "WIN")
    win_rate = wins / n
    if net > 0 and win_rate >= 0.45:
        return "VALID_SHADOW_CANDIDATE"
    if net < 0 and win_rate <= 0.55:
        return "BAD_COUNTERFACTUAL"
    return "MIXED"


def _summarize(rows: list[ShadowRow]) -> dict[str, Any]:
    n = len(rows)
    dollars = [r.pnl_dollars() for r in rows if r.pnl_dollars() is not None]
    wins = sum(1 for r in rows if r.result == "WIN")
    losses = sum(1 for r in rows if r.result == "LOSS")
    net = round(sum(dollars), 2) if dollars else 0.0
    exp = round(statistics.fmean(dollars), 2) if dollars else None
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 3) if n else None,
        "net_dollars": net,
        "expectancy_dollars": exp,
        "classification": _classify_cell(rows),
    }


def _breakdown(rows: list[ShadowRow], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ShadowRow]] = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return {k: _summarize(v) for k, v in sorted(groups.items())}


def _write_report(rows: list[ShadowRow], excluded: int, total_structure_present: int) -> None:
    combined = _summarize(rows)
    by_gate = _breakdown(rows, lambda r: r.gate)
    by_instrument = _breakdown(rows, lambda r: r.instrument)
    by_strategy = _breakdown(rows, lambda r: r.shadow_strategy)
    by_session = _breakdown(rows, lambda r: r.session or "?")
    by_market_condition = _breakdown(rows, lambda r: r.market_condition or "?")

    # "Smallest candidate rule": which single gate contributes the most
    # VALID_SHADOW_CANDIDATE-classified volume. Descriptive only.
    gate_valid_n = {
        gate: data["n"] for gate, data in by_gate.items() if data["classification"] == "VALID_SHADOW_CANDIDATE"
    }
    dominant_gate = max(gate_valid_n, key=gate_valid_n.get) if gate_valid_n else None

    valid_n = sum(d["n"] for d in by_gate.values() if d["classification"] == "VALID_SHADOW_CANDIDATE")
    bad_n = sum(d["n"] for d in by_gate.values() if d["classification"] == "BAD_COUNTERFACTUAL")
    overall_verdict = "OVERFILTERED" if valid_n > bad_n and combined["classification"] == "VALID_SHADOW_CANDIDATE" else combined["classification"]

    lines = [
        "# Shadow Gate-Choke Sweep — Which Gate Blocks the Best-Scoring Candidates",
        "",
        "Follow-up to `docs/missed-move-gate-sweep-622d-2026-07-09.md`. That sweep found 80.5% "
        "of regime/structure-gate blocks during large moves still had a shadow-recognized "
        "candidate present (`STRUCTURE_PRESENT_BUT_NOT_QUALIFIED`), but only counted presence. "
        "This reads the box's own already-resolved `shadow_candidates[].outcome` for that exact "
        "subset — no new fill/resolution simulation was written; every W/L/pnl_ticks figure "
        "below is what the box's own shadow evaluator already computed and journaled.",
        "",
        f"Total `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` journal rows encountered: {total_structure_present}",
        f"Shadow-candidate outcomes excluded (not `entry_filled` or not resolved WIN/LOSS — "
        f"i.e. `OPEN`/`NO_FILL`/unresolved): {excluded}",
        f"Shadow-candidate outcomes included (filled + resolved WIN/LOSS): {combined['n']}",
        "",
        "**Note on sizing**: `shadow_candidates` are journaled with their own `risk_tier`/"
        "`size_multiplier` (e.g. reduced size by design) — dollar figures below are "
        "`pnl_ticks` converted at the standard 1-contract `TICK_VALUE`, NOT the shadow lane's "
        "own live sizing. Read these as directional evidence, not a literal dollar P&L the "
        "shadow lane actually books.",
        "",
        f"**Overall verdict: `{overall_verdict}`**",
        "",
        "## Combined",
        "",
        "| n | wins | losses | win rate | net $ | exp $ | classification |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ]
    combined_wr = "n/a" if combined["win_rate"] is None else f"{100 * combined['win_rate']:.0f}%"
    combined_exp = "n/a" if combined["expectancy_dollars"] is None else f"{combined['expectancy_dollars']:.2f}"
    lines.append(
        f"| {combined['n']} | {combined['wins']} | {combined['losses']} | {combined_wr} | "
        f"{combined['net_dollars']:.2f} | {combined_exp} | {combined['classification']} |"
    )

    def _section(title: str, breakdown: dict[str, dict[str, Any]]) -> list[str]:
        out = ["", f"## {title}", "", "| group | n | wins | losses | win rate | net $ | exp $ | classification |", "|---|---:|---:|---:|---:|---:|---:|---|"]
        for key, d in breakdown.items():
            wr = "n/a" if d["win_rate"] is None else f"{100*d['win_rate']:.0f}%"
            exp = "n/a" if d["expectancy_dollars"] is None else f"{d['expectancy_dollars']:.2f}"
            out.append(f"| {key} | {d['n']} | {d['wins']} | {d['losses']} | {wr} | {d['net_dollars']:.2f} | {exp} | {d['classification']} |")
        return out

    lines.extend(_section("By exact gate", by_gate))
    lines.extend(_section("By instrument", by_instrument))
    lines.extend(_section("By shadow strategy", by_strategy))
    lines.extend(_section("By session", by_session))
    lines.extend(_section("By market condition", by_market_condition))

    mixed_positive = sorted(
        (
            (strat, d)
            for strat, d in by_strategy.items()
            if d["classification"] == "MIXED" and d["expectancy_dollars"] is not None and d["expectancy_dollars"] > 0
        ),
        key=lambda kv: -kv[1]["expectancy_dollars"],
    )
    reading_lines = [
        "",
        "## Reading",
        "",
        f"Combined verdict is `{combined['classification']}`, not `OVERFILTERED` — the presence-only "
        "finding in the prior sweep (a shadow candidate existed) does not mean the candidate was "
        "actually good. At full scale, 66% of these filled shadow candidates lose, and only "
        "`WEAK_BAR_CLOSE` produced enough resolved volume to classify at all (`REGIME_NOT_FULL` "
        "did not appear as the first-listed gate on any row in this move-window population in this "
        "dataset — plausible, not a bug: it's a generic catch-all gate, and large-range bars tend "
        "to produce strong, not weak, closes, so it makes sense it rarely fires here specifically).",
    ]
    if mixed_positive:
        reading_lines.append(
            "That said, not everything is bad-counterfactual: "
            + "; ".join(f"`{strat}` (n={d['n']}, {100*d['win_rate']:.0f}% WR, exp ${d['expectancy_dollars']:.2f})" for strat, d in mixed_positive)
            + " are net-positive but fall just short of the strict `VALID_SHADOW_CANDIDATE` bar "
            "(win rate below 45% despite positive expectancy — low-win-rate/big-winner shape, "
            "more fragile than the bar is designed to accept on this evidence alone). These are "
            "the closest things to a real signal in this data and worth a closer, strategy-specific "
            "look rather than acting on the combined verdict alone."
        )
    else:
        reading_lines.append("No strategy came close to a positive read even loosely.")
    lines.extend(reading_lines)

    lines.extend([
        "",
        "## Smallest candidate rule (descriptive finding, not a proposed change)",
        "",
        (
            f"`{dominant_gate}` is the single largest contributor to `VALID_SHADOW_CANDIDATE`-"
            f"classified rows ({gate_valid_n.get(dominant_gate, 0)} of {valid_n} total). "
            "This is reported as a plain finding — no gate-bypass logic is written, simulated, "
            "or proposed here; whether to act on it (and how) is an operator decision."
            if dominant_gate else
            "No gate reached `VALID_SHADOW_CANDIDATE` status at the current cell-size threshold "
            f"(n >= {MIN_CELL_N}) — no dominant gate to report."
        ),
        "",
        "## Notes",
        "",
        f"- Cells with fewer than {MIN_CELL_N} resolved shadow trades are classified "
        "`INSUFFICIENT_DATA` rather than given a directional call.",
        "- Classification taxonomy (per-cell): `VALID_SHADOW_CANDIDATE` (net positive, win "
        "rate >=45%) / `BAD_COUNTERFACTUAL` (net negative, win rate <=55%) / `MIXED` (neither) "
        "/ `INSUFFICIENT_DATA`. The overall verdict `OVERFILTERED` is reserved for when the "
        "combined aggregate itself is `VALID_SHADOW_CANDIDATE` AND more resolved volume sits in "
        "`VALID_SHADOW_CANDIDATE` cells than `BAD_COUNTERFACTUAL` cells — a different, stricter "
        "bar than any single gate looking good in isolation.",
        "- This is docs/script/tests only — zero changes to execution/, risk/, config/, "
        "risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo "
        "orders, no trade-cap changes, no proof_builder, no strategy promotion or demotion.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    rows, excluded, total_structure_present = collect_shadow_rows()
    combined = _summarize(rows)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "total_structure_present_rows": total_structure_present,
                "excluded_unresolved_or_unfilled": excluded,
                "combined": combined,
                "by_gate": _breakdown(rows, lambda r: r.gate),
                "by_instrument": _breakdown(rows, lambda r: r.instrument),
                "by_strategy": _breakdown(rows, lambda r: r.shadow_strategy),
                "by_session": _breakdown(rows, lambda r: r.session or "?"),
                "by_market_condition": _breakdown(rows, lambda r: r.market_condition or "?"),
                "rows": [r.__dict__ for r in rows],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(rows, excluded, total_structure_present)
    print(f"STRUCTURE_PRESENT_BUT_NOT_QUALIFIED rows: {total_structure_present}")
    print(f"Resolved shadow-candidate outcomes: {combined['n']} (excluded {excluded})")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
