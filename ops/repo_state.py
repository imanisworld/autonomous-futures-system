"""Read-only git/worktree/branch introspection shared by the session-safety and
daily-reconciliation routines.

Every function in this module is a plain read: `status`, `rev-parse`, `branch`,
`worktree list`, `stash list`, `tag -l`, and (best-effort, optional) `gh pr
list`. Nothing here fetches, checks out, commits, pushes, prunes, or deletes
anything. Callers that need a mutation (fetch to refresh origin/*, `gh`
auth, ...) must do it themselves before calling in; this module never does it
for them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_git(repo_root: Path, *args: str, timeout: float = 5.0) -> str | None:
    """Run a read-only git subcommand; return stripped stdout or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def run_git_lines(repo_root: Path, *args: str, timeout: float = 5.0) -> list[str]:
    out = run_git(repo_root, *args, timeout=timeout)
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def repo_root_of(start: Path) -> Path | None:
    out = run_git(start, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def current_branch(repo_root: Path) -> str | None:
    branch = run_git(repo_root, "branch", "--show-current")
    if branch:
        return branch
    # Detached HEAD: --show-current prints empty string (run_git -> None).
    return None


def head_sha(repo_root: Path) -> str | None:
    return run_git(repo_root, "rev-parse", "HEAD")


def is_detached_head(repo_root: Path) -> bool:
    return current_branch(repo_root) is None and head_sha(repo_root) is not None


def upstream_branch(repo_root: Path) -> str | None:
    return run_git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def ref_sha(repo_root: Path, ref: str) -> str | None:
    return run_git(repo_root, "rev-parse", ref)


def ahead_behind(repo_root: Path, local_ref: str, remote_ref: str) -> tuple[int, int] | None:
    """(ahead, behind) of local_ref relative to remote_ref, or None if either ref is unreachable."""
    out = run_git(repo_root, "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}")
    if not out:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def sync_relationship(repo_root: Path, local_ref: str, remote_ref: str) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local_ref vs remote_ref."""
    counts = ahead_behind(repo_root, local_ref, remote_ref)
    if counts is None:
        return "UNKNOWN"
    ahead, behind = counts
    if ahead == 0 and behind == 0:
        return "IN_SYNC"
    if ahead > 0 and behind == 0:
        return "AHEAD"
    if ahead == 0 and behind > 0:
        return "BEHIND"
    return "DIVERGED"


def dirty_files(repo_root: Path) -> dict[str, list[str]]:
    """Split `git status --porcelain=v1` into staged / unstaged(tracked) / untracked."""
    lines = run_git_lines(repo_root, "status", "--porcelain=v1")
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if len(line) < 3:
            continue
        index_state, worktree_state, rest = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(rest)
            continue
        if index_state not in (" ", "?"):
            staged.append(rest)
        if worktree_state not in (" ", "?"):
            unstaged.append(rest)
    return {"staged": staged, "unstaged_tracked": unstaged, "untracked": untracked}


def list_worktrees(repo_root: Path) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` into one dict per worktree."""
    out = run_git(repo_root, "worktree", "list", "--porcelain")
    if not out:
        return []
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "locked":
            current["locked"] = True
    if current:
        worktrees.append(current)
    return worktrees


def local_branches(repo_root: Path) -> list[str]:
    return run_git_lines(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/")


def remote_branches(repo_root: Path) -> list[str]:
    return run_git_lines(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/")


def merged_into(repo_root: Path, base: str) -> list[str]:
    return run_git_lines(repo_root, "branch", "--format=%(refname:short)", "--merged", base)


def not_merged_into(repo_root: Path, base: str) -> list[str]:
    return run_git_lines(repo_root, "branch", "--format=%(refname:short)", "--no-merged", base)


def branches_tracking_deleted_remotes(repo_root: Path) -> list[str]:
    """Local branches whose upstream is gone (`git branch -vv` shows ': gone]')."""
    lines = run_git_lines(repo_root, "branch", "-vv")
    gone: list[str] = []
    for line in lines:
        stripped = line.lstrip("* ").strip()
        if ": gone]" in stripped:
            name = stripped.split()[0]
            gone.append(name)
    return gone


def local_only_branches(repo_root: Path) -> list[str]:
    """Local branches with no `origin/<name>` remote-tracking counterpart."""
    locals_ = set(local_branches(repo_root))
    remotes = set(remote_branches(repo_root))
    remote_names = {ref.split("/", 1)[1] for ref in remotes if "/" in ref}
    return sorted(name for name in locals_ if name not in remote_names)


def stash_list(repo_root: Path) -> list[str]:
    return run_git_lines(repo_root, "stash", "list")


def archive_tags(repo_root: Path) -> list[str]:
    return run_git_lines(repo_root, "tag", "-l", "archive/*")


def _slugify(branch: str) -> str:
    return branch.replace("/", "-").replace("_", "-").lower()


def branches_missing_archive_tag(repo_root: Path, base: str = "main") -> list[dict[str, Any]]:
    """Local-only branches not merged into `base` and not already deleted, cross-checked
    against `archive/*` tags by slug-prefix match. Best-effort convention match (see
    docs/BRANCH_ARCHIVE_INDEX.md); anything ambiguous is reported, never assumed safe.
    """
    unmerged = set(not_merged_into(repo_root, base))
    tags = archive_tags(repo_root)
    findings: list[dict[str, Any]] = []
    for branch in sorted(unmerged):
        slug = _slugify(branch)
        matching_tags = [tag for tag in tags if slug in _slugify(tag)]
        findings.append({
            "branch": branch,
            "unmerged_into": base,
            "matching_archive_tags": matching_tags,
            "has_archive_tag": bool(matching_tags),
        })
    return findings


def gh_pr_list(repo_root: Path, *, state: str = "all", head: str | None = None,
                limit: int = 100, timeout: float = 8.0) -> list[dict[str, Any]] | None:
    """Best-effort `gh pr list` (read-only). Returns None (=> caller reports UNKNOWN)
    if the `gh` binary is missing, unauthenticated, or the call times out/errors.
    """
    args = [
        "gh", "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,state,headRefName,baseRefName,mergedAt,closedAt,updatedAt,createdAt",
    ]
    if head:
        args += ["--head", head]
    try:
        result = subprocess.run(
            args, cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    import json
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def repo_snapshot(repo_root: Path, *, base: str = "main", remote: str = "origin") -> dict[str, Any]:
    """One read-only pass over the common repo-hygiene surface, reused by both
    the session-safety and daily-reconciliation routines."""
    branch = current_branch(repo_root)
    head = head_sha(repo_root)
    upstream = upstream_branch(repo_root)
    remote_main_ref = f"{remote}/{base}"
    remote_main_sha = ref_sha(repo_root, remote_main_ref)
    local_main_relationship = (
        sync_relationship(repo_root, base, remote_main_ref) if remote_main_sha else "UNKNOWN"
    )
    files = dirty_files(repo_root)
    worktrees = list_worktrees(repo_root)
    this_worktree = None
    for wt in worktrees:
        wt_path = Path(wt.get("path", "")).resolve()
        if wt_path == repo_root.resolve():
            this_worktree = wt
            break
    return {
        "repo_root": str(repo_root),
        "branch": branch,
        "detached_head": is_detached_head(repo_root),
        "head_sha": head,
        "upstream": upstream,
        "remote_main_ref": remote_main_ref,
        "remote_main_sha": remote_main_sha,
        "local_main_relationship": local_main_relationship,
        "current_worktree": this_worktree,
        "worktrees": worktrees,
        "dirty_tracked_files": files["unstaged_tracked"],
        "staged_files": files["staged"],
        "untracked_files": files["untracked"],
        "branches_tracking_deleted_remotes": branches_tracking_deleted_remotes(repo_root),
        "local_only_branches": local_only_branches(repo_root),
        "branches_missing_archive_tag": branches_missing_archive_tag(repo_root, base=base),
        "archive_tags": archive_tags(repo_root),
        "stash_list": stash_list(repo_root),
        "stash_count": len(stash_list(repo_root)),
    }
