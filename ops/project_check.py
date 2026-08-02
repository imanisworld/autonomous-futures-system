"""ops.project_check — the three repo/process safety routines, one CLI.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy orb_breakout
    python -m ops.project_check daily [--api-base http://127.0.0.1:8000]

All four subcommands are read-only against the repo, broker, and journal.
The only files this tool ever writes are its own small local state files
under ``logs/`` (already .gitignored):
  - ``logs/project_check_session_state.json`` (written by session-start,
    read by precommit)
  - ``logs/project_check_daily_checkpoint.json`` (written by daily)

No cron, no daemon, no scheduled service — every routine is manually invoked.
"""

from __future__ import annotations

import argparse
import json
import sys


def _cmd_session_start(args: argparse.Namespace) -> int:
    from ops.project_check_session import (
        build_session_start_report,
        format_session_start,
        write_session_state,
    )

    report = build_session_start_report(cwd=args.cwd, log_dir=args.log_dir)
    write_session_state(report, log_dir=args.log_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_session_start(report))
    return 0


def _cmd_precommit(args: argparse.Namespace) -> int:
    from ops.project_check_session import build_precommit_report, format_precommit

    report = build_precommit_report(cwd=args.cwd, log_dir=args.log_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_precommit(report))
    return 0 if report["ok"] else 1


def _cmd_promotion(args: argparse.Namespace) -> int:
    from ops.project_check_promotion import build_promotion_report, format_promotion_report

    report = build_promotion_report(
        args.strategy,
        log_dir=args.log_dir,
        days=args.days,
        evidence_file=args.evidence_file,
        repo_root=args.cwd,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_promotion_report(report))
    # Non-zero exit whenever the gate did not produce a promotable verdict —
    # lets CI/human workflows branch on this without parsing text.
    return 0 if report["classification"] in ("VALIDATED", "PROMISING BUT UNPROVEN") else 1


def _cmd_daily(args: argparse.Namespace) -> int:
    from ops.project_check_daily import build_daily_report, format_daily_report

    report = build_daily_report(
        cwd=args.cwd,
        log_dir=args.log_dir,
        since=args.since,
        api_base=args.api_base,
        update_checkpoint=not args.no_checkpoint_update,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(format_daily_report(report))
    return 0 if report["trade_chain"]["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", default=None, help="repo path (default: current directory)")
    common.add_argument("--log-dir", default="logs", help="journal/state directory (default: logs)")
    common.add_argument("--json", action="store_true", help="emit full JSON report instead of text")

    # NOTE: --cwd/--log-dir/--json live ONLY on the subparsers (not also on
    # the top-level parser). argparse subparsers re-apply their own actions'
    # defaults onto the shared namespace even when the parent already set a
    # value for the same dest, which would silently clobber e.g.
    # `--cwd X session-start` back to None. Requiring the flags after the
    # subcommand name avoids that footgun entirely.
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "session-start", parents=[common],
        help="full repo + runtime snapshot; writes session state",
    )

    sub.add_parser(
        "precommit", parents=[common],
        help="fast fail-closed drift check against session-start state",
    )

    p_promo = sub.add_parser("promotion", parents=[common], help="strategy promotion proof gate")
    p_promo.add_argument("--strategy", required=True, help="strategy concept name, e.g. orb_breakout")
    p_promo.add_argument("--days", type=int, default=90, help="journal window in days (default: 90)")
    p_promo.add_argument(
        "--evidence-file", default=None,
        help="optional standalone backtest/replay results JSON for a coarse identity/parity cross-check",
    )

    p_daily = sub.add_parser("daily", parents=[common], help="daily reconciliation + trade chain integrity")
    p_daily.add_argument("--since", default=None, help="override window start date YYYY-MM-DD (default: since last checkpoint, or today)")
    p_daily.add_argument("--api-base", default=None, help="optional running service base URL for broker/journal parity, e.g. http://127.0.0.1:8000")
    p_daily.add_argument("--no-checkpoint-update", action="store_true", help="don't advance the daily checkpoint file")

    args = parser.parse_args(argv)

    handlers = {
        "session-start": _cmd_session_start,
        "precommit": _cmd_precommit,
        "promotion": _cmd_promotion,
        "daily": _cmd_daily,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
