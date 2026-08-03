"""Read-only git/worktree/branch/stash/PR introspection.

Shared by ops.session_snapshot and ops.daily_reconciliation so branch/worktree/
stash listing logic exists in exactly one place. Every function shells out to
`git` (and optionally `gh`) with short timeouts and never mutates repository
state: no fetch of anything but remote-tracking refs (see `fetch_remote`,
opt-in and best-effort), no pull/push/reset/checkout/tag/branch mutation.

Unavailable data is returned as `UNKNOWN` (or `None` for structured fields)
rather than guessed — callers must not infer values this module could not
determine.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

UNKNOWN = "UNKNOWN"
_TIMEOUT = 8.0


def _run(args: list[str], cwd: Path, timeout: float = _TIMEOUT) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git(root: Path, *args: str, timeout: float = _TIMEOUT) -> str | None:
    out = _run(["git", *args], root, timeout=timeout)
    return out.strip() if out is not None else None


def _git_lines(root: Path, *args: str) -> list[str] | None:
    out = _run(["git", *args], root)
    if out is None:
        return None
    return [line for line in out.splitlines() if line != ""]


def git_dir(root: Path) -> Path | None:
    """The per-worktree git administrative directory (`.git` for the main
    worktree, `.git/worktrees/<name>` for a linked one) — the correct place
    for a tool to keep small local-only state, since it is never tracked and
    is naturally scoped to one worktree."""
    out = _git(root, "rev-parse", "--git-dir")
    if not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else root / path


def find_repo_root(start: Path | str | None = None) -> Path | None:
    start_path = Path(start or Path.cwd())
    out = _git(start_path, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD") or UNKNOWN


def head_sha(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD") or UNKNOWN


def upstream_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or UNKNOWN


def current_worktree(root: Path) -> str:
    top = _git(root, "rev-parse", "--show-toplevel")
    return top or str(root)


def dirty_status(root: Path) -> dict[str, Any]:
    """Classify `git status --porcelain=v1` into staged / unstaged / untracked."""
    lines = _git_lines(root, "status", "--porcelain=v1")
    if lines is None:
        return {"ok": False, "staged": [], "unstaged": [], "untracked": [], "raw": None}
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if len(line) < 4:
            continue
        index_state, worktree_state, path = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            unstaged.append(path)
    return {
        "ok": True,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "raw": lines,
    }


def fetch_remote(root: Path, remote: str = "origin", *, tags: bool = False, timeout: float = 20.0) -> bool:
    """Best-effort `git fetch` — updates only remote-tracking refs (and tags if
    requested), never the working tree or local branches. Never raises; a
    failed/offline fetch just means callers fall back to whatever refs are
    already known locally, reported as such."""
    args = ["fetch", "--quiet", remote]
    if tags:
        args.append("--tags")
    return _run(["git", *args], root, timeout=timeout) is not None


def ref_sha(root: Path, ref: str) -> str | None:
    return _git(root, "rev-parse", "--verify", "--quiet", ref)


def main_sync_state(root: Path, local_ref: str = "main", remote_ref: str = "origin/main") -> dict[str, Any]:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED between local_ref and remote_ref."""
    local_sha = ref_sha(root, local_ref)
    remote_sha = ref_sha(root, remote_ref)
    if local_sha is None or remote_sha is None:
        return {
            "state": UNKNOWN,
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "ahead": None,
            "behind": None,
        }
    counts = _git(root, "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}")
    ahead = behind = None
    if counts:
        parts = counts.split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            ahead, behind = int(parts[0]), int(parts[1])
    if local_sha == remote_sha:
        state = "IN_SYNC"
    elif ahead is None or behind is None:
        state = UNKNOWN
    elif ahead > 0 and behind > 0:
        state = "DIVERGED"
    elif ahead > 0:
        state = "AHEAD"
    elif behind > 0:
        state = "BEHIND"
    else:
        state = "IN_SYNC"
    return {
        "state": state,
        "local_ref": local_ref,
        "remote_ref": remote_ref,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "ahead": ahead,
        "behind": behind,
    }


def worktrees(root: Path) -> list[dict[str, Any]]:
    out = _run(["git", "worktree", "list", "--porcelain"], root)
    if out is None:
        return []
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines():
        if line == "":
            if current:
                entries.append(current)
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "detached":
            current["branch"] = None
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
    if current:
        entries.append(current)
    for entry in entries:
        entry.setdefault("branch", None)
        entry.setdefault("detached", False)
        path = entry.get("path")
        if path and Path(path).is_dir():
            status = dirty_status(Path(path))
            entry["dirty"] = bool(status["staged"] or status["unstaged"] or status["untracked"])
            entry["dirty_files"] = status["staged"] + status["unstaged"] + status["untracked"]
        else:
            entry["dirty"] = None
            entry["dirty_files"] = []
    return entries


def stashes(root: Path) -> list[dict[str, str]]:
    lines = _git_lines(root, "stash", "list", "--format=%gd%x1f%gs")
    if lines is None:
        return []
    result = []
    for line in lines:
        ref, _, subject = line.partition("\x1f")
        result.append({"ref": ref, "subject": subject})
    return result


