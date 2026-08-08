"""Read-only git/worktree/runtime snapshot builders shared by session-start and precommit.

Every function here only ever calls read-only git subcommands (status, branch,
worktree list, tag -l, diff --name-only, rev-parse, for-each-ref, stash list)
via subprocess argument lists — never `shell=True`, never string-interpolated
shell commands, so there is no word-splitting hazard under zsh or bash.

No network access. `origin/main` is read from whatever the local ref cache
already has — this module never fetches, so the value can be stale; callers
should treat it as "as of the last fetch", not "as of right now".
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHECKPOINT_FILENAME = ".project_check_session_state.json"

# Read-only git subcommands only. Never extend this module with anything that
# mutates repo state (commit/push/pull/reset/rebase/checkout/branch -D/stash drop/tag -d/...).
_READ_ONLY_TIMEOUT = 15


def _git(repo_root: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=_READ_ONLY_TIMEOUT,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _git_ok(repo_root: Path, *args: str) -> str | None:
    code, out, _err = _git(repo_root, *args)
    return out if code == 0 else None


def _git_lines(repo_root: Path, *args: str) -> list[str]:
    out = _git_ok(repo_root, *args)
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def repo_root_of(start: str | Path = ".") -> Path | None:
    code, out, _err = _git(Path(start), "rev-parse", "--show-toplevel")
    if code != 0 or not out:
        return None
    return Path(out)


def current_branch(repo_root: Path) -> str | None:
    branch = _git_ok(repo_root, "branch", "--show-current")
    return branch or None


def head_sha(repo_root: Path) -> str | None:
    return _git_ok(repo_root, "rev-parse", "HEAD")


def _sync_relationship(repo_root: Path, local_ref: str, remote_ref: str) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED / UNKNOWN for local_ref vs remote_ref."""
    counts = _git_ok(repo_root, "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}")
    if counts is None:
        return "UNKNOWN"
    parts = counts.split()
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


def worktrees(repo_root: Path) -> list[dict[str, Any]]:
    """Parse `git worktree list --porcelain` into structured records."""
    out = _git_ok(repo_root, "worktree", "list", "--porcelain")
    if not out:
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
            if current:
                entries.append(current)
            current = {"path": line[len("worktree "):].strip(), "bare": False, "detached": False}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].strip().removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line == "locked":
            current["locked"] = True
    if current:
        entries.append(current)
    return entries


def stash_list(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "stash", "list")


def dirty_tracked_files(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "diff", "--name-only")


def staged_files(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "diff", "--cached", "--name-only")


def untracked_files(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "ls-files", "--others", "--exclude-standard")


def upstream_of(repo_root: Path, branch: str | None = None) -> str | None:
    ref = f"{branch}@{{u}}" if branch else "@{u}"
    return _git_ok(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", ref)


def local_branches_report(repo_root: Path) -> dict[str, Any]:
    """Local branches, split into: has-upstream, local-only, tracking-a-gone-remote."""
    out = _git_ok(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)\t%(upstream:short)\t%(upstream:track)",
        "refs/heads",
    )
    local_only: list[str] = []
    tracking_gone: list[str] = []
    tracked: list[str] = []
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        track = parts[2] if len(parts) > 2 else ""
        if not upstream:
            local_only.append(name)
        elif "[gone]" in track:
            tracking_gone.append(name)
        else:
            tracked.append(name)
    return {"tracked": tracked, "local_only": local_only, "tracking_deleted_remote": tracking_gone}


def archive_tags(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "tag", "-l", "archive/*")


def branches_not_merged_into(repo_root: Path, base_ref: str, *, remote: bool = False) -> list[str]:
    ref_pattern = "refs/remotes/origin" if remote else "refs/heads"
    out = _git_ok(repo_root, "for-each-ref", "--format=%(refname:short)", ref_pattern, f"--no-merged={base_ref}")
    names = [n for n in (out or "").splitlines() if n.strip()]
    if remote:
        names = [n for n in names if n not in ("origin/HEAD",) and not n.endswith("/HEAD")]
    return names


def unique_commit_count_vs(repo_root: Path, ref: str, base_ref: str) -> int | None:
    out = _git_ok(repo_root, "rev-list", f"{base_ref}..{ref}", "--count")
    if out is None:
        return None
    try:
        return int(out)
    except ValueError:
        return None


