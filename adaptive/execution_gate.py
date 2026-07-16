"""
adaptive/execution_gate.py

The SINGLE execution-safety chokepoint for schedule modes. Any code path that
could place an order MUST call order_placement_allowed() first and honor a False.
This is what structurally isolates the shadow mode from execution and enforces
paper_eligible_sessions. It is deliberately conservative: anything unrecognized,
and EVERY shadow mode, returns False.

Invariants enforced here:
  * always_on_shadow never places an order (paper or live).
  * always_on_paper places PAPER orders only, and only for paper_eligible_sessions
    (session_gap / off_hours stay shadow-only).
  * Live execution may run ONLY the "current" schedule; always_on_* is refused
    when live_trading_enabled is true (belt-and-suspenders with config validation).
  * demo_execution_hold_sessions denies EXTERNAL-broker placement for the listed
    sessions in every mode (demo and live alike); paper/proof routes are exempt
    so isolated evidence collection continues through a hold.
"""
from __future__ import annotations

VALID_MODES = ("current", "always_on_shadow", "always_on_paper")


def order_placement_allowed(
    *,
    schedule_mode: str,
    session: str,
    live_trading_enabled: bool,
    paper_eligible_sessions,
    demo_execution_hold_sessions=(),
    broker_is_paper: bool = False,
) -> tuple[bool, str]:
    """Return (allowed, reason). NEVER returns True for a shadow mode, and never
    for live execution under any always-on mode."""
    paper_eligible = set(paper_eligible_sessions or [])

    # Operator session hold: external-broker entries are paused for these
    # sessions while shadow lanes, PaperBroker proof lanes, and candidate
    # journaling keep running. Checked before mode logic so a hold can never
    # be bypassed by any schedule mode, including live.
    hold = {
        str(s).strip().lower()
        for s in (demo_execution_hold_sessions or ())
        if str(s).strip()
    }
    if hold and not broker_is_paper and str(session).strip().lower() in hold:
        return False, (
            f"demo_execution_hold: external-broker entries paused for session "
            f"'{session}' — paper/proof routes unaffected"
        )

    # Live execution may ONLY run the current schedule.
    if live_trading_enabled:
        if schedule_mode == "current":
            return True, "live: current schedule"
        return False, (
            f"live execution forbids schedule_mode '{schedule_mode}' — "
            "always-on must never place live orders"
        )

    if schedule_mode == "current":
        return True, "current schedule"
    if schedule_mode == "always_on_shadow":
        return False, "always_on_shadow is read-only — no orders, ever"
    if schedule_mode == "always_on_paper":
        if session in paper_eligible:
            return True, f"always_on_paper: '{session}' is paper-eligible"
        return False, (
            f"always_on_paper: session '{session}' is shadow-only "
            "(not in paper_eligible_sessions)"
        )
    return False, f"unknown schedule_mode '{schedule_mode}' — denied"
