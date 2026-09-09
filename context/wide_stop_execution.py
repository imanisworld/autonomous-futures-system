"""Fail-closed execution selector for the wide-stop evidence campaign.

Only the internal PaperBroker simulation route is valid on this branch. Any
other requested value, including Tradovate/demo/live-like values, resolves to
``disabled`` and the 5-minute feed takes no lane action.
"""
from __future__ import annotations

import os

ROUTE_ENV = "WIDE_STOP_LEDGER_EXECUTION_ROUTE"
PAPER_ROUTE = "paper_sim"
DEFAULT_ROUTE = PAPER_ROUTE
VALID_ROUTES = (PAPER_ROUTE,)


def route() -> str:
    raw = str(os.getenv(ROUTE_ENV, DEFAULT_ROUTE) or DEFAULT_ROUTE).strip().lower()
    return raw if raw in VALID_ROUTES else "disabled"
