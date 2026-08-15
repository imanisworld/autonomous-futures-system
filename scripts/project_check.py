#!/usr/bin/env python3
"""Read-only CLI for the project-check routines.

Exactly three routines:

  1. Session Safety + Runtime Snapshot -- session-start, precommit, and the
     preflight pre-work gate (below) all live under this routine.
  2. Strategy Promotion Proof Gate -- `promotion`.
  3. Daily Reconciliation + Trade Chain Integrity -- `daily` (trade-chain
     accounting, including per-fill entry-model/effective-tolerance
     verification, is folded into this routine's output, not a separate one).

  preflight      Routine 1's pre-work mode: strictly read-only ownership/base
                 check before research or promotion. Makes no bookkeeping
                 writes and never fetches. Unlike session-start/precommit, it
                 fails closed on a dirty worktree -- it exists to gate the
                 START of new evidence generation, not to snapshot or diff an
                 already-in-progress session.
  session-start  Routine 1's mode A: repo/worktree/branch/PR/runtime
                 snapshot; writes a small session-state cache under .git/ so
                 `precommit` can compare against it later. Otherwise
                 read-only.
  precommit      Routine 1's mode B: strictly read-only. Compares current
                 repo state against the last session-start snapshot and
                 fails closed on drift.
  promotion      Strategy Promotion Proof Gate: accounting-identity + safety-
                 gate validator over an explicit evidence-facts file (see
                 ops/project_check/promotion.py's module docstring for the
                 schema). Read-only.
  daily          Daily Reconciliation + Trade Chain Integrity: repo/PR/branch
                 hygiene, evidence preservation, deployed state, strategy
                 source-of-truth drift, and trade-chain accounting since the
                 prior checkpoint -- including, per fill, the entry model and
                 effective tolerance actually used, live-verified against
                 current runtime config. Read-only against git/journals. Does
                 NOT advance its local checkpoint file unless you pass
                 --advance-checkpoint explicitly -- and even then, it never
                 advances on a FAIL result (that would let today's
                 orphans/duplicate-identities/unmatched-outcomes silently
                 drop out of tomorrow's window).

None of these subcommands commit, push, pull, reset, rebase, check out,
delete a branch/worktree, drop a stash, create/delete a tag, cancel an
order, flatten a position, or edit docs/config.

Usage:
  python3 scripts/project_check.py preflight --purpose research|promotion [--json]
  python3 scripts/project_check.py session-start [--json]
  python3 scripts/project_check.py precommit [--json]
  python3 scripts/project_check.py promotion --strategy <name> [--evidence-file path.json] [--json]
  python3 scripts/project_check.py daily [--journal-dir logs] [--advance-checkpoint] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.project_check.daily import build_daily_report
from ops.project_check.preflight import build_ownership_preflight_report
from ops.project_check.promotion import build_promotion_report
from ops.project_check.session import build_precommit_report, build_session_start_report


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _cmd_preflight(args: argparse.Namespace) -> int:
    report = build_ownership_preflight_report(args.purpose, cwd=args.cwd)
    if args.json:
        _print_json(report)
        return 0 if report.get("ok") else 2
    verdict = "PASS" if report.get("ok") else "FAIL_CLOSED"
    print(f"OWNERSHIP PREFLIGHT — {args.purpose.upper()} — {verdict}")
    if report.get("blockers"):
        for blocker in report["blockers"]:
            print(f"  - {blocker}")
    else:
        print("  branch/worktree ownership, remote main, and ancestry verified")
    return 0 if report.get("ok") else 2


def _cmd_session_start(args: argparse.Namespace) -> int:
    report = build_session_start_report(cwd=args.cwd)
    if args.json:
        _print_json(report)
        return 0 if report.get("ok") else 1
    if not report.get("ok"):
        print(f"SESSION-START: ERROR — {report.get('error')}")
        return 1
    repo = report["repo"]
    print("SESSION-START snapshot")
    print(f"  repo root:            {repo['repo_root']}")
    print(f"  current branch:       {repo['current_branch']}")
    print(f"  HEAD sha:             {repo['head_sha']}")
    print(f"  local main vs origin: {repo['local_main_relationship']['state']}")
    print(f"  upstream:             {repo['upstream']}")
    print(f"  dirty tracked files:  {len(repo['dirty_tracked_files'])}")
    print(f"  staged files:         {len(repo['staged_files'])}")
    print(f"  untracked files:      {len(repo['untracked_files'])}")
    print(f"  worktrees:            {len(repo['all_worktrees'])}")
    print(f"  stashes:              {repo['stash_count']}")
    print(f"  branches->deleted remotes: {len(repo['branches_tracking_deleted_remotes'])}")
    print(f"  local-only branches:  {len(repo['local_only_branches'])}")
    print(f"  archive tags:         {len(repo['archive_tags'])}")
    cu = repo["closed_unmerged_branches_missing_archive_tag"]
    print(f"  unmerged remote branches w/o archive tag: {len(cu.get('flagged', []))}")
    print(f"  open PRs available:   {repo['open_prs'].get('available')}")
    if report["branch_changed_during_check"]:
        print("  WARNING: branch/HEAD changed DURING this check")
    rt = report["runtime_snapshot"]
    print("  runtime snapshot:")
    print(f"    deployed_release_sha: {rt['deployed_release_sha']}")
    print(f"    entry_fill_model:     {rt['entry_fill_model']}")
    print(f"    entry_tolerance_ticks:{rt['entry_tolerance_ticks']}")
    print(f"    active_lane_summary:  {rt['active_lanes'].get('active_lane_summary')}")
    return 0


def _cmd_precommit(args: argparse.Namespace) -> int:
    report = build_precommit_report(cwd=args.cwd)
    if args.json:
        _print_json(report)
        return 0 if report.get("ok") else 2
    print(f"PRECOMMIT: {report['verdict']}")
    if report["reasons"]:
        for reason in report["reasons"]:
            print(f"  - {reason}")
    else:
        print("  no drift detected since session-start")
    return 0 if report.get("ok") else 2


def _cmd_promotion(args: argparse.Namespace) -> int:
    report = build_promotion_report(
        strategy=args.strategy,
        repo_root=ROOT,
        evidence_path=args.evidence_file,
    )
    if args.json:
        _print_json(report)
        return 0 if report.get("ok") else 1
    print(f"PROMOTION PROOF GATE: {args.strategy}")
    if report.get("evidence_load_error"):
        print(f"  evidence load error: {report['evidence_load_error']}")
    print(f"  evidence supplied: {report['evidence_supplied']}")
    cls = report["classification"]
    print(f"  stated classification:   {cls['stated_classification']}")
    print(f"  effective classification:{cls['effective_classification']}")
    if cls["override_reason"]:
        print(f"    override reason: {cls['override_reason']}")
    if cls["blockers"]:
        print("  BLOCKERS:")
        for b in cls["blockers"]:
            print(f"    - {b}")
    if cls["warnings"]:
        print("  warnings:")
        for w in cls["warnings"]:
            print(f"    - {w}")
    ctx = report["execution_context"]
    print(f"  execution context parity ok: {ctx['parity_ok']}")
    if ctx["mismatches"]:
        for m in ctx["mismatches"]:
            print(f"    - {m}")
    return 0


def _cmd_daily(args: argparse.Namespace) -> int:
    report = build_daily_report(
        repo_root=ROOT,
        journal_dir=args.journal_dir,
        use_checkpoint=not args.no_checkpoint,
        advance_checkpoint=args.advance_checkpoint,
    )
    if args.json:
        _print_json(report)
        return 0 if report["trade_chain"]["status"] == "PASS" else 1
    tc = report["trade_chain"]
    print("DAILY RECONCILIATION")
    hy = report["repo_reconciliation"]
    print(f"  branch: {hy['current_branch']}  main sync: {hy['local_main_relationship']['state']}")
    print(f"  dirty files: {len(hy['dirty_tracked_files'])}  worktrees: {len(hy['worktrees'])}  stashes: {hy['stash_count']}")
    ep = hy["evidence_preservation"]["closed_unmerged_branches_missing_archive_tag"]
    print(f"  unmerged remote branches missing archive tag: {len(ep.get('flagged', []))}")
    ds = report["deployed_state"]
    print(f"  deployed sha: {ds['deployed_release_sha']}  entry_fill_model: {ds['entry_fill_model']}")
    sd = report["strategy_source_of_truth"]
    if sd.get("checked"):
        print(f"  strategy inventory drift findings: {len(sd['drift_findings'])} (unmatched rows: {len(sd['unmatched_inventory_rows'])})")
    else:
        print(f"  strategy inventory check: NOT CHECKED ({sd.get('reason')})")
    w = tc["window"]
    if w["checkpoint_advance_requested"]:
        if w["checkpoint_advanced"]:
            print(f"  checkpoint advanced to {w['latest_journal_ts']}")
        else:
            print(f"  checkpoint NOT advanced: {w['checkpoint_skip_reason']}")
    if tc["status"] == "PASS":
        s = tc["summary"]
        emt = tc["entry_model_and_tolerance"]
        print(
            f"  TRADE CHAIN: PASS\n"
            f"  {s['attempts']} attempts\n"
            f"  {s['fills']} fills (resolved WIN/LOSS/BREAKEVEN only)\n"
            f"  {s['cancellations']} cancellations\n"
            f"  {s['unverified_open_attempts']} unverified open attempts (no OUTCOME yet, not counted as fills)\n"
            f"  0 orphans\n"
            f"  0 duplicate identities\n"
            f"  0 unmatched outcomes\n"
            f"  entry model (live): {emt['live_entry_fill_model']}"
            f"  (recorded on fills: {emt['recorded_execution_models_in_window']})\n"
            f"  fills missing execution-context: {s['fills_missing_execution_context']}\n"
            f"  fills with slippage outside modelled bound: {s['fills_slippage_outside_bound']}\n"
        )
    else:
        print("  TRADE CHAIN: FAIL")
        _print_json(tc)
    return 0 if tc["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_preflight = sub.add_parser(
        "preflight",
        help="Read-only ownership/base check before research or promotion",
    )
    p_preflight.add_argument("--purpose", choices=("research", "promotion"), required=True)
    p_preflight.add_argument("--cwd", default=None)
    p_preflight.add_argument("--json", action="store_true")
    p_preflight.set_defaults(func=_cmd_preflight)

    p_session = sub.add_parser("session-start", help="Repo/worktree/branch/PR/runtime snapshot")
    p_session.add_argument("--cwd", default=None)
    p_session.add_argument("--json", action="store_true")
    p_session.set_defaults(func=_cmd_session_start)

    p_precommit = sub.add_parser("precommit", help="Read-only drift check vs session-start")
    p_precommit.add_argument("--cwd", default=None)
    p_precommit.add_argument("--json", action="store_true")
    p_precommit.set_defaults(func=_cmd_precommit)

    p_promotion = sub.add_parser("promotion", help="Strategy Promotion Proof Gate")
    p_promotion.add_argument("--strategy", required=True)
    p_promotion.add_argument("--evidence-file", type=Path, default=None)
    p_promotion.add_argument("--json", action="store_true")
    p_promotion.set_defaults(func=_cmd_promotion)

    p_daily = sub.add_parser("daily", help="Daily reconciliation + trade chain integrity")
    p_daily.add_argument("--journal-dir", default="logs")
    p_daily.add_argument("--no-checkpoint", action="store_true", help="ignore any saved checkpoint; scan all journals")
    p_daily.add_argument(
        "--advance-checkpoint",
        action="store_true",
        help="update the local checkpoint file -- only takes effect when the trade-chain result is PASS; "
        "omit this flag to run report-only with no state change (the default)",
    )
    p_daily.add_argument("--json", action="store_true")
    p_daily.set_defaults(func=_cmd_daily)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
