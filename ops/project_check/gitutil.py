"""Read-only git/gh helpers shared by ops.project_check routines.

Every git call here goes through ``run_git``, which only accepts subcommands
in ``_ALLOWED_GIT_SUBCOMMANDS`` -- a defense-in-depth allowlist so a coding
mistake elsewhere in this package cannot accidentally commit, push, pull,
reset, rebase, checkout, delete a branch/worktree, drop a stash, or
create/delete a tag. All subprocess calls pass an argument list (never a
shell string), so there is no shell word-splitting to reason about even
under zsh.

``gh pr list`` (read-only) is used best-effort for PR status; if the ``gh``
binary is not on PATH, or the call fails/times out, PR data is reported as
unavailable rather than guessed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TIMEOUT_S = 6.0


@dataclass(frozen=True)
class GitCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def _is_read_only_git_command(args: list[str]) -> bool:
    """Accept only command shapes used by this package's read-only calls.

    Several Git command families mix inspection and mutation. In particular,
    allowing the top-level ``worktree``, ``branch``, ``stash``, or ``tag``
    name would also admit remove/prune/delete operations. Keep those families
    pinned to their exact inspection forms.
    """
    if not args:
        return False
    command = args[0]
    if command in {"rev-parse", "status", "for-each-ref", "rev-list", "merge-base"}:
        return True
    if command == "ls-remote":
        return args == ["ls-remote", "--heads", "origin", "refs/heads/main"]
    if command == "worktree":
        return args == ["worktree", "list", "--porcelain"]
    if command == "stash":
        return args == ["stash", "list"]
    if command == "tag":
        return args == ["tag", "-l", "archive/*"]
    if command == "branch":
        return (
            len(args) == 3
            and args[1] == "-r"
            and args[2].startswith("--format=")
        ) or (
            len(args) == 5
            and args[1:3] == ["-r", "--merged"]
            and args[4].startswith("--format=")
        )
    return False


def run_git_result(
    args: list[str], *, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S
) -> GitCommandResult:
    """Run one exact read-only Git command and retain its return code."""
    if not _is_read_only_git_command(args):
        raise ValueError(f"git command shape not in read-only allowlist: {args!r}")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitCommandResult(-1, stderr=str(exc))
    return GitCommandResult(
        result.returncode,
        stdout=result.stdout,
        stderr=(result.stderr or "").strip(),
    )


def run_git(args: list[str], *, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S) -> tuple[str | None, str | None]:
    """Run a read-only git subcommand. Returns (stdout, error) -- never raises
    on a git-level failure (nonzero exit, timeout, missing binary)."""
    result = run_git_result(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        return None, result.stderr or f"git {' '.join(args)} exited {result.returncode}"
    return result.stdout, None


def repo_root(cwd: Path) -> Path | None:
    out, _err = run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(out.strip()) if out else None


def current_branch(root: Path) -> str | None:
    out, _err = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    value = out.strip() if out else None
    return value if value and value != "HEAD" else (value or None)


def is_detached_head(root: Path) -> bool:
    out, _err = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root)
    return (out or "").strip() == "HEAD"


def head_sha(root: Path) -> str | None:
    out, _err = run_git(["rev-parse", "HEAD"], cwd=root)
    return out.strip() if out else None


def upstream_of(root: Path, branch: str | None = None) -> str | None:
    ref = f"{branch}@{{upstream}}" if branch else "@{upstream}"
    out, _err = run_git(["rev-parse", "--abbrev-ref", ref], cwd=root)
    return out.strip() if out else None


def local_main_branch(root: Path) -> str | None:
    """Best-effort local default-branch name: prefer 'main', fall back to 'master'."""
    for candidate in ("main", "master"):
        out, _err = run_git(["rev-parse", "--verify", "--quiet", candidate], cwd=root)
        if out:
            return candidate
    return None


def remote_default_ref(root: Path, main_branch: str | None) -> str | None:
    """Best-effort 'origin/<main>' ref name, using only already-known remote-tracking refs (no fetch)."""
    candidates = [f"origin/{main_branch}"] if main_branch else []
    candidates += ["origin/main", "origin/master"]
    for ref in dict.fromkeys(candidates):
        out, _err = run_git(["rev-parse", "--verify", "--quiet", ref], cwd=root)
        if out:
            return ref
    return None


def ref_sha(root: Path, ref: str) -> str | None:
    out, _err = run_git(["rev-parse", ref], cwd=root)
    return out.strip() if out else None


def main_sync_state(root: Path) -> dict[str, Any]:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local main vs origin/main.

    Deliberately does not fetch -- this reflects remote-tracking refs as of the
    last fetch (by this or another tool/operator), not live remote truth. Callers
    that need freshness should fetch separately and re-run.
    """
    main_branch = local_main_branch(root)
    if main_branch is None:
        return {
            "state": "UNKNOWN",
            "reason": "no local 'main' or 'master' branch ref found",
            "local_main_branch": None,
            "remote_ref": None,
            "ahead": None,
            "behind": None,
        }
    remote_ref = remote_default_ref(root, main_branch)
    if remote_ref is None:
        return {
            "state": "UNKNOWN",
            "reason": "no origin/main or origin/master remote-tracking ref found locally "
            "(never fetched in this clone, or remote branch renamed)",
            "local_main_branch": main_branch,
            "remote_ref": None,
            "ahead": None,
            "behind": None,
        }
    out, err = run_git(
        ["rev-list", "--left-right", "--count", f"{remote_ref}...{main_branch}"], cwd=root
    )
    if not out:
        return {
            "state": "UNKNOWN",
            "reason": err or "rev-list failed",
            "local_main_branch": main_branch,
            "remote_ref": remote_ref,
            "ahead": None,
            "behind": None,
        }
    parts = out.strip().split()
    if len(parts) != 2:
        return {
            "state": "UNKNOWN",
            "reason": f"unexpected rev-list output: {out!r}",
            "local_main_branch": main_branch,
            "remote_ref": remote_ref,
            "ahead": None,
            "behind": None,
        }
    behind, ahead = int(parts[0]), int(parts[1])
    if behind == 0 and ahead == 0:
        state = "IN_SYNC"
    elif behind == 0 and ahead > 0:
        state = "AHEAD"
    elif behind > 0 and ahead == 0:
        state = "BEHIND"
    else:
        state = "DIVERGED"
    return {
        "state": state,
        "reason": None,
        "local_main_branch": main_branch,
        "remote_ref": remote_ref,
        "ahead": ahead,
        "behind": behind,
        "as_of": "last local fetch of the remote-tracking ref (this tool does not fetch)",
    }


