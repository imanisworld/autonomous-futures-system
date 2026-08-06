"""Read-only git/worktree/branch/stash state helpers.

Shared by the session-safety (``ops/session_check.py``) and daily
reconciliation (``ops/daily_check.py``) routines. Every function here only
shells out to read-only git subcommands (``status``, ``branch``,
``worktree list``, ``stash list``, ``rev-parse``, ``for-each-ref``,
``tag -l``, ``log``, ``diff --name-only``). Nothing in this module commits,
pushes, pulls, resets, rebases, checks out, deletes a branch, removes a
worktree, drops a stash, or creates/deletes a tag.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

UNKNOWN = "UNKNOWN"
GIT_TIMEOUT_S = 10.0


def _run_git(repo_root: Path, *args: str, timeout: float = GIT_TIMEOUT_S) -> tuple[str | None, str | None]:
    """Run one read-only git command. Returns (stdout, error); never raises."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"git {' '.join(args)} exited {result.returncode}"
        return None, detail
    return result.stdout, None


def _lines(repo_root: Path, *args: str) -> list[str]:
    out, _ = _run_git(repo_root, *args)
    if out is None:
        return []
    return [line for line in out.splitlines() if line.strip()]


def repo_root_of(start: str | Path = ".") -> Path | None:
    out, _ = _run_git(Path(start), "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out else None


def current_branch(repo_root: Path) -> str | None:
    out, _ = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() if out else None


def rev_parse(repo_root: Path, ref: str) -> str | None:
    out, _ = _run_git(repo_root, "rev-parse", ref)
    return out.strip() if out else None


def upstream_ref(repo_root: Path) -> str | None:
    out, _ = _run_git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    return out.strip() if out else None


def ahead_behind(repo_root: Path, local_ref: str, remote_ref: str) -> tuple[int, int] | None:
    """Return (ahead, behind) of local_ref vs remote_ref, or None if unavailable."""
    out, _ = _run_git(repo_root, "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}")
    if not out:
        return None
    parts = out.strip().split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def sync_status(ahead_behind_pair: tuple[int, int] | None) -> str:
    if ahead_behind_pair is None:
        return UNKNOWN
    ahead, behind = ahead_behind_pair
    if ahead == 0 and behind == 0:
        return "IN_SYNC"
    if ahead > 0 and behind == 0:
        return "AHEAD"
    if ahead == 0 and behind > 0:
        return "BEHIND"
    return "DIVERGED"


def porcelain_status(repo_root: Path) -> list[str]:
    return _lines(repo_root, "status", "--porcelain=v1")


def parse_status(status_lines: list[str]) -> dict[str, list[str]]:
    """Split ``git status --porcelain`` output into staged/unstaged/untracked."""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in status_lines:
        if len(line) < 4:
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            untracked.append(path)
            continue
        index_status, worktree_status = code[0], code[1]
        if index_status not in (" ", "?"):
            staged.append(path)
        if worktree_status not in (" ", "?"):
            unstaged.append(path)
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def list_worktrees(repo_root: Path) -> list[dict[str, Any]]:
    out, _ = _run_git(repo_root, "worktree", "list", "--porcelain")
    if not out:
        return []
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        if current:
            current.setdefault("bare", False)
            current.setdefault("detached", False)
            current.setdefault("locked", False)
            worktrees.append(dict(current))

    for line in out.splitlines():
        if not line.strip():
            flush()
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip()
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    flush()
    return worktrees


def worktree_for_path(worktrees: list[dict[str, Any]], path: Path) -> dict[str, Any] | None:
    resolved = str(path.resolve())
    for wt in worktrees:
        wt_path = wt.get("path")
        if wt_path and str(Path(wt_path).resolve()) == resolved:
            return wt
    return None


def branch_summary(repo_root: Path) -> list[dict[str, Any]]:
    """One row per local branch: upstream, and whether it tracks a deleted remote."""
    out, _ = _run_git(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
        "refs/heads/",
    )
    rows: list[dict[str, Any]] = []
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        if not name:
            continue
        rows.append(
            {
                "branch": name,
                "upstream": upstream or None,
                "tracking_deleted_remote": "gone" in track,
                "local_only": not upstream,
            }
        )
    return rows


def merged_branches(repo_root: Path, base: str = "main") -> list[str]:
    return [b.strip().lstrip("* ").strip() for b in _lines(repo_root, "branch", "--merged", base)]


def unmerged_branches(repo_root: Path, base: str = "main") -> list[str]:
    return [b.strip().lstrip("* ").strip() for b in _lines(repo_root, "branch", "--no-merged", base)]


def stash_list(repo_root: Path) -> list[str]:
    return _lines(repo_root, "stash", "list")


def archive_tags(repo_root: Path) -> list[str]:
    return sorted(_lines(repo_root, "tag", "-l", "archive/*"))


def unique_commits(repo_root: Path, branch: str, base: str = "main") -> list[str]:
    """Commits reachable from ``branch`` but not ``base`` (oneline)."""
    return _lines(repo_root, "log", f"{base}..{branch}", "--oneline")


def unique_files(repo_root: Path, branch: str, base: str = "main") -> list[str]:
    """Files that differ between ``base`` and ``branch``."""
    return _lines(repo_root, "diff", "--name-only", f"{base}...{branch}")


def git_state_report(repo_root: str | Path, *, base_branch: str = "main") -> dict[str, Any]:
    """One deterministic, read-only snapshot of repo/branch/worktree state."""
    root = Path(repo_root).resolve()
    branch_before = current_branch(root)
    head = rev_parse(root, "HEAD")
    origin_base = rev_parse(root, f"origin/{base_branch}")
    upstream = upstream_ref(root)
    pair = ahead_behind(root, "HEAD", f"origin/{base_branch}") if origin_base else None
    status_lines = porcelain_status(root)
    parsed_status = parse_status(status_lines)
    worktrees = list_worktrees(root)
    this_worktree = worktree_for_path(worktrees, root)
    branches = branch_summary(root)
    branch_after = current_branch(root)

    return {
        "repo_root": str(root),
        "current_branch": branch_after,
        "branch_changed_during_check": branch_before is not None and branch_after is not None and branch_before != branch_after,
        "head_sha": head,
        "base_branch": base_branch,
        "origin_base_sha": origin_base,
        "local_main_relationship": sync_status(pair),
        "ahead": pair[0] if pair else None,
        "behind": pair[1] if pair else None,
        "upstream": upstream,
        "current_worktree": this_worktree,
        "worktrees": worktrees,
        "dirty_tracked_files": parsed_status["unstaged"],
        "staged_files": parsed_status["staged"],
        "untracked_files": parsed_status["untracked"],
        "branches_tracking_deleted_remotes": [b["branch"] for b in branches if b["tracking_deleted_remote"]],
        "local_only_branches": [b["branch"] for b in branches if b["local_only"]],
        "branch_summary": branches,
        "merged_into_base": merged_branches(root, base_branch),
        "unmerged_into_base": unmerged_branches(root, base_branch),
        "archive_tags": archive_tags(root),
        "stash_count": len(stash_list(root)),
        "stash_labels": stash_list(root),
    }
