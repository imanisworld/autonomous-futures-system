"""Read-only git repo/branch/worktree hygiene primitives.

Codifies the checks already run by hand under `.claude/commands/
repo-hygiene-check.md` so Session Safety and Daily Reconciliation can share
one implementation instead of re-deriving git state twice. Every function
here shells out to read-only git subcommands only (status, branch, tag,
worktree list, rev-parse, rev-list, log, stash list) and, best-effort, to
the read-only `gh pr list`. Nothing here writes, stages, commits, pushes,
deletes, or mutates any git or GitHub state.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _run(cwd: Path, *args: str, timeout: float = 8.0) -> tuple[str | None, str | None]:
    """Run a read-only subprocess command; never raises."""
    try:
        result = subprocess.run(
            list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or "").strip() or f"exit {result.returncode}"
    return result.stdout, None


def _git(repo_root: Path, *args: str, timeout: float = 8.0) -> str | None:
    out, _err = _run(repo_root, "git", *args, timeout=timeout)
    return out.strip() if out is not None else None


def repo_root_of(start: str | Path) -> Path | None:
    start = Path(start).resolve()
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def git_dir_of(repo_root: Path) -> Path | None:
    """Resolve the actual git-dir for this checkout, worktree-aware."""
    out = _git(repo_root, "rev-parse", "--git-common-dir")
    if not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else (repo_root / path).resolve()


def worktree_git_dir_of(repo_root: Path) -> Path | None:
    """Resolve THIS worktree's private git-dir (distinct per worktree)."""
    out = _git(repo_root, "rev-parse", "--git-dir")
    if not out:
        return None
    path = Path(out)
    return path if path.is_absolute() else (repo_root / path).resolve()


def repo_identity(repo_root: Path) -> dict[str, Any]:
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    head_sha = _git(repo_root, "rev-parse", "HEAD")
    return {
        "repo_root": str(repo_root),
        "branch": branch or "UNKNOWN",
        "head_sha": head_sha or "UNKNOWN",
        "detached_head": branch == "HEAD",
    }


