"""Read-only direction-authority audit for Strat direction provenance."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR
from ops.strategy_intent_audit import DECISION_LABELS, journal_paths, read_jsonl
from strategy.strat_classifier import classify_sequence


DIRECTIONS = {"LONG", "SHORT"}


def _normal_direction(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in DIRECTIONS else None


def _nested_value(row: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = row
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_value(
    row: dict[str, Any],
    paths: Iterable[tuple[str, ...]],
) -> tuple[Any, str | None]:
    for path in paths:
        value = _nested_value(row, path)
        if value is not None:
            return value, ".".join(path)
    return None, None


def _field(row: dict[str, Any], name: str) -> tuple[Any, str | None]:
    return _first_value(
        row,
        (
            (f"pine_{name}",),
            (f"payload_{name}",),
            ("payload", name),
            ("raw_payload", name),
            ("raw", name),
            ("context", "strat", name),
            ("strat", name),
            (name,),
        ),
    )


def _strat_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    fields = {}
    sources = {}
    for name in (
        "current_bar_type",
        "previous_bar_type",
        "two_bars_back_type",
        "strat_sequence",
        "strat_trigger",
        "strat_direction",
    ):
        value, source = _field(row, name)
        fields[name] = value
        sources[name] = source
    fields["field_sources"] = sources
    return fields


def _selected_candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = row.get("candidate_audit")
    if not isinstance(candidates, list):
        return None
    selected = next(
        (candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("selected")),
        None,
    )
    if selected is not None:
        return selected
    return next(
        (candidate for candidate in candidates if isinstance(candidate, dict) and candidate.get("winner")),
        None,
    )


def _compact_setup(setup: Any) -> dict[str, Any] | None:
    if not isinstance(setup, dict):
        return None
    return {
        "strategy": setup.get("strategy"),
        "direction": setup.get("direction"),
        "entry": setup.get("entry"),
        "stop": setup.get("stop"),
        "target": setup.get("target"),
        "rr_ratio": setup.get("rr_ratio"),
    }


def _candidate_directions(row: dict[str, Any]) -> dict[str, int]:
    candidates = row.get("candidate_audit")
    if not isinstance(candidates, list):
        return {}
    counts = Counter()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        direction = _normal_direction(candidate.get("candidate_direction") or candidate.get("direction"))
        if direction:
            counts[direction] += 1
    return dict(sorted(counts.items()))


def _selection_direction(row: dict[str, Any]) -> tuple[Optional[str], dict[str, Any] | None]:
    setup = row.get("setup") if isinstance(row.get("setup"), dict) else None
    if setup:
        direction = _normal_direction(setup.get("direction"))
        if direction:
            return direction, _compact_setup(setup)
    candidate = _selected_candidate(row)
    if candidate:
        direction = _normal_direction(candidate.get("candidate_direction") or candidate.get("direction"))
        if direction:
            return direction, {
                "strategy": candidate.get("strategy"),
                "direction": direction,
                "entry": candidate.get("entry"),
                "stop": candidate.get("stop"),
                "target": candidate.get("target"),
                "rr_ratio": candidate.get("rr_ratio"),
            }
    return None, None


def _source(row: dict[str, Any], path: Path) -> str:
    explicit = str(
        row.get("audit_source")
        or row.get("row_source")
        or row.get("source")
        or row.get("mode")
        or ""
    ).strip().lower()
    if explicit in {"live", "replay"}:
        return explicit
    path_text = str(path).lower()
    if row.get("bar_ts") or "replay" in path_text:
        return "replay"
    return "live"


def _row_key(row: dict[str, Any]) -> str:
    context = row.get("context") if isinstance(row.get("context"), dict) else {}
    timestamp = (
        row.get("bar_ts")
        or context.get("timestamp")
        or row.get("signal_timestamp")
        or row.get("ts")
        or "unknown_ts"
    )
    return f"{row.get('instrument') or 'unknown'}|{timestamp}"


def _metadata_differences(pine: dict[str, Any], local: Any) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    comparisons = (
        ("current_bar_type", local.current_bar_type),
        ("previous_bar_type", local.previous_bar_type),
        ("two_bars_back_type", local.two_bars_back_type),
        ("strat_sequence", local.strat_sequence),
        ("strat_trigger", local.strat_trigger),
    )
    for name, local_value in comparisons:
        pine_value = pine.get(name)
        if pine_value is None or local_value is None:
            continue
        if str(pine_value) != str(local_value):
            differences.append({"field": name, "pine": pine_value, "local": local_value})
    return differences


def _selection_impact(
    *,
    pine_direction: Optional[str],
    local_direction: Optional[str],
    selected_direction: Optional[str],
) -> str:
    if pine_direction == local_direction:
        return "no_direction_mismatch"
    if not selected_direction:
        return "no_final_selection"
    if selected_direction == pine_direction and selected_direction != local_direction:
        return "selected_pine_direction_over_local"
    if selected_direction == local_direction and selected_direction != pine_direction:
        return "selected_local_direction_despite_pine"
    return "selected_direction_not_explained_by_strat_mismatch"


def _audit_item(path: Path, line: int, row: dict[str, Any]) -> dict[str, Any]:
    pine = _strat_snapshot(row)
    local = classify_sequence(
        pine.get("two_bars_back_type"),
        pine.get("previous_bar_type"),
        pine.get("current_bar_type"),
    )
    pine_direction = _normal_direction(pine.get("strat_direction"))
    local_direction = _normal_direction(local.strat_direction)
    selected_direction, selected_outcome = _selection_direction(row)
    metadata_diffs = _metadata_differences(pine, local)
    direction_mismatch = bool(pine_direction and local_direction and pine_direction != local_direction)
    harmless_metadata = bool(
        metadata_diffs
        and pine_direction is not None
        and local_direction is not None
        and pine_direction == local_direction
    )
    status = "direction_match"
    if direction_mismatch:
        status = "direction_mismatch"
    elif harmless_metadata:
        status = "metadata_only_difference"
    elif not pine_direction:
        status = "missing_pine_direction"
    elif not local_direction:
        status = "missing_local_direction"

    return {
        "file": str(path),
        "line": line,
        "source": _source(row, path),
        "row_key": _row_key(row),
        "ts": row.get("ts"),
        "bar_ts": row.get("bar_ts"),
        "instrument": row.get("instrument"),
        "decision": row.get("decision"),
        "reason": row.get("reason"),
        "pine_direction": pine_direction,
        "pine_direction_source": pine.get("field_sources", {}).get("strat_direction"),
        "local_direction": local_direction,
        "direction_mismatch": direction_mismatch,
        "status": status,
        "selection_direction": selected_direction,
        "selection_impact": _selection_impact(
            pine_direction=pine_direction,
            local_direction=local_direction,
            selected_direction=selected_direction,
        ),
        "selected_outcome": selected_outcome,
        "candidate_directions": _candidate_directions(row),
        "pine_strat": {
            "current_bar_type": pine.get("current_bar_type"),
            "previous_bar_type": pine.get("previous_bar_type"),
            "two_bars_back_type": pine.get("two_bars_back_type"),
            "strat_sequence": pine.get("strat_sequence"),
            "strat_trigger": pine.get("strat_trigger"),
            "strat_direction": pine.get("strat_direction"),
        },
        "local_strat": {
            "current_bar_type": local.current_bar_type,
            "previous_bar_type": local.previous_bar_type,
            "two_bars_back_type": local.two_bars_back_type,
            "strat_sequence": local.strat_sequence,
            "strat_trigger": local.strat_trigger,
            "strat_direction": local.strat_direction,
        },
        "metadata_differences": metadata_diffs,
        "harmless_metadata_difference": harmless_metadata,
    }


def _compact_pair_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item["source"],
        "file": item["file"],
        "line": item["line"],
        "decision": item["decision"],
        "pine_direction": item["pine_direction"],
        "local_direction": item["local_direction"],
        "selection_direction": item["selection_direction"],
        "status": item["status"],
    }


def _live_replay_disagreements(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[item["row_key"]].append(item)

    disagreements: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        sources = {row["source"] for row in rows}
        if not {"live", "replay"}.issubset(sources):
            continue
        relevant = [
            row
            for row in rows
            if row["source"] in {"live", "replay"}
        ]
        fields = []
        for name in ("decision", "pine_direction", "local_direction", "selection_direction", "status"):
            values = {row.get(name) for row in relevant}
            if len(values) > 1:
                fields.append(name)
        if fields:
            disagreements.append(
                {
                    "row_key": key,
                    "instrument": relevant[0].get("instrument"),
                    "bar_ts": relevant[0].get("bar_ts") or relevant[0].get("ts"),
                    "disagreement_fields": fields,
                    "rows": [_compact_pair_row(row) for row in relevant],
                }
            )
    return disagreements


def build_audit(
    *,
    journal_dir: Path | None = None,
    paths: Iterable[Path] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    selected_paths = journal_paths(journal_dir=journal_dir, paths=paths)
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    rows_scanned = 0

    for path in selected_paths:
        for line, entry, parse_error in read_jsonl(path):
            rows_scanned += 1
            if parse_error:
                issues.append({"file": str(path), "line": line, "code": "journal_read_error", "message": parse_error})
                continue
            if entry is None or entry.get("type") == "OUTCOME":
                continue
            if entry.get("decision") not in DECISION_LABELS:
                continue
            rows.append(_audit_item(path, line, entry))

    considered = rows if limit is None else rows[-limit:]
    disagreement_rows = _live_replay_disagreements(rows)
    source_counts = Counter(item["source"] for item in rows)
    source_mismatch_counts = Counter(
        item["source"] for item in rows if item["direction_mismatch"]
    )
    selection_impact_counts = Counter(item["selection_impact"] for item in rows)
    status_counts = Counter(item["status"] for item in rows)

    return {
        "read_only": True,
        "journal_dir": str(journal_dir or DEFAULT_JOURNAL_DIR) if paths is None else None,
        "files": [str(path) for path in selected_paths],
        "summary": {
            "files_scanned": len(selected_paths),
            "rows_scanned": rows_scanned,
            "decision_rows": len(rows),
            "reported_decision_rows": len(considered),
            "comparable_direction_rows": sum(
                bool(item["pine_direction"] and item["local_direction"]) for item in rows
            ),
            "direction_mismatch_rows": sum(item["direction_mismatch"] for item in rows),
            "selection_direction_changed_rows": sum(
                item["selection_impact"] == "selected_pine_direction_over_local"
                for item in rows
            ),
            "harmless_metadata_difference_rows": sum(item["harmless_metadata_difference"] for item in rows),
            "live_replay_disagreement_count": len(disagreement_rows),
            "source_counts": dict(sorted(source_counts.items())),
            "direction_mismatch_source_counts": dict(sorted(source_mismatch_counts.items())),
            "selection_impact_counts": dict(sorted(selection_impact_counts.items())),
            "status_counts": dict(sorted(status_counts.items())),
            "issue_count": len(issues),
        },
        "issues": issues,
        "live_replay_disagreements": disagreement_rows,
        "rows": considered,
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def format_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "Direction Authority Audit",
        f"Read-only: {report['read_only']}",
        f"Files scanned: {summary['files_scanned']}",
        f"Rows scanned: {summary['rows_scanned']}",
        f"Decision rows: {summary['decision_rows']}",
        f"Comparable direction rows: {summary['comparable_direction_rows']}",
        f"Direction mismatch rows: {summary['direction_mismatch_rows']}",
        f"Selection direction changed rows: {summary['selection_direction_changed_rows']}",
        f"Harmless metadata difference rows: {summary['harmless_metadata_difference_rows']}",
        f"Live/replay disagreement groups: {summary['live_replay_disagreement_count']}",
        f"Sources: {summary['source_counts']}",
        f"Direction mismatches by source: {summary['direction_mismatch_source_counts']}",
        f"Selection impacts: {summary['selection_impact_counts']}",
        f"Statuses: {summary['status_counts']}",
        "",
    ]

    if report["issues"]:
        lines.append("Issues")
        for issue in report["issues"]:
            lines.append(f"- {issue['code']} at {issue['file']}:{issue['line']} - {issue['message']}")
        lines.append("")

    if report["live_replay_disagreements"]:
        lines.append("Live/Replay Disagreements")
        for item in report["live_replay_disagreements"]:
            lines.append(
                f"- {item['row_key']} fields={','.join(item['disagreement_fields'])}"
            )
        lines.append("")

    if not report["rows"]:
        lines.append("No decision rows found.")
        return "\n".join(lines)

    lines.append("Rows")
    for item in report["rows"]:
        meta = " metadata_diff=True" if item["harmless_metadata_difference"] else ""
        selected = item.get("selected_outcome") or {}
        selected_label = "-"
        if item["selection_direction"]:
            strategy = selected.get("strategy") or "unknown"
            selected_label = f"{strategy}/{item['selection_direction']}"
        lines.append(
            f"- {item.get('ts')} {item.get('instrument')} {item['source']} "
            f"{item.get('decision')} pine={item['pine_direction'] or '-'} "
            f"local={item['local_direction'] or '-'} "
            f"selected={selected_label} "
            f"impact={item['selection_impact']} status={item['status']}{meta}"
        )
    return "\n".join(lines)
