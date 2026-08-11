"""Shared read-only git/gh primitives for ops.project_check.

Every helper here is READ ONLY. This module must never gain a helper that
commits, pushes, pulls, resets, rebases, checks out, deletes a branch,
removes a worktree, drops a stash, or creates/deletes a tag. Callers
(session-start, precommit, daily) rely on that invariant to stay fail-closed
by construction rather than by discipline.

All list-shaped git/gh output is parsed via NUL-delimited (`-z`) flags or
structured (`--porcelain`/`--json`) formats and returned as Python lists —
never handed to a shell loop — so there is no newline/word-splitting hazard
regardless of which shell (bash/zsh) happens to invoke this script.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

GIT_TIMEOUT_S = 15.0
GH_TIMEOUT_S = 20.0
FETCH_TIMEOUT_S = 30.0


def _run(cmd: list[str], *, cwd: Optional[Path] = None, timeout: float) -> tuple[Optional[str], Optional[str]]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or result.stdout or f"exit {result.returncode}").strip()
    return result.stdout, None


def git(*args: str, cwd: Optional[Path] = None, timeout: float = GIT_TIMEOUT_S) -> Optional[str]:
    out, _err = _run(["git", *args], cwd=cwd, timeout=timeout)
    return out.strip() if out is not None else None


def git_error(*args: str, cwd: Optional[Path] = None, timeout: float = GIT_TIMEOUT_S) -> Optional[str]:
    _out, err = _run(["git", *args], cwd=cwd, timeout=timeout)
    return err


def repo_root(cwd: Optional[Path] = None) -> Optional[str]:
    return git("rev-parse", "--show-toplevel", cwd=cwd)


def current_branch(cwd: Optional[Path] = None) -> Optional[str]:
    branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return branch


def head_sha(cwd: Optional[Path] = None) -> Optional[str]:
    return git("rev-parse", "HEAD", cwd=cwd)


def ref_sha(ref: str, cwd: Optional[Path] = None) -> Optional[str]:
    return git("rev-parse", ref, cwd=cwd)


def upstream_ref(cwd: Optional[Path] = None) -> Optional[str]:
    return git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=cwd)


def fetch_origin(cwd: Optional[Path] = None, timeout: float = FETCH_TIMEOUT_S) -> tuple[bool, Optional[str]]:
    """Refresh remote-tracking refs (origin/*) only. Never merges, rebases, or touches HEAD."""
    _out, err = _run(["git", "fetch", "--quiet", "origin"], cwd=cwd, timeout=timeout)
    return err is None, err


def sync_state(local_ref: str, remote_ref: str, cwd: Optional[Path] = None) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN of local_ref relative to remote_ref."""
    out = git("rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}", cwd=cwd)
    if out is None:
        return "UNKNOWN"
    parts = out.split()
    if len(parts) != 2:
        return "UNKNOWN"
    try:
        ahead, behind = int(parts[0]), int(parts[1])
    except ValueError:
        return "UNKNOWN"
    if ahead == 0 and behind == 0:
        return "IN_SYNC"
    if ahead > 0 and behind == 0:
        return "AHEAD"
    if ahead == 0 and behind > 0:
        return "BEHIND"
    return "DIVERGED"


def _status_porcelain_entries(cwd: Optional[Path] = None) -> list[str]:
    out, _err = _run(["git", "status", "--porcelain=v1", "-z"], cwd=cwd, timeout=GIT_TIMEOUT_S)
    if not out:
        return []
    return [part for part in out.split("\0") if part]


def working_tree_status(cwd: Optional[Path] = None) -> dict[str, list[str]]:
    """Classify `git status --porcelain=v1 -z` into staged / dirty (unstaged tracked) / untracked.

    Parses the NUL-delimited stream positionally (rename/copy entries emit an
    extra NUL-terminated "from" path token that carries no XY prefix of its
    own) rather than splitting on whitespace, so paths containing spaces are
    never misclassified.
    """
    entries = _status_porcelain_entries(cwd)
    staged: list[str] = []
    dirty: list[str] = []
    untracked: list[str] = []
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if len(entry) < 4 or entry[2] != " ":
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x in ("R", "C"):
            # Rename/copy: the next NUL token is the "from" path with no XY prefix.
            idx += 1
        if x != " " and x != "?":
            staged.append(path)
        if y != " " and y != "?":
            dirty.append(path)
    return {"staged": staged, "dirty": dirty, "untracked": untracked}


def stash_list(cwd: Optional[Path] = None) -> list[dict[str, str]]:
    out = git("stash", "list", "--format=%gd\t%gs", cwd=cwd)
    if not out:
        return []
    result = []
    for line in out.splitlines():
        ref, _, label = line.partition("\t")
        result.append({"ref": ref, "label": label})
    return result


def worktree_list(cwd: Optional[Path] = None) -> list[dict[str, Any]]:
    out, _err = _run(["git", "worktree", "list", "--porcelain"], cwd=cwd, timeout=GIT_TIMEOUT_S)
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
            current["branch"] = line[len("branch "):]
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    if current:
        worktrees.append(current)
    return worktrees


def local_branches(cwd: Optional[Path] = None) -> list[dict[str, str]]:
    out = git(
        "for-each-ref",
        "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
        "refs/heads/",
        cwd=cwd,
    )
    if not out:
        return []
    result = []
    for line in out.splitlines():
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        result.append({"name": name, "upstream": upstream, "track": track})
    return result


def local_only_branches(cwd: Optional[Path] = None) -> list[str]:
    return [b["name"] for b in local_branches(cwd) if not b["upstream"]]


def branches_tracking_deleted_remotes(cwd: Optional[Path] = None) -> list[str]:
    return [b["name"] for b in local_branches(cwd) if "[gone]" in b["track"]]


def merged_branches(base: str = "main", cwd: Optional[Path] = None) -> list[str]:
    out = git("branch", "--merged", base, "--format=%(refname:short)", cwd=cwd)
    if out is None:
        return []
    return [line.strip() for line in out.splitlines() if line.strip() and line.strip() != base]


def unmerged_branches(base: str = "main", cwd: Optional[Path] = None) -> list[str]:
    out = git("branch", "--no-merged", base, "--format=%(refname:short)", cwd=cwd)
    if out is None:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def archive_tags(cwd: Optional[Path] = None) -> list[str]:
    out = git("tag", "-l", "archive/*", cwd=cwd)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def branch_unique_commits(branch: str, base: str = "main", cwd: Optional[Path] = None) -> int:
    out = git("rev-list", "--count", f"{base}..{branch}", cwd=cwd)
    try:
        return int(out) if out is not None else -1
    except ValueError:
        return -1


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_json(args: list[str], timeout: float = GH_TIMEOUT_S) -> tuple[Any, Optional[str]]:
    """Best-effort `gh` invocation. Returns (None, reason) if gh is unavailable,
    unauthenticated, or the call fails — never raises, never invents data."""
    if not gh_available():
        return None, "gh CLI not found on PATH"
    out, err = _run(["gh", *args], timeout=timeout)
    if out is None:
        return None, err or "gh command failed"
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"gh returned non-JSON output: {exc}"
