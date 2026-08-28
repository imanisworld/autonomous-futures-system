"""Read-only git verification helpers used by Routine 1 (Session Safety).

These two checks were originally exposed as a standalone fourth
"ownership-preflight" routine. The system is consolidated to exactly three
routines (Session Safety, Strategy Promotion Proof Gate, Daily
Reconciliation + Trade Chain Integrity), so they now live here as helpers
consumed by ``ops.project_check.session``:

- ``verified_origin_main`` compares the locally known ``origin/main`` SHA
  with the SHA currently advertised by the live remote (via
  ``git ls-remote``, which never fetches or writes anything) so that
  "local origin/main looked in sync" and "origin/main is actually current"
  stay distinguishable -- ``gitutil.main_sync_state`` alone only compares
  against the last-fetched remote-tracking ref.
- ``worktree_ownership`` verifies the checked-out branch uniquely belongs to
  the current worktree (no detached HEAD, no branch registered to more than
  one worktree).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ops.project_check import gitutil


def _remote_main_sha(output: str) -> str | None:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "refs/heads/main":
            return fields[0]
    return None


def verified_origin_main(root: Path) -> dict[str, Any]:
    """Compare local origin/main to the live remote without updating either."""
    local = gitutil.run_git_result(
        ["rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
        cwd=root,
    )
    remote = gitutil.run_git_result(
        ["ls-remote", "--heads", "origin", "refs/heads/main"],
        cwd=root,
    )
    local_sha = local.stdout.strip() if local.returncode == 0 and local.stdout.strip() else None
    remote_sha = _remote_main_sha(remote.stdout) if remote.returncode == 0 else None

    if local_sha is None:
        freshness = "MISSING_LOCAL_REF"
        detail = "local refs/remotes/origin/main is missing"
    elif remote.returncode != 0:
        freshness = "UNVERIFIED"
        detail = remote.stderr or "origin/main could not be verified against origin"
    elif remote_sha is None:
        freshness = "MISSING_REMOTE_REF"
        detail = "origin did not advertise refs/heads/main"
    elif local_sha != remote_sha:
        freshness = "STALE"
        detail = "local origin/main differs from the branch currently advertised by origin"
    else:
        freshness = "CURRENT"
        detail = "local origin/main matches the live remote"

    contains: bool | None = None
    ancestry_detail = "not checked because origin/main was not verified current"
    if freshness == "CURRENT":
        ancestry = gitutil.run_git_result(
            [
                "merge-base",
                "--is-ancestor",
                "refs/remotes/origin/main",
                "HEAD",
            ],
            cwd=root,
        )
        if ancestry.returncode == 0:
            contains = True
            ancestry_detail = "HEAD contains verified current origin/main"
        elif ancestry.returncode == 1:
            contains = False
            ancestry_detail = "HEAD does not contain verified current origin/main"
        else:
            ancestry_detail = ancestry.stderr or "HEAD ancestry could not be verified"

    return {
        "freshness": freshness,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "detail": detail,
        "head_contains_verified_main": contains,
        "ancestry_detail": ancestry_detail,
    }


def worktree_ownership(root: Path) -> dict[str, Any]:
    """Verify an attached branch uniquely belongs to the current worktree."""
    branch = gitutil.current_branch(root)
    detached = gitutil.is_detached_head(root)
    registrations = gitutil.worktrees(root)
    current_path = str(root.resolve())
    current = next(
        (item for item in registrations if str(Path(item.path).resolve()) == current_path),
        None,
    )

    owners: dict[str, set[str]] = defaultdict(set)
    for item in registrations:
        if item.branch:
            owners[item.branch].add(str(Path(item.path).resolve()))
    duplicates = [
        {"branch": name, "paths": sorted(paths)}
        for name, paths in sorted(owners.items())
        if len(paths) > 1
    ]

    errors: list[str] = []
    if detached:
        errors.append("detached HEAD has no auditable branch ownership")
    if current is None:
        errors.append("current worktree is absent from `git worktree list --porcelain`")
    elif not detached and current.branch != branch:
        errors.append(
            "current worktree registration does not own the checked-out branch "
            f"({current.branch!r} != {branch!r})"
        )

    return {
        "ok": not errors and not duplicates,
        "current_branch": branch,
        "current_worktree": current_path,
        "detached_head": detached,
        "current_registration": current.as_dict() if current else None,
        "duplicate_branch_owners": duplicates,
        "errors": errors,
    }
