"""Read-only, manually-invoked repo/process routines.

Four routines, each a thin wrapper over existing ops/* machinery plus a
small amount of new (read-only) git/runtime plumbing that did not exist
elsewhere in the repo:

1. Ownership Preflight                 -> ops.project_check.preflight
2. Session Safety + Runtime Snapshot   -> ops.project_check.session
3. Strategy Promotion Proof Gate       -> ops.project_check.promotion
4. Daily Reconciliation + Trade Chain  -> ops.project_check.daily

Nothing in this package commits, pushes, pulls, resets, rebases, checks out,
deletes branches/worktrees/tags, drops stashes, cancels orders, flattens
positions, or otherwise mutates repo or broker state. See scripts/project_check.py
for the CLI entry point.
"""
from __future__ import annotations

from ops.project_check.session import build_precommit_report, build_session_start_report
from ops.project_check.promotion import build_promotion_report
from ops.project_check.daily import build_daily_report
from ops.project_check.preflight import build_ownership_preflight_report

__all__ = [
    "build_ownership_preflight_report",
    "build_session_start_report",
    "build_precommit_report",
    "build_promotion_report",
    "build_daily_report",
]
