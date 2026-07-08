"""Read-only strategy intent audit from journal candidate_audit rows."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR


DECISION_LABELS = {"TRADE", "NO_TRADE", "RISK_REJECTED", "CONFIG_BLOCKED", "DONE_FOR_DAY", "WAIT"}


def journal_paths(
    *,
    journal_dir: Path | None = None,
    paths: Iterable[Path] | None = None,
) -> list[Path]:
    if paths is not None:
        return sorted(Path(path) for path in paths)
    return sorted((journal_dir or DEFAULT_JOURNAL_DIR).glob("journal_*.jsonl"))


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        yield 0, None, str(exc)
        return
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            yield line_no, None, f"invalid_json: {exc.msg}"
            continue
        if not isinstance(parsed, dict):
            yield line_no, None, "json_row_not_object"
            continue
        yield line_no, parsed, None


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
        "contracts": setup.get("contracts"),
    }


def _context_snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
    return {
        "market_condition": entry.get("market_condition"),
        "regime": entry.get("regime"),
        "trend": context.get("trend"),
        "htf": context.get("htf"),
        "signa": context.get("signa"),
        "previous_day": context.get("previous_day"),
        "close": context.get("close"),
        "context_ref": "journal.context" if context else None,
    }


def _candidate_summary(row: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "strategy": row.get("strategy"),
        "direction": row.get("candidate_direction") or row.get("direction"),
        "rank_score": row.get("rank_score"),
        "rank_reason": row.get("rank_reason"),
        "selected": bool(row.get("selected")),
        "winner": bool(row.get("winner") or row.get("selected")),
        "attempted": bool(row.get("attempted")),
        "fallback_attempt": bool(row.get("fallback_attempt")),
        "fallback_enabled": bool(row.get("fallback_enabled")),
        "fallback_skipped": bool(row.get("fallback_skipped")),
        "skip_reason": row.get("skip_reason"),
        "failed_gates": list(row.get("failed_gates") or ([row.get("reject_code")] if row.get("reject_code") else [])),
        "reject_code": row.get("reject_code"),
        "reject_reason": row.get("reject_reason"),
        "stale_data_flags": list(row.get("stale_data_flags") or []),
        "context_ref": row.get("context_ref"),
    }


def _shadow_summary(entry: dict[str, Any]) -> list[dict[str, Any]]:
    shadows = entry.get("shadow_candidates")
    if not isinstance(shadows, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(shadows):
        if not isinstance(raw, dict):
            continue
        outcome = raw.get("outcome") if isinstance(raw.get("outcome"), dict) else {}
        out.append(
            {
                "index": idx,
                "strategy": raw.get("strategy"),
                "direction": raw.get("direction"),
                "entry": raw.get("entry"),
                "stop": raw.get("stop"),
                "target": raw.get("target"),
                "risk_tier": raw.get("risk_tier"),
                "outcome": outcome.get("result"),
                "pnl_ticks": outcome.get("pnl_ticks"),
            }
        )
    return out


def _decision_item(path: Path, line: int, entry: dict[str, Any]) -> dict[str, Any]:
    audit_rows = entry.get("candidate_audit")
    candidates = [
        _candidate_summary(row, idx)
        for idx, row in enumerate(audit_rows if isinstance(audit_rows, list) else [])
        if isinstance(row, dict)
    ]
    selected = next((candidate for candidate in candidates if candidate["selected"]), None)
    fallback_skipped = [candidate for candidate in candidates if candidate["fallback_skipped"]]
    stale_flags = sorted(
        {
            flag
            for candidate in candidates
            for flag in candidate.get("stale_data_flags", [])
        }
    )
    return {
        "file": str(path),
        "line": line,
        "ts": entry.get("ts"),
        "bar_ts": entry.get("bar_ts"),
        "instrument": entry.get("instrument"),
        "decision": entry.get("decision"),
        "reason": entry.get("reason"),
        "failed_gates": list(entry.get("failed_gates") or []),
        "selected_setup": _compact_setup(entry.get("setup")),
        "selected_candidate": selected,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "fallback_skipped": bool(fallback_skipped),
        "fallback_skipped_candidates": fallback_skipped,
        "stale_data_flags": stale_flags,
        "market_context": _context_snapshot(entry),
        "shadow_matches": _shadow_summary(entry),
    }


def build_audit(
    *,
    journal_dir: Path | None = None,
    paths: Iterable[Path] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    selected_paths = journal_paths(journal_dir=journal_dir, paths=paths)
    decisions: list[dict[str, Any]] = []
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
            decision = entry.get("decision")
            if decision not in DECISION_LABELS:
                continue
            decisions.append(_decision_item(path, line, entry))

    considered = decisions if limit is None else decisions[-limit:]
    decision_counts = Counter(item["decision"] for item in decisions)
    selected_counts = Counter(
        (item.get("selected_setup") or {}).get("strategy") or "none"
        for item in decisions
    )
    skipped_counts = Counter(
        candidate.get("strategy") or "unknown"
        for item in decisions
        for candidate in item["fallback_skipped_candidates"]
    )
    shadow_counts = Counter(
        shadow.get("strategy") or "unknown"
        for item in decisions
        for shadow in item["shadow_matches"]
    )
    missing_candidate_audit = [
        item for item in decisions
        if item["decision"] in {"TRADE", "NO_TRADE", "RISK_REJECTED"} and item["candidate_count"] == 0
    ]
    rank_missing = [
        candidate
        for item in decisions
        for candidate in item["candidates"]
        if candidate.get("rank_score") is None
    ]

    return {
        "read_only": True,
        "journal_dir": str(journal_dir or DEFAULT_JOURNAL_DIR) if paths is None else None,
        "files": [str(path) for path in selected_paths],
        "summary": {
            "files_scanned": len(selected_paths),
            "rows_scanned": rows_scanned,
            "decision_rows": len(decisions),
            "reported_decision_rows": len(considered),
            "candidate_rows": sum(item["candidate_count"] for item in decisions),
            "rows_with_candidate_audit": sum(item["candidate_count"] > 0 for item in decisions),
            "rows_missing_candidate_audit": len(missing_candidate_audit),
            "candidate_rows_missing_rank_score": len(rank_missing),
            "fallback_skipped_rows": sum(item["fallback_skipped"] for item in decisions),
            "rows_with_stale_flags": sum(bool(item["stale_data_flags"]) for item in decisions),
            "shadow_match_rows": sum(bool(item["shadow_matches"]) for item in decisions),
            "decision_counts": dict(sorted(decision_counts.items())),
            "selected_strategy_counts": dict(sorted(selected_counts.items())),
            "fallback_skipped_strategy_counts": dict(sorted(skipped_counts.items())),
            "shadow_strategy_counts": dict(sorted(shadow_counts.items())),
            "issue_count": len(issues),
        },
        "issues": issues,
        "decisions": considered,
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def format_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "Strategy Intent Audit",
        f"Read-only: {report['read_only']}",
        f"Files scanned: {summary['files_scanned']}",
        f"Rows scanned: {summary['rows_scanned']}",
        f"Decision rows: {summary['decision_rows']}",
        f"Candidate rows: {summary['candidate_rows']}",
        f"Rows missing candidate_audit: {summary['rows_missing_candidate_audit']}",
        f"Candidate rows missing rank_score: {summary['candidate_rows_missing_rank_score']}",
        f"Fallback skipped rows: {summary['fallback_skipped_rows']}",
        f"Rows with stale flags: {summary['rows_with_stale_flags']}",
        f"Shadow match rows: {summary['shadow_match_rows']}",
        f"Decision counts: {summary['decision_counts']}",
        f"Selected strategies: {summary['selected_strategy_counts']}",
        f"Fallback skipped strategies: {summary['fallback_skipped_strategy_counts']}",
        f"Shadow strategies: {summary['shadow_strategy_counts']}",
        "",
    ]
    if report["issues"]:
        lines.append("Issues")
        for issue in report["issues"]:
            lines.append(f"- {issue['code']} at {issue['file']}:{issue['line']} - {issue['message']}")
        lines.append("")

    if not report["decisions"]:
        lines.append("No decision rows found.")
        return "\n".join(lines)

    lines.append("Recent Decisions")
    for item in report["decisions"]:
        selected = item.get("selected_setup") or {}
        selected_candidate = item.get("selected_candidate") or {}
        gates = ",".join(item.get("failed_gates") or []) or "-"
        lines.append(
            f"- {item.get('ts')} {item.get('instrument')} {item.get('decision')} "
            f"setup={selected.get('strategy') or '-'} {selected.get('direction') or ''} "
            f"winner={selected_candidate.get('strategy') or '-'} "
            f"candidates={item['candidate_count']} gates={gates} "
            f"fallback_skipped={item['fallback_skipped']} stale={item['stale_data_flags']}"
        )
    return "\n".join(lines)
