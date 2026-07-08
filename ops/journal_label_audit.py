"""Read-only audit for journal decision/risk/outcome label consistency."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator

from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR


ERROR = "error"
WARNING = "warning"


def journal_paths(
    *,
    journal_dir: Path | None = None,
    paths: Iterable[Path] | None = None,
) -> list[Path]:
    """Return explicit journal paths or all journal_*.jsonl files in a directory."""
    if paths is not None:
        return sorted(Path(path) for path in paths)
    root = journal_dir or DEFAULT_JOURNAL_DIR
    return sorted(root.glob("journal_*.jsonl"))


def read_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
    """Yield line number, parsed object, and parse error for each non-empty row."""
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


def _risk_check(entry: dict[str, Any]) -> dict[str, Any]:
    risk = entry.get("risk_check")
    return risk if isinstance(risk, dict) else {}


def _risk_result(entry: dict[str, Any]) -> str | None:
    result = _risk_check(entry).get("result")
    return str(result).upper() if result is not None else None


def _has_setup(entry: dict[str, Any]) -> bool:
    setup = entry.get("setup")
    return isinstance(setup, dict) and bool(setup)


def _add_issue(
    issues: list[dict[str, Any]],
    *,
    path: Path,
    line: int,
    entry: dict[str, Any] | None,
    code: str,
    severity: str,
    message: str,
) -> None:
    body = entry or {}
    issues.append(
        {
            "code": code,
            "severity": severity,
            "message": message,
            "file": str(path),
            "line": line,
            "ts": body.get("ts"),
            "instrument": body.get("instrument"),
            "type": body.get("type"),
            "decision": body.get("decision"),
            "risk_result": _risk_result(body) if body else None,
        }
    )


def audit_entry(path: Path, line: int, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return consistency issues for one journal row."""
    issues: list[dict[str, Any]] = []
    decision = entry.get("decision")
    risk = _risk_check(entry)
    risk_result = _risk_result(entry)

    if entry.get("type") == "OUTCOME" and decision is not None:
        _add_issue(
            issues,
            path=path,
            line=line,
            entry=entry,
            code="outcome_row_has_decision",
            severity=WARNING,
            message="OUTCOME rows should not also carry a decision label.",
        )
        return issues

    if decision == "TRADE":
        if not isinstance(entry.get("risk_check"), dict):
            _add_issue(
                issues,
                path=path,
                line=line,
                entry=entry,
                code="trade_missing_risk_check",
                severity=WARNING,
                message="TRADE row has no structured risk_check object.",
            )
        elif risk_result == "REJECTED":
            _add_issue(
                issues,
                path=path,
                line=line,
                entry=entry,
                code="trade_with_rejected_risk",
                severity=ERROR,
                message="TRADE row has risk_check.result=REJECTED; label should be RISK_REJECTED.",
            )
        elif risk_result == "APPROVED" and not _has_setup(entry):
            _add_issue(
                issues,
                path=path,
                line=line,
                entry=entry,
                code="approved_trade_missing_setup",
                severity=WARNING,
                message="Approved TRADE row is missing setup details needed for audit pairing.",
            )

    if decision == "RISK_REJECTED":
        if risk_result != "REJECTED":
            _add_issue(
                issues,
                path=path,
                line=line,
                entry=entry,
                code="risk_rejected_without_rejected_risk",
                severity=ERROR,
                message="RISK_REJECTED row does not have risk_check.result=REJECTED.",
            )
        if (
            risk_result == "REJECTED"
            and not entry.get("reason")
            and not risk.get("reason")
            and not risk.get("failed_rule")
        ):
            _add_issue(
                issues,
                path=path,
                line=line,
                entry=entry,
                code="risk_rejected_missing_reason",
                severity=WARNING,
                message="RISK_REJECTED row lacks reason, risk reason, and failed_rule detail.",
            )

    if decision not in (None, "TRADE", "RISK_REJECTED") and risk_result == "APPROVED":
        _add_issue(
            issues,
            path=path,
            line=line,
            entry=entry,
            code="non_trade_with_approved_risk",
            severity=WARNING,
            message="Non-TRADE row has risk_check.result=APPROVED; verify label and risk state agree.",
        )

    return issues


def build_audit(
    *,
    journal_dir: Path | None = None,
    paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    selected_paths = journal_paths(journal_dir=journal_dir, paths=paths)
    issues: list[dict[str, Any]] = []
    rows_scanned = 0

    for path in selected_paths:
        for line, entry, parse_error in read_jsonl(path):
            rows_scanned += 1
            if parse_error:
                _add_issue(
                    issues,
                    path=path,
                    line=line,
                    entry=entry,
                    code="journal_read_error",
                    severity=ERROR,
                    message=parse_error,
                )
                continue
            if entry is None:
                continue
            issues.extend(audit_entry(path, line, entry))

    by_code = Counter(issue["code"] for issue in issues)
    by_severity = Counter(issue["severity"] for issue in issues)
    return {
        "read_only": True,
        "journal_dir": str(journal_dir or DEFAULT_JOURNAL_DIR) if paths is None else None,
        "files": [str(path) for path in selected_paths],
        "summary": {
            "files_scanned": len(selected_paths),
            "rows_scanned": rows_scanned,
            "issue_count": len(issues),
            "issues_by_code": dict(sorted(by_code.items())),
            "issues_by_severity": dict(sorted(by_severity.items())),
        },
        "issues": issues,
    }


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def format_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "Journal Label Consistency Audit",
        f"Read-only: {report['read_only']}",
        f"Files scanned: {summary['files_scanned']}",
        f"Rows scanned: {summary['rows_scanned']}",
        f"Issues: {summary['issue_count']}",
        f"By severity: {summary['issues_by_severity']}",
        f"By code: {summary['issues_by_code']}",
        "",
    ]
    if not report["issues"]:
        lines.append("No label consistency issues found.")
        return "\n".join(lines)

    lines.append("Issues")
    for issue in report["issues"]:
        location = f"{issue['file']}:{issue['line']}"
        lines.append(
            f"- {issue['severity'].upper()} {issue['code']} at {location}: "
            f"decision={issue.get('decision')} risk={issue.get('risk_result')} "
            f"instrument={issue.get('instrument')} ts={issue.get('ts')} - {issue['message']}"
        )
    return "\n".join(lines)
