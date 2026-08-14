"""Read-only repository ownership preflight for research and promotion.

This routine is manually invoked before generating evidence or preparing a
promotion. It never fetches or modifies refs. Instead it compares the locally
known ``origin/main`` SHA with the SHA currently advertised by the remote and
fails closed when that comparison cannot be made.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.project_check import gitutil

PURPOSES = frozenset({"research", "promotion"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def build_ownership_preflight_report(
    purpose: str,
    *,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ValueError(f"unsupported preflight purpose: {purpose!r}")
    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    root = gitutil.repo_root(cwd_path)
    if root is None:
        return {
            "ok": False,
            "routine": "ownership-preflight",
            "purpose": purpose,
            "read_only": True,
            "bookkeeping_writes": [],
            "blockers": ["not inside a Git worktree"],
        }

    status = gitutil.status_porcelain(root)
    ownership = worktree_ownership(root)
    main = verified_origin_main(root)
    blockers: list[str] = []

    if status.get("error"):
        blockers.append(f"current worktree evidence could not be inspected: {status['error']}")
    else:
        if status["staged"]:
            blockers.append(
                "current worktree already has staged evidence: "
                + ", ".join(sorted(status["staged"]))
            )
        if status["dirty_tracked"]:
            blockers.append(
                "current worktree has unstaged tracked modifications: "
                + ", ".join(sorted(status["dirty_tracked"]))
            )
        if status["untracked"]:
            blockers.append(
                "current worktree already has untracked evidence: "
                + ", ".join(sorted(status["untracked"]))
            )

    blockers.extend(ownership["errors"])
    for duplicate in ownership["duplicate_branch_owners"]:
        blockers.append(
            f"branch {duplicate['branch']!r} is registered to multiple worktrees: "
            + ", ".join(duplicate["paths"])
        )

    if main["freshness"] != "CURRENT":
        blockers.append(
            f"origin/main verification is {main['freshness']}: {main['detail']}; "
            "refresh origin/main explicitly, then rerun"
        )
    elif main["head_contains_verified_main"] is not True:
        blockers.append(main["ancestry_detail"])

    return {
        "ok": not blockers,
        "routine": "ownership-preflight",
        "purpose": purpose,
        "generated_at": _now_iso(),
        "read_only": True,
        "bookkeeping_writes": [],
        "repo_root": str(root),
        "blockers": blockers,
        "current_worktree_evidence": {
            "staged": sorted(status.get("staged", [])),
            "dirty_tracked": sorted(status.get("dirty_tracked", [])),
            "untracked": sorted(status.get("untracked", [])),
            "error": status.get("error"),
        },
        "worktree_ownership": ownership,
        "origin_main": main,
    }
