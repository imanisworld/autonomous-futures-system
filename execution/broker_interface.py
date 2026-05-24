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
    notes: Optional[str] = None


@dataclass
class Position:
    """An open simulated or live position."""
    instrument: str
    direction: str
    entry_price: float
    stop: float
    target: float
    quantity: int = 1
    open: bool = True


@dataclass
class Fill:
    """Result of an executed order."""
    instrument: str
    direction: str
    entry_price: float
    exit_price: Optional[float]
    exit_reason: Optional[str]   # TARGET_HIT | STOP_HIT | MANUAL_CANCEL | None
    result: str                  # WIN | LOSS | OPEN | CANCELLED
    pnl_ticks: Optional[float]
    pnl_dollars: Optional[float]


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
    def get_broker_name(self) -> str:
        """Return a human-readable broker name for logging."""
        ...
