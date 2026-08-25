"""Read-only, manually-invoked repo/process routines.

Exactly three routines, each a thin wrapper over existing ops/* machinery
plus a small amount of new (read-only) git/runtime plumbing that did not
exist elsewhere in the repo:

1. Session Safety + Runtime Snapshot   -> ops.project_check.session
   (build_session_start_report / build_precommit_report). Also folds in the
   ownership/base-freshness checks from ops.project_check.preflight
   (live origin/main verification, worktree-ownership) rather than exposing
   them as a separate routine.
2. Strategy Promotion Proof Gate       -> ops.project_check.promotion
3. Daily Reconciliation + Trade Chain  -> ops.project_check.daily
   (build_daily_report, which reuses ops.project_check.trade_chain)

Nothing in this package commits, pushes, pulls, resets, rebases, checks out,
deletes branches/worktrees/tags, drops stashes, cancels orders, flattens
positions, or otherwise mutates repo or broker state. See scripts/project_check.py
for the CLI entry point.
"""
from __future__ import annotations

from ops.project_check.session import build_precommit_report, build_session_start_report
from ops.project_check.promotion import build_promotion_report
from ops.project_check.daily import build_daily_report

__all__ = [
    "build_session_start_report",
    "build_precommit_report",
    "build_promotion_report",
    "build_daily_report",
]