def upstream_info(repo_root: Path) -> dict[str, Any]:
    upstream = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream:
        return {"upstream": None, "ahead": None, "behind": None}
    counts, _err = _run(repo_root, "git", "rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    ahead = behind = None
    if counts:
        parts = counts.strip().split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            behind, ahead = int(parts[0]), int(parts[1])
    return {"upstream": upstream, "ahead": ahead, "behind": behind}


def main_sync_status(repo_root: Path, *, local_ref: str = "main", remote_ref: str = "origin/main") -> dict[str, Any]:
    """Relationship of the LOCAL main branch (not necessarily HEAD) to origin/main."""
    local_sha = _git(repo_root, "rev-parse", "--verify", "--quiet", local_ref)
    remote_sha = _git(repo_root, "rev-parse", "--verify", "--quiet", remote_ref)
    if not local_sha or not remote_sha:
        return {
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "relationship": "UNKNOWN",
            "reason": "local or remote ref not resolvable from here",
        }
    if local_sha == remote_sha:
        relationship = "IN_SYNC"
        ahead = behind = 0
    else:
        counts, _err = _run(repo_root, "git", "rev-list", "--left-right", "--count", f"{remote_ref}...{local_ref}")
        if not counts:
            return {
                "local_ref": local_ref,
                "remote_ref": remote_ref,
                "local_sha": local_sha,
                "remote_sha": remote_sha,
                "relationship": "UNKNOWN",
                "reason": "rev-list could not compute ahead/behind",
            }
        parts = counts.strip().split()
        behind, ahead = int(parts[0]), int(parts[1])
        if ahead and behind:
            relationship = "DIVERGED"
        elif ahead:
            relationship = "AHEAD"
        elif behind:
            relationship = "BEHIND"
        else:
            relationship = "IN_SYNC"
    return {
        "local_ref": local_ref,
        "remote_ref": remote_ref,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "relationship": relationship,
        "ahead": ahead,
        "behind": behind,
    }


def working_tree_status(repo_root: Path) -> dict[str, Any]:
    # `porcelain` status is fixed-column ("XY PATH"); it must not be passed
    # through _git()'s blanket .strip(), which eats the leading space of the
    # first line and shifts every column on that line.
    out, _err = _run(repo_root, "git", "status", "--porcelain=v1", "--untracked-files=all")
    staged: list[str] = []
    dirty: list[str] = []
    untracked: list[str] = []
    for line in (out or "").splitlines():
        if not line:
            continue
        code = line[:2]
        path = line[3:]
        index_state, worktree_state = code[0], code[1]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty.append(path)
    return {
        "staged_files": staged,
        "dirty_files": dirty,
        "untracked_files": untracked,
        "clean": not staged and not dirty and not untracked,
    }


def worktrees(repo_root: Path) -> list[dict[str, Any]]:
    out = _git(repo_root, "worktree", "list", "--porcelain")
    if out is None:
        return []
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
            current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            current["head_sha"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line == "locked":
            current["locked"] = True
        elif line == "prunable":
            current["prunable"] = True
    if current:
        entries.append(current)
    for entry in entries:
        entry.setdefault("branch", None)
        entry.setdefault("detached", False)
        wt_status = working_tree_status(Path(entry["path"])) if Path(entry["path"]).is_dir() else None
        entry["dirty"] = bool(wt_status and not wt_status["clean"])
        entry["dirty_files"] = wt_status["dirty_files"] + wt_status["staged_files"] if wt_status else []
    return entries


def stashes(repo_root: Path) -> list[dict[str, Any]]:
    out = _git(repo_root, "stash", "list", "--format=%gd\x1f%gs")
    result = []
    for line in (out or "").splitlines():
        if not line:
            continue
        ref, _, subject = line.partition("\x1f")
        result.append({"ref": ref, "subject": subject})
    return result


def local_branches(repo_root: Path) -> list[dict[str, Any]]:
    out = _git(
        repo_root, "for-each-ref", "refs/heads/",
        "--format=%(refname:short)\x1f%(upstream:short)\x1f%(upstream:track)\x1f%(objectname)",
    )
    branches = []
    for line in (out or "").splitlines():
        if not line:
            continue
        name, upstream, track, sha = (line.split("\x1f") + ["", "", "", ""])[:4]
        gone = "[gone]" in track
        branches.append({
            "name": name,
            "upstream": upstream or None,
            "track": track or None,
            "sha": sha,
            "upstream_gone": gone,
            "local_only": not upstream,
        })
    return branches


def branches_tracking_deleted_remotes(repo_root: Path) -> list[str]:
    return [b["name"] for b in local_branches(repo_root) if b["upstream_gone"]]


def local_only_branches(repo_root: Path) -> list[str]:
    return [b["name"] for b in local_branches(repo_root) if b["local_only"]]


def merged_local_branches(repo_root: Path, *, into_ref: str = "main") -> list[str]:
    out = _git(repo_root, "branch", "--merged", into_ref, "--format=%(refname:short)")
    return [line for line in (out or "").splitlines() if line and line != into_ref]


def archive_tags(repo_root: Path) -> list[str]:
    out = _git(repo_root, "tag", "-l", "archive/*")
    return [line for line in (out or "").splitlines() if line]


def archive_tags_for_commit(repo_root: Path, sha: str) -> list[str]:
    out = _git(repo_root, "tag", "--points-at", sha, "--list", "archive/*")
    return [line for line in (out or "").splitlines() if line]


def branch_unique_vs_main(repo_root: Path, ref: str, *, main_ref: str = "origin/main") -> dict[str, Any]:
    """Unique-commit evidence for `ref` that isn't already reachable from main."""
    sha = _git(repo_root, "rev-parse", "--verify", "--quiet", ref)
    if not sha:
        return {"ref": ref, "resolvable": False}
    log, _err = _run(repo_root, "git", "log", f"{main_ref}..{ref}", "--oneline")
    unique_commits = [line for line in (log or "").splitlines() if line]
    return {
        "ref": ref,
        "resolvable": True,
        "sha": sha,
        "unique_commit_count": len(unique_commits),
        "unique_commits": unique_commits[:20],
        "archive_tags": archive_tags_for_commit(repo_root, sha),
    }


def gh_available(repo_root: Path) -> bool:
    out, err = _run(repo_root, "gh", "--version", timeout=3.0)
    return out is not None


def gh_pr_list(repo_root: Path, *, state: str = "open", limit: int = 100) -> dict[str, Any]:
    """Best-effort read-only `gh pr list`. Never raises; reports UNKNOWN if gh is absent."""
    if not gh_available(repo_root):
        return {"available": False, "reason": "gh CLI not found", "prs": []}
    out, err = _run(
        repo_root, "gh", "pr", "list",
        "--state", state, "--limit", str(limit),
        "--json", "number,title,headRefName,baseRefName,url,createdAt,closedAt,mergedAt,isDraft",
        timeout=15.0,
    )
    if out is None:
        return {"available": False, "reason": err or "gh pr list failed", "prs": []}
    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return {"available": False, "reason": "gh pr list returned invalid JSON", "prs": []}
    return {"available": True, "reason": None, "prs": prs}
