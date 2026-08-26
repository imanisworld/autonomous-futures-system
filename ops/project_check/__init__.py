"""Read-only, manually-invoked repo/process routines.

Three routines, each a thin wrapper over existing ops/* machinery plus a
small amount of new (read-only) git/runtime plumbing that did not exist
elsewhere in the repo:

1. Session Safety + Runtime Snapshot   -> ops.project_check.preflight (live
                                           origin/main freshness, before
                                           research/promotion)
                                        -> ops.project_check.session
                                           (session-start snapshot + precommit
                                           drift check)
2. Strategy Promotion Proof Gate       -> ops.project_check.promotion
3. Daily Reconciliation + Trade Chain  -> ops.project_check.daily (also
                                           wires promotion's "entry model and
                                           effective tolerance must be
                                           checked against live runtime, not
                                           asserted" lesson into per-fill
                                           trade-chain accounting)

Ownership preflight and the session-start/precommit pair are both "Session
Safety" checks -- preflight adds a live (ls-remote) origin/main freshness
check that session-start deliberately does not perform (session-start only
reads local remote-tracking refs; see gitutil.main_sync_state). They are
exposed as separate CLI subcommands (preflight / session-start / precommit)
because they run at different points (before starting work vs. at
session-start vs. before each commit), not because they are conceptually
separate routines.

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
