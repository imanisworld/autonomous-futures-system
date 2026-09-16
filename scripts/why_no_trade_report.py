#!/usr/bin/env python3
"""Read-only why-no-trade report from authoritative journal JSONL.

This is a reporting tool only. It does not import or call strategy, risk,
broker, execution, or webhook runtime code. It summarizes the exact fields the
runner already persisted so an operator can see, per decision bar:

    Pine market condition -> structural observation -> regime -> candidates
    -> selected setup -> failed gate / decision reason

It is intentionally descriptive. ``structural_market_condition`` remains
observation-only and is never promoted or substituted for the Pine label here.
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


def _load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def _expand_inputs(values: Iterable[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in values:
        path = Path(raw)
        if path.is_dir():
            paths.extend(sorted(path.glob("journal_*.jsonl")))
        else:
            paths.append(path)
    return paths


def _candidate_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("candidate_audit", "shadow_candidates"):
        value = row.get(key)
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, dict))
    return out


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: candidate.get(key)
        for key in (
            "strategy",
            "direction",
            "direction_role",
            "selected",
            "attempted",
            "reject_code",
            "reject_reason",
        )
        if candidate.get(key) is not None
    }


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
    candidates = _candidate_rows(row)
    pine = row.get("market_condition") or context.get("market_condition")
    structural = context.get("structural_market_condition")
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
        "structural_market_condition": structural,
        "structural_direction": context.get("structural_direction"),
        "structural_mismatch": context.get("structural_mismatch"),
        "structural_gate_authoritative": context.get("structural_gate_authoritative"),
        "regime": row.get("regime"),
        "failed_gates": failed,
        "primary_rejection": _primary_rejection(row),
        "candidate_count": len(candidates),
        "candidates": [_candidate_summary(item) for item in candidates],
        "selected_setup": (
            {
                key: setup.get(key)
                for key in ("strategy", "direction", "direction_role", "entry", "stop", "target")
                if setup.get(key) is not None
            }
            if setup is not None
            else None
        ),
        "risk_check": risk,
    }


def build_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    bars = [decision_bar(row) for row in rows if row.get("decision") is not None and row.get("type") is None]
    decisions = Counter(str(bar.get("decision") or "MISSING") for bar in bars)
    conditions = Counter(str(bar.get("market_condition") or "MISSING") for bar in bars)
    structures = Counter(str(bar.get("structural_market_condition") or "MISSING") for bar in bars)
    regimes = Counter(str(bar.get("regime") or "MISSING") for bar in bars)
    gates: Counter[str] = Counter()
    strategies: Counter[str] = Counter()
    candidate_bars = 0
    structural_trend_bars = 0
    structural_trend_pine_nontrending = 0
    mismatch_true = 0

    for bar in bars:
        for gate in bar["failed_gates"]:
            gates[str(gate)] += 1
        if bar["candidate_count"]:
            candidate_bars += 1
        for candidate in bar["candidates"]:
            strategy = candidate.get("strategy")
            if strategy:
                strategies[str(strategy)] += 1
        setup = bar.get("selected_setup")
        if isinstance(setup, dict) and setup.get("strategy"):
            strategies[str(setup["strategy"])] += 1
        structural = bar.get("structural_market_condition")
        if structural in _STRUCTURAL_TRENDS:
            structural_trend_bars += 1
            if bar.get("market_condition") != "TRENDING":
                structural_trend_pine_nontrending += 1
        if bar.get("structural_mismatch") is True:
            mismatch_true += 1

    return {
        "authority": "journal_read_only",
        "structural_gate_authoritative": False,
        "decision_bars": len(bars),
        "summary": {
            "decisions": dict(sorted(decisions.items())),
            "market_conditions": dict(sorted(conditions.items())),
            "structural_conditions": dict(sorted(structures.items())),
            "regimes": dict(sorted(regimes.items())),
            "failed_gates": dict(sorted(gates.items())),
            "candidate_strategies": dict(sorted(strategies.items())),
            "bars_with_candidates": candidate_bars,
            "bars_without_candidates": len(bars) - candidate_bars,
            "structural_mismatch_true": mismatch_true,
            "structural_trend_bars": structural_trend_bars,
            "structural_trend_pine_nontrending": structural_trend_pine_nontrending,
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
        if timeframe is not None and row.get("timeframe_minutes") != timeframe:
            continue
        ts = _parse_dt(row.get("ts"))
        if start is not None and (ts is None or ts < start):
            continue
        if end is not None and (ts is None or ts > end):
            continue
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="Journal JSONL file(s) or directories containing journal_*.jsonl")
    parser.add_argument("--instrument", help="Optional exact instrument/root, e.g. MNQ")
    parser.add_argument("--timeframe", type=int, help="Optional exact timeframe_minutes filter")
    parser.add_argument("--start", help="Optional inclusive ISO-8601 UTC/start timestamp")
    parser.add_argument("--end", help="Optional inclusive ISO-8601 UTC/end timestamp")
    parser.add_argument("--json", action="store_true", help="Print full JSON including per-bar chain")
    parser.add_argument("--out", help="Optional path for full JSON report")
    args = parser.parse_args(argv)

    paths = _expand_inputs(args.inputs)
    rows = _load_jsonl(paths)
    report = build_report(
        _filter_rows(
            rows,
            instrument=args.instrument,
            timeframe=args.timeframe,
            start=_parse_dt(args.start),
            end=_parse_dt(args.end),
        )
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        summary = report["summary"]
        print(
            "WHY-NO-TRADE: "
            f"bars={report['decision_bars']} "
            f"with_candidates={summary['bars_with_candidates']} "
            f"without_candidates={summary['bars_without_candidates']} "
            f"structural_mismatch={summary['structural_mismatch_true']} "
            f"structural_trend_vs_nontrending_pine={summary['structural_trend_pine_nontrending']}"
        )
        print("decisions=" + json.dumps(summary["decisions"], sort_keys=True))
        print("market_conditions=" + json.dumps(summary["market_conditions"], sort_keys=True))
        print("regimes=" + json.dumps(summary["regimes"], sort_keys=True))
        print("failed_gates=" + json.dumps(summary["failed_gates"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
