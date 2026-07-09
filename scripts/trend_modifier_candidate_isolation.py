#!/usr/bin/env python3
"""Trend-modifier candidate isolation — is there a single, coherent,
describable strategy/rule with real trend-modifier evidence?

PR #240's TREND_MODIFIER_CANDIDATE cells are grouped only by
(instrument, gate, source) — pooled across every shadow strategy that hit
that gate, and including non-trend gates (e.g. SIGNAL_BAR_VOLUME_TOO_LOW).
That is not a describable single rule. This re-groups the SAME
already-collected candidates (collect_blocked_candidates, imported from
scripts/mes_mnq_mechanical_research.py, not rebuilt) at fine grain:
instrument x strategy x gate x session x direction, restricted to genuine
trend gates (TREND_GATES) for the actual candidate-selection question, with
non-trend-gate cells reported separately as informational only (never used
as trend-modifier proof).

A cell only survives as a coherent candidate if: adequate sample size at the
fine grain (MIN_CELL_N, same bar as everywhere else this session — not
lowered to manufacture a result), walk-forward consistent (positive in both
halves), not outlier-dependent (top-3 trades <= 40% of net), and its best
target-variant mode is both positive and better than the current target.
If nothing survives, that is reported explicitly — this script does not
force-fit a candidate.

Read-only, docs-only. No production behavior change. No proof_builder /
demo_proof architecture is built here — this only answers whether there is
something for it to test yet.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.mes_mnq_mechanical_research import (
    Candidate,
    MIN_CELL_N,
    TREND_GATES,
    _bar_index,
    _candles,
    _simulate,
    _summarize_results,
    _target_for,
    collect_blocked_candidates,
)

OUT_JSON = REPO / "logs/trend_modifier_candidate_isolation.json"
OUT_MD = REPO / "docs/trend-modifier-candidate-isolation-2026-07-09.md"

TARGET_MODES = ("current", "0.5R", "0.75R", "1.0R")
OUTLIER_SHARE_THRESHOLD = 0.40
MIN_HALF_CELL_N = max(10, MIN_CELL_N // 3)


def _fine_key(c: Candidate) -> tuple[str, str, str, str, str, str]:
    return (c.instrument, c.strategy, c.gate, c.source, c.session, c.direction)


def _outlier_share(pnls: list[float]) -> Optional[float]:
    if not pnls:
        return None
    net = sum(pnls)
    if net == 0:
        return None
    top3 = sorted(pnls, key=abs, reverse=True)[:3]
    return round(sum(top3) / net, 4)


def _max_drawdown(ordered_pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in ordered_pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(max_dd, 2)


def _walk_forward_half(days: list[str]) -> dict[str, str]:
    uniq = sorted(set(days))
    if not uniq:
        return {}
    mid = len(uniq) // 2
    h1 = set(uniq[:mid]) if mid else set()
    return {d: ("H1" if d in h1 else "H2") for d in uniq}


def analyze_fine_grained_cells(candidates: list[Candidate]) -> dict[str, Any]:
    """Re-groups already-collected candidates at instrument x strategy x
    gate x source x session x direction grain. Reuses _simulate/_target_for
    (imported, not reimplemented) for the target-variant sweep. Cells below
    MIN_CELL_N on the 'current' target mode are dropped entirely -- not
    reported as weak candidates, simply not reliable enough to report on."""
    by_key: dict[tuple, list[Candidate]] = defaultdict(list)
    for c in candidates:
        by_key[_fine_key(c)].append(c)

    out: dict[str, Any] = {}
    for key, cands in sorted(by_key.items()):
        instrument, strategy, gate, source, session, direction = key
        halves = _walk_forward_half([c.day for c in cands])

        by_mode_results: dict[str, list[dict]] = defaultdict(list)
        current_meta: list[tuple[str, str, float, str]] = []
        for c in cands:
            candles = _candles(c.instrument, c.day)
            idx = _bar_index(candles, c.bar_ts)
            decision_bar = candles[idx] if idx is not None else None
            for mode in TARGET_MODES:
                target = _target_for(c, mode, decision_bar)
                if target is None or target == c.entry:
                    continue
                result = _simulate(c, target)
                by_mode_results[mode].append(result)
                if mode == "current":
                    current_meta.append((c.day, c.bar_ts, float(result.get("pnl") or 0.0), result["result"]))

        cell_summary = {mode: _summarize_results(vals) for mode, vals in by_mode_results.items()}
        current = cell_summary.get("current")
        if current is None or current["cases"] < MIN_CELL_N:
            continue

        current_meta.sort(key=lambda m: (m[0], m[1]))
        filled_pnls_chrono = [m[2] for m in current_meta if m[3] in ("WIN", "LOSS")]
        outlier_share = _outlier_share(filled_pnls_chrono)
        max_dd = _max_drawdown(filled_pnls_chrono)

        half_groups: dict[str, list[dict]] = defaultdict(list)
        for day, _bar_ts, pnl, result in current_meta:
            if result in ("WIN", "LOSS"):
                half_groups[halves.get(day, "?")].append({"result": result, "pnl": pnl, "filled": True})
        half_stats = {h: _summarize_results(v) for h, v in half_groups.items()}
        qualifying_halves = [s for s in half_stats.values() if s["cases"] >= MIN_HALF_CELL_N]
        walk_forward_consistent = len(qualifying_halves) >= 2 and all((s["net_pnl"] or 0) > 0 for s in qualifying_halves)

        best_mode = max(
            cell_summary.items(),
            key=lambda kv: (kv[1]["expectancy"] if kv[1]["expectancy"] is not None else float("-inf")),
        )[0]

        out["|".join(key)] = {
            "instrument": instrument,
            "strategy": strategy,
            "gate": gate,
            "source": source,
            "session": session,
            "direction": direction,
            "by_target_mode": cell_summary,
            "best_mode": best_mode,
            "outlier_share_current": outlier_share,
            "max_drawdown_current": max_dd,
            "half_stats_current": half_stats,
            "walk_forward_consistent": walk_forward_consistent,
            "outlier_dependent": outlier_share is not None and abs(outlier_share) > OUTLIER_SHARE_THRESHOLD,
        }
    return out


def select_coherent_candidate(cells: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A cell qualifies as a coherent, describable trend-modifier candidate
    only if all of: adequate sample (already enforced by MIN_CELL_N above),
    walk-forward consistent, not outlier-dependent, and its best
    target-variant mode is both positive and strictly better than the
    current-target expectancy (i.e. the modifier is actually an improvement,
    not just 'less bad')."""
    survivors = []
    for key, cell in cells.items():
        cur = cell["by_target_mode"]["current"]["expectancy"]
        best = cell["by_target_mode"][cell["best_mode"]]["expectancy"]
        if best is None or best <= 0:
            continue
        if cur is not None and best <= cur:
            continue
        if not cell["walk_forward_consistent"]:
            continue
        if cell["outlier_dependent"]:
            continue
        survivors.append((key, cell, best))
    if not survivors:
        return None
    survivors.sort(key=lambda s: -s[2])
    key, cell, best = survivors[0]
    return {"key": key, "best_expectancy": best, **cell}


