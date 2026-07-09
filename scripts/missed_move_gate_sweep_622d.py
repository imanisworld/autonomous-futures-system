#!/usr/bin/env python3
"""Missed-move / NO_TRADE gate-classification sweep — full 622-day replay set.

Answers a question the entry-fill sweeps can't: when a real, large directional
move happened, did the bot even form a candidate, or did a gate block
something that would have worked? Purely observational — reads decision rows
the box's own decision engine already wrote (failed_gates, shadow_candidates),
does not invent new signal/setup-detection logic. Large-move detection is
plain price-range math over candles, not strategy logic.

Read-only, docs-only output. No changes to execution/, risk/, config/,
webhook/, broker*, or strategy/.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from replay.candle_loader import ReplayCandle, ReplayCandleLoader

JOURNAL_ROOT = REPO / "logs/replay_622d_market_static"
CANDLE_ROOT = REPO / "data/replay_polygon"
OUT_MD = REPO / "docs/missed-move-gate-sweep-622d-2026-07-09.md"
OUT_JSON = REPO / "logs/missed_move_gate_sweep_622d.json"

# Large-move detection: non-overlapping N-bar blocks per day, flagged when the
# block's high-low range meets or exceeds the per-instrument point threshold.
# Both are CLI-overridable defaults, not hardcoded fact — roughly one typical
# bracket-target size, from sampled TRADE rows seen during exploration.
DEFAULT_WINDOW_BARS = 4
DEFAULT_THRESHOLD_POINTS = {"MES": 15.0, "MNQ": 60.0}

# Mechanical mapping from the CLOSED, confirmed failed_gates vocabulary (see
# plan doc) to gate categories. An unrecognized future code is NOT silently
# bucketed into "other" — see _gate_category().
GATE_TAXONOMY = {
    "REGIME_NOT_FULL": "regime_or_structure",
    "WEAK_BAR_CLOSE": "regime_or_structure",
    "MARKET_CONDITION_NOT_TRENDING": "trend_condition",
    "MARKET_CONDITION_NOT_TRADABLE": "trend_condition",
    "TREND_STRENGTH_BELOW_REQUIRED": "trend_condition",
    "REGIME_RESTRICTED": "trend_condition",
    "EMA_STACK_NOT_ALIGNED": "trend_condition",
    "EMA_STACK_NOT_ALIGNED_SOFT": "trend_condition",
    "ENTRY_DETACHED_FROM_PRICE": "entry_mechanics",
    "SIGNAL_BAR_VOLUME_TOO_LOW": "volume",
    "NY_SESSION_WINDOW": "session",
    "STRAT_DIRECTION_CONFLICT": "other",
    "HTF_ALIGNMENT_FAIL": "other",
}
STRUCTURE_GATES = {"REGIME_NOT_FULL", "WEAK_BAR_CLOSE"}


@dataclass
class MoveWindow:
    instrument: str
    day: str
    start_ts: str
    end_ts: str
    range_points: float
    bar_ts_set: set = field(default_factory=set)


@dataclass
class RowClassification:
    instrument: str
    day: str
    bar_ts: str
    classification: str
    gate: Optional[str] = None
    gate_category: Optional[str] = None
    strategy: Optional[str] = None


def _gate_category(gate: str) -> str:
    cat = GATE_TAXONOMY.get(gate)
    if cat is None:
        raise ValueError(
            f"Unrecognized failed_gates code {gate!r} — not in the confirmed vocabulary. "
            "Add it to GATE_TAXONOMY deliberately rather than silently bucketing as 'other'."
        )
    return cat


def _instruments() -> list[str]:
    return ["MES", "MNQ"]


def _candle_days(instrument: str) -> list[str]:
    days = []
    for p in sorted((CANDLE_ROOT / instrument).glob(f"{instrument}_*.jsonl")):
        days.append(p.stem.split("_", 1)[1])
    return days


def find_move_windows(
    instrument: str,
    day: str,
    window_bars: int = DEFAULT_WINDOW_BARS,
    threshold_points: Optional[dict[str, float]] = None,
) -> list[MoveWindow]:
    """Non-overlapping N-bar blocks; range = max(high) - min(low) over the
    block. Non-overlapping (not rolling) so a single move isn't counted many
    times over many shifted windows."""
    threshold_points = threshold_points or DEFAULT_THRESHOLD_POINTS
    threshold = threshold_points.get(instrument, 15.0)
    path = CANDLE_ROOT / instrument / f"{instrument}_{day}.jsonl"
    if not path.exists():
        return []
    candles = ReplayCandleLoader().load_jsonl(path)
    windows: list[MoveWindow] = []
    for i in range(0, len(candles), window_bars):
        block = candles[i:i + window_bars]
        if len(block) < 2:
            continue
        hi = max(c.high for c in block)
        lo = min(c.low for c in block)
        rng = hi - lo
        if rng >= threshold:
            windows.append(
                MoveWindow(
                    instrument=instrument,
                    day=day,
                    start_ts=block[0].timestamp,
                    end_ts=block[-1].timestamp,
                    range_points=round(rng, 2),
                    bar_ts_set={c.timestamp for c in block},
                )
            )
    return windows


def _load_day_rows(instrument: str, day: str) -> list[dict[str, Any]]:
    path = JOURNAL_ROOT / instrument / f"journal_{day}.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _pair_outcomes(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map a TRADE decision row's bar_ts -> its resolving OUTCOME row.
    Single-position-per-instrument model, chronological — same convention as
    scripts/fill_realism_report.py's pair_journal(), scoped to one day file."""
    pairs: dict[str, dict[str, Any]] = {}
    pending_bar_ts: Optional[str] = None
    for row in rows:
        if row.get("decision") == "TRADE":
            pending_bar_ts = row.get("bar_ts")
        elif row.get("type") == "OUTCOME" and pending_bar_ts is not None:
            pairs[pending_bar_ts] = row
            pending_bar_ts = None
    return pairs


