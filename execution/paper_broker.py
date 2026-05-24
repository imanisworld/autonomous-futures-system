"""
execution/paper_broker.py

Paper trading simulator. Implements BrokerInterface.
No real orders. No broker connections. No credentials required.

Simulation logic:
- Entry fill: assumed at entry price (market order sim)
- Resolution: compares next-bar OHLC to stop and target
  - If high (long) or inverted-low (short) hits target → WIN
  - If low (long) or inverted-high (short) hits stop → LOSS
  - If neither: OPEN (position remains pending resolution)

is_live always returns False.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from execution.broker_interface import (
    BrokerCapabilities,
    BrokerInterface,
    BracketOrder,
    Fill,
    Position,
)


# ─── Tick Values (approximate, Phase 1 simplified) ────────────────────────────

TICK_SIZE = {
    "MNQ": 0.25,
    "MES": 0.25,
    "MGC": 0.10,
    "MCL": 0.01,
}

TICK_VALUE = {
    "MNQ": 0.50,   # $0.50/tick
    "MES": 1.25,   # $1.25/tick
    "MGC": 1.00,   # $1.00/tick
    "MCL": 1.00,   # $1.00/tick (per 0.01 = $1)
}


@dataclass
class NextBarOHLC:
    """Optional next-bar data for trade resolution simulation."""
    high: float
    low: float


# ─── Paper Broker ─────────────────────────────────────────────────────────────

class PaperBroker(BrokerInterface):
    """
    Paper trading broker. Simulates bracket order fills locally.
    Never connects to any external service.
    """

    def __init__(self):
        self._position: Optional[Position] = None

    @property
    def is_live(self) -> bool:
        return False

    def get_broker_name(self) -> str:
        return "PaperBroker"

    def get_capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            broker_name=self.get_broker_name(),
            asset_class="futures",
            account_mode="paper",
            starting_capital=1000.0,
            available_cash=1000.0,
            estimated_margin_required=0.0,
            max_dollars_risk_per_trade=10.0,
            supports_brackets=True,
            supports_options=False,
        )

    def execute_bracket(self, order: BracketOrder) -> Fill:
        """
        Simulate a bracket order fill.

        Phase 1 behavior: fills at entry price immediately.
        Resolution requires a call to resolve_position() with next-bar data,
        or returns OPEN if no next-bar data is available.
        """
        if self._position is not None:
            raise RuntimeError(
                "PaperBroker: Cannot open a new position while one is already open. "
                "Call resolve_position() first."
            )

        self._position = Position(
            instrument=order.instrument,
            direction=order.direction,
            entry_price=order.entry,
            stop=order.stop,
            target=order.target,
            quantity=1,
            open=True,
        )

        # Phase 1: return OPEN fill — caller resolves with next bar
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            entry_price=order.entry,
            exit_price=None,
            exit_reason=None,
            result="OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    def resolve_position(self, next_bar: NextBarOHLC) -> Optional[Fill]:
        """
        Attempt to resolve an open paper position using next-bar OHLC data.

        Returns:
            Fill with WIN/LOSS result, or None if position not open.

        Logic:
            Long: target hit if next_bar.high >= target, stop hit if next_bar.low <= stop
            Short: target hit if next_bar.low <= target, stop hit if next_bar.high >= stop
            Target takes priority if both levels are hit in the same bar.
        """
        if self._position is None or not self._position.open:
            return None

        pos = self._position
        instrument = pos.instrument
        tick = TICK_SIZE.get(instrument, 0.25)
        tick_val = TICK_VALUE.get(instrument, 1.0)

        target_hit = False
        stop_hit = False

        if pos.direction == "LONG":
            target_hit = next_bar.high >= pos.target
            stop_hit = next_bar.low <= pos.stop
        elif pos.direction == "SHORT":
            target_hit = next_bar.low <= pos.target
            stop_hit = next_bar.high >= pos.stop

        if target_hit:
            exit_price = pos.target
            exit_reason = "TARGET_HIT"
            result = "WIN"
        elif stop_hit:
            exit_price = pos.stop
            exit_reason = "STOP_HIT"
            result = "LOSS"
        else:
            # Position still open — no resolution yet
            return None

        # Calculate P&L in ticks and dollars
        if pos.direction == "LONG":
            pnl_ticks = (exit_price - pos.entry_price) / tick
        else:
            pnl_ticks = (pos.entry_price - exit_price) / tick

        pnl_dollars = pnl_ticks * tick_val

        self._position = None  # Flat

        return Fill(
            instrument=instrument,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=round(exit_price, 4),
            exit_reason=exit_reason,
            result=result,
            pnl_ticks=round(pnl_ticks, 2),
            pnl_dollars=round(pnl_dollars, 2),
        )

    def get_position(self) -> Optional[Position]:
        return self._position if (self._position and self._position.open) else None

    def cancel_all(self) -> None:
        """Cancel (flatten) any open paper position without P&L."""
        if self._position:
            self._position.open = False
            self._position = None

    def force_resolve(self, result: str, exit_price: float) -> Optional[Fill]:
        """
        Force-resolve an open position at a given price with a given result.
        Used in testing and edge-case handling.
        """
        if self._position is None:
            return None

        pos = self._position
        instrument = pos.instrument
        tick = TICK_SIZE.get(instrument, 0.25)
        tick_val = TICK_VALUE.get(instrument, 1.0)

        if pos.direction == "LONG":
            pnl_ticks = (exit_price - pos.entry_price) / tick
        else:
            pnl_ticks = (pos.entry_price - exit_price) / tick

        pnl_dollars = pnl_ticks * tick_val
        self._position = None

        return Fill(
            instrument=instrument,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=round(exit_price, 4),
            exit_reason="MANUAL_CANCEL",
            result=result,
            pnl_ticks=round(pnl_ticks, 2),
            pnl_dollars=round(pnl_dollars, 2),
        )