def _fmt_money(v: Any) -> str:
    return "n/a" if v is None else f"${float(v):,.2f}"


def _fmt_pct(v: Any) -> str:
    return "n/a" if v is None else f"{100 * float(v):.1f}%"


def _cell_table_row(key: str, cell: dict[str, Any]) -> str:
    cur = cell["by_target_mode"]["current"]
    best = cell["by_target_mode"][cell["best_mode"]]
    return (
        f"| {cell['instrument']} | {cell['strategy']} | {cell['gate']} | {cell['source']} | "
        f"{cell['session']} | {cell['direction']} | {cur['cases']} | {_fmt_pct(cur['win_rate'])} | "
        f"{_fmt_money(cur['expectancy'])} | {cell['best_mode']} | {_fmt_money(best['expectancy'])} | "
        f"{cell['walk_forward_consistent']} | {_fmt_pct(cell['outlier_share_current'])} | "
        f"{_fmt_money(cell['max_drawdown_current'])} |"
    )


def write_report(trend_cells: dict[str, Any], non_trend_cells: dict[str, Any], selected: Optional[dict[str, Any]]) -> None:
    lines = [
        "# Trend-Modifier Candidate Isolation — Is There One Coherent Rule to Test?",
        "",
        "Follow-up to `docs/mes-mnq-mechanical-research-2026-07-09.md`'s `TREND_MODIFIER_CANDIDATE` "
        "cells, which were grouped only by (instrument, gate, source) — pooled across every shadow "
        "strategy that hit that gate, and including non-trend gates. This re-groups the same "
        "already-collected candidates at instrument x strategy x gate x session x direction grain, "
        "restricted to genuine trend gates for the actual candidate question. No new candidate "
        "collection or fill-simulation logic — reuses `collect_blocked_candidates`/`_simulate`/"
        "`_target_for` from `scripts/mes_mnq_mechanical_research.py` by import.",
        "",
        f"Reliability bar: every reported cell already clears `MIN_CELL_N={MIN_CELL_N}` on the "
        "current-target mode (cells below that are dropped entirely, not shown as weak candidates). "
        "A cell only qualifies as a coherent candidate if it is ALSO walk-forward consistent "
        f"(positive in both halves, each half itself >= {MIN_HALF_CELL_N} cases), not "
        f"outlier-dependent (top-3 trades <= {int(100*OUTLIER_SHARE_THRESHOLD)}% of net), and its "
        "best target-variant mode is both positive and strictly better than the current-target "
        "expectancy.",
        "",
    ]

    if selected is None:
        lines.extend([
            "## Verdict: NO SINGLE COHERENT CANDIDATE SURVIVES",
            "",
            "Do not build `proof_builder`/`demo_proof` yet. No cell in the trend-gate population "
            "below clears all four bars (sample size, walk-forward consistency, low outlier "
            "dependence, target-variant improvement) at once. Any cell shown below that fails one "
            "or more of those checks is listed for transparency, not as a fallback candidate.",
        ])
    else:
        cur = selected["by_target_mode"]["current"]
        best = selected["by_target_mode"][selected["best_mode"]]
        lines.extend([
            "## Verdict: ONE CANDIDATE SURVIVES",
            "",
            f"**{selected['instrument']} | `{selected['strategy']}` | blocked by `{selected['gate']}` "
            f"| {selected['session']} | {selected['direction']} | source={selected['source']}**",
            "",
            f"- Current target: n={cur['cases']}, WR={_fmt_pct(cur['win_rate'])}, "
            f"exp={_fmt_money(cur['expectancy'])}",
            f"- Best mode ({selected['best_mode']}): exp={_fmt_money(best['expectancy'])}, "
            f"WR={_fmt_pct(best['win_rate'])}",
            f"- Walk-forward consistent: {selected['walk_forward_consistent']}, "
            f"outlier share: {_fmt_pct(selected['outlier_share_current'])}, "
            f"max drawdown: {_fmt_money(selected['max_drawdown_current'])}",
            "",
            "**Describable rule**: "
            f"when `{selected['strategy']}` forms a {selected['direction']} setup on {selected['instrument']} "
            f"during {selected['session']} that is blocked only by `{selected['gate']}`, allow it with a "
            f"`{selected['best_mode']}` target instead of the strategy's structural target.",
            "",
            "This is evidence to scope a future `proof_builder`/`demo_proof` increment around — "
            "it is not itself a behavior change, and nothing here promotes it to `locked_current`.",
        ])

    lines.extend([
        "",
        "## Trend-gate cells (candidate-selection population)",
        "",
        "| instrument | strategy | gate | source | session | direction | n | WR (current) | "
        "exp (current) | best mode | exp (best) | WF consistent | outlier share | max DD |",
        "|---|---|---|---|---|---|---:|---:|---:|---|---:|---|---:|---:|",
    ])
    for key, cell in sorted(trend_cells.items()):
        lines.append(_cell_table_row(key, cell))

    lines.extend([
        "",
        "## Non-trend-gate cells (informational only — NOT used as trend-modifier proof)",
        "",
        "Excluded from candidate selection per the operator's explicit instruction: gates like "
        "`SIGNAL_BAR_VOLUME_TOO_LOW` are not trend gates, so evidence there doesn't answer the "
        "trend-as-modifier question, however large the sample.",
        "",
        "| instrument | strategy | gate | source | session | direction | n | WR (current) | "
        "exp (current) | best mode | exp (best) | WF consistent | outlier share | max DD |",
        "|---|---|---|---|---|---|---:|---:|---:|---|---:|---|---:|---:|",
    ])
    for key, cell in sorted(non_trend_cells.items()):
        lines.append(_cell_table_row(key, cell))

    lines.extend([
        "",
        "## Notes",
        "",
        "- This is docs/script/tests only — zero changes to execution/, risk/, config/, "
        "risk_rules.yaml, webhook/, broker*, or strategy/. No proof_builder or demo_proof "
        "architecture is built or scoped in code here.",
        "- If a candidate survives above, the next step is scoping the proof_builder/demo_proof "
        "increment around that single cell specifically — per the operator's own instruction, "
        "single-demo-account operation (locked_current stopped, one runner active, verified flat "
        "before boot, TRADOVATE_ENV=demo verified, live rejected, demo_proof-tagged journal rows, "
        "explicit rollback path), not a second Tradovate account.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    all_candidates = collect_blocked_candidates()
    trend_candidates = [c for c in all_candidates if c.gate in TREND_GATES]
    non_trend_candidates = [c for c in all_candidates if c.gate not in TREND_GATES]

    trend_cells = analyze_fine_grained_cells(trend_candidates)
    non_trend_cells = analyze_fine_grained_cells(non_trend_candidates)
    selected = select_coherent_candidate(trend_cells)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "trend_cells": trend_cells,
                "non_trend_cells": non_trend_cells,
                "selected_candidate": selected,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    write_report(trend_cells, non_trend_cells, selected)
    print(f"trend-gate cells clearing MIN_CELL_N: {len(trend_cells)}")
    print(f"non-trend-gate cells clearing MIN_CELL_N: {len(non_trend_cells)}")
    print(f"coherent candidate found: {selected is not None}")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
