#!/usr/bin/env python3
"""ops.project_check -- the three operator routines, one entrypoint.

  python -m ops.project_check session-start
  python -m ops.project_check precommit
  python -m ops.project_check promotion --strategy <name> --evidence <path>
  python -m ops.project_check daily

session-start and precommit are read-only session-safety + runtime-snapshot
checks (ops/session_safety.py). promotion is the strategy promotion proof
gate (ops/promotion_gate.py). daily is the daily reconciliation + trade
chain integrity pass (ops/daily_reconciliation.py), which itself folds in
ops/trade_chain_audit.py.

Manually invoked only -- no cron, no daemon, no scheduled service. Every
subcommand is read-only with respect to git, broker, and trading state; the
only file this module ever writes is its own local, gitignored session
baseline under .project_check/session_start.json (written by session-start,
read by precommit), and only when the operator does not pass --baseline
explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ops import daily_reconciliation, promotion_gate, session_safety

DEFAULT_BASELINE_PATH = Path(".project_check") / "session_start.json"


def _repo_root() -> Path:
    return session_safety._repo_root()


def _print(payload: dict[str, Any], *, as_json: bool, formatter) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    else:
        print(formatter(payload))


def cmd_session_start(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    report = session_safety.build_session_start_report(repo_root=root)
    baseline_path = Path(args.baseline) if args.baseline else (root / DEFAULT_BASELINE_PATH)
    if not args.no_save_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        report["baseline_written_to"] = str(baseline_path)
    _print(report, as_json=args.json, formatter=session_safety.format_session_start)
    return 0


def cmd_precommit(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    baseline_path = Path(args.baseline) if args.baseline else (root / DEFAULT_BASELINE_PATH)
    if not baseline_path.exists():
        print(
            f"FAIL CLOSED: no session-start baseline at {baseline_path}. "
            "Run `python -m ops.project_check session-start` first.",
            file=sys.stderr,
        )
        return 2
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL CLOSED: could not read baseline {baseline_path}: {exc}", file=sys.stderr)
        return 2
    report = session_safety.build_precommit_report(baseline=baseline, repo_root=root)
    _print(report, as_json=args.json, formatter=session_safety.format_precommit)
    return 0 if report["ok"] else 1


def cmd_promotion(args: argparse.Namespace) -> int:
    report = promotion_gate.run(args.evidence, strategy=args.strategy)
    _print(report, as_json=args.json, formatter=promotion_gate.format_report)
    return 0 if report["gate_verdict"].startswith("EVIDENCE_CONSISTENT") else 1


def cmd_daily(args: argparse.Namespace) -> int:
    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    report = daily_reconciliation.build_daily_reconciliation(
        repo_root=root,
        journal_dir=args.log_dir,
        since_date=args.since,
        status_url=args.status_url,
    )
    _print(report, as_json=args.json, formatter=daily_reconciliation.format_daily_reconciliation)
    return 0 if report["trade_chain_integrity"]["overall"] == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description=__doc__.split("\n\n")[0],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("session-start", help="repo/worktree/runtime snapshot at session start")
    p_start.add_argument("--repo-root", default=None)
    p_start.add_argument("--json", action="store_true")
    p_start.add_argument("--baseline", default=None, help="where to write the session baseline")
    p_start.add_argument("--no-save-baseline", action="store_true")
    p_start.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="read-only drift check vs the session-start baseline")
    p_pre.add_argument("--repo-root", default=None)
    p_pre.add_argument("--json", action="store_true")
    p_pre.add_argument("--baseline", default=None, help="baseline written by session-start")
    p_pre.set_defaults(func=cmd_precommit)

    p_promo = sub.add_parser("promotion", help="strategy promotion proof gate")
    p_promo.add_argument("--strategy", required=True)
    p_promo.add_argument("--evidence", required=True, help="path to the strategy's evidence JSON")
    p_promo.add_argument("--json", action="store_true")
    p_promo.set_defaults(func=cmd_promotion)

    p_daily = sub.add_parser("daily", help="daily reconciliation + trade chain integrity")
    p_daily.add_argument("--repo-root", default=None)
    p_daily.add_argument("--log-dir", default="logs")
    p_daily.add_argument("--since", default=None, help="only trace journal entries with ts >= this ISO value")
    p_daily.add_argument("--status-url", default=None, help="optional /status/today or similar for broker parity")
    p_daily.add_argument("--json", action="store_true")
    p_daily.set_defaults(func=cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