def status_porcelain(root: Path) -> dict[str, list[str]]:
    """Split `git status --porcelain=v1` into staged / unstaged (dirty tracked) / untracked."""
    out, err = run_git(["status", "--porcelain=v1"], cwd=root)
    staged: list[str] = []
    dirty_tracked: list[str] = []
    untracked: list[str] = []
    if out is None:
        return {"staged": [], "dirty_tracked": [], "untracked": [], "error": err}
    for line in out.splitlines():
        if not line:
            continue
        code = line[:2]
        path = line[3:]
        if code == "??":
            untracked.append(path)
            continue
        index_state, worktree_state = code[0], code[1]
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty_tracked.append(path)
    return {"staged": staged, "dirty_tracked": dirty_tracked, "untracked": untracked, "error": None}


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str | None
    branch: str | None
    bare: bool
    detached: bool
    locked: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "head": self.head,
            "branch": self.branch,
            "bare": self.bare,
            "detached": self.detached,
            "locked": self.locked,
        }


def worktrees(root: Path) -> list[Worktree]:
    out, _err = run_git(["worktree", "list", "--porcelain"], cwd=root)
    if not out:
        return []
    entries: list[Worktree] = []
    cur: dict[str, Any] = {}

    def flush() -> None:
        if cur.get("path"):
            entries.append(
                Worktree(
                    path=cur["path"],
                    head=cur.get("head"),
                    branch=cur.get("branch"),
                    bare=cur.get("bare", False),
                    detached=cur.get("detached", False),
                    locked=cur.get("locked", False),
                )
            )

    for line in out.splitlines():
        if not line.strip():
            flush()
            cur.clear()
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "bare":
            cur["bare"] = True
        elif line == "detached":
            cur["detached"] = True
        elif line.startswith("locked"):
            cur["locked"] = True
    flush()
    return entries


def worktree_dirty(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_dir():
        return {"checked": False, "reason": "path not found"}
    status = status_porcelain(p)
    if status.get("error"):
        return {"checked": False, "reason": status["error"]}
    dirty = bool(status["staged"] or status["dirty_tracked"] or status["untracked"])
    return {
        "checked": True,
        "dirty": dirty,
        "staged_count": len(status["staged"]),
        "dirty_tracked_count": len(status["dirty_tracked"]),
        "untracked_count": len(status["untracked"]),
    }


def stash_list(root: Path) -> list[dict[str, Any]]:
    out, _err = run_git(["stash", "list"], cwd=root)
    if not out:
        return []
    entries = []
    for line in out.splitlines():
        ref, _, message = line.partition(": ")
        entries.append({"ref": ref, "message": message})
    return entries


def archive_tags(root: Path) -> list[dict[str, Any]]:
    out, _err = run_git(["tag", "-l", "archive/*"], cwd=root)
    if not out:
        return []
    tags = []
    for name in out.splitlines():
        name = name.strip()
        if not name:
            continue
        sha = ref_sha(root, name)
        tags.append({"tag": name, "sha": sha})
    return tags


def local_branches(root: Path) -> list[dict[str, Any]]:
    out, _err = run_git(
        [
            "for-each-ref",
            "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)\t%(objectname)",
            "refs/heads/",
        ],
        cwd=root,
    )
    if not out:
        return []
    branches = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, upstream, track, sha = parts
        branches.append(
            {
                "branch": name,
                "upstream": upstream or None,
                "tracking_deleted_remote": track == "[gone]",
                "local_only": upstream == "",
                "sha": sha,
            }
        )
    return branches


def remote_branches(root: Path) -> list[str]:
    out, _err = run_git(["branch", "-r", "--format=%(refname:short)"], cwd=root)
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip() and "->" not in line]


