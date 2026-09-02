"""Append-only promotion record (one JSON object per line)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import PromotionVerdict
from .policy import policy_fingerprint

RECORD_VERSION = 1
DEFAULT_RECORD_PATH = Path("data/promotion_readiness/promotion_records.jsonl")


def build_promotion_record(verdict: PromotionVerdict) -> dict[str, Any]:
    ev = verdict.evidence
    return {
        "record_version": RECORD_VERSION,
        "record_type": "pr_readiness",
        "tool": "ops.pr_promotion_readiness",
        "timestamp": ev.collected_at,
        "pr_number": ev.pr_number,
        "repo": ev.repo,
        "url": ev.url,
        "title": ev.title,
        "branch": ev.branch,
        "head_sha": ev.head_sha,
        "base_ref": ev.base_ref,
        "base_sha": ev.base_sha,
        "merge_base_sha": ev.merge_base_sha,
        "behind_base_by": ev.behind_base_by,
        "ahead_of_base_by": ev.ahead_of_base_by,
        "state": ev.state,
        "is_draft": ev.is_draft,
        "labels": list(ev.labels),
        "mergeable": ev.mergeable,
        "merge_state": ev.merge_state,
        "review_decision": ev.review_decision,
        "unresolved_review_threads": [asdict(t) for t in ev.review_threads if not t.resolved],
        "ci_checks": [asdict(c) for c in ev.checks],
        "tests": [asdict(t) for t in ev.tests],
        "changed_files": list(ev.changed_files),
        "scope_policy": verdict.scope_policy,
        "policy_fingerprint": policy_fingerprint(),
        "scope_findings": [asdict(f) for f in verdict.scope_findings],
        "collection_errors": list(ev.collection_errors),
        "blockers": list(verdict.blockers),
        "holds": list(verdict.holds),
        "verdict": verdict.verdict,
        "reasons": list(verdict.reasons),
        "action_taken": "none (validation only; merge requires human approval)",
    }


def append_promotion_record(path: Path, record: dict[str, Any]) -> None:
    """Append one line. Never truncates, rewrites, or deletes existing lines."""
    path = Path(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"promotion record path is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_records(path: Path) -> list[dict[str, Any]]:
    """Read-only. Malformed lines are skipped, never repaired."""
    path = Path(path)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
