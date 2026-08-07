"""Read-only git/worktree/branch/PR helpers shared by ops.project_check.

Every function here is a pure read: no commit, push, pull, reset, rebase,
checkout, branch/worktree deletion, stash drop, or tag create/delete. Git
plumbing reuses ``ops.live_box_guard._git`` (same 2s-timeout subprocess
wrapper already trusted by the deploy-drift guard) instead of re-implementing
subprocess handling.

GitHub PR data is read via the ``gh`` CLI, best-effort: any missing binary,
auth failure, or timeout degrades to ``available: False`` rather than
raising, matching the idiom already used by ``scripts/options_pr_audit.py``.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ops.live_box_guard import _git

UNKNOWN = "UNKNOWN"
GH_TIMEOUT_S = 20.0


def find_repo_root(start: Path | None = None) -> Path | None:
    top = _git(start or Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(top) if top else None


def current_branch(root: Path) -> str | None:
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        return None  # detached HEAD
    return branch


def head_sha(root: Path) -> str | None:
    return _git(root, "rev-parse", "HEAD")


def resolve_ref(root: Path, ref: str) -> str | None:
    return _git(root, "rev-parse", ref)


def upstream_ref(root: Path) -> str | None:
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def sync_status(root: Path, ref: str | None, compare_ref: str = "HEAD") -> str:
    """`compare_ref`'s relationship to `ref`: IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN.

    Uses locally-known refs only (no fetch) — the caller is responsible for
    telling the operator the comparison may be stale relative to the remote.
    "AHEAD" means compare_ref has commits ref doesn't; "BEHIND" the reverse.
    """
    if not ref:
        return UNKNOWN
    counts = _git(root, "rev-list", "--left-right", "--count", f"{ref}...{compare_ref}")
    if not counts:
        return UNKNOWN
    parts = counts.split()
    if len(parts) != 2:
        return UNKNOWN
    try:
        behind, ahead = int(parts[0]), int(parts[1])
    except ValueError:
        return UNKNOWN
    if behind == 0 and ahead == 0:
        return "IN_SYNC"
    if behind == 0 and ahead > 0:
        return "AHEAD"
    if behind > 0 and ahead == 0:
        return "BEHIND"
    return "DIVERGED"


def _git_raw_stdout(root: Path, *args: str) -> str | None:
    """Like ops.live_box_guard._git but preserves leading whitespace.

    `_git()`'s `.strip()` is safe for SHA/branch-name output but corrupts
    `git status --porcelain`: its status codes are column-sensitive, and a
    line like " M README.md" has a load-bearing leading space that
    `str.strip()` on the whole blob would eat off the first line.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
        )
    except Exception:
        return None
    return result.stdout.rstrip("\n") or None


def porcelain_status(root: Path) -> dict[str, Any]:
    """Split `git status --porcelain=v1` into staged / dirty(tracked) / untracked."""
    raw = _git_raw_stdout(root, "status", "--porcelain=v1", "--untracked-files=all")
    if raw is None:
        return {"staged": [], "dirty": [], "untracked": [], "available": False}
    staged: list[str] = []
    dirty: list[str] = []
    untracked: list[str] = []
    for line in raw.splitlines():
        if not line:
            continue
        index_state, worktree_state = line[0], line[1]
        path = line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty.append(path)
    return {"staged": staged, "dirty": dirty, "untracked": untracked, "available": True}


def worktrees(root: Path) -> list[dict[str, Any]]:
    raw = _git(root, "worktree", "list", "--porcelain")
    if raw is None:
        return []
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line[len("worktree "):]}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "locked":
            current["locked"] = True
        elif line == "prunable":
            current["prunable"] = True
    if current:
        entries.append(current)
    return entries


def worktree_for_branch(entries: list[dict[str, Any]], branch: str | None) -> dict[str, Any] | None:
    if not branch:
        return None
    for entry in entries:
        if entry.get("branch") == branch:
            return entry
    return None


def stash_list(root: Path) -> list[str]:
    raw = _git(root, "stash", "list")
    if raw is None:
        return []
    return [line for line in raw.splitlines() if line.strip()]


def local_branches(root: Path) -> list[dict[str, Any]]:
    """Every local branch with its upstream tracking status."""
    raw = _git(
        root,
        "for-each-ref",
        "refs/heads/",
        "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
    )
    if raw is None:
        return []
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        out.append(
            {
                "branch": name,
                "upstream": upstream or None,
                "tracking_gone": "[gone]" in track,
                "local_only": upstream == "",
            }
        )
    return out


def archive_tags(root: Path) -> list[str]:
    raw = _git(root, "tag", "-l", "archive/*")
    if raw is None:
        return []
    return sorted(line for line in raw.splitlines() if line.strip())


def has_archive_tag_for_branch(tags: list[str], branch: str) -> bool:
    prefix = f"archive/{branch}-"
    return any(tag.startswith(prefix) or tag == f"archive/{branch}" for tag in tags)


def find_unpreserved_closed_branches(
    root: Path, closed_unmerged_prs: list[dict[str, Any]], tags: list[str]
) -> list[dict[str, Any]]:
    """Closed-but-unmerged PR branches with unique commits vs main and no archive tag.

    Flags a BLOCKER candidate list — never deletes or tags anything itself.
    """
    unpreserved: list[dict[str, Any]] = []
    for pr in closed_unmerged_prs:
        if pr.get("mergedAt"):
            continue
        head_ref = pr.get("headRefName")
        if not head_ref:
            continue
        unique = branch_unique_commits(root, head_ref, base="main")
        if not unique:  # None (branch gone locally) or 0 (no unique commits)
            continue
        if not has_archive_tag_for_branch(tags, head_ref):
            unpreserved.append({"branch": head_ref, "pr_number": pr.get("number"), "unique_commits": unique})
    return unpreserved


def branch_unique_commits(root: Path, branch: str, base: str = "main") -> int | None:
    """Count of commits on `branch` not reachable from `base`. None if either ref is unresolvable."""
    if resolve_ref(root, branch) is None or resolve_ref(root, base) is None:
        return None
    count = _git(root, "rev-list", "--count", f"{base}..{branch}")
    try:
        return int(count) if count is not None else None
    except ValueError:
        return None


# ── GitHub PR data (best-effort, via `gh` CLI) ──────────────────────────────

def _run_gh(root: Path, args: list[str], timeout: float = GH_TIMEOUT_S) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "unavailable (gh CLI not found or timed out)"
    if result.returncode != 0:
        return False, f"unavailable (gh CLI call failed: {result.stderr.strip()[:200]})"
    return True, result.stdout


def list_prs(root: Path, state: str = "open", *, limit: int = 100) -> dict[str, Any]:
    """`state`: open | closed | merged | all. Never raises; degrades to available=False."""
    ok, out = _run_gh(
        root,
        [
            "pr",
            "list",
            "--state",
            state,
            "--json",
            "number,title,headRefName,baseRefName,createdAt,updatedAt,closedAt,mergedAt,isDraft",
            "--limit",
            str(limit),
        ],
    )
    if not ok:
        return {"available": False, "detail": out, "prs": []}
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return {"available": False, "detail": "gh returned invalid JSON", "prs": []}
    return {"available": True, "prs": prs}


def gh_cli_available(root: Path) -> bool:
    ok, _ = _run_gh(root, ["auth", "status"], timeout=10.0)
    return ok
