"""Read-only git/worktree/branch/PR state harvesting shared by ops.project_check.

Every function here is read-only: it never mutates git state (no commit, push,
pull, reset, rebase, checkout/switch, branch/worktree delete, stash drop, tag
create/delete). All subprocess calls pass argv lists, never ``shell=True`` and
never build a shell command line by string concatenation — there is no shell
word-splitting to get wrong here regardless of the invoking shell (bash or
zsh), because there is no shell in the loop at all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

UNKNOWN = "UNKNOWN"


def _run(args: list[str], cwd: Optional[str] = None, timeout: float = 15.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def _git(args: list[str], cwd: Optional[str] = None) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=cwd)


def repo_root(cwd: Optional[str] = None) -> Optional[str]:
    code, out, _ = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return out.strip() if code == 0 and out.strip() else None


def current_branch(cwd: Optional[str] = None) -> Optional[str]:
    code, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    branch = out.strip()
    if code != 0 or not branch or branch == "HEAD":
        return None
    return branch


def is_detached(cwd: Optional[str] = None) -> bool:
    code, _, _ = _git(["symbolic-ref", "-q", "HEAD"], cwd=cwd)
    return code != 0


def head_sha(cwd: Optional[str] = None) -> Optional[str]:
    code, out, _ = _git(["rev-parse", "HEAD"], cwd=cwd)
    return out.strip() if code == 0 and out.strip() else None


def upstream_ref(cwd: Optional[str] = None) -> Optional[str]:
    code, out, _ = _git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=cwd
    )
    return out.strip() if code == 0 and out.strip() else None


def ref_sha(ref: str, cwd: Optional[str] = None) -> Optional[str]:
    code, out, _ = _git(["rev-parse", ref], cwd=cwd)
    return out.strip() if code == 0 and out.strip() else None


def sync_status(local_sha: Optional[str], other_sha: Optional[str], cwd: Optional[str] = None) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local_sha vs other_sha."""
    if not local_sha or not other_sha:
        return UNKNOWN
    if local_sha == other_sha:
        return "IN_SYNC"
    code, out, _ = _git(["merge-base", local_sha, other_sha], cwd=cwd)
    if code != 0:
        return UNKNOWN
    base = out.strip()
    if base == other_sha:
        return "AHEAD"
    if base == local_sha:
        return "BEHIND"
    return "DIVERGED"


def ahead_behind(local_sha: Optional[str], other_sha: Optional[str], cwd: Optional[str] = None) -> Optional[dict]:
    if not local_sha or not other_sha:
        return None
    code, out, _ = _git(
        ["rev-list", "--left-right", "--count", f"{local_sha}...{other_sha}"], cwd=cwd
    )
    if code != 0:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        return {"ahead": int(parts[0]), "behind": int(parts[1])}
    except ValueError:
        return None


def dirty_files(cwd: Optional[str] = None) -> dict:
    """Modified/staged/untracked files via NUL-delimited porcelain output (safe
    against filenames containing spaces or newlines — no line-based parsing)."""
    code, out, _ = _run(["git", "status", "--porcelain=v1", "-z"], cwd=cwd)
    if code != 0:
        return {"modified": [], "staged": [], "untracked": [], "ok": False}
    modified: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 3:
            continue
        x, y, path = entry[0], entry[1], entry[3:]
        if x in ("R", "C") and i < len(parts):
            i += 1  # consume the original-path token that follows a rename/copy
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x != " " and x != "!":
            staged.append(path)
        if y != " " and y != "!":
            modified.append(path)
    return {"modified": modified, "staged": staged, "untracked": untracked, "ok": True}


def worktrees(cwd: Optional[str] = None) -> list[dict]:
    code, out, _ = _git(["worktree", "list", "--porcelain"], cwd=cwd)
    if code != 0:
        return []
    result: list[dict] = []
    current: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                result.append(current)
                current = {}
            continue
        if " " in line:
            key, value = line.split(" ", 1)
        else:
            key, value = line, True
        if key == "worktree":
            current = {"path": value}
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = str(value).removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = value
    if current:
        result.append(current)
    return result


def local_branches(cwd: Optional[str] = None) -> list[dict]:
    """Every local branch with its upstream (if any) and gone-remote flag."""
    code, out, _ = _git(
        [
            "for-each-ref",
            "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
            "refs/heads",
        ],
        cwd=cwd,
    )
    if code != 0:
        return []
    branches = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0]
        upstream_short = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        branches.append(
            {
                "name": name,
                "upstream": upstream_short or None,
                "gone": "[gone]" in track,
                "local_only": not upstream_short,
            }
        )
    return branches


def merged_into(base: str, cwd: Optional[str] = None) -> list[str]:
    code, out, _ = _git(["branch", "--format=%(refname:short)", "--merged", base], cwd=cwd)
    if code != 0:
        return []
    return [b.strip() for b in out.splitlines() if b.strip() and b.strip() != base]


def not_merged_into(base: str, cwd: Optional[str] = None) -> list[str]:
    code, out, _ = _git(["branch", "--format=%(refname:short)", "--no-merged", base], cwd=cwd)
    if code != 0:
        return []
    return [b.strip() for b in out.splitlines() if b.strip()]


def stash_list(cwd: Optional[str] = None) -> list[dict]:
    code, out, _ = _git(["stash", "list", "--format=%gd%x09%s"], cwd=cwd)
    if code != 0:
        return []
    stashes = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        stashes.append({"ref": parts[0], "label": parts[1] if len(parts) > 1 else ""})
    return stashes


def archive_tags(cwd: Optional[str] = None) -> list[dict]:
    code, out, _ = _git(
        ["tag", "-l", "archive/*", "--format=%(refname:short)%09%(objectname)"], cwd=cwd
    )
    if code != 0:
        return []
    tags = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        tags.append({"tag": parts[0], "sha": parts[1] if len(parts) > 1 else None})
    return tags


def tags_pointing_at(sha: str, cwd: Optional[str] = None) -> list[str]:
    code, out, _ = _git(["tag", "--points-at", sha], cwd=cwd)
    if code != 0:
        return []
    return [t.strip() for t in out.splitlines() if t.strip()]


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_pr_list(cwd: Optional[str] = None, limit: int = 200) -> Optional[list[dict]]:
    """All PRs (any state) via ``gh pr list``. Returns None if the ``gh`` CLI is
    unavailable or the call fails — callers MUST report UNKNOWN in that case,
    never assume "no PRs" from an empty/failed lookup."""
    if not gh_available():
        return None
    fields = "number,title,headRefName,baseRefName,state,url,createdAt,mergedAt,closedAt,updatedAt,isDraft"
    code, out, _ = _run(
        ["gh", "pr", "list", "--state", "all", "--limit", str(limit), "--json", fields],
        cwd=cwd,
        timeout=30.0,
    )
    if code != 0 or not out.strip():
        return None
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None
