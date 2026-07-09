#!/usr/bin/env python3
"""Deep-dive on the two shadow strategies that bucked the aggregate
BAD_COUNTERFACTUAL verdict in docs/shadow-gate-choke-sweep-622d-2026-07-09.md:
impulse_first_pullback_observed and strat_22_reversal_observed.

Both were net-positive but missed the strict win-rate bar there. This checks
whether that positive expectancy holds across instruments and walk-forward
halves, or is an artifact of a few big winners (outlier dependence) /
concentrated in one regime.

No new fill/resolution simulation — reuses collect_shadow_rows() from
scripts/shadow_gate_choke_sweep_622d.py, which itself reads outcomes already
resolved by strategy/shadow_setups.py:resolve_shadow_candidate(). That
function's entry-fill test requires a forward bar to actually trade through
the entry price (not an always-fills assumption) and uses pessimistic
same-bar resolution — but does NOT model slippage or commissions; noted
explicitly in the report rather than building a new slippage simulation.

Read-only, docs-only output. No changes to execution/, risk/, config/,
webhook/, broker*, or strategy/ (strategy/shadow_setups.py is read, not
modified). No strategy promotion or demotion is performed by this script.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.missed_move_gate_sweep_622d import _load_day_rows
from scripts.shadow_gate_choke_sweep_622d import ShadowRow, collect_shadow_rows

OUT_MD = REPO / "docs/shadow-strategy-deep-dive-622d-2026-07-09.md"
OUT_JSON = REPO / "logs/shadow_strategy_deep_dive_622d.json"
TARGET_STRATEGIES = ("impulse_first_pullback_observed", "strat_22_reversal_observed")
MIN_CELL_N = 15
OUTLIER_SHARE_THRESHOLD = 0.40


def filter_target_rows(rows: list[ShadowRow]) -> dict[str, list[ShadowRow]]:
    out: dict[str, list[ShadowRow]] = {s: [] for s in TARGET_STRATEGIES}
    for r in rows:
        if r.shadow_strategy in out:
            out[r.shadow_strategy].append(r)
    return out


def walk_forward_half(rows: list[ShadowRow]) -> dict[str, str]:
    """Midpoint-day split, same convention as entry_detached_sweep_622d.py's
    _walk_forward_half — inlined here since it's a 3-line calculation."""
    days = sorted({r.day for r in rows})
    if not days:
        return {}
    mid = len(days) // 2
    h1_days = set(days[:mid]) if mid else set()
    return {d: ("H1" if d in h1_days else "H2") for d in days}


def dollars(row: ShadowRow) -> Optional[float]:
    return row.pnl_dollars()


def outlier_share(rows: list[ShadowRow]) -> Optional[float]:
    """Top-3-trades' dollar contribution as a fraction of total NET (not
    gross) $. None if net is zero (undefined) or no dollar figures."""
    vals = [dollars(r) for r in rows if dollars(r) is not None]
    if not vals:
        return None
    net = sum(vals)
    if net == 0:
        return None
    top3 = sorted(vals, key=abs, reverse=True)[:3]
    return round(sum(top3) / net, 4)


def max_drawdown(rows: list[ShadowRow]) -> float:
    """Chronological (day, bar_ts) equity curve; max peak-to-trough drop."""
    ordered = sorted(rows, key=lambda r: (r.day, r.bar_ts))
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in ordered:
        d = dollars(r)
        if d is None:
            continue
        cum += d
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return round(max_dd, 2)


def _summarize(rows: list[ShadowRow]) -> dict[str, Any]:
    n = len(rows)
    vals = [dollars(r) for r in rows if dollars(r) is not None]
    wins = sum(1 for r in rows if r.result == "WIN")
    losses = sum(1 for r in rows if r.result == "LOSS")
    net = round(sum(vals), 2) if vals else 0.0
    mean_exp = round(statistics.fmean(vals), 2) if vals else None
    median_exp = round(statistics.median(vals), 2) if vals else None
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / n, 3) if n else None,
        "net_dollars": net,
        "mean_dollars": mean_exp,
        "median_dollars": median_exp,
        "outlier_share": outlier_share(rows),
        "max_drawdown": max_drawdown(rows),
    }


