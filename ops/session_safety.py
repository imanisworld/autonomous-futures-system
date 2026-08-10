"""Session Safety + Runtime Snapshot.

Two read-only modes, reusing existing machinery rather than re-deriving it:

- `session_start_report()` — repo/worktree/branch hygiene (ops.repo_state) plus
  a runtime-drift snapshot (ops.live_box_guard.live_box_drift_report) and
  automation-freshness check (ops.automation_evidence), and records a small
  local checkpoint (git-ignored, under `logs/`) that `precommit_report()` reads
  back to detect branch/worktree drift mid-session.
- `precommit_report()` — compares current repo state against the session-start
  checkpoint and FAILS CLOSED on any unexplained difference. Never mutates
  anything: no commit/push/pull/reset/rebase/checkout/branch-delete/worktree-
  remove/stash-drop/tag write.

Both modes only run read-only git subcommands (see ops/repo_state.py) plus
local filesystem/env reads. Neither ever touches the network beyond an
optional best-effort `gh pr list` (see repo_state.gh_pr_list), which fails
soft to None/UNKNOWN.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ops import repo_state
from ops.automation_evidence import automation_evidence_status
from ops.live_box_guard import live_box_drift_report

STATE_FILENAME = ".session_safety_state.json"


def _repo_root(repo_root: str | Path | None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    found = repo_state.repo_root_of(Path.cwd())
    return found or Path(__file__).resolve().parents[1]


def _state_path(repo_root: Path, log_dir: str | Path) -> Path:
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = repo_root / log_path
    return log_path / STATE_FILENAME


def _load_risk_rules(repo_root: Path, risk_rules_path: str | Path) -> dict[str, Any]:
    path = Path(risk_rules_path)
    if not path.is_absolute():
        path = repo_root / path
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _runtime_lanes(rules: dict[str, Any]) -> dict[str, Any]:
    """Best-effort summary of which strategy 'lanes' are paper-eligible right now,
    and the (system-wide, not currently per-lane in config) execution knobs that
    apply to whatever fills. Unknown/absent config reports UNKNOWN, never a guess.
    """
    gate = rules.get("strategy_permission_gate") or {}
    default_status = gate.get("default_status", "UNKNOWN")
    strategy_status = gate.get("strategy_status") or {}
    strategy_cfg = rules.get("strategy") or {}
    enabled_concepts = strategy_cfg.get("enabled_concepts") or []
    disabled_per_instrument = strategy_cfg.get("disabled_concepts_per_instrument") or {}

    lanes = []
    for concept in enabled_concepts:
        status = strategy_status.get(concept, default_status)
        lanes.append({"strategy": concept, "status": status})
    active_paper_lanes = [lane["strategy"] for lane in lanes if lane["status"] == "PAPER_ELIGIBLE"]

    instruments = (rules.get("instruments") or {}).get("allowed") or []
    max_contracts = (rules.get("position_rules") or {}).get("max_contracts_per_instrument") or {}
    quantity_caps = {inst: max_contracts.get(inst, "UNKNOWN") for inst in instruments}

    return {
        "gate_enabled": gate.get("enabled", "UNKNOWN"),
        "default_status": default_status,
        "lanes": lanes,
        "active_paper_eligible_lanes": active_paper_lanes,
        "disabled_concepts_per_instrument": disabled_per_instrument,
        "allowed_instruments": instruments,
        "quantity_contract_caps": quantity_caps,
        "fill_model_config": rules.get("fill_model") or {},
        "note": (
            "Execution mode / entry fill model / effective tolerance are configured "
            "system-wide (env overrides), not per strategy lane, in this repo's current "
            "risk_rules.yaml — see runtime_snapshot.proof_critical_runtime_overrides for "
            "the actual observed values."
        ),
    }


def session_start_report(
    *,
    repo_root: str | Path | None = None,
    log_dir: str | Path = "logs",
    risk_rules_path: str | Path = "risk_rules.yaml",
    base_branch: str = "main",
    remote: str = "origin",
    check_prs: bool = True,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    branch_before = repo_state.current_branch(root)

    snapshot = repo_state.repo_snapshot(root, base=base_branch, remote=remote)

    open_prs = None
    if check_prs:
        open_prs = repo_state.gh_pr_list(root, state="open")

    drift = live_box_drift_report(repo_root=root, risk_rules_path=risk_rules_path, log_dir=log_dir)
    rules = _load_risk_rules(root, risk_rules_path)
    lanes = _runtime_lanes(rules)
    automation = automation_evidence_status(log_dir=str(Path(log_dir) if Path(log_dir).is_absolute() else root / log_dir))

    branch_after = repo_state.current_branch(root)
    branch_changed_during_check = branch_before != branch_after

    now = datetime.now(timezone.utc)
    problems: list[str] = []
    if branch_changed_during_check:
        problems.append(
            f"checked-out branch changed during the check ({branch_before!r} -> {branch_after!r})"
        )
    if snapshot["local_main_relationship"] == "DIVERGED":
        problems.append(f"local {base_branch} has DIVERGED from {remote}/{base_branch}")
    if snapshot["local_main_relationship"] == "UNKNOWN":
        problems.append(f"could not determine local {base_branch} vs {remote}/{base_branch} relationship")
    for finding in snapshot["branches_missing_archive_tag"]:
        if not finding["has_archive_tag"]:
            problems.append(
                f"closed/unmerged-looking branch {finding['branch']!r} has no matching archive/* tag"
            )
    if drift["status"] == "error":
        problems.append("runtime drift guard: " + drift["summary"])
    elif drift["status"] == "warn":
        problems.append("runtime drift guard (warn): " + drift["summary"])
    this_worktree = snapshot["current_worktree"]
    for wt in snapshot["worktrees"]:
        if wt is this_worktree:
            continue
        if wt.get("branch") and snapshot["branch"] and wt.get("branch") == snapshot["branch"]:
            problems.append(
                f"branch {snapshot['branch']!r} appears checked out in another worktree: {wt.get('path')}"
            )

    verdict = "SAFE TO WORK" if not problems else "REVIEW BEFORE WORKING"

    report = {
        "mode": "session-start",
        "generated_at": now.isoformat(),
        "verdict": verdict,
        "problems": problems,
        "repo": snapshot,
        "open_prs": open_prs if open_prs is not None else "UNKNOWN (gh unavailable or call failed)",
        "runtime_snapshot": drift,
        "strategy_lanes": lanes,
        "automation_evidence": automation,
        "branch_changed_during_check": branch_changed_during_check,
    }

    state_path = _state_path(root, log_dir)
    checkpoint = {
        "recorded_at": now.isoformat(),
        "repo_root": str(root),
        "branch": snapshot["branch"],
        "head_sha": snapshot["head_sha"],
        "worktree_path": str(root.resolve()),
        "detached_head": snapshot["detached_head"],
    }
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(checkpoint, indent=2, sort_keys=True), encoding="utf-8")
        report["checkpoint_written"] = str(state_path)
    except OSError as exc:
        report["checkpoint_written"] = None
        report["checkpoint_write_error"] = str(exc)

    return report


def precommit_report(
    *,
    repo_root: str | Path | None = None,
    log_dir: str | Path = "logs",
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    state_path = _state_path(root, log_dir)

    now = datetime.now(timezone.utc)
    if not state_path.exists():
        return {
            "mode": "precommit",
            "generated_at": now.isoformat(),
            "verdict": "FAIL CLOSED",
            "reasons": [
                f"no session-start checkpoint found at {state_path}; "
                "run session-start before trusting this repo state"
            ],
            "checkpoint": None,
        }

    try:
        checkpoint = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "mode": "precommit",
            "generated_at": now.isoformat(),
            "verdict": "FAIL CLOSED",
            "reasons": [f"session-start checkpoint at {state_path} is unreadable: {exc}"],
            "checkpoint": None,
        }

    branch = repo_state.current_branch(root)
    head = repo_state.head_sha(root)
    upstream = repo_state.upstream_branch(root)
    files = repo_state.dirty_files(root)
    ahead_behind = None
    if upstream:
        ahead_behind = repo_state.ahead_behind(root, "HEAD", upstream)

    reasons: list[str] = []
    if checkpoint.get("worktree_path") and checkpoint["worktree_path"] != str(root.resolve()):
        reasons.append(
            f"worktree differs from session-start ({checkpoint['worktree_path']!r} -> {str(root.resolve())!r})"
        )
    if checkpoint.get("branch") != branch:
        reasons.append(
            f"branch differs from session-start ({checkpoint.get('branch')!r} -> {branch!r})"
        )
    if branch is None:
        reasons.append("HEAD is currently detached; ambiguous for a precommit check")

    worktrees = repo_state.list_worktrees(root)
    for wt in worktrees:
        wt_path = Path(wt.get("path", "")).resolve()
        if wt_path == root.resolve():
            continue
        if branch and wt.get("branch") == branch:
            reasons.append(
                f"intended branch {branch!r} is also checked out in another worktree: {wt.get('path')}"
            )

    verdict = "PASS (read-only)" if not reasons else "FAIL CLOSED"

    return {
        "mode": "precommit",
        "generated_at": now.isoformat(),
        "verdict": verdict,
        "reasons": reasons,
        "checkpoint": checkpoint,
        "current": {
            "repo_root": str(root),
            "branch": branch,
            "head_sha": head,
            "session_start_head_sha": checkpoint.get("head_sha"),
            "head_moved_since_session_start": head != checkpoint.get("head_sha"),
            "upstream": upstream,
            "ahead_behind_upstream": (
                {"ahead": ahead_behind[0], "behind": ahead_behind[1]} if ahead_behind else "UNKNOWN"
            ),
            "changed_files_tracked": files["unstaged_tracked"],
            "staged_files": files["staged"],
            "untracked_files": files["untracked"],
        },
    }
