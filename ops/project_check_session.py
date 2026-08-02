"""Session Safety + Runtime Snapshot — routine #1 of ops.project_check.

Two modes:

``session-start``
    Full repo/worktree/branch/stash/PR snapshot, plus a best-effort runtime
    posture snapshot (deployed release SHA, active paper-forward strategy
    lanes, execution mode, entry fill model, effective entry tolerance,
    contract cap). Writes a small local state file so ``precommit`` can later
    detect drift. Read-only against git; the only write is that local state
    file under ``logs/`` (already .gitignored, same convention as
    ``logs/live_preflight_state.json``).

``precommit``
    Fast, read-only comparison of current repo state against the
    ``session-start`` snapshot. FAILS CLOSED (non-zero exit, explicit reasons)
    on branch/worktree/HEAD drift or unverifiable state. Never commits,
    pushes, pulls, resets, rebases, checks out, deletes branches/worktrees,
    drops stashes, creates/deletes tags, or modifies any tracked file.

Every field that cannot be safely determined is reported as the literal
string ``UNKNOWN`` rather than guessed.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ops import project_check_git as pcg

STATE_FILENAME = "project_check_session_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_path(log_dir: str | Path = "logs") -> Path:
    return Path(log_dir) / STATE_FILENAME


def _runtime_posture() -> dict[str, Any]:
    """Best-effort snapshot of deployed/runtime posture. Never raises — every
    field defaults to UNKNOWN with a reason when unavailable. Reuses the
    system's own config loader and release-integrity checker instead of
    re-deriving fill-model/exit-mode/tolerance logic here."""
    posture: dict[str, Any] = {
        "deployed_release_sha": pcg.UNKNOWN,
        "deployed_release_branch": pcg.UNKNOWN,
        "release_manifest_present": False,
        "evidence_epoch": pcg.UNKNOWN,
        "evidence_epoch_note": (
            "No 'evidence epoch' concept is defined anywhere in this repo today. "
            "Not inventing one — reporting UNKNOWN per the routine's own rule."
        ),
        "active_paper_forward_lanes": pcg.UNKNOWN,
        "execution_mode": pcg.UNKNOWN,
        "entry_fill_model": pcg.UNKNOWN,
        "entry_tolerance_ticks_by_root": pcg.UNKNOWN,
        "max_contracts_per_instrument": pcg.UNKNOWN,
        "max_contracts_hard_cap": pcg.UNKNOWN,
    }

    try:
        from ops.release_integrity import verify_release

        report = verify_release()
        posture["release_manifest_present"] = bool(report.get("manifest_present"))
        if report.get("manifest_present"):
            posture["deployed_release_sha"] = report.get("release_commit") or pcg.UNKNOWN
            posture["deployed_release_branch"] = report.get("release_branch") or pcg.UNKNOWN
            posture["release_integrity_ok"] = report.get("ok")
            posture["release_integrity_problems"] = report.get("problems") or []
        else:
            posture["deployed_release_note"] = (
                "No release_manifest.json in this checkout — this is a dev "
                "checkout, not the deploy box. Run on the deploy box (or point "
                "RELEASE_MANIFEST_PATH at its manifest) for deployed-state."
            )
    except Exception as exc:  # noqa: BLE001 — snapshot must never crash on this
        posture["release_integrity_error"] = str(exc)

    try:
        from config.settings import load_config

        config = load_config()
        default_status = config.strategy_permission_default_status
        active_lanes = sorted(
            concept
            for concept in (config.enabled_concepts or [])
            if config.strategy_status.get(concept, default_status) == "PAPER_ELIGIBLE"
        )
        posture["active_paper_forward_lanes"] = active_lanes
        posture["strategy_permission_gate_enabled"] = config.strategy_permission_gate_enabled
        posture["execution_mode"] = config.exit_mode
        posture["entry_fill_model"] = config.entry_fill_model
        posture["entry_tolerance_ticks_by_root"] = config.entry_tolerance_ticks_by_root
        posture["max_contracts_per_instrument"] = config.max_contracts_per_instrument
        posture["max_contracts_hard_cap"] = config.max_contracts_hard_cap
    except Exception as exc:  # noqa: BLE001
        posture["config_load_error"] = str(exc)

    return posture


def build_session_start_report(
    cwd: Optional[str] = None, *, log_dir: str | Path = "logs"
) -> dict[str, Any]:
    root = pcg.repo_root(cwd) or cwd or "."
    branch_before = pcg.current_branch(root)

    head = pcg.head_sha(root)
    upstream = pcg.upstream_ref(root)
    origin_main = pcg.ref_sha("origin/main", root)
    sync = pcg.sync_status(head, origin_main, root)
    ab = pcg.ahead_behind(head, origin_main, root)

    dirty = pcg.dirty_files(root)
    wts = pcg.worktrees(root)
    branches = pcg.local_branches(root)
    gone = [b["name"] for b in branches if b["gone"]]
    local_only = [b["name"] for b in branches if b["local_only"]]
    merged = set(pcg.merged_into("main", root))
    not_merged = set(pcg.not_merged_into("main", root))
    tags = pcg.archive_tags(root)
    stashes = pcg.stash_list(root)
    prs = pcg.gh_pr_list(root)

    # Closed/unmerged-with-no-archive-tag: the first CODE check for this (the
    # existing docs/BRANCH_ARCHIVE_INDEX.md convention is hand-maintained).
    # Awareness only — never auto-tags or deletes anything.
    unarchived_unmerged = []
    for name in sorted(not_merged):
        sha = pcg.ref_sha(name, root)
        if not sha:
            continue
        pointing = pcg.tags_pointing_at(sha, root)
        if not any(t.startswith("archive/") for t in pointing):
            unarchived_unmerged.append({"branch": name, "sha": sha})

    branch_after = pcg.current_branch(root)

    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "repo": {
            "root": root,
            "current_branch": branch_after,
            "detached_head": pcg.is_detached(root),
            "head_sha": head,
            "origin_main_sha": origin_main or pcg.UNKNOWN,
            "main_sync_status": sync,
            "ahead_behind_origin_main": ab,
            "upstream": upstream or pcg.UNKNOWN,
        },
        "worktree": {
            "current": root,
            "all": wts,
        },
        "dirty": dirty,
        "branches": {
            "local": [b["name"] for b in branches],
            "tracking_deleted_remote": gone,
            "local_only": local_only,
            "merged_into_main": sorted(merged),
            "not_merged_into_main": sorted(not_merged),
        },
        "closed_unmerged_no_archive_tag": unarchived_unmerged,
        "archive_tags": tags,
        "stashes": {"count": len(stashes), "items": stashes},
        "open_prs": prs if prs is not None else pcg.UNKNOWN,
        "open_prs_note": (
            None
            if prs is not None
            else "gh CLI unavailable or the lookup failed — cannot verify PR state, do not assume none exist."
        ),
        "branch_changed_during_check": branch_before != branch_after,
        "runtime_snapshot": _runtime_posture(),
    }
    return report


def write_session_state(report: dict[str, Any], *, log_dir: str | Path = "logs") -> Path:
    path = state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "captured_at": report["generated_at"],
        "repo_root": report["repo"]["root"],
        "branch": report["repo"]["current_branch"],
        "head_sha": report["repo"]["head_sha"],
        "worktree": report["worktree"]["current"],
    }
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_precommit_report(
    cwd: Optional[str] = None, *, log_dir: str | Path = "logs"
) -> dict[str, Any]:
    root = pcg.repo_root(cwd) or cwd or "."
    branch = pcg.current_branch(root)
    head = pcg.head_sha(root)
    upstream = pcg.upstream_ref(root)
    dirty = pcg.dirty_files(root)
    wts = pcg.worktrees(root)

    failures: list[str] = []
    path = state_path(log_dir)
    session_state: Optional[dict] = None
    if not path.exists():
        failures.append(
            "session-start state cannot be verified — no session state file at "
            f"{path}. Run `python -m ops.project_check session-start` first."
        )
    else:
        try:
            session_state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            failures.append(
                f"session-start state cannot be verified — unreadable state file: {exc}"
            )

    if branch is None:
        failures.append("repository state is ambiguous — detached HEAD or unreadable branch")

    if session_state is not None:
        if session_state.get("repo_root") != root:
            failures.append(
                "worktree differs unexpectedly — "
                f"session-start={session_state.get('repo_root')} current={root}"
            )
        if session_state.get("branch") != branch:
            failures.append(
                "branch differs from session-start branch unexpectedly — "
                f"session-start={session_state.get('branch')} current={branch}"
            )
        if session_state.get("head_sha") != head:
            failures.append(
                "branch moved unexpectedly — "
                f"session-start HEAD={session_state.get('head_sha')} current HEAD={head} "
                "(a commit, checkout, reset, or rebase happened since session-start)"
            )

    # Intended branch owned by another worktree: some OTHER worktree entry
    # already checked out on this same branch.
    for wt in wts:
        if branch and wt.get("branch") == branch and wt.get("path") != root:
            failures.append(
                f"intended branch '{branch}' is also checked out in another worktree: {wt.get('path')}"
            )

    ab = None
    if upstream:
        ab = pcg.ahead_behind(head, pcg.ref_sha(upstream, root), root)

    ok = not failures
    return {
        "generated_at": _now_iso(),
        "ok": ok,
        "verdict": "PASS" if ok else "FAIL_CLOSED",
        "repo": {
            "root": root,
            "current_branch": branch,
            "head_sha": head,
            "upstream": upstream or pcg.UNKNOWN,
            "ahead_behind_upstream": ab,
        },
        "session_start": (
            {
                "branch": session_state.get("branch"),
                "worktree": session_state.get("repo_root"),
                "head_sha": session_state.get("head_sha"),
                "captured_at": session_state.get("captured_at"),
            }
            if session_state
            else pcg.UNKNOWN
        ),
        "changed_files": dirty.get("modified", []),
        "staged_files": dirty.get("staged", []),
        "untracked_files": dirty.get("untracked", []),
        "failures": failures,
    }


def format_session_start(report: dict[str, Any]) -> str:
    repo = report["repo"]
    lines = [
        f"SESSION START — {report['generated_at']}",
        f"repo: {repo['root']}",
        f"branch: {repo['current_branch']} (detached={repo['detached_head']})",
        f"HEAD: {repo['head_sha']}",
        f"origin/main: {repo['origin_main_sha']} — {repo['main_sync_status']}"
        + (f" (ahead {report['repo']['ahead_behind_origin_main']['ahead']}, "
           f"behind {report['repo']['ahead_behind_origin_main']['behind']})"
           if report['repo']['ahead_behind_origin_main'] else ""),
        f"upstream: {repo['upstream']}",
        f"dirty: {len(report['dirty']['modified'])} modified, "
        f"{len(report['dirty']['staged'])} staged, "
        f"{len(report['dirty']['untracked'])} untracked",
        f"worktrees: {len(report['worktree']['all'])}",
    ]
    for wt in report["worktree"]["all"]:
        lines.append(f"  - {wt.get('path')} [{wt.get('branch') or ('detached' if wt.get('detached') else '?')}]")
    branches = report["branches"]
    lines.append(
        f"branches: {len(branches['local'])} local, {len(branches['local_only'])} local-only, "
        f"{len(branches['tracking_deleted_remote'])} tracking deleted remotes"
    )
    if branches["tracking_deleted_remote"]:
        lines.append(f"  gone: {', '.join(branches['tracking_deleted_remote'])}")
    lines.append(f"stashes: {report['stashes']['count']}")
    if report["closed_unmerged_no_archive_tag"]:
        lines.append(
            f"BLOCKER — {len(report['closed_unmerged_no_archive_tag'])} unmerged branch(es) with no archive/* tag:"
        )
        for item in report["closed_unmerged_no_archive_tag"]:
            lines.append(f"  - {item['branch']} @ {item['sha'][:12]}")
    if report["open_prs"] == pcg.UNKNOWN:
        lines.append(f"open PRs: UNKNOWN — {report['open_prs_note']}")
    else:
        open_prs = [pr for pr in report["open_prs"] if pr.get("state") == "OPEN"]
        lines.append(f"open PRs: {len(open_prs)}")
        for pr in open_prs:
            lines.append(f"  - #{pr['number']} {pr['title']} ({pr['headRefName']})")
    if report["branch_changed_during_check"]:
        lines.append("WARNING — checked-out branch changed DURING this check")
    rt = report["runtime_snapshot"]
    lines.append("--- runtime snapshot ---")
    lines.append(f"deployed release SHA: {rt['deployed_release_sha']} ({rt['deployed_release_branch']})")
    lines.append(f"evidence epoch: {rt['evidence_epoch']} ({rt['evidence_epoch_note']})")
    lines.append(f"active paper-forward lanes: {rt['active_paper_forward_lanes']}")
    lines.append(f"execution mode: {rt['execution_mode']} | entry fill model: {rt['entry_fill_model']}")
    lines.append(f"entry tolerance (ticks/root): {rt['entry_tolerance_ticks_by_root']}")
    lines.append(f"contract cap: per-instrument={rt['max_contracts_per_instrument']} hard_cap={rt['max_contracts_hard_cap']}")
    return "\n".join(lines)


def format_precommit(report: dict[str, Any]) -> str:
    lines = [
        f"PRECOMMIT — {report['generated_at']} — {report['verdict']}",
        f"repo: {report['repo']['root']}",
        f"branch: {report['repo']['current_branch']}  HEAD: {report['repo']['head_sha']}",
    ]
    if report["session_start"] != pcg.UNKNOWN:
        ss = report["session_start"]
        lines.append(
            f"session-start: branch={ss['branch']} worktree={ss['worktree']} head={ss['head_sha']} "
            f"(captured {ss['captured_at']})"
        )
    lines.append(
        f"changed: {len(report['changed_files'])}  staged: {len(report['staged_files'])}  "
        f"untracked: {len(report['untracked_files'])}"
    )
    if report["failures"]:
        lines.append("FAIL CLOSED:")
        for f in report["failures"]:
            lines.append(f"  ✗ {f}")
    else:
        lines.append("no drift detected — safe to proceed")
    return "\n".join(lines)
