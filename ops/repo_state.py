"""Read-only git/worktree/branch/PR hygiene inspection.

Shared by ``ops/project_check.py``'s ``session-start``, ``precommit``, and
``daily`` subcommands so branch/worktree/stash inspection logic lives in one
place instead of being re-derived per routine.

Every function here only *reads* repository state: local git metadata
(``git for-each-ref``, ``git branch -vv``, ``git worktree list``, ``git
stash list``, ...) and, best-effort, the ``gh`` CLI for PR status. Nothing
in this module fetches, pulls, pushes, commits, checks out, resets, or
deletes anything. A ``gh`` call that fails (missing binary, no network, no
auth) degrades to ``None``/``"UNKNOWN"`` rather than raising, matching the
pattern already used by ``scripts/options_pr_audit.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

GH_TIMEOUT_SECONDS = 8
GIT_TIMEOUT_SECONDS = 10


def _run(cmd: list[str], *, cwd: str | Path | None = None, timeout: int = GIT_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def _git(*args: str, cwd: str | Path | None = None, timeout: int = GIT_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=cwd, timeout=timeout)


@dataclass
class WorktreeInfo:
    path: str
    branch: str | None
    head: str | None
    is_bare: bool = False
    is_detached: bool = False
    locked: bool = False
    prunable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "branch": self.branch,
            "head": self.head,
            "is_bare": self.is_bare,
            "is_detached": self.is_detached,
            "locked": self.locked,
            "prunable": self.prunable,
        }


def repo_root(cwd: str | Path | None = None) -> str | None:
    rc, out, _ = _git("rev-parse", "--show-toplevel", cwd=cwd)
    return out if rc == 0 and out else None


def current_branch(cwd: str | Path | None = None) -> str | None:
    rc, out, _ = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    if rc != 0 or not out:
        return None
    return None if out == "HEAD" else out  # detached HEAD


def is_detached_head(cwd: str | Path | None = None) -> bool:
    rc, out, _ = _git("symbolic-ref", "-q", "HEAD", cwd=cwd)
    return rc != 0


def head_sha(cwd: str | Path | None = None) -> str | None:
    rc, out, _ = _git("rev-parse", "HEAD", cwd=cwd)
    return out if rc == 0 and out else None


def upstream_branch(cwd: str | Path | None = None) -> str | None:
    rc, out, _ = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", cwd=cwd)
    return out if rc == 0 and out else None


def default_remote_branch(cwd: str | Path | None = None) -> str | None:
    """Best-effort resolution of the remote's default branch (main/master)."""
    rc, out, _ = _git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=cwd)
    if rc == 0 and out:
        return out.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        rc, out, _ = _git("rev-parse", "--verify", "--quiet", f"origin/{candidate}", cwd=cwd)
        if rc == 0:
            return candidate
    return None


def origin_main_sha(cwd: str | Path | None = None, branch: str | None = None) -> str | None:
    branch = branch or default_remote_branch(cwd=cwd) or "main"
    rc, out, _ = _git("rev-parse", f"origin/{branch}", cwd=cwd)
    return out if rc == 0 and out else None


