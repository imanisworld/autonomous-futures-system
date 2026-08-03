"""Unified manually-invoked entrypoint for the three project-check routines:

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name>
    python -m ops.project_check daily

Every subcommand is read-only over repo/journal/config state. None of them
commit, push, pull, reset, rebase, checkout, delete branches/worktrees/
stashes, create/delete tags, modify trading state, or touch a broker. See
``ops/session_snapshot.py``, ``ops/strategy_promotion.py``, and
``ops/daily_reconciliation.py`` for what each one actually reads and why.

Exit code is 0 when the routine's status is OK/PASS and 1 otherwise, so this
composes with shell conditionals and git hooks without extra glue.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _print_json(report: dict[str, Any]) -> None:
    print(json.dumps(report, indent=2, default=str, sort_keys=True))


def _cmd_session_start(args: argparse.Namespace) -> int:
    from ops.session_snapshot import session_start_report

    report = session_start_report(repo_root=args.repo_root, do_fetch=not args.no_fetch)
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 1

    print(f"SESSION START: {report['status'].upper()}")
    print(f"repo_root: {report.get('repo_root')}")
    print(f"branch: {report.get('branch')}  head: {str(report.get('head_sha'))[:12]}")
    print(f"main_sync_state: {report.get('main_sync_state')} (origin/main {str(report.get('origin_main_sha'))[:12]})")
    print(f"worktree: {report.get('worktree')}  ({len(report.get('worktrees') or [])} worktree(s) total)")
    dirty = report.get("dirty_tracked_files") or []
    untracked = report.get("untracked_files") or []
    print(f"dirty tracked files: {len(dirty)}  untracked: {len(untracked)}")
    stashes = report.get("stashes") or []
    print(f"stashes: {len(stashes)}")
    gone = report.get("branches_tracking_deleted_remotes") or []
    if gone:
        print(f"branches tracking deleted remotes: {gone}")
    local_only = report.get("local_only_branches") or []
    if local_only:
        print(f"local-only branches: {local_only}")
    evidence = report.get("closed_unmerged_evidence") or {}
    if evidence.get("blockers"):
        print(f"BLOCKER: unpreserved evidence on: {evidence['blockers']}")
    runtime = report.get("runtime_snapshot") or {}
    lanes = runtime.get("paper_forward_lanes") or {}
    print(f"paper-eligible+enabled strategies: {lanes.get('paper_eligible_and_enabled_strategies')}")
    print(f"entry_fill_model: {lanes.get('entry_fill_model_global')}  tolerance_ticks: {lanes.get('entry_tolerance_ticks_by_root')}")
    deployed = runtime.get("deployed_release") or {}
    print(f"deployed release sha: {deployed.get('intended_release_sha')}")
    if report.get("blockers"):
        print(f"BLOCKERS: {report['blockers']}")
    print(f"(session state {'persisted' if report.get('session_state_persisted') else 'NOT persisted'} to {report.get('session_state_path')})")
    return 0 if report["ok"] else 1


def _cmd_precommit(args: argparse.Namespace) -> int:
    from ops.session_snapshot import precommit_report

    report = precommit_report(repo_root=args.repo_root)
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 1

    print(f"PRECOMMIT: {report['status']}")
    print(f"branch: {report.get('branch')}  worktree: {report.get('worktree')}")
    if report.get("fail_reasons"):
        print("FAIL REASONS:")
        for reason in report["fail_reasons"]:
            print(f"  - {reason}")
    changed = report.get("changed_files") or []
    untracked = report.get("untracked_files") or []
    print(f"changed files: {len(changed)}  untracked: {len(untracked)}")
    return 0 if report["ok"] else 1


def _cmd_promotion(args: argparse.Namespace) -> int:
    from ops.repo_state import find_repo_root
    from ops.strategy_promotion import build_promotion_report

    root = find_repo_root(args.repo_root) or Path(args.repo_root or ".").resolve()
    report = build_promotion_report(
        args.strategy,
        repo_root=root,
        journal_dir=args.journal_dir,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    if args.json:
        _print_json(report)
        return 0

    print(f"STRATEGY PROMOTION PROOF GATE: {report['strategy']}")
    classification = report["classification"]
    print(f"suggested classification: {classification['suggested']}")
    print("(never VALIDATED automatically — see classification.note)")
    for reason in classification["reasons"]:
        print(f"  - {reason}")
    accounting = report["execution"]["accounting"]
    print(
        f"attempts={accounting['attempts']} fills={accounting['fills']} "
        f"resolved={accounting['resolved_fills']} open={accounting['legitimately_open']} "
        f"no_fills={accounting['no_fills']} other_cancel={accounting['other_cancellations']} "
        f"identity_ok={accounting['identity_ok']}"
    )
    print(f"combined net P&L: ${report['performance']['combined_net_pnl_dollars']}")
    print(f"runtime reachable today: {report['runtime_parity'].get('reachable')} (status={report['runtime_parity'].get('strategy_status')})")
    match_count = report["research_result"]["match_count"]
    print(f"Strategy_Inventory.md matches: {match_count}")
    return 0


def _cmd_daily(args: argparse.Namespace) -> int:
    from ops.daily_reconciliation import build_daily_report

    report = build_daily_report(
        repo_root=args.repo_root,
        journal_dir=args.journal_dir,
        today=args.today,
        from_date=args.from_date,
        do_fetch=not args.no_fetch,
        save_checkpoint=not args.no_checkpoint,
    )
    if args.json:
        _print_json(report)
        return 0 if report["ok"] else 1

    print(f"DAILY RECONCILIATION: {report['status']}")
    print(f"window: {report.get('window')}")
    if report.get("blockers"):
        print(f"BLOCKERS: {report['blockers']}")
    github = report.get("github") or {}
    if github.get("available"):
        print(
            f"PRs opened_today={len(github.get('opened_today', []))} "
            f"merged_today={len(github.get('merged_today', []))} "
            f"open={len(github.get('open_prs', []))} "
            f"stale={len(github.get('stale_prs_over_14d_idle', []))}"
        )
    else:
        print(f"PRs: UNKNOWN ({github.get('reason')})")
    bw = report.get("branches_worktrees") or {}
    print(
        f"worktrees={len(bw.get('worktrees') or [])} dirty={len(bw.get('dirty_worktrees') or [])} "
        f"main_sync={((bw.get('main_sync_state') or {}).get('state'))} stashes={len(bw.get('stashes') or [])}"
    )
    drift = (report.get("strategy_source_of_truth") or {}).get("drift") or []
    if drift:
        print(f"strategy source-of-truth drift ({len(drift)}):")
        for item in drift:
            print(f"  - {item['strategy']}: {item['issue']}")
    else:
        print("strategy source-of-truth drift: none")
    print(report.get("trade_chain_summary_line", ""))
    return 0 if report["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    # --json is a per-subcommand flag (`project_check daily --json`), not a
    # top-level one: argparse's subparsers action re-applies each
    # sub-parser's own defaults into the shared namespace, which would
    # silently clobber a top-level --json back to False.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument("--json", action="store_true", help="print the full machine-readable report instead of a summary")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    session_start = subparsers.add_parser("session-start", help="Routine 1A: session safety + runtime snapshot", parents=[json_parent])
    session_start.add_argument("--repo-root", default=None)
    session_start.add_argument("--no-fetch", action="store_true", help="skip the read-only origin fetch used for main-sync comparison")
    session_start.set_defaults(func=_cmd_session_start)

    precommit = subparsers.add_parser("precommit", help="Routine 1B: precommit/prepush drift guard (strictly read-only)", parents=[json_parent])
    precommit.add_argument("--repo-root", default=None)
    precommit.set_defaults(func=_cmd_precommit)

    promotion = subparsers.add_parser("promotion", help="Routine 2: strategy promotion proof gate", parents=[json_parent])
    promotion.add_argument("--strategy", required=True, help="machine strategy name, e.g. orb_breakout")
    promotion.add_argument("--repo-root", default=None)
    promotion.add_argument("--journal-dir", default=None)
    promotion.add_argument("--from-date", default=None)
    promotion.add_argument("--to-date", default=None)
    promotion.set_defaults(func=_cmd_promotion)

    daily = subparsers.add_parser("daily", help="Routine 3: daily reconciliation + trade chain integrity", parents=[json_parent])
    daily.add_argument("--repo-root", default=None)
    daily.add_argument("--journal-dir", default=None)
    daily.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD), mainly for testing")
    daily.add_argument("--from-date", default=None, help="override the window start; defaults to the last saved checkpoint")
    daily.add_argument("--no-fetch", action="store_true")
    daily.add_argument("--no-checkpoint", action="store_true", help="do not update the saved daily checkpoint")
    daily.set_defaults(func=_cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
