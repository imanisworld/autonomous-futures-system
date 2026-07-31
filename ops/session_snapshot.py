"""Read-only session-safety snapshot and precommit/prepush drift guard.

Two entry points:

- ``build_session_start_report`` — a full repo/worktree/runtime posture
  snapshot, taken once at the start of a working session. As a side effect
  it records a small local marker (branch/worktree/HEAD at session start)
  inside this worktree's own git-dir so a later precommit check can detect
  drift. That marker is never committed or tracked; it lives under
  ``.git/afs_session_state.json`` (or the worktree-private equivalent).

- ``build_precommit_report`` — compares the *current* repo/worktree/branch
  state against the recorded session-start marker and fails closed on any
  unexplained drift. This function is READ ONLY: it never commits, pushes,
  pulls, resets, rebases, checks out, deletes a branch/worktree/stash, or
  creates/deletes a tag. It only reads git state and one local JSON marker.

Both compose existing read-only machinery (``ops.repo_hygiene``,
``ops.live_box_guard``, ``ops.automation_evidence``,
``ops.evidence_lane_health``) rather than re-deriving branch/runtime state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops import repo_hygiene
from ops.automation_evidence import automation_evidence_status
from ops.evidence_lane_health import build_snapshot as evidence_lane_snapshot
from ops.live_box_guard import live_box_drift_report

SESSION_STATE_FILENAME = "afs_session_state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def session_state_path(repo_root: Path) -> Path | None:
    git_dir = repo_hygiene.worktree_git_dir_of(repo_root)
    if git_dir is None:
        return None
    return git_dir / SESSION_STATE_FILENAME


def write_session_start_state(repo_root: Path, *, identity: dict[str, Any]) -> dict[str, Any] | None:
    path = session_state_path(repo_root)
    if path is None:
        return None
    state = {
        "recorded_at": _now().isoformat(),
        "repo_root": str(repo_root),
        "branch": identity["branch"],
        "head_sha": identity["head_sha"],
        "worktree_path": str(repo_root),
    }
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        return None
    return state


def read_session_start_state(repo_root: Path) -> dict[str, Any] | None:
    path = session_state_path(repo_root)
    if path is None or not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _runtime_snapshot(repo_root: Path, log_dir: Path) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    automation = automation_evidence_status(log_dir)
    try:
        lanes = evidence_lane_snapshot(log_dir)
    except Exception as exc:  # pragma: no cover - defensive; lane snapshot must never break session-start
        lanes = {"error": str(exc)}
    return {
        "deployed_release_sha": drift.get("commit"),
        "risk_rules_sha256": drift.get("risk_rules_sha256"),
        "active_runtime_overrides": drift.get("active_runtime_overrides"),
        "unpinned_runtime_overrides": drift.get("unpinned_runtime_overrides"),
        "runtime_evidence_source": drift.get("runtime_evidence_source"),
        "live_box_drift": drift,
        "automation_evidence": automation,
        "evidence_lanes": lanes,
    }


def build_session_start_report(
    repo_root: str | Path,
    *,
    log_dir: str | Path = "logs",
    main_ref: str = "main",
    remote_main_ref: str = "origin/main",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path

    identity = repo_hygiene.repo_identity(root)
    upstream = repo_hygiene.upstream_info(root)
    main_sync = repo_hygiene.main_sync_status(root, local_ref=main_ref, remote_ref=remote_main_ref)
    tree = repo_hygiene.working_tree_status(root)
    wts = repo_hygiene.worktrees(root)
    stash = repo_hygiene.stashes(root)
    gone_branches = repo_hygiene.branches_tracking_deleted_remotes(root)
    local_only = repo_hygiene.local_only_branches(root)
    tags = repo_hygiene.archive_tags(root)
    open_prs = repo_hygiene.gh_pr_list(root, state="open")

    branch_changed_during_check = repo_hygiene.repo_identity(root)["branch"] != identity["branch"]

    recorded_state = write_session_start_state(root, identity=identity)

    return {
        "routine": "session-start",
        "generated_at": _now().isoformat(),
        "repo": {
            **identity,
            "origin_main_sha": main_sync.get("remote_sha"),
            "main_sync": main_sync,
            "upstream": upstream,
        },
        "current_worktree": str(root),
        "worktrees": wts,
        "dirty_tracked_files": tree["dirty_files"],
        "staged_files": tree["staged_files"],
        "untracked_files": tree["untracked_files"],
        "working_tree_clean": tree["clean"],
        "branches_tracking_deleted_remotes": gone_branches,
        "local_only_branches": local_only,
        "archive_tags": tags,
        "open_prs": open_prs,
        "stash_count": len(stash),
        "stashes": stash,
        "branch_changed_during_check": branch_changed_during_check,
        "runtime_snapshot": _runtime_snapshot(root, log_path),
        "session_state_recorded": recorded_state is not None,
        "session_state_path": str(session_state_path(root)) if session_state_path(root) else None,
    }


def build_precommit_report(
    repo_root: str | Path,
    *,
    log_dir: str | Path = "logs",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    identity = repo_hygiene.repo_identity(root)
    tree = repo_hygiene.working_tree_status(root)
    upstream = repo_hygiene.upstream_info(root)
    recorded = read_session_start_state(root)

    fail_reasons: list[str] = []
    ok = True

    if recorded is None:
        ok = False
        fail_reasons.append(
            "session-start state cannot be verified: no recorded session-start marker "
            "found for this worktree (run `session-start` first)"
        )
    else:
        if recorded.get("worktree_path") != str(root):
            ok = False
            fail_reasons.append(
                f"worktree differs from session-start: recorded={recorded.get('worktree_path')!r} "
                f"current={str(root)!r}"
            )
        if recorded.get("branch") != identity["branch"]:
            ok = False
            fail_reasons.append(
                f"branch differs from session-start: recorded={recorded.get('branch')!r} "
                f"current={identity['branch']!r}"
            )
        if recorded.get("head_sha") != identity["head_sha"]:
            # Not automatically fatal (a legitimate commit moves HEAD), but must be
            # surfaced explicitly rather than silently assumed safe.
            fail_reasons.append(
                f"HEAD moved since session-start: recorded={recorded.get('head_sha')!r} "
                f"current={identity['head_sha']!r} (expected if you committed since session-start; "
                "verify this was YOUR commit)"
            )

    # A branch owned by another worktree is a collision risk even if this
    # worktree's own state looks internally consistent.
    other_worktrees = [w for w in repo_hygiene.worktrees(root) if Path(w["path"]).resolve() != root]
    branch_owned_elsewhere = [
        w for w in other_worktrees if w.get("branch") and w["branch"].endswith(f"/{identity['branch']}")
    ]
    if branch_owned_elsewhere:
        ok = False
        fail_reasons.append(
            f"intended branch {identity['branch']!r} appears checked out in another worktree: "
            + ", ".join(w["path"] for w in branch_owned_elsewhere)
        )

    if identity["branch"] == "UNKNOWN" or identity["head_sha"] == "UNKNOWN":
        ok = False
        fail_reasons.append("repository state is ambiguous: branch or HEAD could not be resolved")

    return {
        "routine": "precommit",
        "generated_at": _now().isoformat(),
        "repo_root": str(root),
        "current_branch": identity["branch"],
        "current_head_sha": identity["head_sha"],
        "session_start_branch": recorded.get("branch") if recorded else None,
        "session_start_worktree": recorded.get("worktree_path") if recorded else None,
        "session_start_head_sha": recorded.get("head_sha") if recorded else None,
        "current_worktree": str(root),
        "upstream": upstream,
        "changed_files": sorted(set(tree["dirty_files"]) | set(tree["staged_files"])),
        "staged_files": tree["staged_files"],
        "untracked_files": tree["untracked_files"],
        "ok": ok,
        "fail_closed": not ok,
        "fail_reasons": fail_reasons,
    }