def main_sync_state(cwd: str | Path | None = None, branch: str | None = None) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local vs origin/<branch>."""
    branch = branch or default_remote_branch(cwd=cwd)
    if not branch:
        return "UNKNOWN"
    local_sha = head_sha(cwd=cwd)
    remote_sha = origin_main_sha(cwd=cwd, branch=branch)
    if not local_sha or not remote_sha:
        return "UNKNOWN"
    if local_sha == remote_sha:
        return "IN_SYNC"
    rc, out, _ = _git(
        "rev-list", "--left-right", "--count", f"{local_sha}...{remote_sha}", cwd=cwd
    )
    if rc != 0 or not out:
        return "UNKNOWN"
    parts = out.split()
    if len(parts) != 2:
        return "UNKNOWN"
    ahead, behind = parts
    if ahead == "0" and behind == "0":
        return "IN_SYNC"
    if ahead != "0" and behind == "0":
        return "AHEAD"
    if ahead == "0" and behind != "0":
        return "BEHIND"
    return "DIVERGED"


def list_worktrees(cwd: str | Path | None = None) -> list[WorktreeInfo]:
    rc, out, _ = _git("worktree", "list", "--porcelain", cwd=cwd)
    if rc != 0 or not out:
        return []
    worktrees: list[WorktreeInfo] = []
    current: dict[str, Any] = {}

    def flush() -> None:
        if current.get("path"):
            worktrees.append(
                WorktreeInfo(
                    path=current["path"],
                    branch=current.get("branch"),
                    head=current.get("head"),
                    is_bare="bare" in current,
                    is_detached="detached" in current,
                    locked="locked" in current,
                    prunable="prunable" in current,
                )
            )

    for line in out.splitlines():
        if line.startswith("worktree "):
            flush()
            current = {"path": line[len("worktree "):].strip()}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            prefix = "refs/heads/"
            current["branch"] = ref[len(prefix):] if ref.startswith(prefix) else (ref or None)
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    flush()
    return worktrees


def current_worktree_path(cwd: str | Path | None = None) -> str | None:
    root = repo_root(cwd=cwd)
    if not root:
        return None
    # A linked worktree's rev-parse --show-toplevel already gives its own
    # path; --git-common-dir differs from --git-dir only for linked
    # worktrees, which is how the *main* worktree could be told apart if
    # ever needed, but callers here only need "which worktree am I in".
    return root


def dirty_tracked_files(cwd: str | Path | None = None) -> list[str]:
    """Tracked files with unstaged modifications (working tree vs index)."""
    rc, out, _ = _git("diff", "--name-only", cwd=cwd)
    return [line for line in out.splitlines() if line.strip()] if rc == 0 else []


def staged_files(cwd: str | Path | None = None) -> list[str]:
    rc, out, _ = _git("diff", "--name-only", "--cached", cwd=cwd)
    return [line for line in out.splitlines() if line.strip()] if rc == 0 else []


def untracked_files(cwd: str | Path | None = None) -> list[str]:
    rc, out, _ = _git("ls-files", "--others", "--exclude-standard", cwd=cwd)
    return [line for line in out.splitlines() if line.strip()] if rc == 0 else []


def all_changed_files(cwd: str | Path | None = None) -> list[str]:
    """Union of dirty tracked + staged + untracked, for a single "did anything
    change" comparison. Order-independent; callers that need staged vs.
    unstaged detail should call the specific functions instead."""
    return sorted(set(dirty_tracked_files(cwd=cwd)) | set(staged_files(cwd=cwd)) | set(untracked_files(cwd=cwd)))


def stash_list(cwd: str | Path | None = None) -> list[str]:
    rc, out, _ = _git("stash", "list", cwd=cwd)
    return [line for line in out.splitlines() if line.strip()] if rc == 0 else []


@dataclass
class LocalBranch:
    name: str
    upstream: str | None
    upstream_gone: bool
    ahead: int | None
    behind: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "upstream": self.upstream,
            "upstream_gone": self.upstream_gone,
            "ahead": self.ahead,
            "behind": self.behind,
        }


def local_branches(cwd: str | Path | None = None) -> list[LocalBranch]:
    """Local branches with upstream tracking info, from local git metadata only
    (no network calls: relies on git's cached knowledge of "gone" upstreams,
    refreshed by the last `git fetch --prune` -- may be stale if no fetch has
    happened recently, which is reported by the caller, not hidden here)."""
    fmt = "%(refname:short)\t%(upstream:short)\t%(upstream:track)"
    rc, out, _ = _git("for-each-ref", "refs/heads", f"--format={fmt}", cwd=cwd)
    if rc != 0:
        return []
    branches: list[LocalBranch] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0] if len(parts) > 0 else ""
        upstream = parts[1] if len(parts) > 1 and parts[1] else None
        track = parts[2] if len(parts) > 2 else ""
        gone = "[gone]" in track
        ahead = behind = None
        if track:
            for token in track.strip("[]").split(", "):
                if token.startswith("ahead "):
                    ahead = int(token.split()[1])
                elif token.startswith("behind "):
                    behind = int(token.split()[1])
        branches.append(LocalBranch(name=name, upstream=upstream, upstream_gone=gone, ahead=ahead, behind=behind))
    return branches


def local_only_branches(cwd: str | Path | None = None) -> list[str]:
    return [b.name for b in local_branches(cwd=cwd) if b.upstream is None]


def branches_tracking_deleted_remotes(cwd: str | Path | None = None) -> list[str]:
    return [b.name for b in local_branches(cwd=cwd) if b.upstream_gone]


def branches_not_merged(cwd: str | Path | None = None, branch: str | None = None) -> list[str]:
    branch = branch or default_remote_branch(cwd=cwd) or "main"
    rc, out, _ = _git("branch", "--no-merged", f"origin/{branch}", "--format=%(refname:short)", cwd=cwd)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def archive_tags(cwd: str | Path | None = None) -> list[str]:
    rc, out, _ = _git("tag", "--list", "archive/*", cwd=cwd)
    return [line.strip() for line in out.splitlines() if line.strip()] if rc == 0 else []


def branches_checked_out_in_worktrees(cwd: str | Path | None = None) -> set[str]:
    return {wt.branch for wt in list_worktrees(cwd=cwd) if wt.branch}


def gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_json(args: list[str], cwd: str | Path | None = None) -> Any | None:
    if not gh_available():
        return None
    rc, out, _ = _run(["gh", *args], cwd=cwd, timeout=GH_TIMEOUT_SECONDS)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except (ValueError, TypeError):
        return None


def open_prs(cwd: str | Path | None = None) -> list[dict[str, Any]] | None:
    """Best-effort list of open PRs. None means UNKNOWN (gh unavailable/failed),
    never a guess."""
    data = _gh_json(
        ["pr", "list", "--state", "open", "--json", "number,title,headRefName,url,createdAt,isDraft"],
        cwd=cwd,
    )
    return data if isinstance(data, list) else None


def prs_touched_on(day: date, cwd: str | Path | None = None) -> dict[str, Any] | None:
    """PRs opened/merged/closed-unmerged on ``day``. None means UNKNOWN."""
    data = _gh_json(
        [
            "pr", "list", "--state", "all", "--limit", "200",
            "--json", "number,title,state,createdAt,mergedAt,closedAt,headRefName,isDraft",
        ],
        cwd=cwd,
    )
    if not isinstance(data, list):
        return None
    day_str = day.isoformat()
    opened = [p for p in data if str(p.get("createdAt") or "").startswith(day_str)]
    merged = [p for p in data if str(p.get("mergedAt") or "").startswith(day_str)]
    closed_unmerged = [
        p for p in data
        if str(p.get("closedAt") or "").startswith(day_str) and not p.get("mergedAt")
    ]
    stale_open = [p for p in data if p.get("state") == "OPEN"]
    return {
        "opened_today": opened,
        "merged_today": merged,
        "closed_unmerged_today": closed_unmerged,
        "open_prs": stale_open,
    }


@dataclass
class RepoStateReport:
    read_only: bool = True
    repo_root: str | None = None
    current_branch: str | None = None
    detached_head: bool = False
    head_sha: str | None = None
    default_remote_branch: str | None = None
    origin_main_sha: str | None = None
    main_sync_state: str = "UNKNOWN"
    upstream: str | None = None
    current_worktree: str | None = None
    worktrees: list[dict[str, Any]] = field(default_factory=list)
    dirty_tracked_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    branches_tracking_deleted_remotes: list[str] = field(default_factory=list)
    local_only_branches: list[str] = field(default_factory=list)
    branches_not_merged: list[str] = field(default_factory=list)
    branches_checked_out_elsewhere: list[str] = field(default_factory=list)
    archive_tags: list[str] = field(default_factory=list)
    stash_list: list[str] = field(default_factory=list)
    open_prs: list[dict[str, Any]] | None = None
    gh_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "read_only": self.read_only,
            "repo_root": self.repo_root,
            "current_branch": self.current_branch,
            "detached_head": self.detached_head,
            "head_sha": self.head_sha,
            "default_remote_branch": self.default_remote_branch,
            "origin_main_sha": self.origin_main_sha,
            "main_sync_state": self.main_sync_state,
            "upstream": self.upstream,
            "current_worktree": self.current_worktree,
            "worktrees": self.worktrees,
            "dirty_tracked_files": self.dirty_tracked_files,
            "staged_files": self.staged_files,
            "untracked_files": self.untracked_files,
            "branches_tracking_deleted_remotes": self.branches_tracking_deleted_remotes,
            "local_only_branches": self.local_only_branches,
            "branches_not_merged": self.branches_not_merged,
            "branches_checked_out_elsewhere": self.branches_checked_out_elsewhere,
            "archive_tags": self.archive_tags,
            "stash_list": self.stash_list,
            "open_prs": self.open_prs,
            "open_prs_status": "UNKNOWN (gh unavailable or call failed)" if self.open_prs is None else "OK",
            "gh_available": self.gh_available,
        }


def build_report(cwd: str | Path | None = None, *, include_prs: bool = True) -> RepoStateReport:
    """One read-only snapshot of git/worktree/branch/stash/PR state."""
    branch = default_remote_branch(cwd=cwd)
    checked_out_elsewhere = branches_checked_out_in_worktrees(cwd=cwd)
    own_branch = current_branch(cwd=cwd)
    return RepoStateReport(
        repo_root=repo_root(cwd=cwd),
        current_branch=own_branch,
        detached_head=is_detached_head(cwd=cwd),
        head_sha=head_sha(cwd=cwd),
        default_remote_branch=branch,
        origin_main_sha=origin_main_sha(cwd=cwd, branch=branch),
        main_sync_state=main_sync_state(cwd=cwd, branch=branch),
        upstream=upstream_branch(cwd=cwd),
        current_worktree=current_worktree_path(cwd=cwd),
        worktrees=[wt.as_dict() for wt in list_worktrees(cwd=cwd)],
        dirty_tracked_files=dirty_tracked_files(cwd=cwd),
        staged_files=staged_files(cwd=cwd),
        untracked_files=untracked_files(cwd=cwd),
        branches_tracking_deleted_remotes=branches_tracking_deleted_remotes(cwd=cwd),
        local_only_branches=local_only_branches(cwd=cwd),
        branches_not_merged=[b for b in branches_not_merged(cwd=cwd, branch=branch) if b != own_branch],
        branches_checked_out_elsewhere=sorted(checked_out_elsewhere - {own_branch} if own_branch else checked_out_elsewhere),
        archive_tags=archive_tags(cwd=cwd),
        stash_list=stash_list(cwd=cwd),
        open_prs=open_prs(cwd=cwd) if include_prs else None,
        gh_available=gh_available(),
    )
