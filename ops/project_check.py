#!/usr/bin/env python3
"""ops/project_check.py — the three operator-invoked repo/process routines.

  python -m ops.project_check session-start
  python -m ops.project_check precommit
  python -m ops.project_check promotion --strategy <name>
  python -m ops.project_check daily

All four subcommands are read-only: none of them commits, pushes, pulls,
resets, rebases, checks out/switches, deletes a branch, removes a worktree,
drops a stash, creates/deletes a tag, cancels an order, flattens a position,
repairs a journal, or edits any file other than this tool's own small,
gitignored session-checkpoint file (``.claude/project_check_state.json``,
written by ``session-start`` and read by ``precommit`` -- never by anything
that touches trading state).

This module is deliberately a thin CLI: all the actual read-only inspection
lives in ``ops.repo_state``, ``ops.session_snapshot``, ``ops.trade_chain_audit``,
and ``ops.promotion_gate`` so each concern has exactly one implementation,
reused by whichever subcommand needs it (session-start and daily both use
``ops.repo_state`` and ``ops.session_snapshot``; daily and precommit both use
``ops.repo_state``).

No scheduled service, cron, or daemon is created by this file or expected to
run it -- every subcommand is meant to be invoked by hand.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ops import repo_state, session_snapshot, trade_chain_audit
from ops.promotion_gate import build_promotion_report, format_report as format_promotion_report

CHECKPOINT_PATH = Path(".claude/project_check_state.json")
DAILY_CHECKPOINT_PATH = Path(".claude/project_check_daily_checkpoint.json")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


# ─────────────────────────────────────────────────────────────────────────
# session-start
# ─────────────────────────────────────────────────────────────────────────


def _write_checkpoint(report: repo_state.RepoStateReport) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": report.repo_root,
        "branch": report.current_branch,
        "detached_head": report.detached_head,
        "worktree": report.current_worktree,
        "head_sha": report.head_sha,
    }
    CHECKPOINT_PATH.write_text(json.dumps(payload, indent=2))


def cmd_session_start(args: argparse.Namespace) -> int:
    branch_before = repo_state.current_branch()
    report = repo_state.build_report(include_prs=not args.no_gh)
    snapshot = session_snapshot.build_runtime_snapshot(args.log_dir, risk_rules_path=args.risk_rules)
    branch_after = repo_state.current_branch()
    branch_changed_during_check = branch_before != branch_after

    _write_checkpoint(report)

    combined = {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "branch_changed_during_check": branch_changed_during_check,
        "repo": report.as_dict(),
        "runtime_snapshot": snapshot,
        "checkpoint_written_to": str(CHECKPOINT_PATH),
    }

    if args.json:
        _print_json(combined)
        return 0

    d = combined["repo"]
    print("SESSION START — REPO")
    print(f"  repo root:              {d['repo_root']}")
    print(f"  current branch:         {d['current_branch']}{' (DETACHED)' if d['detached_head'] else ''}")
    print(f"  HEAD sha:               {d['head_sha']}")
    print(f"  origin/{d['default_remote_branch']} sha:{'':<7}{d['origin_main_sha']}")
    print(f"  main sync state:        {d['main_sync_state']}")
    print(f"  upstream:               {d['upstream']}")
    print(f"  current worktree:       {d['current_worktree']}")
    print(f"  branch changed mid-check:{branch_changed_during_check}")
    print(f"  worktrees ({len(d['worktrees'])}):")
    for wt in d["worktrees"]:
        print(f"    - {wt['path']} [{wt['branch']}] {wt['head']}")
    print(f"  dirty tracked files:    {d['dirty_tracked_files'] or 'none'}")
    print(f"  staged files:           {d['staged_files'] or 'none'}")
    print(f"  untracked files:        {d['untracked_files'] or 'none'}")
    print(f"  branches tracking deleted remotes: {d['branches_tracking_deleted_remotes'] or 'none'}")
    print(f"  local-only branches:    {d['local_only_branches'] or 'none'}")
    print(f"  branches not merged into origin/{d['default_remote_branch']}: {d['branches_not_merged'] or 'none'}")
    print(f"  branches checked out in other worktrees: {d['branches_checked_out_elsewhere'] or 'none'}")
    print(f"  archive/* tags:         {d['archive_tags'] or 'none'}")
    print(f"  stash count:            {len(d['stash_list'])}")
    print(f"  open PRs:               {d['open_prs_status']}" + ("" if d["open_prs"] is None else f" ({len(d['open_prs'])})"))
    print()
    print("RUNTIME SNAPSHOT")
    rel = combined["runtime_snapshot"]["deployed_release"]
    print(f"  deployed release:       {rel.get('status')} ({rel.get('reason') or rel.get('release_commit')})")
    posture = combined["runtime_snapshot"]["risk_config_posture"]
    print(f"  risk config posture:    live_trading_enabled={posture.get('live_trading_enabled')} paper_mode={posture.get('paper_mode')}")
    print(f"  max contracts per instrument: {posture.get('max_contracts_per_instrument')}")
    lane_health = combined["runtime_snapshot"]["evidence_lane_health"]
    print(f"  evidence lane overall status: {lane_health.get('overall_status')}")
    print(f"  active lane modes:      {combined['runtime_snapshot']['active_lane_modes']}")
    print(f"  entry execution mode:   {combined['runtime_snapshot']['entry_execution_mode']}")
    print(f"  entry tolerance ticks:  {combined['runtime_snapshot']['entry_tolerance_ticks_by_instrument']}")
    print(f"  NOTE: {combined['runtime_snapshot']['caveat']}")
    print()
    print(f"checkpoint written to {CHECKPOINT_PATH} for the precommit subcommand to compare against.")
    return 0


# ─────────────────────────────────────────────────────────────────────────
# precommit
# ─────────────────────────────────────────────────────────────────────────


def cmd_precommit(args: argparse.Namespace) -> int:
    problems: list[str] = []

    if not CHECKPOINT_PATH.exists():
        print("FAIL CLOSED: no session-start checkpoint found "
              f"({CHECKPOINT_PATH}) — session-start state cannot be verified. "
              "Run `python -m ops.project_check session-start` first.")
        return 1

    try:
        checkpoint = json.loads(CHECKPOINT_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL CLOSED: session-start checkpoint unreadable ({exc}) — state cannot be verified.")
        return 1

    report = repo_state.build_report(include_prs=False)
    d = report.as_dict()

    if d["repo_root"] is None or d["current_branch"] is None and not d["detached_head"]:
        problems.append("repository state is ambiguous (git could not resolve repo root/branch)")

    if checkpoint.get("repo_root") and d["repo_root"] != checkpoint["repo_root"]:
        problems.append(
            f"worktree differs unexpectedly: session-start was {checkpoint['repo_root']!r}, now {d['repo_root']!r}"
        )

    if checkpoint.get("worktree") and d["current_worktree"] != checkpoint["worktree"]:
        problems.append(
            f"worktree differs unexpectedly: session-start was {checkpoint['worktree']!r}, now {d['current_worktree']!r}"
        )

    if checkpoint.get("branch") is not None and d["current_branch"] != checkpoint["branch"]:
        problems.append(
            f"branch differs from session-start branch unexpectedly: was {checkpoint['branch']!r}, now {d['current_branch']!r}"
        )

    if bool(checkpoint.get("detached_head")) != bool(d["detached_head"]):
        problems.append("HEAD attachment state changed since session-start (detached <-> attached)")

    intended_branch = checkpoint.get("branch")
    if intended_branch:
        owners = [wt for wt in d["worktrees"] if wt["branch"] == intended_branch]
        other_owners = [wt for wt in owners if wt["path"] != checkpoint.get("worktree")]
        if other_owners:
            problems.append(f"intended branch {intended_branch!r} is owned by another worktree: {other_owners}")

    result = {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checkpoint": checkpoint,
        "current": {
            "repo_root": d["repo_root"],
            "current_branch": d["current_branch"],
            "head_sha": d["head_sha"],
            "current_worktree": d["current_worktree"],
            "upstream": d["upstream"],
        },
        "changed_files": d["dirty_tracked_files"],
        "staged_files": d["staged_files"],
        "untracked_files": d["untracked_files"],
        "problems": problems,
        "pass": not problems,
    }

    if args.json:
        _print_json(result)
    else:
        print(f"PRECOMMIT CHECK — {'PASS' if result['pass'] else 'FAIL CLOSED'}")
        print(f"  repo root:        {d['repo_root']}")
        print(f"  current branch:   {d['current_branch']} (session-start: {checkpoint.get('branch')})")
        print(f"  current worktree: {d['current_worktree']} (session-start: {checkpoint.get('worktree')})")
        print(f"  upstream:         {d['upstream']}")
        print(f"  changed files:    {d['dirty_tracked_files'] or 'none'}")
        print(f"  staged files:     {d['staged_files'] or 'none'}")
        print(f"  untracked files:  {d['untracked_files'] or 'none'}")
        if problems:
            print("  PROBLEMS:")
            for p in problems:
                print(f"    - {p}")
    return 0 if result["pass"] else 1


# ─────────────────────────────────────────────────────────────────────────
# promotion
# ─────────────────────────────────────────────────────────────────────────


def cmd_promotion(args: argparse.Namespace) -> int:
    report = build_promotion_report(
        args.strategy,
        journal_dir=args.log_dir,
        risk_rules_path=args.risk_rules,
        inventory_path=args.inventory,
    )
    if args.json:
        _print_json(report)
    else:
        print(format_promotion_report(report))
    return 0


# ─────────────────────────────────────────────────────────────────────────
# daily
# ─────────────────────────────────────────────────────────────────────────


def _load_daily_checkpoint() -> dict[str, Any]:
    try:
        return json.loads(DAILY_CHECKPOINT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_daily_checkpoint(today: date) -> None:
    DAILY_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_CHECKPOINT_PATH.write_text(json.dumps({"last_checked_date": today.isoformat()}, indent=2))


def cmd_daily(args: argparse.Namespace) -> int:
    today = date.today()
    prior = _load_daily_checkpoint()

    repo = repo_state.build_report(include_prs=not args.no_gh)
    repo_d = repo.as_dict()
    prs_today = None
    if not args.no_gh:
        prs_today = repo_state.prs_touched_on(today)

    evidence_preservation = []
    for branch in repo_d["branches_not_merged"]:
        if branch in repo_d["branches_checked_out_elsewhere"]:
            continue  # still active work, not a closed-unmerged candidate
        has_archive_tag = any(tag.endswith(f"/{branch}") or branch in tag for tag in repo_d["archive_tags"])
        evidence_preservation.append(
            {
                "branch": branch,
                "has_archive_tag": has_archive_tag,
                "classification": "OK" if has_archive_tag else "BLOCKER — unique evidence may be unpreserved",
            }
        )

    snapshot = session_snapshot.build_runtime_snapshot(args.log_dir, risk_rules_path=args.risk_rules)

    trade_chain = trade_chain_audit.audit_trade_chain(
        args.log_dir,
        from_date=prior.get("last_checked_date"),
    )

    _write_daily_checkpoint(today)

    result = {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since_checkpoint": prior.get("last_checked_date"),
        "github_repo_hygiene": {
            "prs_today": prs_today,
            "current_open_prs": repo_d["open_prs"],
            "branches_tracking_deleted_remotes": repo_d["branches_tracking_deleted_remotes"],
            "local_only_branches": repo_d["local_only_branches"],
            "main_sync_state": repo_d["main_sync_state"],
            "worktrees": repo_d["worktrees"],
            "stash_count": len(repo_d["stash_list"]),
        },
        "evidence_preservation": evidence_preservation,
        "deployed_state": snapshot,
        "trade_chain": trade_chain,
    }

    if args.json:
        _print_json(result)
        return 0

    print("DAILY RECONCILIATION")
    print(f"  since checkpoint: {result['since_checkpoint'] or '(first run — no prior checkpoint)'}")
    print()
    print("A. GITHUB / REPO RECONCILIATION")
    if prs_today is None:
        print("  PR activity today: UNKNOWN (gh unavailable or --no-gh passed)")
    else:
        print(f"  PRs opened today:  {len(prs_today['opened_today'])}")
        print(f"  PRs merged today:  {len(prs_today['merged_today'])}")
        print(f"  PRs closed-unmerged today: {len(prs_today['closed_unmerged_today'])}")
        print(f"  currently open PRs: {len(prs_today['open_prs'])}")
    print(f"  branches tracking deleted remotes: {repo_d['branches_tracking_deleted_remotes'] or 'none'}")
    print(f"  local-only branches: {repo_d['local_only_branches'] or 'none'}")
    print(f"  local main sync state: {repo_d['main_sync_state']}")
    print(f"  stash count: {len(repo_d['stash_list'])}")
    blockers = [e for e in evidence_preservation if e["classification"] != "OK"]
    print(f"  evidence preservation blockers: {blockers or 'none'}")
    print()
    print("B. DEPLOYED STATE")
    rel = snapshot["deployed_release"]
    print(f"  deployed release: {rel.get('status')} ({rel.get('reason') or rel.get('release_commit')})")
    print(f"  active lane modes: {snapshot['active_lane_modes']}")
    print(f"  entry tolerance ticks: {snapshot['entry_tolerance_ticks_by_instrument']}")
    print()
    print("D. TRADE CHAIN INTEGRITY")
    print(trade_chain_audit.format_compact(trade_chain))
    return 0 if not blockers and trade_chain["pass"] else 1


# ─────────────────────────────────────────────────────────────────────────
# CLI wiring
# ─────────────────────────────────────────────────────────────────────────


def _common_parser() -> argparse.ArgumentParser:
    """Shared options, usable both before and after the subcommand name
    (``ops.project_check --json daily`` and ``ops.project_check daily --json``
    both work) by attaching this as a ``parents=[...]`` parser to every
    subparser as well as the top-level one."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit full JSON instead of a text summary")
    common.add_argument("--log-dir", default="logs", help="journal/evidence-lane log directory (default: logs)")
    common.add_argument("--risk-rules", default="risk_rules.yaml", help="path to risk_rules.yaml")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(prog="ops.project_check", description=__doc__, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("session-start", help="report repo/worktree/runtime state; write a checkpoint", parents=[common])
    p_start.add_argument("--no-gh", action="store_true", help="skip the best-effort gh PR lookup")
    p_start.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="read-only, fail-closed session-continuity check", parents=[common])
    p_pre.set_defaults(func=cmd_precommit)

    p_promo = sub.add_parser("promotion", help="strategy promotion proof-gate report", parents=[common])
    p_promo.add_argument("--strategy", required=True, help="strategy name, matched fuzzily against the inventory")
    p_promo.add_argument("--inventory", default="docs/strategy-rules/Strategy_Inventory.md")
    p_promo.set_defaults(func=cmd_promotion)

    p_daily = sub.add_parser("daily", help="daily reconciliation + trade chain integrity", parents=[common])
    p_daily.add_argument("--no-gh", action="store_true", help="skip the best-effort gh PR lookup")
    p_daily.set_defaults(func=cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
