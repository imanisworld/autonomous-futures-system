"""Fail-closed execution selector for the wide-stop evidence campaign.

Paper collection is unconditional and always runs. Tradovate demo is an
explicit, proof-pinned lane that runs ADDITIVELY alongside paper for 4HR
Re-Trigger and 60M 3-2-2 only. Daily 2-2 is never demo-eligible here.

The demo lane carries its own LANE-LOCAL execution permission so it never
depends on — and can never be enabled by — the box-wide ``SCHEDULE_MODE``.
It still goes through ``adaptive.execution_gate.order_placement_allowed``,
the single execution chokepoint; only the *schedule mode fed to that gate*
is lane-local. Unarmed, the lane resolves to ``always_on_shadow`` and the
gate refuses every order.
"""
from __future__ import annotations

import os

ROUTE_ENV = "WIDE_STOP_LEDGER_EXECUTION_ROUTE"
ROUTE_PROOF_PIN_ENV = "EXPECTED_PROOF_WIDE_STOP_LEDGER_EXECUTION_ROUTE"
PAPER_ROUTE = "paper_sim"
DEMO_ROUTE = "tradovate_demo"
DEFAULT_ROUTE = PAPER_ROUTE
VALID_ROUTES = (PAPER_ROUTE, DEMO_ROUTE)
FROZEN_MNQ_IOC_TICKS = 8.0
# The demo route pins its own entry construction via BracketOrder overrides
# (see context/wide_stop_demo_runtime_core.py), not via the process-wide
# TRADOVATE_ENTRY_EXECUTION_MODE env var — that env var is shared with any
# other Tradovate strategy this same process runs and must not be forced to
# match this route's requirement.
DEMO_ENTRY_EXECUTION_MODE = "ioc_limit"

# Lane-local execution permission. Deliberately NOT the box-wide SCHEDULE_MODE:
# arming this lane must never re-arm order placement for any other strategy in
# this process, and changing the box posture must never silently arm this lane.
DEMO_EXECUTION_ENABLED_ENV = "WIDE_STOP_DEMO_EXECUTION_ENABLED"
DEMO_EXECUTION_PROOF_PIN_ENV = "EXPECTED_PROOF_WIDE_STOP_DEMO_EXECUTION_ENABLED"
DEMO_SESSIONS_ENV = "WIDE_STOP_DEMO_SESSIONS"
DEFAULT_DEMO_SESSIONS = ("new_york",)
# Fed to order_placement_allowed() as the lane's own schedule mode. Unarmed the
# lane is shadow — the gate's own "no orders, ever" branch does the refusing.
DEMO_ARMED_SCHEDULE_MODE = "current"
DEMO_DISARMED_SCHEDULE_MODE = "always_on_shadow"


def route() -> str:
    raw = str(os.getenv(ROUTE_ENV, DEFAULT_ROUTE) or DEFAULT_ROUTE).strip().lower()
    return raw if raw in VALID_ROUTES else "disabled"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes"}


def demo_execution_armed() -> bool:
    """True only when the lane is explicitly armed AND proof-pinned.

    Both the flag and its matching pin are required, so a single stray env var
    can never arm external-broker execution.
    """
    if not _bool_env(DEMO_EXECUTION_ENABLED_ENV, False):
        return False
    return _bool_env(DEMO_EXECUTION_PROOF_PIN_ENV, False)


def demo_lane_schedule_mode() -> str:
    """The lane's OWN schedule mode for the execution gate. Never reads SCHEDULE_MODE."""
    return DEMO_ARMED_SCHEDULE_MODE if demo_execution_armed() else DEMO_DISARMED_SCHEDULE_MODE


def demo_sessions() -> tuple[str, ...]:
    """Lane-local session allowlist. Narrows the global gate, never widens it."""
    raw = os.getenv(DEMO_SESSIONS_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_DEMO_SESSIONS
    return tuple(s.strip().lower() for s in str(raw).split(",") if s.strip())


def demo_session_allowed(session) -> bool:
    return str(session or "").strip().lower() in demo_sessions()


def demo_config_errors(cfg=None) -> list[str]:
    errors: list[str] = []
    selected = route()
    if selected != DEMO_ROUTE:
        return ["route_not_tradovate_demo"]
    if str(os.getenv(ROUTE_PROOF_PIN_ENV, "")).strip().lower() != selected:
        errors.append("wide_stop_execution_route_not_proof_pinned")
    evidence_mode = str(
        getattr(cfg, "wide_stop_ledger_mode", None)
        or os.getenv("WIDE_STOP_LEDGER_MODE", "observe_only")
    ).strip().lower()
    if evidence_mode != "paper_sim":
        errors.append("wide_stop_evidence_mode_not_paper_sim")
    if str(os.getenv("BROKER", "paper")).strip().lower() != "tradovate":
        errors.append("broker_not_tradovate")
    if str(os.getenv("TRADOVATE_ENV", "demo")).strip().lower() != "demo":
        errors.append("tradovate_env_not_demo")
    if _bool_env("LIVE_TRADING_ENABLED", False):
        errors.append("live_trading_enabled")
    account_pin = str(os.getenv("TRADOVATE_EXPECTED_ACCOUNT_ID", "")).strip()
    if not account_pin or not account_pin.lstrip("-").isdigit():
        errors.append("tradovate_expected_account_id_missing_or_invalid")
    if not _bool_env("FIVE_MIN_FEED_ENABLED", False):
        errors.append("five_min_feed_not_enabled")
    return errors
