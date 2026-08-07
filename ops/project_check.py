"""ops.project_check — the three repo/process safety routines, one CLI.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name> [--instrument MNQ]
    python -m ops.project_check daily

Built to close three repeat-failure classes without adding broad automation:
branch/worktree confusion, strategies promoted on standalone research instead
of the real executable path, and drift between what the repo/journal say is
true and what's actually running. Every routine is READ ONLY: none of them
commit, push, pull, reset, rebase, checkout, delete a branch/worktree, drop a
stash, create/delete a tag, cancel an order, flatten a position, or touch
risk/strategy/broker code. `session-start` and `daily` write exactly one
local bookkeeping file each, under logs/.project_check/ (already gitignored
via logs/) — nothing else.

Reuses rather than re-implements: ops.live_box_guard's git subprocess
plumbing, ops.proof_30_mnq's trade pairing/outcome classification,
ops.strategy_intent_audit's candidate-gate parsing, and
config.settings.load_config() for runtime/lane state. See
ops/project_check_git.py, ops/project_check_runtime.py,
ops/project_check_promotion.py, and ops/project_check_trade_chain.py for the
routines' actual logic — this file is CLI wiring only.

Unknown data is reported as the literal string "UNKNOWN", never guessed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops import project_check_git as pcg
from ops import project_check_runtime as pcr

UNKNOWN = "UNKNOWN"
STATE_DIR_NAME = "logs/.project_check"
SESSION_STATE_FILENAME = "session_state.json"
DAILY_CHECKPOINT_FILENAME = "daily_checkpoint.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_dir(root: Path) -> Path:
    return root / STATE_DIR_NAME


def _load_config_safely():
    """Best-effort config.settings.load_config() — never raises out of a report."""
    try:
        from config.settings import load_config

        return load_config(), None
    except Exception as exc:  # noqa: BLE001 - reporting tool, must not crash on config drift
        return None, str(exc)


# ── session-start ────────────────────────────────────────────────────────

def build_session_start_report(root: Path) -> dict[str, Any]:
    branch_t0 = pcg.current_branch(root)
    head_t0 = pcg.head_sha(root)

    status = pcg.porcelain_status(root)
    wts = pcg.worktrees(root)
    local_bs = pcg.local_branches(root)
    tags = pcg.archive_tags(root)
    stashes = pcg.stash_list(root)
    upstream = pcg.upstream_ref(root)
    origin_main_sha = pcg.resolve_ref(root, "origin/main")
    local_main_relationship = pcg.sync_status(root, "origin/main", "main")
    current_vs_origin_main = pcg.sync_status(root, "origin/main", "HEAD")

    pr_data = pcg.list_prs(root, state="open")
    closed_data = pcg.list_prs(root, state="closed")

    gone_branches = [b["branch"] for b in local_bs if b["tracking_gone"]]
    local_only_branches = [b["branch"] for b in local_bs if b["local_only"]]

    unpreserved: list[dict[str, Any]] = []
    if closed_data["available"]:
        unpreserved = pcg.find_unpreserved_closed_branches(root, closed_data["prs"], tags)

    config, config_error = _load_config_safely()
    lanes = pcr.active_lanes(config)
    permission = pcr.strategy_permission_snapshot(config)
    concepts = pcr.enabled_concepts_snapshot(config)
    release_identity = pcr.intended_release_identity()

    branch_t1 = pcg.current_branch(root)
    head_t1 = pcg.head_sha(root)
    branch_changed_during_check = (branch_t0 != branch_t1) or (head_t0 != head_t1)

    report = {
        "routine": "session-start",
        "read_only": True,
        "generated_at": _now_iso(),
        "repo": {
            "repo_root": str(root),
            "current_branch": branch_t0 or UNKNOWN,
            "head_sha": head_t0 or UNKNOWN,
            "origin_main_sha": origin_main_sha or UNKNOWN,
            "origin_main_note": "locally cached; run `git fetch origin main` for freshness, not done automatically here",
            "local_main_relationship": local_main_relationship,
            "current_branch_vs_origin_main": current_vs_origin_main,
            "upstream": upstream or UNKNOWN,
            "current_worktree": str(root),
            "worktrees": wts,
            "dirty_tracked_files": status["dirty"],
            "staged_files": status["staged"],
            "untracked_files": status["untracked"],
            "branches_tracking_deleted_remotes": gone_branches,
            "local_only_branches": local_only_branches,
            "open_prs": pr_data["prs"] if pr_data["available"] else UNKNOWN,
            "open_prs_detail": None if pr_data["available"] else pr_data["detail"],
            "closed_unmerged_branches_missing_archive_tag": unpreserved,
            "archive_tags": tags,
            "stash_count": len(stashes),
            "stash_labels": stashes,
        },
        "runtime_snapshot": {
            "config_load_error": config_error,
            "intended_deployed_release": release_identity,
            "active_paper_forward_lanes": lanes,
            "strategy_permission_gate": permission,
            "enabled_concepts": concepts,
        },
        "branch_changed_during_check": branch_changed_during_check,
    }
    return report


def _write_session_state(root: Path, report: dict[str, Any]) -> Path:
    state_dir = _state_dir(root)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / SESSION_STATE_FILENAME
    state = {
        "ts": report["generated_at"],
        "repo_root": str(root),
        "branch": report["repo"]["current_branch"],
        "worktree": report["repo"]["current_worktree"],
        "head_sha": report["repo"]["head_sha"],
    }
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return state_path


def cmd_session_start(args: argparse.Namespace) -> int:
    root = pcg.find_repo_root()
    if root is None:
        print("FAIL CLOSED: could not resolve a git repo root from the current directory.")
        return 2
    report = build_session_start_report(root)
    state_path = _write_session_state(root, report)
    report["session_state_file"] = str(state_path)
    _print_report(report)
    if report["branch_changed_during_check"]:
        print("\nWARNING: branch/HEAD changed while this check was running — re-run before trusting this snapshot.")
        return 1
    return 0


# ── precommit / prepush ──────────────────────────────────────────────────

def build_precommit_report(root: Path) -> dict[str, Any]:
    state_path = _state_dir(root) / SESSION_STATE_FILENAME
    session_state: dict[str, Any] | None = None
    session_state_error: str | None = None
    if state_path.exists():
        try:
            session_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            session_state_error = str(exc)
    else:
        session_state_error = f"no session-start state found at {state_path}; run `python -m ops.project_check session-start` first"

    branch = pcg.current_branch(root)
    head = pcg.head_sha(root)
    status = pcg.porcelain_status(root)
    wts = pcg.worktrees(root)
    upstream = pcg.upstream_ref(root)
    origin_main_sha = pcg.resolve_ref(root, "origin/main")
    ahead_behind = pcg.sync_status(root, "origin/main", "HEAD")

    owning_worktree = pcg.worktree_for_branch(wts, branch)
    branch_owned_elsewhere = (
        owning_worktree is not None and str(Path(owning_worktree.get("path", root))) != str(root)
    )

    failures: list[str] = []
    if session_state is None:
        failures.append(f"session-start state cannot be verified: {session_state_error}")
    else:
        if session_state.get("branch") != branch:
            failures.append(
                f"branch differs from session-start: was {session_state.get('branch')!r}, now {branch!r}"
            )
        if session_state.get("worktree") != str(root):
            failures.append(
                f"worktree differs from session-start: was {session_state.get('worktree')!r}, now {str(root)!r}"
            )
    if branch_owned_elsewhere:
        failures.append(f"branch {branch!r} is also checked out in another worktree: {owning_worktree.get('path')}")
    if branch is None:
        failures.append("HEAD is detached — ambiguous branch state")

    report = {
        "routine": "precommit",
        "read_only": True,
        "generated_at": _now_iso(),
        "repo_root": str(root),
        "current_branch": branch or UNKNOWN,
        "current_head": head or UNKNOWN,
        "session_start_branch": (session_state or {}).get("branch", UNKNOWN),
        "session_start_worktree": (session_state or {}).get("worktree", UNKNOWN),
        "current_worktree": str(root),
        "upstream": upstream or UNKNOWN,
        "origin_main_sha": origin_main_sha or UNKNOWN,
        "ahead_behind_origin_main": ahead_behind,
        "changed_files": sorted(set(status["dirty"]) | set(status["staged"])),
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "fail_closed": bool(failures),
        "fail_reasons": failures,
    }
    return report


def cmd_precommit(args: argparse.Namespace) -> int:
    root = pcg.find_repo_root()
    if root is None:
        print("FAIL CLOSED: could not resolve a git repo root from the current directory.")
        return 2
    report = build_precommit_report(root)
    _print_report(report)
    if report["fail_closed"]:
        print("\nFAIL CLOSED:")
        for reason in report["fail_reasons"]:
            print(f"  - {reason}")
        return 1
    print("\nPASS — safe to proceed with commit/push in this worktree.")
    return 0


# ── shared printing ───────────────────────────────────────────────────────

def _print_report(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description="Session safety, promotion proof gate, and daily reconciliation routines.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("session-start", help="Report git/worktree/runtime-snapshot state; writes session tracking state.")
    p_start.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="Read-only branch/worktree safety check against session-start state. Fails closed.")
    p_pre.set_defaults(func=cmd_precommit)

    from ops import project_check_promotion as pcp

    p_promo = sub.add_parser("promotion", help="Strategy Promotion Proof Gate — traces a strategy through the real journal-recorded pipeline.")
    p_promo.add_argument("--strategy", required=True, help="Strategy key as it appears in journal setup.strategy / candidate_audit rows.")
    p_promo.add_argument("--instrument", default=None, help="Restrict to one instrument (e.g. MNQ). Default: all.")
    p_promo.add_argument("--journal-dir", default=None, help="Override journal directory (default: $LOG_DIR or logs/).")
    p_promo.set_defaults(func=pcp.cmd_promotion)

    from ops import project_check_trade_chain as pctc

    p_daily = sub.add_parser("daily", help="Daily Reconciliation + Trade Chain Integrity.")
    p_daily.add_argument("--journal-dir", default=None, help="Override journal directory (default: $LOG_DIR or logs/).")
    p_daily.add_argument("--since", default=None, help="ISO timestamp; only trade-chain-check journal rows at/after this. Default: last daily checkpoint, or start of today.")
    p_daily.add_argument("--no-checkpoint-update", action="store_true", help="Don't advance the local daily checkpoint file after this run.")
    p_daily.set_defaults(func=pctc.cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
