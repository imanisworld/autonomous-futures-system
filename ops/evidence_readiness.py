"""Unified, read-only readiness audit for strategy research evidence.

The audit reads journal/artifact files only.  It never runs collectors, changes
configuration, promotes a strategy, or participates in trade decisions.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


STATUSES = {
    "NOT INSTALLED",
    "DISABLED",
    "NOT COLLECTING",
    "COLLECTING",
    "INSUFFICIENT SAMPLE",
    "READY FOR REVIEW",
    "DATA QUALITY BLOCKED",
}
RESULTS = {"WIN", "LOSS", "BREAKEVEN"}
STRATEGY_MIN_EXAMPLES = 30
STRATEGY_MIN_DAYS = 10
CONTEXT_MIN_EXAMPLES = 50
MIN_PROFIT_FACTOR = 1.20


def build_evidence_readiness(
    log_dir: str | Path,
    *,
    days: int = 30,
    through_date: date | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Return one normalized scorecard across all research evidence streams."""
    days = max(1, int(days))
    end = through_date or date.today()
    start = end - timedelta(days=days - 1)
    root = Path(log_dir)
    paths = [root / f"journal_{start + timedelta(days=i)}.jsonl" for i in range(days)]
    entries, corrupt_rows, files_found = _read_jsonl(path for path in paths if path.exists())
    pairs = _resolved_trade_pairs(entries)

    range_rows = [
        entry for entry in entries
        if isinstance(entry.get("range_signal"), dict)
        or isinstance(entry.get("shadow_range_signal"), dict)
    ]
    shadow_candidates = [
        (entry, candidate)
        for entry in entries
        for candidate in (
            entry.get("shadow_candidates")
            if isinstance(entry.get("shadow_candidates"), list) else []
        )
        if isinstance(candidate, dict)
    ]

    tracks = [
        _candidate_track(
            "range_signal",
            "RangeSignal / WallContext",
            range_rows,
            corrupt_rows=corrupt_rows,
            outcome_contract=(
                "Candidate observations are live; a causal future-bar outcome "
                "resolver is still required before expectancy can be measured."
            ),
        ),
        _candidate_track(
            "shadow_setups",
            "Shadow setup candidates",
            [entry for entry, _ in shadow_candidates],
            observation_count=len(shadow_candidates),
            malformed=sum(
                not _valid_bracket(candidate) for _, candidate in shadow_candidates
            ),
            corrupt_rows=corrupt_rows,
            outcome_contract=(
                "Candidates include entry/stop/target, but no live journal outcome "
                "is linked to them yet."
            ),
        ),
        _artifact_track(
            "adaptive_schedule_shadow",
            "Adaptive schedule shadow",
            list((root / "opportunities").glob("*.jsonl")),
            disabled_reason=(
                "Deferred: current strategy configuration has no schedule gate "
                "for the counterfactual runner to bypass."
            ),
        ),
        _context_track(
            "gex_context",
            "GEX context",
            pairs,
            field="gex_observed",
            enabled=bool(getattr(config, "gex_shadow_analysis_enabled", False)),
        ),
        _signa_track(
            pairs,
            enabled=bool(getattr(config, "signa_api_enabled", False)),
        ),
        _artifact_track(
            "trailing_stop_shadow",
            "Trailing-stop shadow",
            list(root.glob("*trail*shadow*.json*")),
        ),
        _artifact_track(
            "options_shadow_journal",
            "Options shadow journal",
            list(root.glob("*options*shadow*.json*"))
            + list((root / "options").glob("*.jsonl")),
            enabled=bool(getattr(config, "options_companion_enabled", False)),
        ),
        _fill_track(entries),
        _artifact_track(
            "replay_mfe_mae",
            "Replay / MFE / MAE",
            list(root.glob("replay_mfe*/*")) + list(root.glob("*mfe*.json*")),
        ),
    ]

    counts = Counter(track["status"] for track in tracks)
    return {
        "mode": "read_only",
        "strategy_or_gate_changed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window": {
            "days": days,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "journal_files_found": files_found,
            "journal_entries": len(entries),
            "corrupt_rows": corrupt_rows,
        },
        "thresholds": {
            "strategy_min_resolved_examples": STRATEGY_MIN_EXAMPLES,
            "strategy_min_distinct_days": STRATEGY_MIN_DAYS,
            "context_min_comparable_trades": CONTEXT_MIN_EXAMPLES,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "requires_positive_net_expectancy": True,
            "requires_stability_review": True,
        },
        "summary": {
            "ready_for_review": counts["READY FOR REVIEW"],
            "collecting": counts["COLLECTING"] + counts["INSUFFICIENT SAMPLE"],
            "blocked": counts["DATA QUALITY BLOCKED"],
            "inactive": (
                counts["NOT INSTALLED"] + counts["DISABLED"]
                + counts["NOT COLLECTING"]
            ),
        },
        "tracks": tracks,
        "promotion_policy": (
            "READY FOR REVIEW permits human replay/paper review only. "
            "This report never enables a strategy or gate."
        ),
    }


def _read_jsonl(paths: Iterable[Path]) -> tuple[list[dict], int, int]:
    entries: list[dict] = []
    corrupt = 0
    files = 0
    for path in paths:
        files += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError):
                corrupt += 1
                continue
            if isinstance(row, dict):
                entries.append(row)
            else:
                corrupt += 1
    return entries, corrupt, files


def _day(row: dict) -> str | None:
    raw = row.get("ts") or row.get("timestamp") or row.get("bar_ts")
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    return raw[:10]


def _base_track(key: str, name: str, status: str, **extra: Any) -> dict[str, Any]:
    assert status in STATUSES
    return {
        "key": key,
        "name": name,
        "status": status,
        "trade_gating_changed": False,
        **extra,
    }


