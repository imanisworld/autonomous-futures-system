"""Read-only session-safety + runtime-snapshot routine.

Two modes, one module:

  1. ``start`` — reports git/worktree/branch/PR/runtime state at the
     beginning of a work session. As the ONE deliberate write this whole
     module ever performs, it also snapshots {branch, worktree path,
     repo root, head, timestamp} to a small JSON file inside the current
     worktree's git-dir (``ops-session-safety-state.json``, resolved via
     ``git rev-parse --absolute-git-dir`` so it lives under ``.git/`` for the
     main worktree or ``.git/worktrees/<name>/`` for a linked one) — never
     tracked, never needs a .gitignore entry.

  2. ``precommit`` (aliased ``prepush`` — same checks, different call site)
     — strictly read-only. Re-derives the same git state and compares it
     against the session-start snapshot, failing closed (non-zero exit,
     clear message) on anything that looks like branch/worktree drift, a
     branch owned by another worktree, an ambiguous branch identity, or a
     session-start snapshot that is missing/unparseable/stale.

Global prohibition for this whole module: it NEVER runs ``git fetch``,
``git pull``, ``git checkout``/``switch``, ``git reset``, ``git rebase``,
``git commit``, ``git push``, branch/tag create-or-delete, or stash
drop/apply. ``origin/main`` (or whatever ``--origin-ref`` is set to) is read
from the existing local remote-tracking ref as-is — this reflects the last
time a human ran ``git fetch``, which may be stale; refreshing it is the
user's job, not this script's.

Also exposes ``collect_git_state`` and the small git-plumbing helpers below
as importable functions — ``ops/daily_reconciliation.py`` reuses them
directly (per repo convention) instead of re-deriving git state.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SESSION_STATE_FILENAME = "ops-session-safety-state.json"
# A "start" snapshot older than this is treated as unverifiable in precommit
# mode and fails closed — a session that has sat for a full day plus is more
# likely to have had the repo touched by something else than to still be the
# same continuous work session. Chosen to comfortably span one working day
# without being so long it papers over an actually-stale session.
STALE_SESSION_SECONDS = 24 * 60 * 60
DEFAULT_MAIN_REF = "main"
DEFAULT_ORIGIN_REF = "origin/main"
# Above this many unmerged-branch candidates, the O(candidates * archive_tags)
# merge-base scan is skipped and reported as a scope limitation rather than
# left to run arbitrarily long on a large repo.
BRANCH_CANDIDATE_SCOPE_LIMIT = 50
GH_TIMEOUT_SECONDS = 10.0
# Documented threshold for "stale PR": no activity (updatedAt) in this many
# days. 14 days is long enough that a PR mid-review isn't flagged, short
# enough to surface genuinely abandoned work.
STALE_PR_DAYS = 14


# ─── Low-level git plumbing (argument lists only — never shell=True) ──────

def _git(repo_root: Path, *args: str, timeout: float = 5.0) -> str | None:
    """Run a read-only git command; return stripped stdout or None on any failure."""
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
    out = result.stdout.strip()
    return out or None


def _git_lines(repo_root: Path, *args: str, timeout: float = 5.0) -> list[str]:
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
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines()]


def _git_ok(repo_root: Path, *args: str, timeout: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def resolve_repo_root(repo_root: str | Path | None = None) -> Path | None:
    """Discover the git worktree root; None if not inside a git repo."""
    start = Path(repo_root).resolve() if repo_root else Path.cwd()
    out = _git(start, "rev-parse", "--show-toplevel")
    return Path(out).resolve() if out else None


def git_dir(repo_root: Path) -> Path | None:
    """Absolute git-dir for the CURRENT worktree (``.git`` for the main
    worktree, ``.git/worktrees/<name>`` for a linked one). This is where the
    session-safety state file lives — private per worktree, never tracked."""
    out = _git(repo_root, "rev-parse", "--absolute-git-dir")
    return Path(out).resolve() if out else None


def current_branch(repo_root: Path) -> str | None:
    """Branch name, or None on detached HEAD (or any failure)."""
    return _git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")


def current_head(repo_root: Path) -> str | None:
    return _git(repo_root, "rev-parse", "HEAD")


def _parse_status_branch(status_b_line: str) -> str | None:
    """Parse the first line of `git status --porcelain=v1 -b`, e.g.
    '## main...origin/main [behind 1]' or '## HEAD (no branch)'."""
    if not status_b_line.startswith("## "):
        return None
    body = status_b_line[3:]
    if body.startswith("HEAD ("):
        return None  # detached
    return body.split("...")[0].split(" ")[0] or None


def cross_checked_branch(repo_root: Path) -> tuple[str | None, str | None, bool]:
    """Two independent branch-identity methods: symbolic-ref and the first
    line of `git status -b`. Returns (symbolic_ref_branch, status_branch,
    ambiguous). ``ambiguous`` is True only when both resolved to a branch
    name and they disagree — callers must not silently pick one in that
    case."""
    symbolic = current_branch(repo_root)
    # `_git` returns the FULL stripped stdout, not just the first line — a
    # dirty tree adds many more `git status -b` lines after the `## branch`
    # header, so this must only ever look at that first line.
    status_lines = _git_lines(repo_root, "status", "--porcelain=v1", "-b")
    status_branch = _parse_status_branch(status_lines[0]) if status_lines else None
    ambiguous = bool(symbolic and status_branch and symbolic != status_branch)
    return symbolic, status_branch, ambiguous


def upstream_branch(repo_root: Path) -> str | None:
    return _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def origin_ref_sha(repo_root: Path, ref: str = DEFAULT_ORIGIN_REF) -> str | None:
    """SHA of the given remote-tracking ref AS LAST FETCHED — never fetches."""
    return _git(repo_root, "rev-parse", ref)


def sync_relationship(repo_root: Path, ref: str = DEFAULT_ORIGIN_REF) -> dict[str, Any]:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN vs a local ref, using
    `git rev-list --left-right --count HEAD...ref` — no fetch performed.
    Reflects the state as of the last local fetch, which may be stale;
    refreshing it (a manual `git fetch`) is the user's responsibility."""
    head = current_head(repo_root)
    ref_sha = origin_ref_sha(repo_root, ref)
    note = (
        "reflects the last local fetch of this ref, which may be stale; "
        "run `git fetch` yourself to refresh before trusting this number"
    )
    if head is None or ref_sha is None:
        return {
            "relationship": "UNKNOWN", "ahead": None, "behind": None,
            "ref": ref, "ref_sha": ref_sha, "note": note,
        }
    counts = _git(repo_root, "rev-list", "--left-right", "--count", f"HEAD...{ref}")
    if not counts:
        return {
            "relationship": "UNKNOWN", "ahead": None, "behind": None,
            "ref": ref, "ref_sha": ref_sha, "note": note,
        }
    try:
        ahead_s, behind_s = counts.split()
        ahead, behind = int(ahead_s), int(behind_s)
    except ValueError:
        return {
            "relationship": "UNKNOWN", "ahead": None, "behind": None,
            "ref": ref, "ref_sha": ref_sha, "note": note,
        }
    if ahead == 0 and behind == 0:
        relationship = "IN_SYNC"
    elif ahead > 0 and behind == 0:
        relationship = "AHEAD"
    elif ahead == 0 and behind > 0:
        relationship = "BEHIND"
    else:
        relationship = "DIVERGED"
    return {"relationship": relationship, "ahead": ahead, "behind": behind, "ref": ref, "ref_sha": ref_sha, "note": note}