def _breakdown(rows: list[ShadowRow], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[ShadowRow]] = defaultdict(list)
    for r in rows:
        groups[key_fn(r)].append(r)
    return {k: _summarize(v) for k, v in sorted(groups.items())}


def _co_occurring_real_strategy(row: ShadowRow) -> Optional[str]:
    """If the underlying NO_TRADE journal row's setup.strategy is populated
    (the real strategy being evaluated when the gate fired), return it.
    Reads an existing field only — no new correlation logic."""
    day_rows = _load_day_rows(row.instrument, row.day)
    for jr in day_rows:
        if jr.get("bar_ts") == row.bar_ts and jr.get("decision") == "NO_TRADE":
            return (jr.get("setup") or {}).get("strategy")
    return None


def _is_outlier_dependent(cell: dict) -> bool:
    share = cell.get("outlier_share")
    return share is not None and abs(share) > OUTLIER_SHARE_THRESHOLD


def classify(combined: dict, by_instrument: dict, by_half: dict) -> str:
    if combined["net_dollars"] <= 0:
        return "REJECT"
    instr_cells = [d for d in by_instrument.values() if d["n"] >= MIN_CELL_N]
    half_cells = [d for d in by_half.values() if d["n"] >= MIN_CELL_N]
    all_consistency_cells = instr_cells + half_cells
    walk_forward_consistent = bool(all_consistency_cells) and all(
        d["net_dollars"] > 0 for d in all_consistency_cells
    )
    # Outlier-dependence is checked at the combined level AND at every
    # individual instrument/half cell — a cell can be net-positive yet still
    # be a near-zero result propped up almost entirely by 1-3 trades (e.g.
    # top-3 trades summing to several times the cell's own thin net), which
    # is exactly the fragility this check exists to catch, not just the
    # aggregate figure.
    outlier_dependent = _is_outlier_dependent(combined) or any(
        _is_outlier_dependent(d) for d in all_consistency_cells
    )
    if walk_forward_consistent and not outlier_dependent and combined["n"] >= MIN_CELL_N:
        return "VALIDATED_SHADOW_CANDIDATE"
    if combined["net_dollars"] > 0 and (not walk_forward_consistent or outlier_dependent):
        return "PROMISING_BUT_UNPROVEN"
    return "WATCH"


