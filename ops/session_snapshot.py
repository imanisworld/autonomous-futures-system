"""Routine 1: Session Safety + Runtime Snapshot.

Two modes:

- ``session_start_report`` — full repo/worktree/branch/stash/PR picture plus
  a best-effort runtime snapshot (deployed release identity, active
  paper-forward lanes, entry fill model/tolerance per lane, contract cap),
  and persists a small local state file (inside ``.git/``, never tracked)
  recording the branch/worktree/HEAD this session started from.
- ``precommit_report`` — compares current branch/worktree/HEAD against that
  recorded session-start state and fails closed on any unexplained drift.
  READ ONLY: never commits, pushes, pulls, resets, rebases, checks out,
  deletes branches/worktrees/stashes, creates/deletes tags, or modifies
  files.

Reuses ``ops.repo_state`` for all git introspection and
``ops.live_box_guard.live_box_drift_report`` for the runtime-override /
risk_rules fingerprint / security-runtime pieces rather than re-deriving
them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ops import repo_state as rs
from ops.live_box_guard import live_box_drift_report

SESSION_STATE_FILENAME = "ops_session_check.json"


def _session_state_path(root: Path) -> Path | None:
    admin_dir = rs.git_dir(root)
    return admin_dir / SESSION_STATE_FILENAME if admin_dir else None


def runtime_snapshot(root: Path) -> dict[str, Any]:
    """Best-effort deployed/runtime posture. UNKNOWN, never guessed, when a
    value isn't available from existing local/ops data."""
    from ops.release_integrity import DEFAULT_MANIFEST_NAME

    manifest_path = root / DEFAULT_MANIFEST_NAME
    deployed = {"release_manifest_found": manifest_path.exists()}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            repo_info = manifest.get("repo") or {}
            deployed["intended_release_sha"] = repo_info.get("commit") or rs.UNKNOWN
            deployed["intended_release_branch"] = repo_info.get("branch") or rs.UNKNOWN
            deployed["manifest_built_at"] = manifest.get("built_at") or rs.UNKNOWN
        except (OSError, ValueError):
            deployed["intended_release_sha"] = rs.UNKNOWN
            deployed["intended_release_branch"] = rs.UNKNOWN
            deployed["manifest_built_at"] = rs.UNKNOWN
    else:
        deployed["intended_release_sha"] = rs.UNKNOWN
        deployed["intended_release_branch"] = rs.UNKNOWN
        deployed["manifest_built_at"] = rs.UNKNOWN

    lanes: dict[str, Any] = {
        "evidence_epoch": rs.UNKNOWN,
        "evidence_epoch_note": (
            "No 'evidence epoch' concept exists in this repo's code/docs today "
            "(confirmed by inspection) — this is not a value that was missed, "
            "the concept has not yet been defined/tracked anywhere to read."
        ),
    }
    try:
        from config.settings import load_config

        config = load_config(str(root / "risk_rules.yaml"))
        active_lanes = sorted(
            name for name, status in config.strategy_status.items() if status == "PAPER_ELIGIBLE"
        )
        active_and_enabled = [name for name in active_lanes if name in config.enabled_concepts]
        lanes["strategy_permission_gate_enabled"] = config.strategy_permission_gate_enabled
        lanes["paper_eligible_strategies"] = active_lanes
        lanes["paper_eligible_and_enabled_strategies"] = active_and_enabled
        lanes["enabled_concepts"] = list(config.enabled_concepts)
        lanes["entry_fill_model_global"] = config.entry_fill_model
        lanes["entry_tolerance_ticks_by_root"] = dict(config.entry_tolerance_ticks_by_root)
        lanes["max_contracts_hard_cap"] = config.max_contracts_hard_cap
        lanes["note"] = (
            "entry_fill_model and contract cap are process-wide, not literally "
            "per-lane; per-strategy proof-mode env overrides (if any are "
            "active) are reported separately under live_box_drift."
        )
    except Exception as exc:  # config load is best-effort evidence, never fatal here
        lanes["strategy_permission_gate_enabled"] = rs.UNKNOWN
        lanes["paper_eligible_strategies"] = rs.UNKNOWN
        lanes["load_error"] = f"{type(exc).__name__}: {exc}"

    # ops.live_box_guard already computes the risk_rules.yaml fingerprint,
    # which proof-critical runtime overrides are active (and whether they're
    # pinned/reproducible), and webhook-secret rotation state — exactly the
    # "does runtime differ from the evidence assumptions" question this
    # section needs. Reused wholesale rather than re-derived.
    drift = live_box_drift_report(repo_root=root)
    config_vs_evidence = {
        "live_box_guard_status": drift["status"],
        "risk_rules_sha256": drift["risk_rules_sha256"],
        "active_runtime_overrides": drift["active_runtime_overrides"],
        "unpinned_runtime_overrides": drift["unpinned_runtime_overrides"],
        "security_runtime_ok": drift["security_runtime"]["ok"],
        "note": (
            "An active, unpinned proof-critical override (see "
            "unpinned_runtime_overrides) means this process's runtime "
            "cannot be assumed to match whatever config a strategy's "
            "journaled evidence was collected under — reconcile with the "
            "promotion routine's execution_context for that strategy."
        ),
    }

    return {
        "deployed_release": deployed,
        "paper_forward_lanes": lanes,
        "live_box_drift": drift,
        "config_vs_evidence_assumptions": config_vs_evidence,
    }


