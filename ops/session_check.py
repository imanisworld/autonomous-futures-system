"""Session Safety + Runtime Snapshot.

Two read-only modes, composed from existing machinery rather than
re-deriving it:

  session_start_report()  -- full repo/branch/worktree snapshot (ops.git_state)
                              plus a runtime posture snapshot (deployed-state
                              drift via ops.live_box_guard, active
                              paper-forward lanes via ops.evidence_lane_health).
                              Persists a small session-identity snapshot to a
                              local, gitignored state file so precommit_report()
                              can detect drift later in the same session.

  precommit_report()      -- read-only, fail-closed comparison of current git
                              state against the persisted session-start
                              snapshot. Never commits, pushes, pulls, resets,
                              rebases, checks out, deletes a branch, removes a
                              worktree, drops a stash, creates/deletes a tag,
                              or modifies any tracked file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops.evidence_lane_health import build_snapshot as evidence_lane_snapshot
from ops.git_state import current_branch, git_state_report, worktree_for_path
from ops.live_box_guard import live_box_drift_report

DEFAULT_STATE_RELATIVE_PATH = Path("logs") / ".ops_session_state.json"


def default_state_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / DEFAULT_STATE_RELATIVE_PATH


def _runtime_snapshot(repo_root: Path, *, log_dir: str | Path) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    try:
        lanes = evidence_lane_snapshot(log_dir)
    except Exception as exc:  # pragma: no cover - defensive; lane health must not crash session-start
        lanes = {"error": str(exc)}
    active_overrides = {
        item["name"]: item["observed"]
        for item in drift.get("proof_critical_runtime_overrides", [])
        if item.get("active")
    }
    return {
        "deployed_state": {
            "identity_source": drift.get("identity_source"),
            "branch": drift.get("branch"),
            "commit": drift.get("commit"),
            "risk_rules_sha256": drift.get("risk_rules_sha256"),
            "status": drift.get("status"),
            "summary": drift.get("summary"),
            "missing_pins": drift.get("missing_pins"),
            "mismatches": drift.get("mismatches"),
            "unpinned_runtime_overrides": drift.get("unpinned_runtime_overrides"),
        },
        "active_runtime_overrides": active_overrides,
        "evidence_lane_snapshot": lanes,
        "config_vs_evidence_assumptions": (
            "UNKNOWN — cross-referencing active runtime overrides against a specific "
            "evidence packet's assumptions requires naming that packet; see "
            "ops.promotion_gate for a per-strategy version of this comparison."
        ),
    }


def session_start_report(
    repo_root: str | Path,
    *,
    log_dir: str | Path = "logs",
    base_branch: str = "main",
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Full read-only repo + runtime snapshot. Persists a minimal state file."""
    root = Path(repo_root).resolve()
    repo = git_state_report(root, base_branch=base_branch)
    runtime = _runtime_snapshot(root, log_dir=log_dir)

    report: dict[str, Any] = {
        "mode": "session-start",
        "read_only": True,
        "repo": repo,
        "runtime": runtime,
    }

    persist_path = Path(state_path) if state_path else default_state_path(root)
    snapshot = {
        "repo_root": repo["repo_root"],
        "branch": repo["current_branch"],
        "head_sha": repo["head_sha"],
        "worktree_path": (repo["current_worktree"] or {}).get("path"),
    }
    try:
        persist_path.parent.mkdir(parents=True, exist_ok=True)
        persist_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True), encoding="utf-8")
        report["state_persisted_to"] = str(persist_path)
    except OSError as exc:
        report["state_persist_error"] = str(exc)
    return report


