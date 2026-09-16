#!/usr/bin/env python3
"""Supplemental read-only why-no-trade chain report from journal JSONL.

The repository already has ``ops/strategy_intent_audit.py`` for detailed
candidate intent and ``scripts/session_audit.py`` for session forensics. This
script does not replace either one. It fills the specific structural-mismatch
visibility gap by putting these already-journaled fields on one per-bar chain:

    Pine market condition -> structural observation -> regime -> candidate
    sources -> selected setup -> failed gate / primary rejection

``candidate_audit`` and ``shadow_candidates`` remain separate sources. The
structural classifier remains observation-only; this reporter never promotes or
substitutes it for the Pine label and never imports trading runtime modules.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_STRUCTURAL_TRENDS = {"STRUCTURAL_TREND_UP", "STRUCTURAL_TREND_DOWN"}


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_filter_dt(value: str | None, *, label: str) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_dt(value)
    if parsed is None:
        raise ValueError(f"{label} must be a valid ISO-8601 timestamp")
    return parsed


def _load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{lineno}: journal row must be a JSON object")
                rows.append(value)
    return rows


def _expand_inputs(values: Iterable[str]) -> list[Path]:
    paths: dict[str, Path] = {}
    for raw in values:
        path = Path(raw)
        matches = sorted(path.glob("journal_*.jsonl")) if path.is_dir() else [path]
        for match in matches:
            paths[str(match)] = match
    return [paths[key] for key in sorted(paths)]


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "strategy": candidate.get("strategy"),
            "direction": candidate.get("candidate_direction") or candidate.get("direction"),
            "direction_role": candidate.get("direction_role"),
            "selected": candidate.get("selected"),
            "attempted": candidate.get("attempted"),
            "reject_code": candidate.get("reject_code"),
            "reject_reason": candidate.get("reject_reason"),
        }.items()
        if value is not None
    }


def _candidate_source(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = row.get(key)
    if not isinstance(value, list):
        return []
    return [_candidate_summary(item) for item in value if isinstance(item, dict)]


def _primary_rejection(row: dict[str, Any]) -> str | None:
    failed = row.get("failed_gates")
    if isinstance(failed, list) and failed:
        return str(failed[-1])
    risk = row.get("risk_check")
    if isinstance(risk, dict) and risk.get("failed_rule"):
        return str(risk["failed_rule"])
    reason = str(row.get("reason") or "").strip()
    return reason or None


def decision_bar(row: dict[str, Any]) -> dict[str, Any]:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    setup = row.get("setup") if isinstance(row.get("setup"), dict) else None
    decision_candidates = _candidate_source(row, "candidate_audit")
    shadow_candidates = _candidate_source(row, "shadow_candidates")
    pine = row.get("market_condition") or context.get("market_condition")
    failed = row.get("failed_gates") if isinstance(row.get("failed_gates"), list) else []
    risk = row.get("risk_check") if isinstance(row.get("risk_check"), dict) else None
    return {
        "ts": row.get("ts"),
        "instrument": row.get("instrument") or context.get("instrument"),
        "session": row.get("session") or context.get("session"),
        "timeframe_minutes": row.get("timeframe_minutes"),
        "decision": row.get("decision"),
        "reason": row.get("reason"),
        "market_condition": pine,
        "structural_market_condition": context.get("structural_market_condition"),
        "structural_direction": context.get("structural_direction"),
        "structural_mismatch": context.get("structural_mismatch"),
        "structural_gate_authoritative": context.get("structural_gate_authoritative"),
        "regime": row.get("regime"),
        "failed_gates": failed,
        "primary_rejection": _primary_rejection(row),
        "candidate_sources": {
            "decision": decision_candidates,
            "shadow": shadow_candidates,
        },
        "decision_candidate_count": len(decision_candidates),
        "shadow_candidate_count": len(shadow_candidates),
        "candidate_record_count": len(decision_candidates) + len(shadow_candidates),
        "selected_setup": (
            {
                key: setup.get(key)
                for key in (
                    "strategy",
                    "direction",
                    "direction_role",
                    "entry",
                    "stop",
                    "target",
                )
                if setup.get(key) is not None
            }
            if setup is not None
            else None
        ),
        "risk_check": risk,
    }


def build_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    bars = [
        decision_bar(row)
        for row in rows
        if row.get("decision") is not None and row.get("type") is None
    ]
    decisions = Counter(str(bar.get("decision") or "MISSING") for bar in bars)
    conditions = Counter(str(bar.get("market_condition") or "MISSING") for bar in bars)
    structures = Counter(
        str(bar.get("structural_market_condition") or "MISSING") for bar in bars
    )
    regimes = Counter(str(bar.get("regime") or "MISSING") for bar in bars)
    gates: Counter[str] = Counter()
    primary_rejections: Counter[str] = Counter()
    decision_strategies: Counter[str] = Counter()
    shadow_strategies: Counter[str] = Counter()
    selected_strategies: Counter[str] = Counter()
    bars_with_decision_candidates = 0
    bars_with_shadow_candidates = 0
    bars_with_any_candidate = 0
    structural_trend_bars = 0
    structural_trend_pine_nontrending = 0
    mismatch_true = 0
    unexpected_structural_authority = 0

    for bar in bars:
        for gate in bar["failed_gates"]:
            gates[str(gate)] += 1
        rejection = bar.get("primary_rejection")
        if rejection:
            primary_rejections[str(rejection)] += 1

        sources = bar["candidate_sources"]
        if sources["decision"]:
            bars_with_decision_candidates += 1
        if sources["shadow"]:
            bars_with_shadow_candidates += 1
        if sources["decision"] or sources["shadow"]:
            bars_with_any_candidate += 1
        for candidate in sources["decision"]:
            if candidate.get("strategy"):
                decision_strategies[str(candidate["strategy"])] += 1
        for candidate in sources["shadow"]:
            if candidate.get("strategy"):
                shadow_strategies[str(candidate["strategy"])] += 1
        setup = bar.get("selected_setup")
        if isinstance(setup, dict) and setup.get("strategy"):
            selected_strategies[str(setup["strategy"])] += 1

        structural = bar.get("structural_market_condition")
        if structural in _STRUCTURAL_TRENDS:
            structural_trend_bars += 1
            if bar.get("market_condition") != "TRENDING":
                structural_trend_pine_nontrending += 1
        if bar.get("structural_mismatch") is True:
            mismatch_true += 1
        if bar.get("structural_gate_authoritative") is True:
            unexpected_structural_authority += 1

    return {
        "authority": "journal_read_only",
        "supplements_existing_audits": [
            "ops/strategy_intent_audit.py",
            "scripts/session_audit.py",
        ],
        "candidate_sources_preserved": True,
        "structural_gate_expected_authoritative": False,
        "decision_bars": len(bars),
        "summary": {
            "decisions": dict(sorted(decisions.items())),
            "market_conditions": dict(sorted(conditions.items())),
            "structural_conditions": dict(sorted(structures.items())),
            "regimes": dict(sorted(regimes.items())),
            "failed_gates": dict(sorted(gates.items())),
            "primary_rejections": dict(sorted(primary_rejections.items())),
            "decision_candidate_strategy_records": dict(
                sorted(decision_strategies.items())
            ),
            "shadow_candidate_strategy_records": dict(sorted(shadow_strategies.items())),
            "selected_strategy_rows": dict(sorted(selected_strategies.items())),
            "bars_with_decision_candidates": bars_with_decision_candidates,
            "bars_with_shadow_candidates": bars_with_shadow_candidates,
            "bars_with_any_candidate": bars_with_any_candidate,
            "bars_without_any_candidate": len(bars) - bars_with_any_candidate,
            "structural_mismatch_true": mismatch_true,
            "structural_trend_bars": structural_trend_bars,
            "structural_trend_pine_nontrending": structural_trend_pine_nontrending,
            "unexpected_structural_authority_true": unexpected_structural_authority,
        },
        "bars": bars,
    }


def _filter_rows(
    rows: Iterable[dict[str, Any]],
    *,
    instrument: str | None,
    timeframe: int | None,
    start: datetime | None,
    end: datetime | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    wanted = instrument.upper() if instrument else None
    for row in rows:
        if row.get("decision") is None or row.get("type") is not None:
            continue
        context = row.get("context") if isinstance(row.get("context"), dict) else {}
        symbol = str(row.get("instrument") or context.get("instrument") or "").upper()
        if wanted and symbol != wanted:
            continue
        if timeframe is not None:
            try:
                row_timeframe = int(row.get("timeframe_minutes"))
            except (TypeError, ValueError):
                continue
            if row_timeframe != timeframe:
                continue
        if start is not None or end is not None:
            ts = _parse_dt(row.get("ts"))
            if ts is None:
                raise ValueError("decision row has invalid timestamp inside a time-filtered audit")
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Journal JSONL file(s) or directories containing journal_*.jsonl",
    )
    parser.add_argument("--instrument", help="Optional exact instrument/root, e.g. MNQ")
    parser.add_argument(
        "--timeframe", type=int, help="Optional exact timeframe_minutes filter"
    )
    parser.add_argument("--start", help="Optional inclusive ISO-8601 UTC/start timestamp")
    parser.add_argument("--end", help="Optional inclusive ISO-8601 UTC/end timestamp")
    parser.add_argument(
        "--json", action="store_true", help="Print full JSON including per-bar chain"
    )
    parser.add_argument("--out", help="Optional path for full JSON report")
    args = parser.parse_args(argv)

    try:
        paths = _expand_inputs(args.inputs)
        if not paths:
            raise ValueError("no journal_*.jsonl files found in supplied inputs")
        start = _parse_filter_dt(args.start, label="--start")
        end = _parse_filter_dt(args.end, label="--end")
        if start is not None and end is not None and start > end:
            raise ValueError("--start must be before or equal to --end")
        rows = _load_jsonl(paths)
        report = build_report(
            _filter_rows(
                rows,
                instrument=args.instrument,
                timeframe=args.timeframe,
                start=start,
                end=end,
            )
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        summary = report["summary"]
        print(
            "WHY-NO-TRADE: "
            f"bars={report['decision_bars']} "
            f"with_any_candidate={summary['bars_with_any_candidate']} "
            f"without_any_candidate={summary['bars_without_any_candidate']} "
            f"structural_mismatch={summary['structural_mismatch_true']} "
            f"structural_trend_vs_nontrending_pine="
            f"{summary['structural_trend_pine_nontrending']}"
        )
        print("decisions=" + json.dumps(summary["decisions"], sort_keys=True))
        print("market_conditions=" + json.dumps(summary["market_conditions"], sort_keys=True))
        print("regimes=" + json.dumps(summary["regimes"], sort_keys=True))
        print("failed_gates=" + json.dumps(summary["failed_gates"], sort_keys=True))
        print(
            "primary_rejections="
            + json.dumps(summary["primary_rejections"], sort_keys=True)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