def _write_report(per_strategy: dict[str, dict[str, Any]]) -> None:
    lines = [
        "# Shadow Strategy Deep-Dive — impulse_first_pullback_observed + strat_22_reversal_observed",
        "",
        "Follow-up to `docs/shadow-gate-choke-sweep-622d-2026-07-09.md`, which found the "
        "aggregate `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` population is `BAD_COUNTERFACTUAL` but "
        "these two strategies were net-positive within it. This checks whether that holds up "
        "across instruments, walk-forward halves, and outlier dependence, or was a fluke of a "
        "few big winners. Same population and data source as the prior sweep — a drill-down, "
        "not a new study.",
        "",
        "**Fill-realism note**: every W/L/`pnl_ticks` figure below comes from "
        "`strategy/shadow_setups.py:resolve_shadow_candidate` — its entry-fill test requires a "
        "forward bar to actually trade through the entry price (not an always-fills "
        "assumption) and uses pessimistic same-bar resolution, same convention as the rest of "
        "this codebase. It does **not** model slippage or commissions — real executed P&L "
        "would likely run somewhat lower than shown here.",
        "",
    ]
    for strat, data in per_strategy.items():
        combined = data["combined"]
        lines.extend([
            f"## `{strat}`",
            "",
            f"**Classification: `{data['classification']}`**",
            "",
            "### Combined",
            "",
            "| n | wins | losses | win rate | net $ | mean $ | median $ | outlier share | max DD |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        wr = "n/a" if combined["win_rate"] is None else f"{100*combined['win_rate']:.0f}%"
        mean_s = "n/a" if combined["mean_dollars"] is None else f"{combined['mean_dollars']:.2f}"
        med_s = "n/a" if combined["median_dollars"] is None else f"{combined['median_dollars']:.2f}"
        outs = "n/a" if combined["outlier_share"] is None else f"{100*combined['outlier_share']:.0f}%"
        lines.append(
            f"| {combined['n']} | {combined['wins']} | {combined['losses']} | {wr} | "
            f"{combined['net_dollars']:.2f} | {mean_s} | {med_s} | {outs} | {combined['max_drawdown']:.2f} |"
        )

        def _section(title, breakdown):
            out = ["", f"### {title}", "", "| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
            for key, d in breakdown.items():
                wr_ = "n/a" if d["win_rate"] is None else f"{100*d['win_rate']:.0f}%"
                mean_ = "n/a" if d["mean_dollars"] is None else f"{d['mean_dollars']:.2f}"
                med_ = "n/a" if d["median_dollars"] is None else f"{d['median_dollars']:.2f}"
                out_ = "n/a" if d["outlier_share"] is None else f"{100*d['outlier_share']:.0f}%"
                out.append(f"| {key} | {d['n']} | {wr_} | {d['net_dollars']:.2f} | {mean_} | {med_} | {out_} | {d['max_drawdown']:.2f} |")
            return out

        lines.extend(_section("By instrument", data["by_instrument"]))
        lines.extend(_section("By walk-forward half", data["by_half"]))
        lines.extend(_section("By session", data["by_session"]))

        lines.extend(["", "### Co-occurring real (rejected) candidate strategy on the same bar", "", "| real strategy | count |", "|---|---:|"])
        for real_strat, count in data["co_occurrence"].most_common(10):
            lines.append(f"| {real_strat or '(none logged)'} | {count} |")
        lines.append("")

    lines.extend([
        "## Notes",
        "",
        f"- Classification requires n >= {MIN_CELL_N} per breakdown cell to count toward "
        "walk-forward-consistency; outlier-dependent means the top-3 trades account for more "
        f"than {int(100*OUTLIER_SHARE_THRESHOLD)}% of total net $.",
        "- `VALIDATED_SHADOW_CANDIDATE` here is a research finding, not a promotion — no "
        "config, risk, or strategy file is changed by this script regardless of the label.",
        "- This is docs/script/tests only — zero changes to execution/, risk/, config/, "
        "risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo "
        "orders, no trade-cap changes, no proof_builder, no strategy promotion or demotion.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    all_rows, _excluded, _total = collect_shadow_rows()
    by_strategy = filter_target_rows(all_rows)

    per_strategy: dict[str, dict[str, Any]] = {}
    for strat, rows in by_strategy.items():
        halves = walk_forward_half(rows)
        by_instrument = _breakdown(rows, lambda r: r.instrument)
        by_half = _breakdown(rows, lambda r: halves.get(r.day, "?"))
        by_session = _breakdown(rows, lambda r: r.session or "?")
        combined = _summarize(rows)
        co_occurrence = Counter(_co_occurring_real_strategy(r) for r in rows)
        classification = classify(combined, by_instrument, by_half)
        per_strategy[strat] = {
            "combined": combined,
            "by_instrument": by_instrument,
            "by_half": by_half,
            "by_session": by_session,
            "co_occurrence": co_occurrence,
            "classification": classification,
        }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                strat: {
                    **{k: v for k, v in data.items() if k != "co_occurrence"},
                    "co_occurrence": dict(data["co_occurrence"]),
                }
                for strat, data in per_strategy.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(per_strategy)
    for strat, data in per_strategy.items():
        print(f"{strat}: n={data['combined']['n']} classification={data['classification']}")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