def merged_remote_branches(root: Path, into_ref: str) -> set[str]:
    out, _err = run_git(["branch", "-r", "--merged", into_ref, "--format=%(refname:short)"], cwd=root)
    if not out:
        return set()
    return {line.strip() for line in out.splitlines() if line.strip() and "->" not in line}


def unique_commit_count(root: Path, base_ref: str, branch_ref: str) -> int | None:
    out, _err = run_git(["rev-list", "--count", f"{base_ref}..{branch_ref}"], cwd=root)
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def unmerged_remote_branches_missing_archive_tag(root: Path) -> dict[str, Any]:
    """Remote branches not merged into origin/main with unique commits and no
    archive/* tag pointing at their current tip. Mirrors the manual disposition
    method documented in docs/BRANCH_ARCHIVE_INDEX.md, without deleting anything."""
    main_branch = local_main_branch(root)
    remote_ref = remote_default_ref(root, main_branch)
    if remote_ref is None:
        return {"checked": False, "reason": "no origin/main remote-tracking ref available", "flagged": []}

    all_remote = [b for b in remote_branches(root) if b != remote_ref and not b.endswith("/HEAD")]
    merged = merged_remote_branches(root, remote_ref)
    tags = archive_tags(root)
    tag_shas = {t["sha"] for t in tags if t["sha"]}

    flagged = []
    for branch in all_remote:
        if branch in merged:
            continue
        tip = ref_sha(root, branch)
        unique = unique_commit_count(root, remote_ref, branch)
        has_archive_tag = tip is not None and tip in tag_shas
        if unique and unique > 0 and not has_archive_tag:
            flagged.append(
                {
                    "branch": branch,
                    "tip_sha": tip,
                    "unique_commit_count": unique,
                    "archive_tag": None,
                    "pr_status": "UNKNOWN (gh unavailable or not queried)",
                }
            )
    return {"checked": True, "reason": None, "remote_ref": remote_ref, "flagged": flagged}


def gh_available() -> bool:
    return shutil.which("gh") is not None


def open_prs(root: Path, *, timeout: float = 10.0) -> dict[str, Any]:
    """Best-effort `gh pr list` (read-only). Reports unavailable rather than guessing."""
    if not gh_available():
        return {"available": False, "reason": "gh CLI not found on PATH", "prs": []}
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,url,isDraft,updatedAt",
                "--limit",
                "50",
            ],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": f"gh pr list failed: {exc}", "prs": []}
    if result.returncode != 0:
        return {
            "available": False,
            "reason": (result.stderr or "").strip() or f"gh exited {result.returncode}",
            "prs": [],
        }
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"available": False, "reason": f"could not parse gh output: {exc}", "prs": []}
    return {"available": True, "reason": None, "prs": prs}


def _remote_main_sha(output: str) -> str | None:
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1] == "refs/heads/main":
            return fields[0]
    return None


def verified_origin_main(root: Path) -> dict[str, Any]:
    """Compare local origin/main to the live remote without updating either.

    Used before trusting research/promotion work as based on current main:
    a stale local origin/main (never fetched, or fetched before the remote
    moved) would silently let evidence be generated against outdated code.
    """
    local = run_git_result(
        ["rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
        cwd=root,
    )
    remote = run_git_result(
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
        ancestry = run_git_result(
            ["merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"],
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
    """Verify the checked-out branch uniquely and correctly belongs to this worktree."""
    branch = current_branch(root)
    detached = is_detached_head(root)
    registrations = worktrees(root)
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


def pr_status_for_branch(root: Path, branch: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Best-effort PR status (open/merged/closed/unknown) for one branch via gh."""
    if not gh_available():
        return {"available": False, "status": "UNKNOWN", "reason": "gh CLI not found on PATH"}
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--head", branch, "--json", "state,number,url"],
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "status": "UNKNOWN", "reason": str(exc)}
    if result.returncode != 0:
        return {"available": False, "status": "UNKNOWN", "reason": (result.stderr or "").strip()}
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"available": False, "status": "UNKNOWN", "reason": "could not parse gh output"}
    if not rows:
        return {"available": True, "status": "NO_PR_FOUND", "reason": None}
    return {"available": True, "status": rows[0].get("state", "UNKNOWN"), "reason": None, "pr": rows[0]}