def evidence_preservation_report(repo_root: Path, *, base_ref: str = "main") -> dict[str, Any]:
    """Branches (local + remote-tracking) not merged into base_ref, with unique
    commits, and whether an `archive/*` tag exists.

    This is a proxy for "closed-unmerged branch with unique evidence" — true
    GitHub PR-closed status requires GitHub API access this script does not
    call (see `pr_status` in the repo report: UNKNOWN). A branch that simply
    hasn't been opened as a PR yet will also show up here; that's expected —
    this reports "unmerged + unique + unpreserved", not "PR-closed".
    """
    tags = set(archive_tags(repo_root))
    tag_shas = {}
    for tag in tags:
        sha = _git_ok(repo_root, "rev-list", "-n", "1", tag)
        if sha:
            tag_shas[tag] = sha

    candidates: list[dict[str, Any]] = []
    seen_shas: set[str] = set()
    local_unmerged = branches_not_merged_into(repo_root, base_ref, remote=False)
    remote_unmerged = branches_not_merged_into(repo_root, base_ref, remote=True)
    for kind, names in (("local", local_unmerged), ("remote", remote_unmerged)):
        for name in names:
            if name == base_ref or name == f"origin/{base_ref}":
                continue
            sha = _git_ok(repo_root, "rev-parse", name)
            unique_commits = unique_commit_count_vs(repo_root, name, base_ref)
            has_archive_tag = sha in tag_shas.values() if sha else False
            matching_tags = [t for t, s in tag_shas.items() if s == sha] if sha else []
            record = {
                "branch": name,
                "kind": kind,
                "sha": sha,
                "unique_commits_vs_base": unique_commits,
                "archive_tag_present": has_archive_tag,
                "matching_archive_tags": matching_tags,
            }
            key = sha or name
            if key in seen_shas:
                continue
            seen_shas.add(key)
            candidates.append(record)

    blockers = [
        c for c in candidates
        if c["unique_commits_vs_base"] not in (None, 0) and not c["archive_tag_present"]
    ]
    return {
        "base_ref": base_ref,
        "archive_tags": sorted(tags),
        "unmerged_branches": candidates,
        "blockers_unique_evidence_no_archive_tag": blockers,
    }


def git_repo_report(repo_root: Path) -> dict[str, Any]:
    branch = current_branch(repo_root)
    head = head_sha(repo_root)
    origin_main = _git_ok(repo_root, "rev-parse", "origin/main")
    local_main_relationship = "UNKNOWN"
    if origin_main and _git_ok(repo_root, "rev-parse", "--verify", "main"):
        local_main_relationship = _sync_relationship(repo_root, "main", "origin/main")
    upstream = upstream_of(repo_root, branch) if branch else None
    wts = worktrees(repo_root)
    this_wt = next((w for w in wts if Path(w.get("path", "")) == repo_root), None)

    return {
        "repo_root": str(repo_root),
        "current_branch": branch,
        "head_sha": head,
        "origin_main_sha": origin_main,
        "origin_main_sha_note": "from local ref cache; this script never fetches, so it may be stale",
        "local_main_vs_origin_main": local_main_relationship,
        "upstream": upstream,
        "current_worktree": str(repo_root),
        "current_worktree_detached": bool(this_wt.get("detached")) if this_wt else None,
        "worktrees": wts,
        "dirty_tracked_files": dirty_tracked_files(repo_root),
        "staged_files": staged_files(repo_root),
        "untracked_files": untracked_files(repo_root),
        "branches": local_branches_report(repo_root),
        "archive_tags": archive_tags(repo_root),
        "stash_count": len(stash_list(repo_root)),
        "stash_entries": stash_list(repo_root),
        "open_prs": "UNKNOWN — this read-only script does not call the GitHub API; "
                    "check separately (e.g. GitHub MCP tools or `gh pr list`)",
        "closed_unmerged_with_unique_evidence_no_archive_tag":
            evidence_preservation_report(repo_root)["blockers_unique_evidence_no_archive_tag"],
    }


def _entry_tolerance_and_fill_model() -> dict[str, Any]:
    """Actual effective execution config, from the same loader the runtime uses."""
    try:
        from config.settings import load_config
        cfg = load_config()
        return {
            "source": "config.settings.load_config()",
            "entry_fill_model": cfg.entry_fill_model,
            "entry_tolerance_ticks_by_root": dict(cfg.entry_tolerance_ticks_by_root),
        }
    except Exception as exc:  # noqa: BLE001 — config load must never crash the snapshot
        return {"source": "config.settings.load_config()", "error": f"UNKNOWN ({exc})"}


