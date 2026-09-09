"""Execution-route contract for the wide-stop forward evidence campaign.

`WIDE_STOP_LEDGER_MODE` remains the existing evidence enable/epoch switch.
This module adds a separate, explicit route selector so the old paper collector
never silently becomes external-broker execution merely because BROKER changes.

Default route is paper_sim.  tradovate_demo is allowed only when every demo pin
matches the frozen evidence contract.  Any unknown/missing safety input returns
errors and the caller must not submit an order.
"""
from __future__ import annotations

import os
from typing import Optional

ROUTE_ENV = "WIDE_STOP_LEDGER_EXECUTION_ROUTE"
ROUTE_PROOF_PIN_ENV = "EXPECTED_PROOF_WIDE_STOP_LEDGER_EXECUTION_ROUTE"
VALID_ROUTES = ("paper_sim", "tradovate_demo")
DEFAULT_ROUTE = "paper_sim"
DEMO_ROUTE = "tradovate_demo"
FROZEN_MNQ_IOC_TICKS = 8.0


def route() -> str:
    raw = os.getenv(ROUTE_ENV, DEFAULT_ROUTE).strip().lower() or DEFAULT_ROUTE
    return raw if raw in VALID_ROUTES else "disabled"


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return raw.strip().lower() in {"1", "true", "yes"}


def _mnq_entry_tolerance() -> Optional[float]:
    raw = os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ")
    if raw is None:
        raw = os.getenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS")
    if raw is None or not str(raw).strip():
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def demo_config_errors(cfg=None) -> list[str]:
    """Return every reason the wide-stop route may NOT submit to Tradovate demo."""
    errors: list[str] = []
    selected = route()
    if selected != DEMO_ROUTE:
        errors.append("route_not_tradovate_demo")
        return errors

    # The route itself must be explicitly proof-pinned. This keeps the selector
    # reproducible even before it is folded into the global live-box guard list.
    expected_route = str(os.getenv(ROUTE_PROOF_PIN_ENV, "")).strip().lower()
    if expected_route != selected:
        errors.append("wide_stop_execution_route_not_proof_pinned")

    # The existing evidence mode/epoch stays mandatory. We deliberately do not
    # overload it with broker semantics so paper remains the fail-safe default.
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

    # Exact venue parity with the preregistered 8-tick IOC evidence cell.
    exec_mode = str(os.getenv("TRADOVATE_ENTRY_EXECUTION_MODE", "legacy")).strip().lower()
    if exec_mode != "ioc_limit":
        errors.append("tradovate_entry_execution_mode_not_ioc_limit")
    tol = _mnq_entry_tolerance()
    if tol is None or abs(tol - FROZEN_MNQ_IOC_TICKS) > 1e-9:
        errors.append("mnq_ioc_tolerance_not_8_ticks")

    if not _bool_env("FIVE_MIN_FEED_ENABLED", False):
        errors.append("five_min_feed_not_enabled")

    return errors


def demo_config_ok(cfg=None) -> bool:
    return not demo_config_errors(cfg)
