"""Routine 1: Session Safety + Runtime Snapshot.

Two modes:
  A. build_session_start_report()  -- full repo/runtime snapshot; the only
     thing in this module that writes anything, and all it writes is a small
     session-state cache file under .git/ (never a tracked path, never
     committed) so precommit can later detect drift. Also folds in the
     ownership/origin-freshness check from ops.project_check.preflight (a
     read-only `git ls-remote` plus worktree-ownership check) so there is a
     single before-you-start check instead of a separate routine.
  B. build_precommit_report()      -- strictly read-only; fails closed on
     branch/worktree drift, a worktree/branch ownership collision, or an
     unverifiable session-start baseline. Never commits, pushes, pulls,
     resets, rebases, checks out, deletes a branch/worktree, drops a stash,
     creates/deletes a tag, or modifies files.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.project_check import gitutil
from ops.project_check.preflight import build_ownership_preflight_report, worktree_ownership
from ops.project_check.runtime import runtime_snapshot

STATE_SUBDIR = "afs-project-check"
STATE_FILENAME = "session_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(root: Path) -> Path:
    return root / ".git" / STATE_SUBDIR / STATE_FILENAME


def _save_state(root: Path, state: dict[str, Any]) -> None:
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{STATE_FILENAME}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _load_state(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    path = _state_path(root)
    if not path.exists():
        return None, "no session-start state file found -- run session-start first"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"session-start state file unreadable/corrupt: {exc}"


def build_session_start_report(*, cwd: str | Path | None = None) -> dict[str, Any]:
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    root = gitutil.repo_root(cwd_path)
    if root is None:
        return {"ok": False, "error": "not inside a git repository", "cwd": str(cwd_path)}

    branch_before = gitutil.current_branch(root)
    head_before = gitutil.head_sha(root)

    main_sync = gitutil.main_sync_state(root)
    upstream = gitutil.upstream_of(root)
    status = gitutil.status_porcelain(root)
    all_worktrees = [w.as_dict() for w in gitutil.worktrees(root)]
    current_wt = next((w for w in all_worktrees if Path(w["path"]).resolve() == root.resolve()), None)
    branches_tracking_deleted_remotes = [
        b for b in gitutil.local_branches(root) if b["tracking_deleted_remote"]
    ]
    local_only_branches = [b for b in gitutil.local_branches(root) if b["local_only"]]
    archive_tags = gitutil.archive_tags(root)
    stashes = gitutil.stash_list(root)
    prs = gitutil.open_prs(root)
    closed_unmerged = gitutil.unmerged_remote_branches_missing_archive_tag(root)
    runtime = runtime_snapshot(repo_root=root)
    # Live remote freshness + worktree-ownership check -- the only thing here
    # that touches the network (a read-only `git ls-remote`, never a fetch).
    # This is the same machinery a standalone "preflight" routine used to
    # expose on its own; folded in here so session-start remains the single
    # before-you-start check instead of a fourth routine.
    ownership_preflight = build_ownership_preflight_report("research", cwd=root)

    branch_after = gitutil.current_branch(root)
    head_after = gitutil.head_sha(root)
    branch_changed_during_check = branch_before != branch_after or head_before != head_after

    report = {
        "ok": True,
        "routine": "session-start",
        "generated_at": _now_iso(),
        "repo": {
            "repo_root": str(root),
            "current_branch": branch_after,
            "head_sha": head_after,
            "origin_main_sha": (
                gitutil.ref_sha(root, main_sync["remote_ref"]) if main_sync.get("remote_ref") else None
            ),
            "local_main_relationship": main_sync,
            "upstream": upstream,
            "current_worktree": current_wt,
            "all_worktrees": all_worktrees,
            "dirty_tracked_files": status.get("dirty_tracked", []),
            "staged_files": status.get("staged", []),
            "untracked_files": status.get("untracked", []),
            "branches_tracking_deleted_remotes": branches_tracking_deleted_remotes,
            "local_only_branches": local_only_branches,
            "open_prs": prs,
            "closed_unmerged_branches_missing_archive_tag": closed_unmerged,
            "archive_tags": archive_tags,
            "stash_count": len(stashes),
            "stashes": stashes,
        },
        "branch_changed_during_check": branch_changed_during_check,
        "runtime_snapshot": runtime,
        "ownership_preflight": ownership_preflight,
    }

    _save_state(
        root,
        {
            "saved_at": report["generated_at"],
            "repo_root": str(root),
            "branch": branch_after,
            "head_sha": head_after,
            "worktree_path": str(root.resolve()),
            "upstream": upstream,
        },
    )
    return report


def build_precommit_report(*, cwd: str | Path | None = None) -> dict[str, Any]:
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    root = gitutil.repo_root(cwd_path)
    if root is None:
        return {
            "ok": False,
            "routine": "precommit",
            "verdict": "FAIL_CLOSED",
            "reasons": ["not inside a git repository -- repository state is ambiguous"],
        }

    state, state_error = _load_state(root)
    reasons: list[str] = []
    if state_error:
        reasons.append(f"session-start state cannot be verified: {state_error}")
    elif state.get("repo_root") != str(root):
        reasons.append(
            "session-start state cannot be verified: it was captured for a different "
            f"repo_root ({state.get('repo_root')!r} != {str(root)!r})"
        )

    branch = gitutil.current_branch(root)
    head = gitutil.head_sha(root)
    upstream = gitutil.upstream_of(root)
    status = gitutil.status_porcelain(root)
    all_worktrees = [w.as_dict() for w in gitutil.worktrees(root)]
    current_wt = next((w for w in all_worktrees if Path(w["path"]).resolve() == root.resolve()), None)

    if status.get("error"):
        reasons.append(f"repository state is ambiguous: git status failed ({status['error']})")

    if state and not state_error:
        session_branch = state.get("branch")
        session_worktree = state.get("worktree_path")
        session_head = state.get("head_sha")
        current_worktree_path = str(root.resolve())

        if session_branch is not None and branch != session_branch:
            reasons.append(
                f"branch differs from session-start branch unexpectedly: "
                f"session-start={session_branch!r} now={branch!r}"
            )
        if session_worktree is not None and current_worktree_path != session_worktree:
            reasons.append(
                f"worktree differs from session-start worktree unexpectedly: "
                f"session-start={session_worktree!r} now={current_worktree_path!r}"
            )
        if (
            session_branch is not None
            and branch == session_branch
            and session_head is not None
            and head != session_head
        ):
            reasons.append(
                f"branch moved unexpectedly since session-start on the same branch "
                f"{branch!r}: session-start HEAD={session_head} now HEAD={head} "
                f"(nothing in this session should change HEAD before a commit)"
            )

    owner = next((w for w in all_worktrees if w.get("branch") == branch), None)
    if owner is not None and Path(owner["path"]).resolve() != root.resolve():
        reasons.append(
            f"intended branch {branch!r} is checked out in another worktree: {owner['path']}"
        )

    # No live network call here (unlike session-start's ownership_preflight) --
    # precommit stays fast and local; it only re-checks worktree/branch
    # ownership, which is derivable from already-registered worktrees.
    ownership = worktree_ownership(root)
    reasons.extend(ownership["errors"])
    for duplicate in ownership["duplicate_branch_owners"]:
        reasons.append(
            f"branch {duplicate['branch']!r} is registered to multiple worktrees: "
            + ", ".join(duplicate["paths"])
        )

    verdict = "FAIL_CLOSED" if reasons else "OK"
    return {
        "ok": verdict == "OK",
        "routine": "precommit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "reasons": reasons,
        "repo": {
            "repo_root": str(root),
            "current_branch": branch,
            "head_sha": head,
            "session_start_branch": (state or {}).get("branch"),
            "session_start_worktree": (state or {}).get("worktree_path"),
            "session_start_head_sha": (state or {}).get("head_sha"),
            "current_worktree": current_wt,
            "upstream": upstream,
            "ahead_behind_upstream": _ahead_behind_upstream(root, upstream),
            "changed_files": status.get("dirty_tracked", []),
            "staged_files": status.get("staged", []),
            "untracked_files": status.get("untracked", []),
        },
        "worktree_ownership": ownership,
        "note": (
            "This routine is read-only and never commits/pushes/pulls/resets/rebases/"
            "checks out/deletes branches or worktrees/drops stashes/creates or deletes "
            "tags/modifies files. It does not classify individual changed files as "
            "'unexpected' -- that judgment is the operator's; the full changed/staged/"
            "untracked lists above are for that review."
        ),
    }


def _ahead_behind_upstream(root: Path, upstream: str | None) -> dict[str, Any]:
    if not upstream:
        return {"state": "UNKNOWN", "reason": "no upstream configured for current branch"}
    out, err = gitutil.run_git(["rev-list", "--left-right", "--count", f"{upstream}...HEAD"], cwd=root)
    if not out:
        return {"state": "UNKNOWN", "reason": err or "rev-list failed"}
    parts = out.strip().split()
    if len(parts) != 2:
        return {"state": "UNKNOWN", "reason": f"unexpected rev-list output: {out!r}"}
    behind, ahead = int(parts[0]), int(parts[1])
    return {"ahead": ahead, "behind": behind}
