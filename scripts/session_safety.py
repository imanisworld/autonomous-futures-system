#!/usr/bin/env python3
"""Read-only CLI: session-start safety check + runtime snapshot.

  python3 scripts/session_safety.py start
      Reports repo/branch/worktree/PR/runtime state and writes ONE small
      local snapshot to .git/ops-session-safety-state.json (never tracked)
      so a later `precommit` run can verify nothing shifted underneath the
      session.

  python3 scripts/session_safety.py precommit   (alias: prepush)
      Strictly read-only. Re-checks state against the session-start
      snapshot and FAILS CLOSED (non-zero exit) on branch/worktree drift,
      a branch owned by another worktree, ambiguous branch identity, or a
      missing/unparseable/stale session-start snapshot.

Never fetches/pulls/checks-out/resets/rebases/commits/pushes/creates or
deletes branches or tags/drops stashes. See ops/session_safety.py for the
underlying (tested) logic — this file is only argument parsing and
printing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.session_safety import (
    STALE_SESSION_SECONDS,
    build_precommit_report,
    build_start_report,
    format_precommit_report,
    format_start_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["start", "precommit", "prepush"], help="Which check to run.")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repo root (default: discovered from cwd).")
    parser.add_argument(
        "--stale-after-hours", type=float, default=STALE_SESSION_SECONDS / 3600,
        help="precommit/prepush only: session-start snapshot age (hours) beyond which it fails closed as stale.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    args = parser.parse_args(argv)

    mode = "precommit" if args.mode == "prepush" else args.mode

    if mode == "start":
        report = build_start_report(repo_root=args.repo_root)
        print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_start_report(report))
        return 0 if report.get("ok") else 2

    report = build_precommit_report(
        repo_root=args.repo_root,
        stale_after_seconds=int(args.stale_after_hours * 3600),
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_precommit_report(report))
    return report.get("exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
