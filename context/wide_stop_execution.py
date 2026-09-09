"""Fail-closed execution selector for the wide-stop day-strategy campaign.

`WIDE_STOP_LEDGER_MODE` remains the evidence/epoch switch. This separate route
selector prevents a broker configuration change from silently turning paper
collection into external submission.

Only 4HR Re-Trigger and 60M 3-2-2 may use `tradovate_demo`. Daily 2-2 remains a
separate PaperBroker-only swing collector and is never routed by this module.
"""
from __future__ import annotations

import os
from typing import Optional

ROUTE_ENV = "WIDE_STOP_LEDGER_EXECUTION_ROUTE"
ROUTE_PROOF_PIN_ENV = "EXPECTED_PROOF_WIDE_STOP_LEDGER_EXECUTION_ROUTE"
PAPER_ROUTE = "paper_sim"
DEMO_ROUTE = "tradovate_demo"
DEFAULT_ROUTE = PAPER_ROUTE
VALID_ROUTES = (PAPER_ROUTE, DEMO_ROUTE)
FROZEN_MNQ_IOC_TICKS = 8.0


def route() -> str:
    raw = str(os.getenv(ROUTE_ENV, DEFAULT_ROUTE) or DEFAULT_ROUTE).strip().lower()
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
    """Return every reason a Tradovate-demo submission must be refused."""
    errors: list[str] = []
    selected = route()
    if selected != DEMO_ROUTE:
        errors.append("route_not_tradovate_demo")
        return errors

    expected_route = str(os.getenv(ROUTE_PROOF_PIN_ENV, "")).strip().lower()
    if expected_route != selected:
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

    exec_mode = str(os.getenv("TRADOVATE_ENTRY_EXECUTION_MODE", "legacy")).strip().lower()
    if exec_mode != "ioc_limit":
        errors.append("tradovate_entry_execution_mode_not_ioc_limit")
    tolerance = _mnq_entry_tolerance()
    if tolerance is None or abs(tolerance - FROZEN_MNQ_IOC_TICKS) > 1e-9:
        errors.append("mnq_ioc_tolerance_not_8_ticks")

    if not _bool_env("FIVE_MIN_FEED_ENABLED", False):
        errors.append("five_min_feed_not_enabled")

    return errors


def demo_config_ok(cfg=None) -> bool:
    return not demo_config_errors(cfg)