def classify_windows(instrument: str, day: str, windows: list[MoveWindow]) -> list[RowClassification]:
    if not windows:
        return []
    rows = _load_day_rows(instrument, day)
    by_bar_ts: dict[str, dict[str, Any]] = {}
    for row in rows:
        bt = row.get("bar_ts")
        if bt and row.get("decision") in ("TRADE", "NO_TRADE"):
            by_bar_ts[bt] = row
    outcome_by_bar_ts = _pair_outcomes(rows)

    all_window_bar_ts: set = set()
    for w in windows:
        all_window_bar_ts |= w.bar_ts_set

    out: list[RowClassification] = []
    for bar_ts in sorted(all_window_bar_ts):
        row = by_bar_ts.get(bar_ts)
        if row is None:
            out.append(RowClassification(instrument, day, bar_ts, "NO_ROW_LOGGED"))
            continue
        strategy = (row.get("setup") or {}).get("strategy")
        if row.get("decision") == "TRADE":
            outcome = outcome_by_bar_ts.get(bar_ts)
            if outcome and (outcome.get("outcome") or {}).get("result") == "CANCELLED":
                out.append(RowClassification(instrument, day, bar_ts, "DETECTED_BUT_NO_FILL", strategy=strategy))
            else:
                out.append(RowClassification(instrument, day, bar_ts, "DETECTED_AND_TRADED", strategy=strategy))
            continue
        gates = row.get("failed_gates") or []
        if not gates:
            out.append(RowClassification(instrument, day, bar_ts, "NO_GATE_LOGGED"))
            continue
        gate = gates[0]
        category = _gate_category(gate)
        if category == "regime_or_structure":
            has_shadow = bool(row.get("shadow_candidates"))
            label = "STRUCTURE_PRESENT_BUT_NOT_QUALIFIED" if has_shadow else "NO_COVERED_STRUCTURE_PRESENT"
        else:
            label = "DETECTED_BUT_BLOCKED"
        out.append(RowClassification(instrument, day, bar_ts, label, gate=gate, gate_category=category, strategy=strategy))
    return out


