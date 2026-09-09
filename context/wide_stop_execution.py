"""Fail-closed execution selector for the wide-stop evidence campaign.

Paper remains the default. Tradovate demo is an explicit, proof-pinned route for
4HR Re-Trigger and 60M 3-2-2 only. Daily 2-2 is never demo-eligible here.
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


def route() -> str:
    raw = str(os.getenv(ROUTE_ENV, DEFAULT_ROUTE) or DEFAULT_ROUTE).strip().lower()
    return raw if raw in VALID_ROUTES else "disabled"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes"}


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
