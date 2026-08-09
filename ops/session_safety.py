"""Read-only session-safety + runtime-snapshot checks.

Two entry points, both read-only and fail-closed:

  build_session_start_report(...)  -- broad repo/worktree/runtime snapshot,
      meant to be run once at the start of a working session.
  build_precommit_report(..., baseline=...) -- narrow drift check comparing
      the current repo state against a baseline captured earlier in the same
      session (normally the session-start report). Never mutates git state.

Nothing here ever commits, pushes, pulls, fetches, resets, rebases, checks
out, deletes a branch, removes a worktree, drops a stash, or creates/deletes
a tag. Every git invocation is a read-only subcommand (status/rev-parse/
for-each-ref/worktree list/stash list/tag -l/diff --name-only). `gh` calls
(best-effort, degrade to "unavailable") are similarly read-only.

Local knowledge of `origin/main` reflects the last time this repo fetched --
this module never fetches, so treat it as "as of last fetch", not live.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ops.live_box_guard import live_box_drift_report

UNKNOWN = "UNKNOWN"


def _repo_root(cwd: Path | None = None) -> Path:
    out = _git(cwd or Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(out) if out else (cwd or Path.cwd()).resolve()


def _git(cwd: Path, *args: str, timeout: float = 15.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_lines(cwd: Path, *args: str, timeout: float = 15.0) -> list[str]:
    raw = _git(cwd, *args, timeout=timeout)
    if raw is None:
        return []
    return [line for line in raw.splitlines() if line.strip()]


def _git_raw_lines(cwd: Path, *args: str, timeout: float = 15.0) -> list[str]:
    """Like _git_lines but does NOT strip the overall output first -- some
    porcelain formats (git status --porcelain) carry meaningful leading
    whitespace on the first line that a whole-output .strip() would eat."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.split("\n") if line]


def _gh_json(cwd: Path, *args: str, timeout: float = 20.0) -> tuple[Any | None, str | None]:
    """Best-effort read-only `gh` call. Never raises; degrades to (None, reason)."""
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return None, "unavailable (gh CLI not found)"
    except subprocess.TimeoutExpired:
        return None, "unavailable (gh CLI timed out)"
    except OSError as exc:
        return None, f"unavailable (gh CLI error: {exc})"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[:1]
        return None, f"unavailable (gh exited {result.returncode}: {detail[0] if detail else 'no detail'})"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError:
        return None, "unavailable (gh returned non-JSON output)"


# --------------------------------------------------------------------- repo


def _worktrees(root: Path) -> list[dict[str, Any]]:
    raw = _git(root, "worktree", "list", "--porcelain")
    if raw is None:
        return []
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
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
            current["branch"] = line[len("branch "):].replace("refs/heads/", "")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    if current:
        worktrees.append(current)
    return worktrees