def _write_report(classifications: list[RowClassification], total_windows: int, window_bars: int, thresholds: dict) -> None:
    from collections import Counter

    label_counts = Counter(c.classification for c in classifications)
    gate_counts = Counter(c.gate for c in classifications if c.gate)
    category_counts = Counter(c.gate_category for c in classifications if c.gate_category)

    total_rows = len(classifications)
    lines = [
        "# Missed-Move / NO_TRADE Gate-Classification Sweep — 622-Day Replay Set",
        "",
        f"Scope: {total_windows} large-move windows found across the full 622-day replay set "
        f"(2024-07-01 to 2026-06-26, both instruments), non-overlapping {window_bars}-bar blocks, "
        f"thresholds MES>={thresholds['MES']}pt / MNQ>={thresholds['MNQ']}pt (CLI-overridable "
        "defaults). For every 15m bar inside a flagged window, this reads the box's own "
        "already-computed decision row (`failed_gates`, `shadow_candidates`) — no new "
        "signal-detection or setup logic is invented here.",
        "",
        f"Total classified bars across all move windows: {total_rows}",
        "",
        "## Classification breakdown",
        "",
        "| classification | count | % |",
        "|---|---:|---:|",
    ]
    for label, count in label_counts.most_common():
        pct = 100 * count / total_rows if total_rows else 0
        lines.append(f"| {label} | {count} | {pct:.1f}% |")

    lines.extend(["", "## Gate-category breakdown (DETECTED_BUT_BLOCKED rows)", "", "| category | count |", "|---|---:|"])
    for cat, count in category_counts.most_common():
        lines.append(f"| {cat} | {count} |")

    lines.extend(["", "## Individual gate-code breakdown", "", "| gate | count |", "|---|---:|"])
    for gate, count in gate_counts.most_common():
        lines.append(f"| {gate} | {count} |")

    lines.extend([
        "",
        "## Examples per classification",
        "",
        "| instrument | day | bar_ts | classification | gate | strategy |",
        "|---|---|---|---|---|---|",
    ])
    seen_per_label: dict[str, int] = {}
    for c in classifications:
        if seen_per_label.get(c.classification, 0) >= 5:
            continue
        seen_per_label[c.classification] = seen_per_label.get(c.classification, 0) + 1
        lines.append(
            f"| {c.instrument} | {c.day} | {c.bar_ts} | {c.classification} | {c.gate or ''} | {c.strategy or ''} |"
        )

    verdict = "INSUFFICIENT_DATA" if total_rows < 30 else None
    if verdict is None:
        no_cov = label_counts.get("NO_COVERED_STRUCTURE_PRESENT", 0)
        struct_present = label_counts.get("STRUCTURE_PRESENT_BUT_NOT_QUALIFIED", 0)
        traded = label_counts.get("DETECTED_AND_TRADED", 0)
        no_fill = label_counts.get("DETECTED_BUT_NO_FILL", 0)
        if struct_present > no_cov and struct_present > (traded + no_fill):
            verdict = "OVERFILTERED"
        elif no_cov > struct_present and no_cov > (traded + no_fill):
            verdict = "NO_COVERED_STRUCTURE_DOMINANT"
        else:
            verdict = "MIXED"

    lines.extend([
        "",
        f"## Overall verdict: `{verdict}`",
        "",
        "Reading: `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` means the box's own shadow-evaluation "
        "layer recognized a structural candidate at that bar even though the live decision was "
        "NO_TRADE — this is the closest observable signal to \"a real setup existed and a gate "
        "blocked it,\" but it is not proof the shadow candidate would have won; it only says a "
        "structure was present. `NO_COVERED_STRUCTURE_PRESENT` means not even the shadow layer "
        "saw anything — the weaker, more honest reading of \"detection gap\" than asserting a "
        "specific missing strategy.",
        "",
        "## Notes",
        "",
        "- `shadow_candidates` presence is used as the structure-presence signal (not "
        "`context.orb`/`context.vwap` fields, which the replay journal writer does not "
        "populate — confirmed 0/84 in a sampled day; only the live box's journal writer "
        "includes the richer `context` block).",
        "- Gate-category taxonomy is a closed, mechanical mapping from the confirmed "
        "`failed_gates` vocabulary; an unrecognized future gate code raises loudly instead of "
        "silently landing in `other`.",
        "- This is docs/script/tests only — zero changes to execution/, risk/, config/, "
        "risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo "
        "orders, no strategy promotion or demotion.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    all_windows: list[MoveWindow] = []
    all_classifications: list[RowClassification] = []
    for instrument in _instruments():
        for day in _candle_days(instrument):
            windows = find_move_windows(instrument, day)
            if not windows:
                continue
            all_windows.extend(windows)
            all_classifications.extend(classify_windows(instrument, day, windows))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "total_windows": len(all_windows),
                "window_bars": DEFAULT_WINDOW_BARS,
                "thresholds": DEFAULT_THRESHOLD_POINTS,
                "classifications": [c.__dict__ for c in all_classifications],
            },
            indent=2,
            default=lambda o: list(o) if isinstance(o, set) else o,
        ),
        encoding="utf-8",
    )
    _write_report(all_classifications, len(all_windows), DEFAULT_WINDOW_BARS, DEFAULT_THRESHOLD_POINTS)
    print(f"Found {len(all_windows)} large-move windows, classified {len(all_classifications)} bars")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
