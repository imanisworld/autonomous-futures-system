"""Read-only repo/process safety routines: session-start, precommit, daily.

Three manually-invoked, read-only checks aimed at the failure modes this repo
has actually hit before: branch/worktree collisions, stale local state, dirty
files landing in the wrong branch, and closed research branches losing their
only copy of unique evidence.

This module never mutates git state. It does not commit, push, reset, rebase,
checkout, pull, fetch (unless --fetch is passed explicitly), delete branches,
delete worktrees, or create tags. It shells out to `git` (and, best-effort,
`gh` if installed) purely to read state.

Usage:
    python3 -m ops.project_check session-start
    python3 -m ops.project_check precommit
    python3 -m ops.project_check daily
    ... any subcommand accepts --json for machine-readable output

session-start writes a small local baseline to .git/project_check/session_start.json
(untracked, never pushed) so a later `precommit` run in the same working copy
can fail closed if the branch/worktree/HEAD lineage changed underneath it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_SUBDIR = "project_check"
STATE_FILENAME = "session_start.json"
GIT_TIMEOUT_SECONDS = 15
GH_TIMEOUT_SECONDS = 20


# --------------------------------------------------------------------------- git helpers


def _run(cmd: list[str], cwd: str | None = None, timeout: int = GIT_TIMEOUT_SECONDS) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        # rstrip only the trailing newline(s) here, never a full .strip() — git
        # status/branch porcelain output uses meaningful leading spaces on the
        # first line (e.g. " M file.py") that a blind .strip() would eat.
        return proc.returncode, proc.stdout.rstrip("\n"), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(cmd)}: timed out"


def _git(args: list[str], cwd: str | None = None) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=cwd)


def _git_lines(args: list[str], cwd: str | None = None) -> list[str]:
    rc, out, _ = _git(args, cwd=cwd)
    if rc != 0 or not out:
        return []
    return out.splitlines()


def is_git_repo(cwd: str | None = None) -> bool:
    rc, _, _ = _git(["rev-parse", "--is-inside-work-tree"], cwd=cwd)
    return rc == 0


def repo_root(cwd: str | None = None) -> str | None:
    rc, out, _ = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return out if rc == 0 else None


def current_branch(cwd: str | None = None) -> str | None:
    rc, out, _ = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if rc != 0:
        return None
    return out or None


def is_detached_head(cwd: str | None = None) -> bool:
    return current_branch(cwd) == "HEAD"


def head_sha(cwd: str | None = None) -> str | None:
    rc, out, _ = _git(["rev-parse", "HEAD"], cwd=cwd)
    return out if rc == 0 else None


def current_worktree_path(cwd: str | None = None) -> str | None:
    """The specific worktree the caller is standing in (may differ from the
    main repo root when running inside a linked worktree)."""
    rc, out, _ = _git(["rev-parse", "--show-toplevel"], cwd=cwd)
    return out if rc == 0 else None


def default_branch(cwd: str | None = None) -> tuple[str, str]:
    """Best-effort local-only default branch detection.

    Returns (short_name, remote_ref) e.g. ("main", "origin/main"). Never
    fetches. Falls back through origin/HEAD -> origin/main -> origin/master
    -> local main -> local master.
    """
    rc, out, _ = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=cwd)
    if rc == 0 and out:
        short = out.split("/", 1)[-1]
        return short, out
    for candidate in ("main", "master"):
        rc, _, _ = _git(["rev-parse", "--verify", "--quiet", f"origin/{candidate}"], cwd=cwd)
        if rc == 0:
            return candidate, f"origin/{candidate}"
    for candidate in ("main", "master"):
        rc, _, _ = _git(["rev-parse", "--verify", "--quiet", candidate], cwd=cwd)
        if rc == 0:
            return candidate, candidate
    return "main", "origin/main"


def rev_parse(ref: str, cwd: str | None = None) -> str | None:
    rc, out, _ = _git(["rev-parse", "--verify", "--quiet", ref], cwd=cwd)
    return out if rc == 0 else None


def ahead_behind(local_ref: str, remote_ref: str, cwd: str | None = None) -> dict[str, Any]:
    local_sha = rev_parse(local_ref, cwd=cwd)
    remote_sha = rev_parse(remote_ref, cwd=cwd)
    if not local_sha or not remote_sha:
        return {
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "status": "UNKNOWN — one or both refs not resolvable locally",
        }
    if local_sha == remote_sha:
        return {
            "local_ref": local_ref,
            "remote_ref": remote_ref,
            "local_sha": local_sha,
            "remote_sha": remote_sha,
            "ahead": 0,
            "behind": 0,
            "status": "UP TO DATE",
        }
    rc, out, _ = _git(
        ["rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}"], cwd=cwd
    )
    ahead, behind = (0, 0)
    if rc == 0 and out:
        parts = out.split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    if ahead and behind:
        status = "DIVERGED"
    elif ahead:
        status = "AHEAD"
    elif behind:
        status = "BEHIND"
    else:
        status = "UP TO DATE"
    return {
        "local_ref": local_ref,
        "remote_ref": remote_ref,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
        "ahead": ahead,
        "behind": behind,
        "status": status,
    }


def worktree_list(cwd: str | None = None) -> list[dict[str, Any]]:
    lines = _git_lines(["worktree", "list", "--porcelain"], cwd=cwd)
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in lines:
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line[len("worktree "):], "bare": False, "detached": False}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "locked":
            current["locked"] = True
    if current:
        entries.append(current)
    return entries


def status_porcelain(cwd: str | None = None) -> dict[str, list[str]]:
    """Parse `git status --porcelain=v1 -z`-free output into dirty/untracked buckets."""
    lines = _git_lines(["status", "--porcelain"], cwd=cwd)
    dirty_tracked: list[str] = []
    untracked: list[str] = []
    staged: list[str] = []
    for line in lines:
        if len(line) < 3:
            continue
        index_state, worktree_state, path = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty_tracked.append(path)
    return {"dirty_tracked": dirty_tracked, "untracked": untracked, "staged": staged}


def upstream_of(branch: str, cwd: str | None = None) -> str | None:
    rc, out, _ = _git(
        ["for-each-ref", f"--format=%(upstream:short)", f"refs/heads/{branch}"], cwd=cwd
    )
    return out or None


def local_branches_with_deleted_remote(cwd: str | None = None) -> list[str]:
    """Local branches whose configured upstream no longer exists on the remote."""
    lines = _git_lines(
        ["for-each-ref", "--format=%(refname:short) %(upstream:track)", "refs/heads"], cwd=cwd
    )
    gone = []
    for line in lines:
        if "[gone]" in line:
            gone.append(line.split(" ", 1)[0])
    return gone


def local_only_branches(cwd: str | None = None) -> list[str]:
    """Local branches with no upstream configured at all (never pushed, or
    upstream config was never set)."""
    lines = _git_lines(
        ["for-each-ref", "--format=%(refname:short) %(upstream)", "refs/heads"], cwd=cwd
    )
    result = []
    for line in lines:
        parts = line.split(" ", 1)
        name = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        if not upstream.strip():
            result.append(name)
    return result


def archive_tags(cwd: str | None = None) -> dict[str, str]:
    """Map archive tag name -> the commit SHA it points at (dereferenced)."""
    lines = _git_lines(["tag", "-l", "archive/*"], cwd=cwd)
    result: dict[str, str] = {}
    for tag in lines:
        rc, out, _ = _git(["rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"], cwd=cwd)
        if rc == 0 and out:
            result[tag] = out
    return result


def branches_checked_out_in_worktrees(cwd: str | None = None) -> dict[str, str]:
    """Map local branch name -> worktree path, for every worktree with a branch checked out."""
    result: dict[str, str] = {}
    for wt in worktree_list(cwd=cwd):
        branch = (wt.get("branch") or "").replace("refs/heads/", "")
        if branch:
            result[branch] = wt.get("path")
    return result


def evidence_preservation_report(default: str, remote_default: str, cwd: str | None = None) -> list[dict[str, Any]]:
    """Best-effort local proxy for BLOCKER-worthy unpreserved research branches.

    True "closed-unmerged" status lives on GitHub, not in git, so this can only
    flag local branches that look abandoned and check whether an archive/* tag
    already protects their tip. Always label this as a proxy — GitHub PR state
    must be cross-checked before treating anything here as a final BLOCKER/OK
    call.

    Deliberately does NOT treat `git branch --no-merged` / ancestry-based
    "unique commit count" as decisive proof of unmerged content: a
    squash-merged PR produces a branch whose individual commits are never
    ancestors of `default`, so ancestry alone falsely flags fully-preserved,
    fully-landed branches as unmerged forever. The decisive signal for "is
    there content here that doesn't already exist on default" is a direct,
    two-ref file-level diff (`git diff --name-only default branch` — NOT the
    three-dot `default...branch` form, which diffs against the merge-base
    and would silently ignore anything `default` did after the branch's fork
    point, defeating the whole point of this check) — if that's empty, the
    branch's tree state is already subsumed by default's current tip
    regardless of what its commit history looks like. Ancestry-only unique
    commits with zero file diff are reported as "LIKELY SQUASH-MERGED", not
    BLOCKER — real evidence preservation risk requires an actual content
    difference.

    A branch currently checked out in any worktree is always classified
    ACTIVE WIP regardless of the above — it's live local work, not an
    abandoned branch, no matter what its merge/content status looks like.
    """
    tags = archive_tags(cwd=cwd)
    tag_shas = set(tags.values())
    not_merged = set(_git_lines(["branch", "--no-merged", remote_default], cwd=cwd))
    # git prefixes the current branch with "* " and a branch checked out in
    # ANOTHER worktree with "+ " — strip both, not just "*", or a
    # worktree-checked-out branch's name comes through mangled (e.g.
    # "+feature/x") and silently fails every later lookup for it.
    not_merged = {b.strip().lstrip("*+ ").strip() for b in not_merged}
    gone_remote = set(local_branches_with_deleted_remote(cwd=cwd))
    checked_out = branches_checked_out_in_worktrees(cwd=cwd)
    findings = []
    for branch in sorted(not_merged):
        if branch == default or not branch:
            continue
        sha = rev_parse(branch, cwd=cwd)
        if not sha:
            continue
        unique_commit_count = len(_git_lines(["log", f"{remote_default}..{branch}", "--oneline"], cwd=cwd))
        # Direct tip-to-tip diff (two refs, not three-dot merge-base diff):
        # three-dot deliberately ignores anything default did after the fork
        # point, which is exactly wrong here — a squash-merge lands the
        # branch's content back onto default's current tip, and only a
        # direct comparison against that current tip can see it landed.
        unique_files = _git_lines(["diff", "--name-only", remote_default, branch], cwd=cwd)
        has_archive_tag = sha in tag_shas
        has_unique_content = len(unique_files) > 0
        remote_deleted = branch in gone_remote
        worktree_path = checked_out.get(branch)

        if worktree_path:
            classification = f"ACTIVE WIP — checked out in worktree {worktree_path}"
        elif has_unique_content and has_archive_tag:
            classification = "OK — unique content, preserved by archive tag"
        elif has_unique_content and remote_deleted:
            classification = "BLOCKER — unique content, no archive tag"
        elif has_unique_content:
            classification = "REVIEW — unmerged with unique content (remote still present, likely active WIP)"
        elif unique_commit_count > 0:
            classification = (
                "LIKELY SQUASH-MERGED — file content matches default despite "
                "unmerged ancestry; confirm via PR state (merged squash commit) "
                "before archiving or treating as unpreserved"
            )
        else:
            classification = "OK — no unique commits or content vs default branch"

        findings.append(
            {
                "branch": branch,
                "tip_sha": sha,
                "remote_upstream_deleted": remote_deleted,
                "checked_out_in_worktree": worktree_path,
                "unique_commit_count": unique_commit_count,
                "unique_commit_count_note": (
                    "ancestry-based only — unreliable under squash-merge, "
                    "see unique_file_count for the decisive signal"
                ),
                "unique_file_count": len(unique_files),
                "unique_files_sample": unique_files[:10],
                "archive_tag": next((t for t, s in tags.items() if s == sha), None),
                "classification": classification,
            }
        )
    return findings


def gh_pr_list(state: str = "open", cwd: str | None = None) -> dict[str, Any]:
    if shutil.which("gh") is None:
        return {
            "available": False,
            "note": "gh CLI not found locally — cross-check open/closed PRs via "
            "the GitHub MCP tools (list_pull_requests / search_pull_requests) "
            "or `gh pr list` on a machine with gh installed.",
            "prs": [],
        }
    rc, out, err = _run(
        [
            "gh", "pr", "list", "--state", state, "--limit", "50",
            "--json", "number,title,headRefName,url,isDraft,updatedAt",
        ],
        cwd=cwd,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if rc != 0:
        return {"available": False, "note": f"gh pr list failed: {err or out}", "prs": []}
    try:
        prs = json.loads(out) if out else []
    except json.JSONDecodeError:
        return {"available": False, "note": "gh pr list returned non-JSON output", "prs": []}
    return {"available": True, "note": None, "prs": prs}


# --------------------------------------------------------------------------- state file


def state_dir(root: str) -> Path:
    return Path(root) / ".git" / STATE_SUBDIR


def state_path(root: str) -> Path:
    return state_dir(root) / STATE_FILENAME


def write_session_start_state(root: str, snapshot: dict[str, Any]) -> Path:
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = state_path(root)
    p.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def read_session_start_state(root: str) -> dict[str, Any] | None:
    p = state_path(root)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- reports


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_session_start_report(cwd: str | None = None, include_gh: bool = True) -> dict[str, Any]:
    root = repo_root(cwd)
    if root is None:
        return {"ok": False, "error": "not a git repository (or git not found)"}

    branch_before = current_branch(root)
    default, remote_default = default_branch(root)
    local_default_sha = rev_parse(default, cwd=root)
    ab = ahead_behind(default, remote_default, cwd=root) if local_default_sha else {
        "local_ref": default, "remote_ref": remote_default, "status": "UNKNOWN — no local branch named " + default,
    }
    status = status_porcelain(root)
    worktrees = worktree_list(root)
    gone = local_branches_with_deleted_remote(root)
    evidence = evidence_preservation_report(default, remote_default, cwd=root)
    branch_after = current_branch(root)

    report: dict[str, Any] = {
        "ok": True,
        "mode": "session-start",
        "generated_at": _now(),
        "repo_root": root,
        "current_branch": branch_after,
        "detached_head": branch_after == "HEAD",
        "head_sha": head_sha(root),
        "default_branch": default,
        "default_branch_remote_ref": remote_default,
        f"{remote_default}_sha": rev_parse(remote_default, cwd=root),
        "local_default_branch_sha": local_default_sha,
        "local_main_vs_origin": ab,
        "current_worktree": current_worktree_path(root),
        "all_worktrees": worktrees,
        "dirty_tracked_files": status["dirty_tracked"],
        "untracked_files": status["untracked"],
        "staged_files": status["staged"],
        "branches_with_deleted_remote": gone,
        "closed_unmerged_candidates_without_archive_tag": [
            f for f in evidence if f["classification"].startswith("BLOCKER")
        ],
        "evidence_preservation_detail": evidence,
        "branch_changed_during_check": branch_before != branch_after,
        "note": (
            "origin/* SHAs reflect local remote-tracking refs only — this does "
            "not fetch. Run with --fetch to refresh origin/* refs first, or run "
            "`git fetch origin` yourself if this may be stale."
        ),
    }
    if include_gh:
        report["open_prs"] = gh_pr_list("open", cwd=root)
    return report


FAIL_CLOSED = "FAIL-CLOSED"
WARN = "WARN"
OK = "OK"


def build_precommit_report(cwd: str | None = None) -> dict[str, Any]:
    root = repo_root(cwd)
    if root is None:
        return {"ok": False, "verdict": FAIL_CLOSED, "error": "not a git repository (or git not found)"}

    baseline = read_session_start_state(root)
    branch = current_branch(root)
    worktree = current_worktree_path(root)
    sha = head_sha(root)
    status = status_porcelain(root)
    default, remote_default = default_branch(root)
    upstream = upstream_of(branch, cwd=root) if branch and branch != "HEAD" else None
    ab = ahead_behind(branch, upstream, cwd=root) if upstream else {
        "status": "NO UPSTREAM CONFIGURED — cannot compute ahead/behind",
    }

    reasons: list[str] = []
    verdict = OK

    rc_merge, out_merge, _ = _git(["rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=root)
    merge_in_progress = rc_merge == 0
    rebase_in_progress = (Path(root) / ".git" / "rebase-merge").exists() or (
        Path(root) / ".git" / "rebase-apply"
    ).exists()
    unmerged_paths = _git_lines(["diff", "--name-only", "--diff-filter=U"], cwd=root)

    if branch == "HEAD":
        reasons.append("repository is in a DETACHED HEAD state — ambiguous, refusing to proceed")
        verdict = FAIL_CLOSED
    if merge_in_progress:
        reasons.append("a merge is in progress (MERGE_HEAD present) — ambiguous repository state")
        verdict = FAIL_CLOSED
    if rebase_in_progress:
        reasons.append("a rebase is in progress — ambiguous repository state")
        verdict = FAIL_CLOSED
    if unmerged_paths:
        reasons.append(f"unresolved merge conflicts present: {', '.join(unmerged_paths[:10])}")
        verdict = FAIL_CLOSED

    for wt in worktree_list(root):
        wt_branch = (wt.get("branch") or "").replace("refs/heads/", "")
        if wt_branch and wt_branch == branch and wt.get("path") != worktree:
            reasons.append(
                f"branch '{branch}' is checked out in another worktree at {wt.get('path')} "
                "— ambiguous ownership of this branch"
            )
            verdict = FAIL_CLOSED

    if baseline is None:
        reasons.append(
            "no session-start baseline found for this repo — branch/worktree/HEAD "
            "continuity cannot be verified; run `session-start` at the beginning of "
            "the work session before trusting this precommit check fully"
        )
        if verdict == OK:
            verdict = WARN
    else:
        if baseline.get("current_branch") != branch:
            reasons.append(
                f"current branch '{branch}' differs from session-start branch "
                f"'{baseline.get('current_branch')}'"
            )
            verdict = FAIL_CLOSED
        if baseline.get("current_worktree") != worktree:
            reasons.append(
                f"current worktree '{worktree}' differs from session-start worktree "
                f"'{baseline.get('current_worktree')}'"
            )
            verdict = FAIL_CLOSED
        baseline_sha = baseline.get("head_sha")
        if baseline_sha and sha and baseline_sha != sha:
            rc, _, _ = _git(["merge-base", "--is-ancestor", baseline_sha, sha], cwd=root)
            if rc != 0:
                reasons.append(
                    f"HEAD ({sha[:12]}) is not a descendant of the session-start HEAD "
                    f"({baseline_sha[:12]}) — branch history was rewritten (reset/rebase/"
                    "force-push) since the session started"
                )
                verdict = FAIL_CLOSED

    if verdict == OK and not reasons:
        reasons.append("no anomalies detected against session-start baseline")

    report: dict[str, Any] = {
        "ok": verdict != FAIL_CLOSED,
        "mode": "precommit",
        "verdict": verdict,
        "generated_at": _now(),
        "repo_root": root,
        "current_branch": branch,
        "head_sha": sha,
        "current_worktree": worktree,
        "changed_files": sorted(set(status["dirty_tracked"] + status["staged"])),
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "upstream": upstream,
        "ahead_behind_upstream": ab,
        "default_branch": default,
        "session_start_baseline_present": baseline is not None,
        "session_start_baseline": baseline,
        "reasons": reasons,
    }
    return report


def build_daily_report(cwd: str | None = None, include_gh: bool = True) -> dict[str, Any]:
    root = repo_root(cwd)
    if root is None:
        return {"ok": False, "error": "not a git repository (or git not found)"}

    default, remote_default = default_branch(root)
    local_default_sha = rev_parse(default, cwd=root)
    ab = ahead_behind(default, remote_default, cwd=root) if local_default_sha else {
        "status": "UNKNOWN — no local branch named " + default,
    }
    status = status_porcelain(root)
    worktrees = worktree_list(root)
    gone = local_branches_with_deleted_remote(root)
    local_only = local_only_branches(root)
    evidence = evidence_preservation_report(default, remote_default, cwd=root)
    tags = archive_tags(root)

    report: dict[str, Any] = {
        "ok": True,
        "mode": "daily",
        "generated_at": _now(),
        "repo_root": root,
        "current_branch": current_branch(root),
        "working_tree_dirty": bool(status["dirty_tracked"] or status["staged"] or status["untracked"]),
        "dirty_tracked_files": status["dirty_tracked"],
        "untracked_files": status["untracked"],
        "default_branch": default,
        "default_branch_remote_ref": remote_default,
        "local_main_vs_origin": ab,
        "active_worktrees": worktrees,
        "branches_with_deleted_remote": gone,
        "local_only_branches": local_only,
        "archive_tags_present": tags,
        "evidence_preservation": evidence,
        "evidence_preservation_blockers": [
            f for f in evidence if f["classification"].startswith("BLOCKER")
        ],
        "sections_requiring_github_or_docs_review_not_covered_here": [
            "GITHUB: PRs opened/merged/closed today, current open PRs, stale PRs "
            "— use `gh pr list` / GitHub MCP tools (this script makes no network "
            "calls beyond an optional best-effort `gh pr list`)",
            "DEPLOYED STATE: current deployed SHA, evidence epoch(s), active "
            "paper-forward strategies — not verifiable from a repo checkout "
            "alone; use ops/live_box_guard.py / /futures-deployment-safety-audit "
            "against the actual box",
            "STRATEGY SOURCE OF TRUTH: compare docs/strategy-rules/Strategy_Inventory.md, "
            "strategy README, risk_rules.yaml comments, latest evidence PRs, and "
            "runtime enablement — this is a judgment-driven doc/code diff, not a "
            "mechanical check; see the daily-reconciliation checklist",
        ],
    }
    if include_gh:
        report["prs_open"] = gh_pr_list("open", cwd=root)
        report["prs_closed_recent"] = gh_pr_list("closed", cwd=root)
    return report


# --------------------------------------------------------------------------- text rendering


def _fmt_list(items: list[str], empty: str = "(none)") -> str:
    return ", ".join(items) if items else empty


def render_session_start_text(r: dict[str, Any]) -> str:
    if not r.get("ok", True):
        return f"SESSION-START: ERROR — {r.get('error')}"
    lines = []
    lines.append("=== SESSION SAFETY CHECK: START ===")
    lines.append(f"generated_at:        {r['generated_at']}")
    lines.append(f"repo root:           {r['repo_root']}")
    lines.append(f"current branch:      {r['current_branch']}{' (DETACHED HEAD)' if r['detached_head'] else ''}")
    lines.append(f"HEAD SHA:            {r['head_sha']}")
    lines.append(f"default branch:      {r['default_branch']} ({r['default_branch_remote_ref']})")
    lines.append(f"{r['default_branch_remote_ref']} SHA:      {r.get(r['default_branch_remote_ref'] + '_sha')}")
    lines.append(f"local {r['default_branch']} SHA:      {r['local_default_branch_sha']}")
    ab = r["local_main_vs_origin"]
    lines.append(f"local/{r['default_branch']} vs {r['default_branch_remote_ref']}: {ab.get('status')} "
                  f"(ahead={ab.get('ahead')}, behind={ab.get('behind')})")
    lines.append(f"current worktree:    {r['current_worktree']}")
    lines.append("all worktrees:")
    for wt in r["all_worktrees"]:
        lines.append(f"  - {wt.get('path')}  branch={wt.get('branch') or '(detached)'}")
    lines.append(f"dirty tracked files: {_fmt_list(r['dirty_tracked_files'])}")
    lines.append(f"untracked files:     {_fmt_list(r['untracked_files'])}")
    lines.append(f"branches w/ deleted remote: {_fmt_list(r['branches_with_deleted_remote'])}")
    blockers = r["closed_unmerged_candidates_without_archive_tag"]
    if blockers:
        lines.append("closed-unmerged branches WITHOUT archive tag protection (proxy — verify PR state):")
        for f in blockers:
            lines.append(f"  - {f['branch']} ({f['unique_commit_count']} unique commits, "
                          f"{f['unique_file_count']} unique files) — {f['classification']}")
    else:
        lines.append("closed-unmerged branches without archive tag protection: (none detected)")
    if "open_prs" in r:
        gh = r["open_prs"]
        if gh["available"]:
            lines.append(f"open PRs ({len(gh['prs'])}):")
            for pr in gh["prs"]:
                lines.append(f"  - #{pr['number']} {pr['title']} ({pr['headRefName']})")
        else:
            lines.append(f"open PRs: unavailable — {gh['note']}")
    lines.append(f"branch changed during check: {r['branch_changed_during_check']}")
    lines.append(f"note: {r['note']}")
    lines.append("")
    lines.append("Baseline written for later `precommit` use.")
    return "\n".join(lines)


def render_precommit_text(r: dict[str, Any]) -> str:
    if "error" in r and not r.get("verdict"):
        return f"PRECOMMIT: ERROR — {r.get('error')}"
    lines = []
    lines.append("=== SESSION SAFETY CHECK: PRECOMMIT ===")
    lines.append(f"VERDICT:             {r['verdict']}")
    lines.append(f"generated_at:        {r['generated_at']}")
    lines.append(f"repo root:           {r['repo_root']}")
    lines.append(f"current branch:      {r['current_branch']}")
    lines.append(f"HEAD SHA:            {r['head_sha']}")
    lines.append(f"current worktree:    {r['current_worktree']}")
    lines.append(f"changed files:       {_fmt_list(r['changed_files'])}")
    lines.append(f"staged files:        {_fmt_list(r['staged_files'])}")
    lines.append(f"untracked files:     {_fmt_list(r['untracked_files'])}")
    lines.append(f"upstream:            {r['upstream']}")
    lines.append(f"ahead/behind:        {r['ahead_behind_upstream']}")
    lines.append(f"session-start baseline found: {r['session_start_baseline_present']}")
    lines.append("reasons:")
    for reason in r["reasons"]:
        lines.append(f"  - {reason}")
    if r["verdict"] == FAIL_CLOSED:
        lines.append("")
        lines.append("REFUSING (fail-closed). This tool takes no action — resolve the")
        lines.append("condition above manually before committing/pushing.")
    return "\n".join(lines)


def render_daily_text(r: dict[str, Any]) -> str:
    if not r.get("ok", True):
        return f"DAILY: ERROR — {r.get('error')}"
    lines = []
    lines.append("=== DAILY RECONCILIATION / CLEANUP CHECK (mechanical, git-only section) ===")
    lines.append(f"generated_at:        {r['generated_at']}")
    lines.append(f"repo root:           {r['repo_root']}")
    lines.append(f"current branch:      {r['current_branch']}")
    lines.append(f"working tree dirty:  {r['working_tree_dirty']}")
    ab = r["local_main_vs_origin"]
    lines.append(f"local {r['default_branch']} vs {r['default_branch_remote_ref']}: {ab.get('status')} "
                  f"(ahead={ab.get('ahead')}, behind={ab.get('behind')})")
    lines.append("active worktrees:")
    for wt in r["active_worktrees"]:
        lines.append(f"  - {wt.get('path')}  branch={wt.get('branch') or '(detached)'}")
    lines.append(f"branches w/ deleted remote: {_fmt_list(r['branches_with_deleted_remote'])}")
    lines.append(f"local-only branches (never pushed / no upstream): {_fmt_list(r['local_only_branches'])}")
    lines.append(f"archive/* tags present: {_fmt_list(list(r['archive_tags_present'].keys()))}")
    lines.append("")
    lines.append("EVIDENCE PRESERVATION (unmerged branches vs default, proxy classification):")
    if r["evidence_preservation"]:
        for f in r["evidence_preservation"]:
            lines.append(
                f"  - {f['branch']}: {f['classification']} "
                f"(unique_commits={f['unique_commit_count']}, unique_files={f['unique_file_count']}, "
                f"remote_deleted={f['remote_upstream_deleted']}, archive_tag={f['archive_tag']})"
            )
    else:
        lines.append("  (no unmerged local branches found)")
    if r["evidence_preservation_blockers"]:
        lines.append("")
        lines.append("*** BLOCKERS — unique evidence with no archive tag: ***")
        for f in r["evidence_preservation_blockers"]:
            lines.append(f"  - {f['branch']} ({f['tip_sha'][:12]})")
    if "prs_open" in r:
        gh = r["prs_open"]
        if gh["available"]:
            lines.append(f"\nopen PRs ({len(gh['prs'])}) via gh:")
            for pr in gh["prs"]:
                lines.append(f"  - #{pr['number']} {pr['title']} ({pr['headRefName']})")
        else:
            lines.append(f"\nopen PRs: unavailable — {gh['note']}")
    lines.append("")
    lines.append("NOT covered by this script (do these separately, see daily-reconciliation checklist):")
    for item in r["sections_requiring_github_or_docs_review_not_covered_here"]:
        lines.append(f"  - {item}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def cmd_session_start(args: argparse.Namespace) -> int:
    root = repo_root()
    if args.fetch and root:
        default, _ = default_branch(root)
        _git(["fetch", "origin", default, "--quiet"], cwd=root)
    report = build_session_start_report(include_gh=not args.no_gh)
    if not report.get("ok", True):
        print(json.dumps(report) if args.json else f"ERROR: {report.get('error')}", file=sys.stderr)
        return 2
    if root:
        write_session_start_state(root, report)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_session_start_text(report))
    return 0


def cmd_precommit(args: argparse.Namespace) -> int:
    report = build_precommit_report()
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_precommit_text(report))
    if report.get("verdict") == FAIL_CLOSED:
        return 2
    if report.get("verdict") == WARN:
        return 1
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    root = repo_root()
    if args.fetch and root:
        default, _ = default_branch(root)
        _git(["fetch", "origin", default, "--quiet"], cwd=root)
    report = build_daily_report(include_gh=not args.no_gh)
    if not report.get("ok", True):
        print(json.dumps(report) if args.json else f"ERROR: {report.get('error')}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_daily_text(report))
    if report["evidence_preservation_blockers"]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("session-start", help="Run at the start of a work session.")
    p_start.add_argument("--json", action="store_true")
    p_start.add_argument("--no-gh", action="store_true", help="skip best-effort `gh pr list`")
    p_start.add_argument("--fetch", action="store_true", help="git fetch origin <default> first (only network call in this tool)")
    p_start.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="Run immediately before commit/push.")
    p_pre.add_argument("--json", action="store_true")
    p_pre.set_defaults(func=cmd_precommit)

    p_daily = sub.add_parser("daily", help="Daily read-only repo/process reconciliation pass.")
    p_daily.add_argument("--json", action="store_true")
    p_daily.add_argument("--no-gh", action="store_true", help="skip best-effort `gh pr list`")
    p_daily.add_argument("--fetch", action="store_true", help="git fetch origin <default> first (only network call in this tool)")
    p_daily.set_defaults(func=cmd_daily)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
