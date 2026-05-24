"""
execution/tradovate_broker_stub.py

Placeholder for future Tradovate live broker integration.

PHASE 1 STATUS: DISABLED
─────────────────────────
This file exists to scaffold the broker abstraction layer.
It does NOT place real orders. It does NOT connect to Tradovate.
All execution methods raise NotImplementedError in Phase 1.

is_live returns True, which causes RiskEngine to block execution
before this class is ever called.

Future activation requires:
1. LIVE_TRADING_ENABLED=true in config (currently blocked)
2. Explicit Phase 2 implementation of all abstract methods
3. Credential validation
4. Separate test suite for live integration
5. Rulebook version increment acknowledging live trading
"""

from __future__ import annotations

import os
from typing import Optional

from execution.broker_interface import BrokerInterface, BracketOrder, Position, Fill


class TradovateBrokerStub(BrokerInterface):
    """
    Tradovate broker stub — scaffolded for Phase 2, disabled in Phase 1.

    IMPORTANT: is_live returns True. The RiskEngine in Phase 1 blocks
    any broker where is_live=True from executing. This class cannot
    place orders even if instantiated.
    """

    TRADOVATE_BASE_URL_DEMO = "https://demo.tradovateapi.com/v1"
    TRADOVATE_BASE_URL_LIVE = "https://live.tradovateapi.com/v1"

    def __init__(self):
        # Credentials exist in env but are NOT used in Phase 1
        self._username = os.getenv("TRADOVATE_USERNAME", "")
        self._app_id = os.getenv("TRADOVATE_APP_ID", "")
        self._environment = os.getenv("TRADOVATE_ENVIRONMENT", "demo")
        self._authenticated = False

    @property
    def is_live(self) -> bool:
        """
        Always returns True — this is a live broker adapter.
        RiskEngine will block execution for any is_live=True broker in Phase 1.
        """
        return True

    def get_broker_name(self) -> str:
        return f"TradovateStub({self._environment})"

    def execute_bracket(self, order: BracketOrder) -> Fill:
        """
        NOT IMPLEMENTED — Phase 1.

        In Phase 2, this will submit an OSO (Order-Sends-Order) bracket
        to the Tradovate REST API using authenticated session tokens.
        """
        raise NotImplementedError(
            "TradovateBrokerStub.execute_bracket is not implemented in Phase 1. "
            "Live trading is disabled. Use PaperBroker instead."
        )

    def get_position(self) -> Optional[Position]:
        """NOT IMPLEMENTED — Phase 1."""
        raise NotImplementedError(
            "TradovateBrokerStub.get_position is not implemented in Phase 1."
        )

    def cancel_all(self) -> None:
        """NOT IMPLEMENTED — Phase 1."""
        raise NotImplementedError(
            "TradovateBrokerStub.cancel_all is not implemented in Phase 1."
        )

    def authenticate(self) -> None:
        """
        NOT IMPLEMENTED — Phase 1.

        Phase 2 will use Tradovate OAuth flow:
        POST /auth/accesstokenrequest
        with username, password, appId, appVersion, cid, sec
        """
        raise NotImplementedError(
            "TradovateBrokerStub.authenticate is not implemented in Phase 1. "
            "Credentials will not be validated until Phase 2."
        )

    def _get_base_url(self) -> str:
        """Return the correct Tradovate base URL for the configured environment."""
        if self._environment == "live":
            return self.TRADOVATE_BASE_URL_LIVE
        return self.TRADOVATE_BASE_URL_DEMO

    # ── Phase 2 scaffolding (not called in Phase 1) ─────────────────────────

    def _place_order(self, symbol: str, action: str, qty: int, order_type: str,
                     price: Optional[float] = None) -> dict:
        """Phase 2: place a single order via REST API."""
        raise NotImplementedError("Phase 2 only.")

    def _place_bracket(self, entry_order_id: int, stop: float, target: float) -> dict:
        """Phase 2: attach stop and target to an existing order (OSO)."""
        raise NotImplementedError("Phase 2 only.")

    def _cancel_order(self, order_id: int) -> None:
        """Phase 2: cancel a specific order."""
        raise NotImplementedError("Phase 2 only.")

    def _get_account_info(self) -> dict:
        """Phase 2: fetch account balance and margin info."""
        raise NotImplementedError("Phase 2 only.")