def _branch_sync_status(root: Path, local_ref: str, remote_ref: str) -> tuple[str, dict[str, Any]]:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local_ref vs remote_ref."""
    local_sha = _git(root, "rev-parse", "--verify", "--quiet", local_ref)
    remote_sha = _git(root, "rev-parse", "--verify", "--quiet", remote_ref)
    detail: dict[str, Any] = {
        "local_ref": local_ref,
        "remote_ref": remote_ref,
        "local_sha": local_sha,
        "remote_sha": remote_sha,
    }
    if not local_sha or not remote_sha:
        detail["reason"] = "one or both refs do not resolve locally"
        return UNKNOWN, detail
    counts = _git(root, "rev-list", "--left-right", "--count", f"{remote_ref}...{local_ref}")
    if not counts:
        detail["reason"] = "rev-list failed"
        return UNKNOWN, detail
    try:
        behind_str, ahead_str = counts.split()
        behind, ahead = int(behind_str), int(ahead_str)
    except ValueError:
        detail["reason"] = "unparseable rev-list output"
        return UNKNOWN, detail
    detail["ahead"] = ahead
    detail["behind"] = behind
    if ahead == 0 and behind == 0:
        return "IN_SYNC", detail
    if ahead > 0 and behind == 0:
        return "AHEAD", detail
    if ahead == 0 and behind > 0:
        return "BEHIND", detail
    return "DIVERGED", detail


def _dirty_staged_untracked(root: Path) -> dict[str, list[str]]:
    dirty: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    for line in _git_raw_lines(root, "status", "--porcelain=v1"):
        if len(line) < 3:
            continue
        index_state, worktree_state, path = line[0], line[1], line[3:]
        if index_state == "?" and worktree_state == "?":
            untracked.append(path)
            continue
        if index_state not in (" ", "?"):
            staged.append(path)
        if worktree_state not in (" ", "?"):
            dirty.append(path)
    return {"dirty_tracked": dirty, "staged": staged, "untracked": untracked}


def _branches_tracking_deleted_remotes(root: Path) -> list[str]:
    raw = _git(root, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads")
    if raw is None:
        return []
    gone: list[str] = []
    for line in raw.splitlines():
        name, _, track = line.partition("|")
        if "[gone]" in track:
            gone.append(name)
    return gone


def _local_only_branches(root: Path) -> list[str]:
    raw = _git(root, "for-each-ref", "--format=%(refname:short)|%(upstream)", "refs/heads")
    if raw is None:
        return []
    local_only: list[str] = []
    for line in raw.splitlines():
        name, _, upstream = line.partition("|")
        if not upstream.strip():
            local_only.append(name)
    return local_only


def _stash_list(root: Path) -> list[str]:
    return _git_lines(root, "stash", "list")


def _archive_tags(root: Path) -> list[str]:
    return _git_lines(root, "tag", "-l", "archive/*")


def _unmerged_remote_branches_without_archive(
    root: Path, archive_index_path: Path
) -> list[dict[str, Any]]:
    """Heuristic, read-only: remote branches not reachable from origin/main
    (i.e. not merged) that do not already appear as an 'Original branch' row
    in docs/BRANCH_ARCHIVE_INDEX.md and have no matching archive/* tag by
    name-slug convention. This never deletes or tags anything -- it only
    flags candidates for a human to triage."""
    remote_branches = [
        line.replace("origin/", "", 1)
        for line in _git_lines(root, "branch", "-r", "--format=%(refname:short)")
        if line.startswith("origin/") and not line.endswith("/HEAD")
    ]
    if not remote_branches:
        return []
    indexed_names: set[str] = set()
    if archive_index_path.exists():
        try:
            text = archive_index_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            if line.startswith("| `"):
                cell = line.split("|")[1].strip()
                indexed_names.add(cell.strip("`"))
    archive_tags = _archive_tags(root)
    flagged: list[dict[str, Any]] = []
    for branch in remote_branches:
        if branch == "main":
            continue
        is_ancestor = _git(
            root, "merge-base", "--is-ancestor", f"origin/{branch}", "origin/main"
        )
        # git merge-base --is-ancestor exits 0 (empty stdout, no error) when true;
        # _git returns None on nonzero exit, so re-run with explicit returncode check.
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", f"origin/{branch}", "origin/main"],
            cwd=str(root),
            capture_output=True,
            timeout=15,
        )
        merged = result.returncode == 0
        if merged:
            continue
        if branch in indexed_names:
            continue
        has_matching_tag = any(branch.replace("/", "-") in tag for tag in archive_tags)
        if has_matching_tag:
            continue
        flagged.append({
            "branch": branch,
            "merged_into_origin_main": merged,
            "in_archive_index": False,
            "matching_archive_tag": False,
        })
    return flagged


def _open_prs(root: Path) -> tuple[list[dict[str, Any]] | None, str | None]:
    data, err = _gh_json(
        root, "pr", "list", "--state", "open", "--json", "number,title,headRefName,isDraft,url"
    )
    return data, err


# ------------------------------------------------------------------ runtime


def _active_strategy_lanes(root: Path) -> dict[str, Any]:
    """Compute active (enabled_concepts minus disabled_concepts_per_instrument)
    strategy concepts per allowed instrument, straight from risk_rules.yaml --
    the same source ops/release_manifest.py's config summary reads."""
    try:
        import yaml
    except ImportError:
        return {"available": False, "reason": "pyyaml not importable"}
    risk_path = root / "risk_rules.yaml"
    if not risk_path.exists():
        return {"available": False, "reason": "risk_rules.yaml not found"}
    try:
        rules = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"failed to parse risk_rules.yaml: {exc}"}
    instruments = (rules.get("instruments") or {}).get("allowed") or []
    strategy = rules.get("strategy") or {}
    enabled = list(strategy.get("enabled_concepts") or [])
    disabled_per_instrument = strategy.get("disabled_concepts_per_instrument") or {}
    lanes = {}
    for instrument in instruments:
        disabled = set(disabled_per_instrument.get(instrument) or [])
        lanes[instrument] = [concept for concept in enabled if concept not in disabled]
    return {
        "available": True,
        "allowed_instruments": instruments,
        "enabled_concepts": enabled,
        "active_lanes_by_instrument": lanes,
    }


def _runtime_snapshot(root: Path) -> dict[str, Any]:
    guard = live_box_drift_report(repo_root=root)
    lanes = _active_strategy_lanes(root)
    expected_commit = os.getenv("EXPECTED_LIVE_COMMIT") or None
    pinned_overrides = {
        item["name"]: item["expected"]
        for item in guard["proof_critical_runtime_overrides"]
        if item["pinned"]
    }
    return {
        "intended_deployed_release_sha": expected_commit or UNKNOWN,
        "evidence_epoch": {
            "note": (
                "This repo has no first-class 'evidence epoch' object. The closest "
                "proxy is the live-box guard's pinned identity: EXPECTED_LIVE_COMMIT, "
                "EXPECTED_RISK_RULES_SHA256, and any pinned EXPECTED_PROOF_* runtime "
                "overrides. UNKNOWN fields below mean no pin is set locally."
            ),
            "expected_live_commit": expected_commit or UNKNOWN,
            "expected_risk_rules_sha256": os.getenv("EXPECTED_RISK_RULES_SHA256") or UNKNOWN,
            "pinned_proof_critical_overrides": pinned_overrides or UNKNOWN,
        },
        "active_paper_forward_strategy_lanes": lanes,
        "execution_mode_per_lane": {
            "TRADOVATE_ENTRY_EXECUTION_MODE": os.getenv("TRADOVATE_ENTRY_EXECUTION_MODE") or UNKNOWN,
            "ENTRY_FILL_MODEL": os.getenv("ENTRY_FILL_MODEL") or UNKNOWN,
        },
        "entry_fill_model": os.getenv("ENTRY_FILL_MODEL") or UNKNOWN,
        "effective_entry_tolerance": {
            "ENTRY_SLIPPAGE_TOLERANCE_TICKS": os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS") or UNKNOWN,
            "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES": os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES") or UNKNOWN,
            "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ": os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ") or UNKNOWN,
        },
        "quantity_contract_cap": os.getenv("MAX_CONTRACTS_HARD_CAP") or UNKNOWN,
        "runtime_config_vs_evidence_assumptions": {
            "unpinned_active_overrides": guard["unpinned_runtime_overrides"],
            "mismatched_pins": guard["mismatches"],
            "note": (
                "Non-empty unpinned_active_overrides means an active runtime knob has "
                "no EXPECTED_PROOF_<NAME> pin -- config may differ from whatever "
                "evidence run is being compared against, and this guard cannot say "
                "either way."
            ),
        },
        "live_box_guard": guard,
    }


# ------------------------------------------------------------------- public


@dataclass
class SessionStartReport:
    repo_root: str
    branch: str | None
    branch_changed_during_check: bool
    head_sha: str | None
    origin_main_sha: str | None
    local_main_status: str
    local_main_detail: dict[str, Any]
    upstream: str | None
    current_worktree: str | None
    worktrees: list[dict[str, Any]]
    dirty_tracked: list[str]
    staged: list[str]
    untracked: list[str]
    branches_tracking_deleted_remotes: list[str]
    local_only_branches: list[str]
    open_prs: list[dict[str, Any]] | None
    open_prs_error: str | None
    unmerged_branches_missing_archive: list[dict[str, Any]]
    archive_tags: list[str]
    stash_list: list[str]
    runtime_snapshot: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "session-start",
            "read_only": True,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "branch_changed_during_check": self.branch_changed_during_check,
            "head_sha": self.head_sha,
            "origin_main_sha": self.origin_main_sha,
            "origin_main_sha_note": "local knowledge as of last fetch, not live",
            "local_main_status": self.local_main_status,
            "local_main_detail": self.local_main_detail,
            "upstream": self.upstream,
            "current_worktree": self.current_worktree,
            "worktrees": self.worktrees,
            "dirty_tracked_files": self.dirty_tracked,
            "staged_files": self.staged,
            "untracked_files": self.untracked,
            "branches_tracking_deleted_remotes": self.branches_tracking_deleted_remotes,
            "local_only_branches": self.local_only_branches,
            "open_prs": self.open_prs,
            "open_prs_error": self.open_prs_error,
            "closed_unmerged_branches_missing_archive_tag": self.unmerged_branches_missing_archive,
            "archive_tags": self.archive_tags,
            "stash_list": self.stash_list,
            "stash_count": len(self.stash_list),
            "runtime_snapshot": self.runtime_snapshot,
        }


def build_session_start_report(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root else _repo_root()
    branch_before = _git(root, "rev-parse", "--abbrev-ref", "HEAD")

    head_sha = _git(root, "rev-parse", "HEAD")
    origin_main_sha = _git(root, "rev-parse", "--verify", "--quiet", "origin/main")
    local_main_status, local_main_detail = _branch_sync_status(root, "main", "origin/main")
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    current_worktree = _git(root, "rev-parse", "--show-toplevel")
    worktrees = _worktrees(root)
    dsu = _dirty_staged_untracked(root)
    gone = _branches_tracking_deleted_remotes(root)
    local_only = _local_only_branches(root)
    open_prs, open_prs_err = _open_prs(root)
    unmerged_missing = _unmerged_remote_branches_without_archive(
        root, root / "docs" / "BRANCH_ARCHIVE_INDEX.md"
    )
    archive_tags = _archive_tags(root)
    stash_list = _stash_list(root)
    runtime_snapshot = _runtime_snapshot(root)

    branch_after = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch_changed = bool(branch_before) and bool(branch_after) and branch_before != branch_after

    report = SessionStartReport(
        repo_root=str(root),
        branch=branch_after or branch_before or UNKNOWN,
        branch_changed_during_check=branch_changed,
        head_sha=head_sha,
        origin_main_sha=origin_main_sha,
        local_main_status=local_main_status,
        local_main_detail=local_main_detail,
        upstream=upstream or UNKNOWN,
        current_worktree=current_worktree,
        worktrees=worktrees,
        dirty_tracked=dsu["dirty_tracked"],
        staged=dsu["staged"],
        untracked=dsu["untracked"],
        branches_tracking_deleted_remotes=gone,
        local_only_branches=local_only,
        open_prs=open_prs,
        open_prs_error=open_prs_err,
        unmerged_branches_missing_archive=unmerged_missing,
        archive_tags=archive_tags,
        stash_list=stash_list,
        runtime_snapshot=runtime_snapshot,
    )
    return report.as_dict()


@dataclass
class PrecommitFailure:
    code: str
    detail: str


@dataclass
class PrecommitReport:
    repo_root: str
    branch: str | None
    head_sha: str | None
    session_start_branch: str | None
    current_worktree: str | None
    session_start_worktree: str | None
    upstream: str | None
    local_main_status: str
    changed_files: list[str]
    staged: list[str]
    untracked: list[str]
    failures: list[PrecommitFailure] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": "precommit",
            "read_only": True,
            "ok": self.ok,
            "fail_closed": not self.ok,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "session_start_branch": self.session_start_branch,
            "current_worktree": self.current_worktree,
            "session_start_worktree": self.session_start_worktree,
            "upstream": self.upstream,
            "local_main_status": self.local_main_status,
            "changed_files": self.changed_files,
            "staged_files": self.staged,
            "untracked_files": self.untracked,
            "failures": [f.__dict__ for f in self.failures],
        }


def build_precommit_report(
    *,
    baseline: dict[str, Any],
    repo_root: str | Path | None = None,
    expected_files: list[str] | None = None,
) -> dict[str, Any]:
    """Compare current repo state against a session-start baseline dict
    (the output of build_session_start_report). Read-only; never mutates
    anything. Fails closed (reports failures, does not raise) whenever the
    state is ambiguous or has moved in an unexpected way."""
    root = Path(repo_root).resolve() if repo_root else _repo_root()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    head_sha = _git(root, "rev-parse", "HEAD")
    current_worktree = _git(root, "rev-parse", "--show-toplevel")
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    local_main_status, _ = _branch_sync_status(root, "main", "origin/main")
    dsu = _dirty_staged_untracked(root)
    changed_files = sorted(set(dsu["dirty_tracked"]) | set(dsu["staged"]) | set(dsu["untracked"]))

    session_start_branch = baseline.get("branch")
    session_start_worktree = baseline.get("current_worktree")

    failures: list[PrecommitFailure] = []
    if branch is None or head_sha is None or current_worktree is None:
        failures.append(PrecommitFailure(
            "ambiguous_state", "one or more required git reads failed; cannot verify safely"
        ))
    if session_start_branch in (None, UNKNOWN):
        failures.append(PrecommitFailure(
            "session_start_unverifiable", "baseline has no recorded session-start branch"
        ))
    elif branch is not None and branch != session_start_branch:
        failures.append(PrecommitFailure(
            "branch_changed",
            f"current branch {branch!r} differs from session-start branch {session_start_branch!r}",
        ))
    if session_start_worktree in (None, UNKNOWN):
        failures.append(PrecommitFailure(
            "session_start_worktree_unverifiable",
            "baseline has no recorded session-start worktree",
        ))
    elif current_worktree is not None and current_worktree != session_start_worktree:
        failures.append(PrecommitFailure(
            "worktree_changed",
            f"current worktree {current_worktree!r} differs from session-start worktree "
            f"{session_start_worktree!r}",
        ))
    if expected_files is not None:
        unexpected = sorted(set(changed_files) - set(expected_files))
        if unexpected:
            failures.append(PrecommitFailure(
                "unexpected_files_changed", f"files not in the expected set: {unexpected}"
            ))
    # Cross-worktree branch ownership: is the intended branch checked out in
    # a *different* worktree than this one right now?
    for wt in _worktrees(root):
        wt_branch = wt.get("branch")
        wt_path = wt.get("path")
        if wt_branch and wt_branch == branch and wt_path and current_worktree and wt_path != current_worktree:
            failures.append(PrecommitFailure(
                "branch_owned_by_other_worktree",
                f"branch {branch!r} is also checked out at {wt_path!r}",
            ))

    report = PrecommitReport(
        repo_root=str(root),
        branch=branch,
        head_sha=head_sha,
        session_start_branch=session_start_branch,
        current_worktree=current_worktree,
        session_start_worktree=session_start_worktree,
        upstream=upstream or UNKNOWN,
        local_main_status=local_main_status,
        changed_files=changed_files,
        staged=dsu["staged"],
        untracked=dsu["untracked"],
        failures=failures,
    )
    return report.as_dict()


def format_session_start(report: dict[str, Any]) -> str:
    lines = [
        "SESSION SAFETY + RUNTIME SNAPSHOT",
        f"repo_root: {report['repo_root']}",
        f"branch: {report['branch']} (changed during check: {report['branch_changed_during_check']})",
        f"head_sha: {report['head_sha']}",
        f"origin/main sha: {report['origin_main_sha']} ({report['origin_main_sha_note']})",
        f"local main vs origin/main: {report['local_main_status']} {report['local_main_detail']}",
        f"upstream: {report['upstream']}",
        f"current worktree: {report['current_worktree']}",
        f"active worktrees: {len(report['worktrees'])}",
    ]
    for wt in report["worktrees"]:
        lines.append(f"  - {wt.get('path')} @ {wt.get('branch') or '(detached)'}")
    lines += [
        f"dirty tracked files: {len(report['dirty_tracked_files'])}",
        f"staged files: {len(report['staged_files'])}",
        f"untracked files: {len(report['untracked_files'])}",
        f"branches tracking deleted remotes: {report['branches_tracking_deleted_remotes']}",
        f"local-only branches: {report['local_only_branches']}",
        f"open PRs: {report['open_prs'] if report['open_prs'] is not None else report['open_prs_error']}",
        f"closed-unmerged branches missing archive tag: {report['closed_unmerged_branches_missing_archive_tag']}",
        f"archive/* tags: {len(report['archive_tags'])}",
        f"stash count: {report['stash_count']} {report['stash_list']}",
        "",
        "RUNTIME SNAPSHOT",
        f"intended deployed release sha: {report['runtime_snapshot']['intended_deployed_release_sha']}",
        f"active paper-forward lanes: {report['runtime_snapshot']['active_paper_forward_strategy_lanes']}",
        f"entry fill model: {report['runtime_snapshot']['entry_fill_model']}",
        f"effective entry tolerance: {report['runtime_snapshot']['effective_entry_tolerance']}",
        f"quantity/contract cap: {report['runtime_snapshot']['quantity_contract_cap']}",
        f"live box guard status: {report['runtime_snapshot']['live_box_guard']['status']}"
        f" -- {report['runtime_snapshot']['live_box_guard']['summary']}",
    ]
    return "\n".join(lines)


def format_precommit(report: dict[str, Any]) -> str:
    lines = [
        "PRECOMMIT / PREPUSH (read-only)",
        f"ok: {report['ok']}",
        f"repo_root: {report['repo_root']}",
        f"branch: {report['branch']} (session-start: {report['session_start_branch']})",
        f"worktree: {report['current_worktree']} (session-start: {report['session_start_worktree']})",
        f"upstream: {report['upstream']}",
        f"local main vs origin/main: {report['local_main_status']}",
        f"changed files: {report['changed_files']}",
        f"staged files: {report['staged_files']}",
        f"untracked files: {report['untracked_files']}",
    ]
    if report["failures"]:
        lines.append("FAIL CLOSED:")
        for f in report["failures"]:
            lines.append(f"  - {f['code']}: {f['detail']}")
    else:
        lines.append("No fail-closed conditions detected.")
    return "\n".join(lines)