def session_start_report(
    *,
    repo_root: str | Path | None = None,
    do_fetch: bool = True,
) -> dict[str, Any]:
    root = rs.find_repo_root(repo_root)
    if root is None:
        return {"ok": False, "status": "error", "summary": "Not inside a git repository.", "repo_root": rs.UNKNOWN}

    fetched = rs.fetch_remote(root, tags=True) if do_fetch else False

    branch_before = rs.current_branch(root)
    head_before = rs.head_sha(root)
    worktree = rs.current_worktree(root)

    dirty = rs.dirty_status(root)
    branch_after = rs.current_branch(root)
    branch_changed_during_check = branch_after != branch_before

    sync = rs.main_sync_state(root)
    gone_branches = [b["name"] for b in rs.local_branches(root) if b["gone"]]
    local_only_branches = [b["name"] for b in rs.local_branches(root) if b["upstream"] is None and b["name"] != "main"]
    evidence = rs.unmerged_branch_evidence(root)
    prs = rs.pull_requests(root, state="open", limit=50)

    runtime = runtime_snapshot(root)

    state_path = _session_state_path(root)
    persisted = False
    if state_path is not None:
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(
                    {
                        "branch": branch_after,
                        "worktree": worktree,
                        "head_sha": head_before,
                        "repo_root": str(root),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            persisted = True
        except OSError:
            persisted = False

    blockers = []
    if dirty["staged"] or dirty["unstaged"]:
        pass  # dirty tracked files are reported, not automatically a blocker for starting a session
    if branch_changed_during_check:
        blockers.append("checked-out branch changed while this check was running")

    ok = not blockers
    return {
        "ok": ok,
        "status": "ok" if ok else "warn",
        "mode": "session-start",
        "repo_root": str(root),
        "branch": branch_after,
        "branch_changed_during_check": branch_changed_during_check,
        "head_sha": head_before,
        "origin_main_sha": sync["remote_sha"],
        "main_sync_state": sync["state"],
        "main_sync_detail": sync,
        "fetched_before_check": fetched,
        "upstream": rs.upstream_branch(root),
        "worktree": worktree,
        "worktrees": rs.worktrees(root),
        "dirty_tracked_files": dirty["staged"] + dirty["unstaged"],
        "staged_files": dirty["staged"],
        "untracked_files": dirty["untracked"],
        "branches_tracking_deleted_remotes": gone_branches,
        "local_only_branches": local_only_branches,
        "open_pull_requests": prs,
        "closed_unmerged_evidence": evidence,
        "archive_tags": evidence["archive_tags"],
        "stashes": rs.stashes(root),
        "runtime_snapshot": runtime,
        "session_state_persisted": persisted,
        "session_state_path": str(state_path) if state_path else None,
        "blockers": blockers,
    }


def precommit_report(*, repo_root: str | Path | None = None) -> dict[str, Any]:
    """READ ONLY. Never commits, pushes, pulls, resets, rebases, checks out,
    deletes branches/worktrees/stashes, creates/deletes tags, or modifies
    files — it only compares current state to the recorded session-start
    state and reports."""
    root = rs.find_repo_root(repo_root)
    if root is None:
        return {"ok": False, "status": "FAIL_CLOSED", "summary": "Not inside a git repository.", "repo_root": rs.UNKNOWN}

    branch = rs.current_branch(root)
    head = rs.head_sha(root)
    worktree = rs.current_worktree(root)
    upstream = rs.upstream_branch(root)
    sync = rs.main_sync_state(root, local_ref="HEAD", remote_ref=f"{upstream}") if upstream != rs.UNKNOWN else None
    dirty = rs.dirty_status(root)

    state_path = _session_state_path(root)
    session_state: dict[str, Any] | None = None
    if state_path is not None and state_path.exists():
        try:
            session_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            session_state = None

    reasons: list[str] = []
    if session_state is None:
        reasons.append("session-start state could not be verified (no recorded state found or unreadable)")
    else:
        if session_state.get("branch") != branch:
            reasons.append(
                f"branch differs from session-start branch: started on "
                f"{session_state.get('branch')!r}, now on {branch!r}"
            )
        if session_state.get("worktree") != worktree:
            reasons.append(
                f"worktree differs from session-start worktree: started in "
                f"{session_state.get('worktree')!r}, now in {worktree!r}"
            )

    # Is the intended branch (this worktree's branch) checked out in another worktree?
    all_worktrees = rs.worktrees(root)
    owners = [w for w in all_worktrees if w.get("branch") == branch]
    if len(owners) > 1:
        reasons.append(f"branch {branch!r} is checked out in more than one worktree: " + ", ".join(w["path"] for w in owners))

    ambiguous = not dirty["ok"]
    if ambiguous:
        reasons.append("repository state is ambiguous: `git status` could not be read")

    fail_closed = bool(reasons)
    return {
        "ok": not fail_closed,
        "status": "FAIL_CLOSED" if fail_closed else "PASS",
        "mode": "precommit",
        "read_only": True,
        "repo_root": str(root),
        "branch": branch,
        "head_sha": head,
        "worktree": worktree,
        "upstream": upstream,
        "ahead_behind_upstream": sync,
        "session_start_state": session_state,
        "changed_files": dirty["staged"] + dirty["unstaged"],
        "staged_files": dirty["staged"],
        "untracked_files": dirty["untracked"],
        "fail_reasons": reasons,
    }
