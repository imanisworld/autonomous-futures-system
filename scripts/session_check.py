#!/usr/bin/env python3
"""Read-only CLI for Session Safety + Runtime Snapshot.

Usage:
    python3 scripts/session_check.py session-start
    python3 scripts/session_check.py precommit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.session_check import (
    format_precommit,
    format_session_start,
    precommit_report,
    session_start_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("session-start", "precommit"))
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    if args.mode == "session-start":
        report = session_start_report(
            args.repo_root,
            log_dir=args.log_dir,
            base_branch=args.base_branch,
            state_path=args.state_path,
        )
        print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_session_start(report))
        return 0

    report = precommit_report(
        args.repo_root,
        base_branch=args.base_branch,
        state_path=args.state_path,
    )
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else format_precommit(report))
    return 2 if report["fail_closed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
