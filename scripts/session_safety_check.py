#!/usr/bin/env python3
"""Read-only CLI for Session Safety + Runtime Snapshot.

Usage:
    python3 scripts/session_safety_check.py session-start [--json]
    python3 scripts/session_safety_check.py precommit [--json]

session-start reports repo/worktree/branch hygiene plus a runtime-drift
snapshot, and records a local checkpoint precommit reads back. precommit
compares current repo state against that checkpoint and FAILS CLOSED on any
unexplained difference. Neither mode ever commits, pushes, pulls, resets,
rebases, checks out, deletes a branch/worktree, drops a stash, or writes a
tag -- both are read-only plus (session-start only) writing its own small
checkpoint file under logs/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.session_safety import precommit_report, session_start_report


def _print_human(report: dict) -> None:
    print(f"Session safety [{report['mode']}]: {report['verdict']}")
    print(f"Generated at: {report['generated_at']}")
    reasons = report.get("problems") or report.get("reasons") or []
    if reasons:
        print()
        for reason in reasons:
            print(f"  - {reason}")
    print()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("session-start", "precommit"))
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.mode == "session-start":
        report = session_start_report(repo_root=args.repo_root, log_dir=args.log_dir)
    else:
        report = precommit_report(repo_root=args.repo_root, log_dir=args.log_dir)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)

    verdict = report["verdict"]
    return 0 if verdict in ("SAFE TO WORK", "PASS (read-only)") else 1


if __name__ == "__main__":
    raise SystemExit(main())