def _candidate_track(
    key: str,
    name: str,
    rows: list[dict],
    *,
    observation_count: int | None = None,
    malformed: int = 0,
    corrupt_rows: int = 0,
    outcome_contract: str,
) -> dict[str, Any]:
    observations = len(rows) if observation_count is None else observation_count
    distinct_days = len({day for row in rows if (day := _day(row))})
    if malformed or corrupt_rows:
        status = "DATA QUALITY BLOCKED"
    elif observations:
        status = "COLLECTING"
    else:
        status = "NOT COLLECTING"
    return _base_track(
        key,
        name,
        status,
        observations=observations,
        resolved_examples=0,
        distinct_days=distinct_days,
        malformed_examples=malformed,
        outcome_resolution_available=False,
        note=outcome_contract,
    )


def _artifact_track(
    key: str,
    name: str,
    paths: list[Path],
    *,
    enabled: bool | None = None,
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    readable = [path for path in paths if path.is_file()]
    if disabled_reason or enabled is False:
        status = "DISABLED"
    elif readable:
        status = "COLLECTING"
    else:
        status = "NOT COLLECTING"
    return _base_track(
        key,
        name,
        status,
        artifacts=len(readable),
        evidence_paths=[str(path) for path in readable[:5]],
        note=disabled_reason,
    )


def _resolved_trade_pairs(entries: list[dict]) -> list[tuple[dict, dict]]:
    pending: dict[str, list[dict]] = defaultdict(list)
    pairs: list[tuple[dict, dict]] = []
    for row in entries:
        instrument = str(row.get("instrument") or "")
        if (
            row.get("decision") == "TRADE"
            and (row.get("risk_check") or {}).get("result") == "APPROVED"
            and instrument
        ):
            inline = row.get("outcome") or {}
            if inline.get("result") in RESULTS:
                pairs.append((row, inline))
            else:
                pending[instrument].append(row)
        elif row.get("type") == "OUTCOME" and instrument:
            outcome = row.get("outcome") or {}
            if outcome.get("result") in RESULTS and pending[instrument]:
                pairs.append((pending[instrument].pop(0), outcome))
    return pairs


def _context_track(
    key: str,
    name: str,
    pairs: list[tuple[dict, dict]],
    *,
    field: str,
    enabled: bool,
) -> dict[str, Any]:
    measured = [
        (decision, outcome)
        for decision, outcome in pairs
        if isinstance(decision.get(field), dict)
        and decision[field].get("ok") is True
    ]
    return _performance_track(
        key, name, measured, minimum=CONTEXT_MIN_EXAMPLES, enabled=enabled
    )


def _signa_track(
    pairs: list[tuple[dict, dict]], *, enabled: bool
) -> dict[str, Any]:
    measured = [
        (decision, outcome)
        for decision, outcome in pairs
        if decision.get("signa_status") not in (None, "", "NOT_CHECKED")
    ]
    return _performance_track(
        "signa_context",
        "Signa context",
        measured,
        minimum=CONTEXT_MIN_EXAMPLES,
        enabled=enabled,
    )


def _performance_track(
    key: str,
    name: str,
    pairs: list[tuple[dict, dict]],
    *,
    minimum: int,
    enabled: bool,
) -> dict[str, Any]:
    metrics = _performance_metrics(pairs)
    if not enabled:
        status = "DISABLED"
    elif not pairs:
        status = "NOT COLLECTING"
    elif metrics["sample_size"] < minimum or metrics["distinct_days"] < STRATEGY_MIN_DAYS:
        status = "INSUFFICIENT SAMPLE"
    elif (
        metrics["expectancy"] > 0
        and (
            metrics["profit_factor"] == "infinite"
            or (
                isinstance(metrics["profit_factor"], (int, float))
                and metrics["profit_factor"] >= MIN_PROFIT_FACTOR
            )
        )
    ):
        status = "READY FOR REVIEW"
    else:
        status = "COLLECTING"
    return _base_track(
        key,
        name,
        status,
        enabled=enabled,
        required_examples=minimum,
        metrics=metrics,
    )


def _performance_metrics(pairs: list[tuple[dict, dict]]) -> dict[str, Any]:
    pnls = [float((outcome or {}).get("pnl_dollars") or 0.0) for _, outcome in pairs]
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in pnls if pnl < 0))
    running = peak = max_drawdown = 0.0
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    return {
        "sample_size": len(pairs),
        "distinct_days": len(
            {day for decision, _ in pairs if (day := _day(decision))}
        ),
        "net_pnl_dollars": round(sum(pnls), 2),
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "profit_factor": (
            round(gross_profit / gross_loss, 2)
            if gross_loss else (None if not gross_profit else "infinite")
        ),
        "max_drawdown_dollars": round(max_drawdown, 2),
    }


def _valid_bracket(candidate: dict) -> bool:
    try:
        direction = str(candidate.get("direction") or "").upper()
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        direction == "LONG" and stop < entry < target
    ) or (
        direction == "SHORT" and target < entry < stop
    )


def _fill_track(entries: list[dict]) -> dict[str, Any]:
    pairs = _resolved_trade_pairs(entries)
    days = len({day for decision, _ in pairs if (day := _day(decision))})
    status = (
        "READY FOR REVIEW"
        if len(pairs) >= STRATEGY_MIN_EXAMPLES and days >= STRATEGY_MIN_DAYS
        else "INSUFFICIENT SAMPLE" if pairs else "NOT COLLECTING"
    )
    return _base_track(
        "fill_realism",
        "Fill realism",
        status,
        resolved_attempts=len(pairs),
        distinct_days=days,
        note=(
            "Readiness here means the observed fill/no-fill sample is reviewable; "
            "it does not promote an entry policy."
        ),
    )