def local_branches(root: Path) -> list[dict[str, Any]]:
    """Every local branch with upstream, gone-tracking, and merged/main state."""
    lines = _git_lines(
        root,
        "for-each-ref",
        "refs/heads",
        "--format=%(refname:short)%1f%(upstream:short)%1f%(upstream:track)",
    )
    if lines is None:
        return []
    merged = set(_git_lines(root, "branch", "--merged", "main", "--format=%(refname:short)") or [])
    branches = []
    for line in lines:
        name, _, rest = line.partition("\x1f")
        upstream, _, track = rest.partition("\x1f")
        branches.append(
            {
                "name": name,
                "upstream": upstream or None,
                "gone": "[gone]" in track,
                "tracking_note": track or None,
                "merged_into_main": name in merged,
            }
        )
    return branches


def remote_branches(root: Path, remote: str = "origin") -> list[str]:
    lines = _git_lines(root, "branch", "-r", "--format=%(refname:short)")
    if lines is None:
        return []
    prefix = f"{remote}/"
    return [line for line in lines if line.startswith(prefix) and not line.endswith("/HEAD")]


def archive_tags(root: Path, prefix: str = "archive/") -> list[str]:
    lines = _git_lines(root, "tag", "--list", f"{prefix}*")
    return lines or []


def _remote_url(root: Path, remote: str = "origin") -> str | None:
    return _git(root, "remote", "get-url", remote)


def repo_slug(root: Path, remote: str = "origin") -> str | None:
    """owner/repo parsed from the origin URL, for gh CLI calls."""
    url = _remote_url(root, remote)
    if not url:
        return None
    cleaned = url.removesuffix(".git")
    if cleaned.startswith("git@"):
        _, _, path = cleaned.partition(":")
    elif "://" in cleaned:
        path = cleaned.split("://", 1)[1].split("/", 1)[-1]
    else:
        path = cleaned
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


def _tag_target_shas(root: Path, tags: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for tag in tags:
        sha = _git(root, "rev-list", "-n", "1", tag)
        if sha:
            result[tag] = sha
    return result


def unmerged_branch_evidence(root: Path, *, include_remote: bool = True) -> dict[str, Any]:
    """Local (and optionally remote) branches not merged into main, whether
    each carries commits unique to it, and whether any ``archive/*`` tag's
    tip commit exactly matches — the same-SHA match is precise and needs no
    name-guessing. Branches already deleted before this check ran cannot be
    evaluated; this is an inherent limitation of a point-in-time git read,
    not something this function can infer around."""
    tags = archive_tags(root)
    tag_shas = _tag_target_shas(root, tags)
    preserved_shas = set(tag_shas.values())

    candidates: list[tuple[str, str]] = []  # (kind, name)
    for branch in local_branches(root):
        if branch["name"] != "main" and not branch["merged_into_main"]:
            candidates.append(("local", branch["name"]))
    if include_remote:
        merged_remote = set(_git_lines(root, "branch", "-r", "--merged", "main", "--format=%(refname:short)") or [])
        for name in remote_branches(root):
            if name not in merged_remote and not name.endswith("/main"):
                candidates.append(("remote", name))

    report = []
    for kind, name in candidates:
        tip_sha = ref_sha(root, name)
        unique_count = None
        if tip_sha:
            counts = _git(root, "rev-list", "main..%s" % name, "--count")
            unique_count = int(counts) if counts and counts.isdigit() else None
        preserved = tip_sha in preserved_shas if tip_sha else None
        report.append(
            {
                "kind": kind,
                "name": name,
                "tip_sha": tip_sha,
                "unique_commit_count": unique_count,
                "has_unique_evidence": bool(unique_count) if unique_count is not None else None,
                "archive_tag_preserved": preserved,
                "blocker": bool(unique_count) and preserved is False,
            }
        )
    return {
        "archive_tags": tags,
        "branches": report,
        "blockers": [item["name"] for item in report if item["blocker"]],
        "limitations": [
            "Only evaluates branches still visible to this clone (local heads "
            "and origin's remote-tracking refs after a fetch). A branch "
            "already deleted everywhere before this check ran cannot be "
            "retroactively evaluated.",
            "Preservation match is exact-SHA (tag tip == branch tip) — a tag "
            "preserving an earlier or later commit on the same branch will "
            "not match and will be reported as unpreserved.",
        ],
    }


def gh_available() -> bool:
    return shutil.which("gh") is not None


def pull_requests(root: Path, *, state: str = "all", limit: int = 100) -> dict[str, Any]:
    """List PRs via `gh pr list`. Read-only; returns UNKNOWN status if `gh` is
    missing, unauthenticated, or the call otherwise fails — never guesses."""
    if not gh_available():
        return {"available": False, "reason": "gh CLI not found on PATH", "prs": []}
    slug = repo_slug(root)
    args = [
        "gh", "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json", "number,title,state,headRefName,baseRefName,createdAt,updatedAt,mergedAt,closedAt,isDraft,url",
    ]
    if slug:
        args.extend(["--repo", slug])
    out = _run(args, root, timeout=20.0)
    if out is None:
        return {"available": False, "reason": "gh pr list failed (network/auth/rate-limit)", "prs": []}
    try:
        prs = json.loads(out)
    except (TypeError, ValueError):
        return {"available": False, "reason": "gh pr list returned unparseable output", "prs": []}
    return {"available": True, "reason": None, "prs": prs}