def list_worktrees(repo_root: Path) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain`."""
    lines = _git_lines(repo_root, "worktree", "list", "--porcelain")
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current is not None:
            worktrees.append(current)

    for line in lines:
        if not line.strip():
            flush()
            current = None
            continue
        if line.startswith("worktree "):
            flush()
            current = {
                "path": line[len("worktree "):].strip(),
                "head": None, "branch": None,
                "detached": False, "bare": False,
                "locked": False, "locked_reason": None,
                "prunable": False, "prunable_reason": None,
            }
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip()
        elif line == "detached":
            current["detached"] = True
        elif line == "bare":
            current["bare"] = True
        elif line.startswith("locked"):
            current["locked"] = True
            current["locked_reason"] = line[len("locked"):].strip() or None
        elif line.startswith("prunable"):
            current["prunable"] = True
            current["prunable_reason"] = line[len("prunable"):].strip() or None
    flush()
    return worktrees


def worktree_dirty(path: str, *, timeout: float = 5.0) -> bool | None:
    """Best-effort: has this worktree got any tracked/untracked changes?
    None if the path can't be inspected (e.g. a pruned/missing worktree)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=path,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def git_status(repo_root: Path) -> dict[str, Any]:
    """Parse `git status --porcelain=v1` by status code (not naive line
    splitting): staged = index has a change, unstaged = tracked working-tree
    change, untracked = '??' entries. A rename/renamed-and-modified line can
    appear in more than one bucket, which is correct."""
    lines = _git_lines(repo_root, "status", "--porcelain=v1")
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines:
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
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked, "raw": lines}


_VV_RE = re.compile(r"^[*+]?\s*(\(.*\)|\S+)\s+[0-9a-f]+\s+(?:\[([^\]]+)\])?")


def branch_vv_report(repo_root: Path) -> dict[str, list[str]]:
    """Parse `git branch -vv`: branches tracking a deleted remote (': gone]')
    and local-only branches (no upstream configured)."""
    gone: list[str] = []
    local_only: list[str] = []
    tracked: list[str] = []
    for line in _git_lines(repo_root, "branch", "-vv"):
        match = _VV_RE.match(line)
        if not match:
            continue
        name, bracket = match.group(1), match.group(2)
        if name.startswith("("):
            continue  # "(HEAD detached at ...)" pseudo-entry
        if bracket is None:
            local_only.append(name)
        elif "gone" in bracket:
            gone.append(name)
        else:
            tracked.append(name)
    return {"gone": gone, "local_only": local_only, "tracked": tracked}


def stash_list(repo_root: Path) -> list[dict[str, str]]:
    entries = []
    for line in _git_lines(repo_root, "stash", "list"):
        index, _, label = line.partition(":")
        entries.append({"index": index.strip(), "label": label.strip()})
    return entries


def archive_tags(repo_root: Path) -> list[str]:
    return sorted(_git_lines(repo_root, "tag", "-l", "archive/*"))


def local_branches(repo_root: Path) -> list[str]:
    return sorted(_git_lines(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/"))


def remote_branches(repo_root: Path, remote: str = "origin") -> list[str]:
    out = _git_lines(repo_root, "for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}/")
    return sorted(b for b in out if not b.endswith("/HEAD"))


def is_ancestor(repo_root: Path, commit_ish: str, of: str) -> bool:
    return _git_ok(repo_root, "merge-base", "--is-ancestor", commit_ish, of)


def merged_local_branches(repo_root: Path, main_ref: str = DEFAULT_MAIN_REF) -> list[str]:
    """Local branches already fully merged into main_ref (excludes main itself)."""
    out = _git_lines(repo_root, "branch", "--merged", main_ref, "--format=%(refname:short)")
    return sorted(b for b in out if b not in (main_ref, "main", "master"))


def branch_last_commit_iso(repo_root: Path, ref: str) -> str | None:
    return _git(repo_root, "log", "-1", "--format=%cI", ref)


def _tag_commit_sha(repo_root: Path, tag: str) -> str | None:
    """Resolve a tag (lightweight or annotated) to the commit it points at."""
    return _git(repo_root, "rev-parse", f"{tag}^{{commit}}")


@dataclass(frozen=True)
class UnmergedBranchCandidate:
    branch: str
    ref: str
    in_local: bool
    in_remote: bool
    tip_sha: str | None
    unique_commit_count: int
    archive_matches: tuple[dict[str, Any], ...]

    @property
    def archive_exact_preserved(self) -> bool:
        return any(m["exact"] for m in self.archive_matches)

    @property
    def archive_descendant_preserved(self) -> bool:
        return any(m["descends"] for m in self.archive_matches)

    def as_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "ref": self.ref,
            "in_local": self.in_local,
            "in_remote": self.in_remote,
            "tip_sha": self.tip_sha,
            "unique_commit_count": self.unique_commit_count,
            "archive_matches": list(self.archive_matches),
            "archive_exact_preserved": self.archive_exact_preserved,
            "archive_descendant_preserved": self.archive_descendant_preserved,
        }


def closed_unmerged_branches_with_evidence(
    repo_root: Path,
    *,
    main_ref: str = DEFAULT_MAIN_REF,
    archive_tags_list: list[str] | None = None,
    scope_limit: int = BRANCH_CANDIDATE_SCOPE_LIMIT,
    exclude_branches: set[str] | None = None,
) -> dict[str, Any]:
    """Best-effort: local+remote branches not merged into main_ref that carry
    commits main doesn't have, cross-checked against archive/* tags.

    Without `gh` (or when a branch's PR status can't be determined), this
    cannot actually tell "closed" apart from "still open/active WIP" — it can
    only tell "not yet merged into main". `exclude_branches` lets a caller
    exclude branches already known to be active (e.g. the branch currently
    checked out) so the report isn't dominated by a session's own WIP.

    Deliberately scoped: this is an O(candidates * archive_tags) merge-base
    scan, which can be slow/imperfect on a large repo. Above scope_limit
    candidates, scanning is skipped entirely and the limitation is reported
    rather than left to run arbitrarily long or silently truncate results."""
    tags = archive_tags_list if archive_tags_list is not None else archive_tags(repo_root)
    excluded = exclude_branches or set()
    local = [b for b in local_branches(repo_root) if b not in ("main", "master") and b not in excluded]
    remote = remote_branches(repo_root)
    remote_short = {
        b.split("/", 1)[1] for b in remote
        if "/" in b and b.split("/", 1)[1] not in ("main", "master") and b.split("/", 1)[1] not in excluded
    }
    names = sorted(set(local) | remote_short)

    if len(names) > scope_limit:
        return {
            "scoped": False,
            "scope_limit": scope_limit,
            "candidate_count": len(names),
            "limitation": (
                f"{len(names)} candidate branches exceeds the {scope_limit}-branch scan "
                "scope; skipped to avoid an unbounded merge-base scan. Narrow the branch "
                "set (e.g. delete/archive stale branches first) to get a full report."
            ),
            "branches": [],
        }

    if not _git_ok(repo_root, "rev-parse", "--verify", "--quiet", main_ref):
        return {
            "scoped": True, "scope_limit": scope_limit, "candidate_count": len(names),
            "limitation": f"main ref {main_ref!r} does not resolve locally; cannot compute merge status.",
            "branches": [],
        }

    results: list[dict[str, Any]] = []
    for name in names:
        ref = name if name in local else f"origin/{name}"
        if not _git_ok(repo_root, "rev-parse", "--verify", "--quiet", ref):
            continue
        if is_ancestor(repo_root, ref, main_ref):
            continue  # fully merged into main — not "unmerged with unique evidence"
        count_raw = _git(repo_root, "rev-list", "--count", f"{main_ref}..{ref}")
        try:
            unique_count = int(count_raw) if count_raw else 0
        except ValueError:
            unique_count = 0
        if unique_count <= 0:
            continue
        tip_sha = _git(repo_root, "rev-parse", ref)
        matches = []
        for tag in tags:
            tag_sha = _tag_commit_sha(repo_root, tag)
            if tag_sha is None:
                continue
            exact = tip_sha is not None and tag_sha == tip_sha
            descends = exact or is_ancestor(repo_root, ref, tag)
            if exact or descends:
                matches.append({"tag": tag, "exact": exact, "descends": descends})
        results.append(
            UnmergedBranchCandidate(
                branch=name,
                ref=ref,
                in_local=name in local,
                in_remote=name in remote_short,
                tip_sha=tip_sha,
                unique_commit_count=unique_count,
                archive_matches=tuple(matches),
            ).as_dict()
        )

    return {
        "scoped": True,
        "scope_limit": scope_limit,
        "candidate_count": len(names),
        "limitation": None,
        "branches": results,
    }


# ─── GitHub PR helpers (never mutate; UNKNOWN if `gh` unavailable/fails) ──

def gh_available() -> bool:
    return shutil.which("gh") is not None


def _gh_json(repo_root: Path, args: list[str], *, timeout: float = GH_TIMEOUT_SECONDS) -> tuple[Any, str | None]:
    if not gh_available():
        return None, "gh_not_available"
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"gh_error: {exc}"
    if result.returncode != 0:
        return None, f"gh_error: {(result.stderr or '').strip()[:300] or 'nonzero exit'}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"gh_parse_error: {exc}"


_PR_FIELDS = "number,title,headRefName,baseRefName,url,state,createdAt,updatedAt,mergedAt,closedAt"


def prs_by_state(
    repo_root: Path, state: str, *, timeout: float = GH_TIMEOUT_SECONDS, limit: int = 200
) -> tuple[list[dict[str, Any]] | str, str | None]:
    """('UNKNOWN', reason) if `gh` is unavailable or the call fails/times out;
    never invents PR data."""
    data, err = _gh_json(
        repo_root,
        ["pr", "list", "--state", state, "--json", _PR_FIELDS, "--limit", str(limit)],
        timeout=timeout,
    )
    if err:
        return "UNKNOWN", err
    return data, None


def prs_active_today(repo_root: Path, *, today: str | None = None, timeout: float = GH_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Opened / merged / closed-unmerged PRs whose relevant date matches
    today (local date string, YYYY-MM-DD). Fields are 'UNKNOWN' together if
    `gh` is unavailable/fails — never partially invented."""
    today = today or datetime.now(timezone.utc).date().isoformat()
    all_prs, err = prs_by_state(repo_root, "all", timeout=timeout, limit=300)
    if err:
        return {"opened_today": "UNKNOWN", "merged_today": "UNKNOWN", "closed_unmerged_today": "UNKNOWN", "error": err}
    opened = [p for p in all_prs if str(p.get("createdAt") or "")[:10] == today]
    merged = [p for p in all_prs if p.get("mergedAt") and str(p["mergedAt"])[:10] == today]
    closed_unmerged = [
        p for p in all_prs
        if p.get("closedAt") and not p.get("mergedAt") and str(p["closedAt"])[:10] == today
    ]
    return {
        "opened_today": opened, "merged_today": merged,
        "closed_unmerged_today": closed_unmerged, "error": None,
    }


def stale_open_prs(
    repo_root: Path, *, days: int = STALE_PR_DAYS, now: datetime | None = None, timeout: float = GH_TIMEOUT_SECONDS
) -> tuple[list[dict[str, Any]] | str, str | None]:
    now = now or datetime.now(timezone.utc)
    data, err = prs_by_state(repo_root, "open", timeout=timeout)
    if err:
        return "UNKNOWN", err
    stale = []
    for pr in data:
        updated = _parse_iso(pr.get("updatedAt"))
        if updated is not None and (now - updated).days >= days:
            stale.append(pr)
    return stale, None


# ─── Combined git-state collector (shared by session_safety + daily_reconciliation) ──

def collect_git_state(
    repo_root: Path,
    *,
    main_ref: str = DEFAULT_MAIN_REF,
    origin_ref: str = DEFAULT_ORIGIN_REF,
    branch_scope_limit: int = BRANCH_CANDIDATE_SCOPE_LIMIT,
    gh_timeout: float = GH_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One read-only pass over repo/branch/worktree/PR/stash state. Never
    fetches. This is the single source of truth both `session_safety start`
    and `daily_reconciliation`'s repo-reconciliation section build on."""
    now = now or datetime.now(timezone.utc)
    symbolic_branch, status_branch, branch_ambiguous = cross_checked_branch(repo_root)
    head = current_head(repo_root)
    worktree_path = _git(repo_root, "rev-parse", "--show-toplevel")
    upstream = upstream_branch(repo_root)
    sync = sync_relationship(repo_root, ref=origin_ref)
    status = git_status(repo_root)
    worktrees = list_worktrees(repo_root)
    for wt in worktrees:
        wt["dirty"] = worktree_dirty(wt["path"]) if wt.get("path") else None
    vv = branch_vv_report(repo_root)
    stashes = stash_list(repo_root)
    tags = archive_tags(repo_root)
    open_prs, open_prs_error = prs_by_state(repo_root, "open", timeout=gh_timeout)
    candidates = closed_unmerged_branches_with_evidence(
        repo_root, main_ref=main_ref, archive_tags_list=tags, scope_limit=branch_scope_limit,
        exclude_branches={symbolic_branch} if symbolic_branch else None,
    )
    local = local_branches(repo_root)
    remote = remote_branches(repo_root)
    remote_short = {b.split("/", 1)[1] for b in remote if "/" in b}
    unexpected_remote_branches = sorted(remote_short - set(local))

    return {
        "generated_at": now.isoformat(),
        "repo_root": str(repo_root),
        "branch": symbolic_branch,
        "status_branch": status_branch,
        "branch_ambiguous": branch_ambiguous,
        "head": head,
        "current_worktree": worktree_path,
        "upstream": upstream,
        "sync": sync,
        "git_status": status,
        "worktrees": worktrees,
        "branches_tracking_deleted_remotes": vv["gone"],
        "local_only_branches": vv["local_only"],
        "local_branches": local,
        "remote_branches": remote,
        "unexpected_remote_branches": unexpected_remote_branches,
        "open_prs": open_prs,
        "open_prs_error": open_prs_error,
        "closed_unmerged_candidates": candidates,
        "archive_tags": tags,
        "stashes": stashes,
    }


# ─── Runtime snapshot (best-effort; UNKNOWN where unavailable, never invented) ──

def _fill_model_from_inventory_text(text: str) -> str | None:
    lowered = text.lower()
    if "ioc" in lowered:
        return "ioc_limit"
    if "stop" in lowered and "market" in lowered:
        return "stop_market"
    if "market" in lowered:
        return "market"
    return None


def _strategy_inventory_profile_fill_models(inventory_path: Path) -> dict[str, str]:
    """Best-effort: {profile heading -> recorded 'Fill model:' text} parsed
    from Strategy_Inventory.md's '### <heading>' profile sections."""
    if not inventory_path.exists():
        return {}
    try:
        text = inventory_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    headings: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("### "):
            current = line[4:].strip()
            continue
        stripped = line.strip()
        if current and stripped.lower().startswith("- fill model:"):
            headings[current] = stripped.split(":", 1)[1].strip()
    return headings


def fill_model_drift(config: Any, inventory_path: Path, active_lanes: list[str]) -> list[dict[str, Any]]:
    """Best-effort cross-check: does the deployed SystemConfig.entry_fill_model
    match what the canonical-evidence profile for each active lane recorded?
    Heuristic substring matching only (no strategy-name fuzzy scoring) —
    reports UNKNOWN rather than guessing when no profile can be matched."""
    headings = _strategy_inventory_profile_fill_models(inventory_path)
    results = []
    for lane in active_lanes:
        needle = lane.replace("_", " ")
        matched_heading = next(
            (h for h in headings if needle in h.lower() or lane in h.lower().replace(" ", "_").replace("-", "_")),
            None,
        )
        if matched_heading is None:
            results.append({
                "strategy": lane, "status": "UNKNOWN",
                "reason": "no matching Strategy_Inventory.md profile heading found",
            })
            continue
        recorded_text = headings[matched_heading]
        mapped = _fill_model_from_inventory_text(recorded_text)
        config_model = getattr(config, "entry_fill_model", None)
        status = "UNKNOWN" if mapped is None else ("MISMATCH" if mapped != config_model else "MATCH")
        results.append({
            "strategy": lane,
            "inventory_profile": matched_heading,
            "inventory_fill_model_text": recorded_text,
            "inventory_mapped_model": mapped,
            "config_entry_fill_model": config_model,
            "status": status,
        })
    return results


def build_runtime_snapshot(repo_root: Path) -> dict[str, Any]:
    """Best-effort deployed-state snapshot. Every sub-section independently
    degrades to UNKNOWN/empty on failure rather than raising or guessing —
    this function must never crash the caller."""
    snapshot: dict[str, Any] = {
        "live_box_guard": {"available": False, "error": None, "data": None},
        "intended_deployed_commit": "UNKNOWN",
        "recorded_evidence_epochs": "UNKNOWN",
        "active_paper_forward_lanes": [],
        "lane_execution": {},
        "entry_tolerance_ticks_by_root": {},
        "contract_cap_per_instrument": {},
        "fill_model_drift": [],
        "config_load_error": None,
    }

    try:
        from ops.live_box_guard import live_box_drift_report
        lbg = live_box_drift_report(repo_root=repo_root)
        snapshot["live_box_guard"] = {
            "available": True, "error": None,
            "data": {
                "status": lbg.get("status"),
                "branch": lbg.get("branch"),
                "commit": lbg.get("commit"),
                "identity_source": lbg.get("identity_source"),
                "missing_pins": lbg.get("missing_pins"),
                "mismatches": lbg.get("mismatches"),
                "summary": lbg.get("summary"),
            },
        }
        for comparison in lbg.get("comparisons", []):
            if comparison.get("name") == "commit" and comparison.get("expected"):
                snapshot["intended_deployed_commit"] = comparison["expected"]
        # No evidence-epoch concept exists anywhere in this codebase today
        # (checked: no EVIDENCE_EPOCH env/field in live_box_guard or
        # evidence_report/evidence_readiness). Left UNKNOWN rather than
        # invented; update this if/when one is added.
    except Exception as exc:  # live_box_guard may legitimately not be runnable off-box
        snapshot["live_box_guard"] = {"available": False, "error": str(exc), "data": None}

    try:
        from config.settings import load_config
        config = load_config(str(repo_root / "risk_rules.yaml"))
    except Exception as exc:
        snapshot["config_load_error"] = str(exc)
        return snapshot

    active_lanes = [
        strategy for strategy in (config.enabled_concepts or [])
        if config.strategy_status.get(strategy, config.strategy_permission_default_status) == "PAPER_ELIGIBLE"
    ]
    snapshot["active_paper_forward_lanes"] = active_lanes

    try:
        from ops.evidence_report import LANE_CLASS
    except Exception:
        LANE_CLASS = {}
    for strategy in active_lanes:
        snapshot["lane_execution"][strategy] = {
            "lane_class_per_evidence_report": LANE_CLASS.get(strategy, "UNKNOWN"),
            "entry_fill_model": config.entry_fill_model,
        }
    snapshot["entry_tolerance_ticks_by_root"] = dict(config.entry_tolerance_ticks_by_root or {})
    snapshot["contract_cap_per_instrument"] = dict(config.max_contracts_per_instrument or {})

    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    snapshot["fill_model_drift"] = fill_model_drift(config, inventory_path, active_lanes)
    return snapshot


# ─── Session-start snapshot file (the ONE write this module performs) ─────

def _write_session_snapshot(
    repo_root: Path, *, branch: str | None, worktree_path: str | None, head: str | None, now: datetime,
) -> tuple[bool, str | None, str | None]:
    gd = git_dir(repo_root)
    if gd is None:
        return False, None, "could not determine git-dir; session snapshot not written"
    path = gd / SESSION_STATE_FILENAME
    payload = {
        "branch": branch,
        "worktree_path": worktree_path,
        "repo_root": str(repo_root),
        "head": head,
        "timestamp": now.isoformat(),
    }
    try:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        return False, str(path), f"could not write session snapshot at {path}: {exc}"
    return True, str(path), None


def load_session_snapshot(repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    gd = git_dir(repo_root)
    if gd is None:
        return None, "cannot determine git-dir for this worktree"
    path = gd / SESSION_STATE_FILENAME
    if not path.exists():
        return None, f"no session-start snapshot found at {path} (run `session_safety.py start` first)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"session-start snapshot at {path} is unparseable: {exc}"
    required = {"branch", "worktree_path", "repo_root", "head", "timestamp"}
    if not required.issubset(data):
        return None, f"session-start snapshot at {path} is missing required fields"
    return data, None


# ─── Mode A: start ─────────────────────────────────────────────────────────

def build_start_report(*, repo_root: str | Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    root = resolve_repo_root(repo_root)
    if root is None:
        return {
            "ok": False, "mode": "start",
            "error": "not inside a git repository (or `git` is unavailable)",
            "repo_root": None,
        }

    branch_before = current_branch(root)
    state = collect_git_state(root, now=now)
    runtime = build_runtime_snapshot(root)
    branch_after = current_branch(root)
    branch_changed_during_check = branch_before != branch_after

    warnings: list[str] = []
    if branch_changed_during_check:
        warnings.append(
            f"branch changed DURING this check: {branch_before!r} -> {branch_after!r} — "
            "something else touched this repo concurrently; re-run before trusting this snapshot"
        )
    if state["branch_ambiguous"]:
        warnings.append(
            f"branch identity is ambiguous: symbolic-ref={state['branch']!r} vs "
            f"status -b={state['status_branch']!r}"
        )
    if state["open_prs_error"]:
        warnings.append(f"open PRs: UNKNOWN ({state['open_prs_error']})")
    if runtime["config_load_error"]:
        warnings.append(f"runtime snapshot: config.settings.load_config() failed: {runtime['config_load_error']}")
    if not runtime["live_box_guard"]["available"]:
        warnings.append(
            f"live_box_guard unavailable from this context: {runtime['live_box_guard']['error']} "
            "(expected off the deployed box)"
        )

    snapshot_written, snapshot_path, snapshot_warning = _write_session_snapshot(
        root,
        branch=branch_after,
        worktree_path=state["current_worktree"],
        head=state["head"],
        now=now,
    )
    if snapshot_warning:
        warnings.append(snapshot_warning)

    return {
        "ok": True,
        "mode": "start",
        **state,
        "runtime_snapshot": runtime,
        "branch_changed_during_check": branch_changed_during_check,
        "snapshot_written": snapshot_written,
        "snapshot_path": snapshot_path,
        "warnings": warnings,
    }


def format_start_report(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"SESSION SAFETY: start — FAILED\n{report.get('error')}"
    sync = report["sync"]
    lines = [
        "SESSION SAFETY: start",
        f"repo_root: {report['repo_root']}",
        f"branch: {report['branch']}  head: {report['head']}",
        f"upstream: {report['upstream']}",
        f"vs {sync['ref']}: {sync['relationship']} (ahead={sync['ahead']} behind={sync['behind']}) "
        f"[{sync['note']}]",
        f"current worktree: {report['current_worktree']}",
        f"worktrees ({len(report['worktrees'])}):",
    ]
    for wt in report["worktrees"]:
        lines.append(f"  - {wt['path']}  branch={wt.get('branch')}  dirty={wt.get('dirty')}")
    gs = report["git_status"]
    lines.append(
        f"dirty tracked: {len(gs['unstaged'])}  staged: {len(gs['staged'])}  untracked: {len(gs['untracked'])}"
    )
    lines.append(f"branches tracking deleted remotes: {report['branches_tracking_deleted_remotes']}")
    lines.append(f"local-only branches: {report['local_only_branches']}")
    open_prs = report["open_prs"]
    lines.append(f"open PRs: {len(open_prs) if isinstance(open_prs, list) else open_prs}")
    candidates = report["closed_unmerged_candidates"]
    if candidates.get("scoped"):
        unpreserved = [b for b in candidates["branches"] if not b["archive_descendant_preserved"]]
        lines.append(
            f"closed-unmerged-with-evidence candidates: {len(candidates['branches'])} "
            f"({len(unpreserved)} without an archive tag)"
        )
    else:
        lines.append(f"closed-unmerged-with-evidence scan skipped: {candidates.get('limitation')}")
    lines.append(f"archive/* tags: {len(report['archive_tags'])}")
    lines.append(f"stashes: {len(report['stashes'])}")
    lines.append(f"branch changed during check: {report['branch_changed_during_check']}")
    lines.append("")
    lines.append("RUNTIME SNAPSHOT")
    runtime = report["runtime_snapshot"]
    lbg = runtime["live_box_guard"]
    lines.append(f"  live_box_guard available: {lbg['available']}  error: {lbg['error']}")
    lines.append(f"  intended deployed commit: {runtime['intended_deployed_commit']}")
    lines.append(f"  recorded evidence epoch(s): {runtime['recorded_evidence_epochs']}")
    lines.append(f"  active paper-forward lanes: {runtime['active_paper_forward_lanes']}")
    lines.append(f"  entry_tolerance_ticks_by_root: {runtime['entry_tolerance_ticks_by_root']}")
    lines.append(f"  contract_cap_per_instrument: {runtime['contract_cap_per_instrument']}")
    for row in runtime["fill_model_drift"]:
        if row.get("status") == "MISMATCH":
            lines.append(f"  FILL MODEL DRIFT: {row}")
    if report["warnings"]:
        lines.append("")
        lines.append("WARNINGS")
        for warning in report["warnings"]:
            lines.append(f"  - {warning}")
    lines.append("")
    lines.append(f"session snapshot written: {report['snapshot_written']} ({report['snapshot_path']})")
    return "\n".join(lines)


# ─── Mode B: precommit / prepush ───────────────────────────────────────────

def build_precommit_report(
    *,
    repo_root: str | Path | None = None,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_SESSION_SECONDS,
) -> dict[str, Any]:
    """STRICTLY READ ONLY. Never commits/pushes/pulls/resets/rebases/
    checks-out/switches/deletes branches/removes worktrees/drops stashes/
    creates-or-deletes tags/modifies any file — including its own session
    state file, which it only ever reads.

    "Unexpected files changed" is deliberately NOT a fail condition here:
    this routine has no policy input describing which files a given commit
    is allowed to touch, so cheaply defining "unexpected" isn't possible
    without guessing. Changed/staged/untracked files are reported for human
    review, never used to fail the check on their own."""
    now = now or datetime.now(timezone.utc)
    root = resolve_repo_root(repo_root)
    if root is None:
        return {
            "ok": False, "mode": "precommit",
            "failures": ["not inside a git repository (or `git` is unavailable)"],
            "exit_code": 2,
        }

    branch_before = current_branch(root)
    symbolic_branch, status_branch, branch_ambiguous = cross_checked_branch(root)
    head = current_head(root)
    worktree_path = _git(root, "rev-parse", "--show-toplevel")
    upstream = upstream_branch(root)
    sync = sync_relationship(root)
    status = git_status(root)
    worktrees = list_worktrees(root)

    failures: list[str] = []

    if branch_ambiguous:
        failures.append(
            f"branch identity is ambiguous (symbolic-ref={symbolic_branch!r} vs "
            f"status -b={status_branch!r}); classifying UNKNOWN and failing closed"
        )
    effective_branch = "UNKNOWN" if branch_ambiguous else symbolic_branch

    snapshot, snapshot_error = load_session_snapshot(root)
    if snapshot_error:
        failures.append(f"session-start state cannot be verified: {snapshot_error}")

    session_branch = session_worktree = None
    if snapshot:
        session_branch = snapshot.get("branch")
        session_worktree = snapshot.get("worktree_path")
        snap_ts = _parse_iso(snapshot.get("timestamp"))
        if snap_ts is None:
            failures.append("session-start state cannot be verified: snapshot timestamp is unparseable")
        else:
            age_seconds = (now - snap_ts).total_seconds()
            if age_seconds < 0:
                failures.append("session-start snapshot timestamp is in the future; cannot trust session state")
            elif age_seconds > stale_after_seconds:
                failures.append(
                    f"session-start state cannot be verified: snapshot is stale "
                    f"({age_seconds / 3600:.1f}h old, threshold {stale_after_seconds / 3600:.0f}h) — "
                    "start a new session"
                )

        if not branch_ambiguous and effective_branch != session_branch:
            failures.append(
                f"branch differs from session-start branch: now {effective_branch!r}, "
                f"was {session_branch!r} at session start"
            )
        if worktree_path != session_worktree:
            failures.append(
                f"worktree differs from session-start worktree: now {worktree_path!r}, "
                f"was {session_worktree!r} at session start"
            )
        if session_branch:
            for wt in worktrees:
                wt_branch = (wt.get("branch") or "").removeprefix("refs/heads/")
                if wt_branch == session_branch and wt.get("path") != worktree_path:
                    failures.append(
                        f"session-start branch {session_branch!r} is checked out in a different "
                        f"worktree ({wt['path']}) — it is owned there, not here"
                    )

    # Branch-moved-during-this-very-check, independent of the session-start comparison above.
    branch_after = current_branch(root)
    if branch_before != branch_after:
        failures.append(f"branch changed DURING this precommit check: {branch_before!r} -> {branch_after!r}")

    changed_files = sorted(set(status["staged"]) | set(status["unstaged"]))
    ok = not failures

    return {
        "ok": ok,
        "mode": "precommit",
        "generated_at": now.isoformat(),
        "repo_root": str(root),
        "current_branch": effective_branch,
        "current_head": head,
        "session_start_branch": session_branch,
        "current_worktree": worktree_path,
        "session_start_worktree": session_worktree,
        "upstream": upstream,
        "sync": sync,
        "changed_files": changed_files,
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "snapshot": snapshot,
        "snapshot_error": snapshot_error,
        "failures": failures,
        "exit_code": 0 if ok else 1,
        "note": (
            "'unexpected files changed' is informational only in this routine — see docstring; "
            "it is reported but never fails the check by itself."
        ),
    }


def format_precommit_report(report: dict[str, Any]) -> str:
    if report.get("repo_root") is None:
        return f"SESSION SAFETY: precommit — FAIL CLOSED\n" + "\n".join(f"  - {f}" for f in report["failures"])
    lines = [
        f"SESSION SAFETY: precommit — {'PASS' if report['ok'] else 'FAIL CLOSED'}",
        f"repo_root: {report['repo_root']}",
        f"current branch: {report['current_branch']}  (session-start: {report['session_start_branch']})",
        f"current head: {report['current_head']}",
        f"current worktree: {report['current_worktree']}  (session-start: {report['session_start_worktree']})",
        f"upstream: {report['upstream']}",
        f"vs origin/main: {report['sync']['relationship']} "
        f"(ahead={report['sync']['ahead']} behind={report['sync']['behind']})",
        f"changed files: {len(report['changed_files'])}  staged: {len(report['staged_files'])}  "
        f"untracked: {len(report['untracked_files'])}",
        f"note: {report['note']}",
    ]
    if report["failures"]:
        lines.append("")
        lines.append("FAILURES")
        for failure in report["failures"]:
            lines.append(f"  - {failure}")
    return "\n".join(lines)
