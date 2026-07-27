"""
execution/broker_interface.py

Abstract base class for all broker adapters.
Both PaperBroker and future live brokers implement this interface.

The RiskEngine uses is_live to block any live broker from executing
in Phase 1.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ─── Common Types ────────────────────────────────────────────────────────────

@dataclass
class BracketOrder:
    """A complete bracket order: entry + stop + target."""
    instrument: str
    direction: str       # LONG | SHORT
    entry: float
    stop: float
    target: float
    rr_ratio: float
    strategy: str
    contracts: int = 1
    notes: Optional[str] = None
    # Per-order overrides for the MNQ orb_reclaim proof mode (Stage 2,
    # 2026-07-11, see context/mnq_orb_reclaim_proof.py). Both default False —
    # every other order in the system is unaffected. Broker adapters that
    # understand these fields must fall back to their normal behavior when
    # both are False.
    force_market_entry: bool = False
    force_runner_exit: bool = False
    # The risk engine's configured floor, carried into execution so an adverse
    # fill cannot silently turn an approved setup into a below-minimum trade.
    min_rr_ratio: float = 2.0
    max_dollar_risk: Optional[float] = None
    max_stop_ticks: Optional[float] = None
    max_slippage_ticks: Optional[float] = None
    execution_model: str = "anchored_structure"
    post_fill_validation_required: bool = False
    # Deterministic client order identity (Tradovate clOrdId), derived from the
    # originating signal/event identity by the caller. Optional — brokers that
    # support it use it for submit idempotency; PaperBroker/replay ignore it.
    client_order_id: Optional[str] = None


@dataclass
class Position:
    """An open simulated or live position."""
    instrument: str
    direction: str
    entry_price: float
    stop: float
    target: Optional[float]
    quantity: int = 1
    open: bool = True


@dataclass
class Fill:
    """Result of an executed order."""
    instrument: str
    direction: str
    contracts: int
    entry_price: float
    exit_price: Optional[float]
    exit_reason: Optional[str]   # TARGET_HIT | STOP_HIT | MANUAL_CANCEL | None
    result: str                  # WIN | LOSS | BREAKEVEN | OPEN | CANCELLED
    pnl_ticks: Optional[float]
    pnl_dollars: Optional[float]
    # Diagnostic-only fields for CANCELLED/no-fill outcomes. Never read by
    # execution/risk logic — populated best-effort, None when unknown. See
    # execution/no_fill_taxonomy.py for the no_fill_reason bucket meanings.
    no_fill_reason: Optional[str] = None
    order_type: Optional[str] = None
    # Internal simulator order identifier. Real broker order ids remain in the
    # adapter's structured _last_order_ids mapping; this field gives paper
    # fills an auditable identity without pretending they are broker orders.
    paper_order_id: Optional[str] = None
    # Structured actual-fill validation and controlled-flatten evidence.
    execution_audit: Optional[dict] = None


@dataclass(frozen=True)
class BrokerCapabilities:
    """Static broker metadata used for future routing and safety checks."""
    broker_name: str
    asset_class: str
    account_mode: str       # paper | sim | live
    starting_capital: Optional[float]
    available_cash: Optional[float]
    estimated_margin_required: Optional[float]
    max_dollars_risk_per_trade: Optional[float]
    supports_brackets: bool
    supports_options: bool


# ─── Abstract Interface ───────────────────────────────────────────────────────

class BrokerInterface(ABC):
    """
    Abstract broker interface.

    All execution must go through this interface.
    The RiskEngine checks is_live before allowing any execution.
    A live broker in Phase 1 will be blocked before execute_bracket is called.
    """

    @property
    @abstractmethod
    def is_live(self) -> bool:
        """
        Returns True if this broker connects to real markets.
        PaperBroker returns False.
        Any live broker returns True and will be blocked by RiskEngine in Phase 1.
        """
        ...

    @abstractmethod
    def execute_bracket(self, order: BracketOrder) -> Fill:
        """
        Execute a bracket order. Paper brokers simulate; live brokers send real orders.

        Args:
            order: Complete bracket order with entry, stop, target.

        Returns:
            Fill with result and P&L.
        """
        ...

    @abstractmethod
    def get_position(self) -> Optional[Position]:
        """Return the current open position, or None if flat."""
        ...

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel all open orders and flatten any open position."""
        ...

    @abstractmethod
    def get_account_balance(self) -> Optional[float]:
        """Return current account balance/equity when available."""
        ...

    @abstractmethod
    def get_broker_name(self) -> str:
        """Return a human-readable broker name for logging."""
        ...

    @abstractmethod
    def get_capabilities(self) -> BrokerCapabilities:
        """Return static capability metadata for planning and safety checks."""
        ...
