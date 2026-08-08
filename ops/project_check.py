"""ops.project_check — the three repo/process safety routines, one CLI.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy orb_breakout
    python -m ops.project_check daily

All four are read-only. `session-start` and `daily` each persist one small
local checkpoint file under `logs/` (gitignored, same pattern as
`logs/health_digest_latest.json`) so later runs can detect drift or scope a
window — that is the only file-system write any of these subcommands
performs. None of them ever touches git state, risk_rules.yaml, a journal,
or a broker/order.

Manually invoked only. No cron, no daemon, no scheduled service is created
or implied by this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    from ops.session_snapshot import repo_root_of
    found = repo_root_of(ROOT)
    return found or ROOT


def _print(payload, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def cmd_session_start(args: argparse.Namespace) -> int:
    from ops.session_snapshot import build_session_start_report
    repo_root = _repo_root(args.repo_root)
    report = build_session_start_report(repo_root, log_dir=args.log_dir)
    _print(report, as_json=args.json)
    return 0


def cmd_precommit(args: argparse.Namespace) -> int:
    from ops.session_snapshot import build_precommit_report
    repo_root = _repo_root(args.repo_root)
    report = build_precommit_report(repo_root, log_dir=args.log_dir)
    _print(report, as_json=args.json)
    if report["verdict"] != "PASS":
        print(f"\nFAIL CLOSED: {len(report['violations'])} violation(s):", file=sys.stderr)
        for v in report["violations"]:
            print(f"  - {v}", file=sys.stderr)
        return 1
    return 0


def cmd_promotion(args: argparse.Namespace) -> int:
    from ops.promotion_gate import build_promotion_report
    repo_root = _repo_root(args.repo_root)
    report = build_promotion_report(args.strategy, repo_root=repo_root, log_dir=args.log_dir)
    _print(report, as_json=args.json)
    if report.get("classification") == "BLOCKED" or report.get("gate_status") == "BLOCKER_FOUND":
        return 1
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    from ops.daily_reconciliation import build_daily_report, format_trade_chain
    repo_root = _repo_root(args.repo_root)
    report = build_daily_report(repo_root, log_dir=args.log_dir)
    if args.json:
        _print(report, as_json=True)
    else:
        print(format_trade_chain(report["trade_chain_integrity"]))
        print()
        print(json.dumps(
            {k: v for k, v in report.items() if k != "trade_chain_integrity_formatted"},
            indent=2, sort_keys=True, default=str,
        ))
    blockers = report["trade_chain_integrity"].get("blockers") or []
    preservation_blockers = (
        report.get("repo_and_branch_hygiene", {})
        .get("evidence_preservation", {})
        .get("blockers_unique_evidence_no_archive_tag", [])
    )
    return 1 if (blockers or preservation_blockers) else 0


def _common_parent() -> argparse.ArgumentParser:
    # Shared flags, usable both before AND after the subcommand
    # (`project_check --json daily` and `project_check daily --json` both work).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", default=None, help="repo root (default: auto-detected)")
    common.add_argument("--log-dir", default="logs", help="journal/log directory (default: logs)")
    common.add_argument("--json", action="store_true", help="print raw JSON instead of a human report")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parent()
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description="Session safety, promotion proof, and daily reconciliation routines. Read-only.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("session-start", help="repo + runtime snapshot at the start of a work session", parents=[common])

    sub.add_parser("precommit", help="read-only drift check before commit/push", parents=[common])

    promo = sub.add_parser("promotion", help="strategy promotion proof gate", parents=[common])
    promo.add_argument("--strategy", required=True, help="strategy key, e.g. orb_breakout")

    sub.add_parser("daily", help="daily reconciliation + trade chain integrity", parents=[common])

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "session-start": cmd_session_start,
        "precommit": cmd_precommit,
        "promotion": cmd_promotion,
        "daily": cmd_daily,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