def _load_session_state(state_path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not state_path.exists():
        return None, f"session-start state file not found at {state_path}"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"session-start state file at {state_path} could not be read: {exc}"
    if not isinstance(data, dict) or not data.get("repo_root"):
        return None, f"session-start state file at {state_path} is malformed"
    return data, None


def precommit_report(
    repo_root: str | Path,
    *,
    base_branch: str = "main",
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read-only fail-closed check. Never mutates git or filesystem state."""
    root = Path(repo_root).resolve()
    persist_path = Path(state_path) if state_path else default_state_path(root)
    session_state, load_error = _load_session_state(persist_path)

    repo = git_state_report(root, base_branch=base_branch)
    worktrees = repo["worktrees"]
    reasons: list[str] = []

    if load_error:
        reasons.append(load_error)

    if session_state:
        if session_state.get("branch") != repo["current_branch"]:
            reasons.append(
                f"branch differs from session-start: session-start={session_state.get('branch')!r} "
                f"current={repo['current_branch']!r}"
            )
        if session_state.get("repo_root") != repo["repo_root"]:
            reasons.append(
                f"repo root differs from session-start: session-start={session_state.get('repo_root')!r} "
                f"current={repo['repo_root']!r}"
            )
        session_worktree = session_state.get("worktree_path")
        current_worktree_path = (repo["current_worktree"] or {}).get("path")
        if session_worktree != current_worktree_path:
            reasons.append(
                f"worktree differs from session-start: session-start={session_worktree!r} "
                f"current={current_worktree_path!r}"
            )

    if repo["branch_changed_during_check"]:
        reasons.append("checked-out branch changed while this check was running")

    owning_worktree = next(
        (wt for wt in worktrees if wt.get("branch", "").endswith(f"/{repo['current_branch']}")),
        None,
    ) if repo["current_branch"] else None
    other_owners = [
        wt for wt in worktrees
        if wt.get("branch", "").endswith(f"/{repo['current_branch']}")
        and wt.get("path") != (repo["current_worktree"] or {}).get("path")
    ] if repo["current_branch"] else []
    if other_owners:
        reasons.append(
            f"branch {repo['current_branch']!r} is also checked out in another worktree: "
            f"{[wt.get('path') for wt in other_owners]}"
        )

    if repo["current_worktree"] is None:
        reasons.append("current worktree could not be identified from `git worktree list`")

    fail_closed = bool(reasons)

    return {
        "mode": "precommit",
        "read_only": True,
        "fail_closed": fail_closed,
        "status": "BLOCK" if fail_closed else "OK",
        "reasons": reasons,
        "repo_root": repo["repo_root"],
        "current_branch": repo["current_branch"],
        "head_sha": repo["head_sha"],
        "session_start_branch": (session_state or {}).get("branch"),
        "session_start_worktree": (session_state or {}).get("worktree_path"),
        "current_worktree": repo["current_worktree"],
        "upstream": repo["upstream"],
        "ahead": repo["ahead"],
        "behind": repo["behind"],
        "changed_files": repo["dirty_tracked_files"],
        "staged_files": repo["staged_files"],
        "untracked_files": repo["untracked_files"],
        "owning_worktree": owning_worktree,
    }


def format_session_start(report: dict[str, Any]) -> str:
    repo = report["repo"]
    runtime = report["runtime"]
    lines = [
        f"SESSION START | {repo['repo_root']}",
        f"branch={repo['current_branch']} head={ (repo['head_sha'] or '')[:12] } "
        f"vs origin/{repo['base_branch']}: {repo['local_main_relationship']}",
        f"worktree={ (repo['current_worktree'] or {}).get('path') } "
        f"(worktrees total: {len(repo['worktrees'])})",
        f"dirty={len(repo['dirty_tracked_files'])} staged={len(repo['staged_files'])} "
        f"untracked={len(repo['untracked_files'])} stash={repo['stash_count']}",
        f"branches: local_only={len(repo['local_only_branches'])} "
        f"tracking_deleted_remote={len(repo['branches_tracking_deleted_remotes'])}",
        f"deployed state: {runtime['deployed_state']['status']} - {runtime['deployed_state']['summary']}",
        f"active runtime overrides: {list(runtime['active_runtime_overrides'])}",
    ]
    if report.get("state_persisted_to"):
        lines.append(f"state persisted -> {report['state_persisted_to']}")
    if report.get("state_persist_error"):
        lines.append(f"state persist FAILED: {report['state_persist_error']}")
    return "\n".join(lines)


def format_precommit(report: dict[str, Any]) -> str:
    lines = [f"PRECOMMIT: {report['status']}"]
    lines.append(f"branch={report['current_branch']} worktree={(report['current_worktree'] or {}).get('path')}")
    if report["reasons"]:
        lines.append("FAIL CLOSED:")
        lines.extend(f"  - {reason}" for reason in report["reasons"])
    return "\n".join(lines)