def _contract_caps() -> dict[str, Any]:
    try:
        import yaml
        rules = yaml.safe_load(Path("risk_rules.yaml").read_text(encoding="utf-8")) or {}
        pos = rules.get("position_sizing", {}) or {}
        return {
            "max_contracts_top_level": rules.get("max_contracts"),
            "max_contracts_per_instrument": pos.get("max_contracts_per_instrument"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"UNKNOWN ({exc})"}


def runtime_snapshot(repo_root: Path, log_dir: str = "logs") -> dict[str, Any]:
    """Best-effort, local-only, read-only snapshot of the intended runtime posture.

    Never SSHes or hits an external service. Everything here comes from local
    files (risk_rules.yaml, journal logs) and env vars already visible to this
    process — the same sources `ops/live_box_guard.py` and
    `ops/evidence_lane_health.py` already use safely and read-only.
    """
    snapshot: dict[str, Any] = {}

    try:
        from ops.live_box_guard import live_box_drift_report
        drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
        snapshot["deployed_release_sha"] = drift.get("commit") or "UNKNOWN"
        snapshot["evidence_epoch"] = (
            next((c["observed"] for c in drift.get("comparisons", []) if c.get("name") == "runtime_evidence_source"), None)
            or "UNKNOWN"
        )
        snapshot["runtime_override_comparisons"] = drift.get("comparisons", [])
        snapshot["config_vs_evidence_assumption_mismatches"] = [
            c for c in drift.get("comparisons", []) if c.get("status") not in ("match", "unpinned")
        ]
    except Exception as exc:  # noqa: BLE001 — never let a runtime-snapshot probe crash session-start
        snapshot["live_box_guard_error"] = f"UNKNOWN ({exc})"

    try:
        from ops.evidence_lane_health import build_snapshot as lane_snapshot
        lanes = lane_snapshot(log_dir=log_dir)
        snapshot["active_paper_forward_lanes"] = [
            {
                "instrument": lane.get("instrument"),
                "lane": lane.get("lane"),
                "mode": lane.get("mode"),
                "status": lane.get("status"),
                "counts": lane.get("counts"),
            }
            for lane in lanes.get("lanes", [])
        ]
    except Exception as exc:  # noqa: BLE001
        snapshot["evidence_lane_health_error"] = f"UNKNOWN ({exc})"

    snapshot["effective_execution_config"] = _entry_tolerance_and_fill_model()
    snapshot["contract_quantity_caps"] = _contract_caps()
    return snapshot


def _checkpoint_path(log_dir: str) -> Path:
    return Path(log_dir) / CHECKPOINT_FILENAME


def load_session_checkpoint(log_dir: str = "logs") -> dict[str, Any] | None:
    path = _checkpoint_path(log_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_session_checkpoint(log_dir: str, checkpoint: dict[str, Any]) -> None:
    path = _checkpoint_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")


def build_session_start_report(repo_root: Path, log_dir: str = "logs") -> dict[str, Any]:
    """SESSION START report. Persists a small checkpoint (branch/worktree/HEAD)
    to `<log_dir>/.project_check_session_state.json` so `precommit` can later
    detect drift. This is the one write this module performs; it is a local,
    gitignored status marker (same pattern as `logs/health_digest_latest.json`),
    never a repo file, never a git ref.
    """
    branch_before = current_branch(repo_root)
    report = git_repo_report(repo_root)
    report["runtime_snapshot"] = runtime_snapshot(repo_root, log_dir=log_dir)
    branch_after = current_branch(repo_root)
    report["branch_changed_during_check"] = branch_before != branch_after
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["read_only"] = True

    checkpoint = {
        "generated_at": report["generated_at"],
        "repo_root": str(repo_root),
        "branch": branch_after,
        "worktree": str(repo_root),
        "head_sha": report["head_sha"],
    }
    _write_session_checkpoint(log_dir, checkpoint)
    report["checkpoint_written"] = str(_checkpoint_path(log_dir))
    return report


def build_precommit_report(repo_root: Path, log_dir: str = "logs") -> dict[str, Any]:
    """PRECOMMIT / PREPUSH report. Strictly read-only: never commits, pushes,
    pulls, resets, rebases, checks out, deletes branches/worktrees, drops
    stashes, creates/deletes tags, or modifies files. Fails closed on any
    ambiguity.
    """
    violations: list[str] = []
    checkpoint = load_session_checkpoint(log_dir)
    if checkpoint is None:
        violations.append("no session-start checkpoint found; session-start state cannot be verified")

    branch = current_branch(repo_root)
    head = head_sha(repo_root)
    if branch is None or head is None:
        violations.append("repository state is ambiguous (could not read branch/HEAD)")

    wts = worktrees(repo_root)
    branch_owner_paths = [w["path"] for w in wts if w.get("branch") == branch and Path(w["path"]) != repo_root]
    if branch_owner_paths:
        violations.append(
            f"intended branch {branch!r} is checked out in another worktree: {branch_owner_paths}"
        )

    if checkpoint is not None:
        if checkpoint.get("worktree") != str(repo_root):
            violations.append(
                f"worktree differs from session-start worktree "
                f"(session-start={checkpoint.get('worktree')!r}, now={str(repo_root)!r})"
            )
        if branch is not None and checkpoint.get("branch") is not None and branch != checkpoint.get("branch"):
            violations.append(
                f"branch differs from session-start branch "
                f"(session-start={checkpoint.get('branch')!r}, now={branch!r})"
            )

    report = {
        "read_only": True,
        "repo_root": str(repo_root),
        "current_branch": branch,
        "current_head": head,
        "session_start_branch": (checkpoint or {}).get("branch", "UNKNOWN"),
        "session_start_worktree": (checkpoint or {}).get("worktree", "UNKNOWN"),
        "current_worktree": str(repo_root),
        "upstream": upstream_of(repo_root, branch) if branch else None,
        "changed_files": dirty_tracked_files(repo_root),
        "staged_files": staged_files(repo_root),
        "untracked_files": untracked_files(repo_root),
        "violations": violations,
        "verdict": "FAIL_CLOSED" if violations else "PASS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return report
