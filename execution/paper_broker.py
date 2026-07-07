"""
execution/paper_broker.py

Paper trading simulator. Implements BrokerInterface.
No real orders. No broker connections. No credentials required.

Simulation logic:
- Entry fill: market order at entry price ± slippage_ticks (adverse).
- Resolution: compares next-bar OHLC to stop and target
  - If high (long) or inverted-low (short) hits target → WIN (clean limit fill)
  - If low (long) or inverted-high (short) hits stop → LOSS (market fill, slipped)
  - If a bar hits BOTH: pessimistic_both_hit=True → STOP (worst case),
    False → legacy target-priority (optimistic)
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
from execution.trailing import compute_trailed_stop


# ─── Tick Values (approximate, Phase 1 simplified) ────────────────────────────

TICK_SIZE = {
    "MNQ": 0.25,
    "MES": 0.25,
    "ES": 0.25,
    "NQ": 0.25,
    "MGC": 0.10,
    "MCL": 0.01,
}

TICK_VALUE = {
    "MNQ": 0.50,   # $0.50/tick
    "MES": 1.25,   # $1.25/tick
    "ES": 12.50,   # $12.50/tick
    "NQ": 5.00,    # $5.00/tick
    "MGC": 10.00,  # $10.00/tick
    "MCL": 10.00,  # $10.00/tick
}


@dataclass
class NextBarOHLC:
    """Optional next-bar data for trade resolution simulation."""
    high: float
    low: float
    # Next-bar open — used by pending-entry fill models (e.g. a stop entry that
    # gaps through its trigger). Optional so existing callers are unaffected.
    open: Optional[float] = None


@dataclass
class _PendingStopMarketEntry:
    order: BracketOrder
    contracts: int


# ─── Paper Broker ─────────────────────────────────────────────────────────────

class PaperBroker(BrokerInterface):
    """
    Paper trading broker. Simulates bracket order fills locally.
    Never connects to any external service.
    """

    def __init__(
        self,
        starting_balance: float = 1500.0,
        slippage_ticks: float = 0.0,
        pessimistic_both_hit: bool = False,
        breakeven_at_1r: bool = False,
        runner_mode: bool = False,
        runner_activation_r: float = 1.0,
        runner_trail_r: float = 0.5,
        entry_fill_model: str = "market",
        entry_tolerance_ticks_by_root: Optional[dict] = None,
        entry_tolerance_ticks_default: float = 0.0,
    ):
        """
        Args:
            starting_balance: paper account balance.
            slippage_ticks: adverse slippage (in ticks) applied to MARKET fills —
                the entry and any stop exit. Limit exits (target) fill clean.
                0.0 reproduces the original optimistic behavior.
            pessimistic_both_hit: when a single bar's range contains BOTH the stop
                and the target, you cannot know intrabar order — True resolves it
                as the STOP (worst case), False keeps the legacy target-priority
                (optimistic) behavior.
        """
        self._position: Optional[Position] = None
        self._balance = float(starting_balance)
        self._slippage_ticks = max(0.0, float(slippage_ticks or 0.0))
        self._pessimistic_both_hit = bool(pessimistic_both_hit)
        # When False, the 1R→breakeven stop trail is disabled: trades run to the
        # original stop (full LOSS) or target (WIN), never scratched at entry.
        self._breakeven_at_1r = bool(breakeven_at_1r)
        # Runner mode (1-contract): once price reaches runner_activation_r * R in
        # our favour, DROP the fixed target and trail the stop runner_trail_r * R
        # behind the running favourable extreme — i.e. let winners run instead of
        # capping at target. Default off → exit behaviour is unchanged.
        self._runner_mode = bool(runner_mode)
        self._runner_activation_r = float(runner_activation_r)
        self._runner_trail_r = float(runner_trail_r)
        self._runner_max_fav: Optional[float] = None  # running favourable price extreme
        # Entry fill model. "market" = legacy: every
        # entry fills at the plan price ± slippage. "ioc_limit" mirrors the live
        # Tradovate entry leg (#2): a Limit-IOC capped at entry ± tolerance —
        # the order fills at the CURRENT market (never worse than the cap) or
        # self-cancels. "stop_market" arms a one-next-bar stop entry; it fills
        # causally from NextBarOHLC.open/high/low or fails closed.
        model = str(entry_fill_model or "market").strip().lower()
        if model not in ("market", "ioc_limit", "stop_market"):
            raise ValueError(f"PaperBroker: unknown entry_fill_model {entry_fill_model!r}")
        self._entry_fill_model = model
        self._entry_tol_by_root = dict(entry_tolerance_ticks_by_root or {})
        self._entry_tol_default = max(0.0, float(entry_tolerance_ticks_default or 0.0))
        self._pending_stop_entry: Optional[_PendingStopMarketEntry] = None

    def _entry_tolerance_ticks(self, instrument: str) -> float:
        root = "".join(ch for ch in str(instrument or "").upper() if ch.isalpha())[:3]
        try:
            return max(0.0, float(self._entry_tol_by_root.get(root, self._entry_tol_default)))
        except (TypeError, ValueError):
            return self._entry_tol_default

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
            starting_capital=self._balance,
            available_cash=self._balance,
            estimated_margin_required=0.0,
            max_dollars_risk_per_trade=10.0,
            supports_brackets=True,
            supports_options=False,
        )

    def execute_bracket(
        self, order: BracketOrder, market_price: Optional[float] = None
    ) -> Fill:
        """
        Simulate a bracket order fill.

        market model (legacy): fills at entry price ± slippage immediately.
        ioc_limit model: mirrors the live Tradovate entry leg (#2) — a Limit-IOC
        capped at entry ± tolerance ticks. `market_price` (the decision bar's
        close) is where the market actually is when the order arrives: the fill
        happens AT the market (capped at the limit), or not at all. An
        unmarketable IOC self-cancels → result=CANCELLED / ENTRY_NOT_FILLED,
        exactly how the live box books it, and no position is opened.

        Resolution requires a call to resolve_position() with next-bar data,
        or returns OPEN if no next-bar data is available.
        """
        if self._position is not None or self._pending_stop_entry is not None:
            raise RuntimeError(
                "PaperBroker: Cannot open a new position while one is already open. "
                "Call resolve_position() first."
            )

        contracts = max(1, int(order.contracts or 1))
        if self._entry_fill_model == "stop_market":
            self._pending_stop_entry = _PendingStopMarketEntry(order=order, contracts=contracts)
            return Fill(
                instrument=order.instrument,
                direction=order.direction,
                contracts=contracts,
                entry_price=order.entry,
                exit_price=None,
                exit_reason=None,
                result="PENDING",
                pnl_ticks=None,
                pnl_dollars=None,
            )

        # Entry is a MARKET order — apply adverse slippage. LONG fills higher,
        # SHORT fills lower. Stop/target stay at their ordered (resting) prices.
        tick = TICK_SIZE.get(order.instrument, 0.25)
        slip = self._slippage_ticks * tick
        if self._entry_fill_model == "ioc_limit":
            if market_price is None:
                raise ValueError(
                    "PaperBroker(entry_fill_model='ioc_limit') requires "
                    "market_price (the decision bar's close) on execute_bracket()"
                )
            tol = self._entry_tolerance_ticks(order.instrument) * tick
            market = float(market_price)
            if order.direction == "LONG":
                limit_px = order.entry + tol
                if market > limit_px:
                    return self._entry_not_filled(order, contracts)
                fill_entry = min(limit_px, market + slip)
            elif order.direction == "SHORT":
                limit_px = order.entry - tol
                if market < limit_px:
                    return self._entry_not_filled(order, contracts)
                fill_entry = max(limit_px, market - slip)
            else:
                fill_entry = market
        elif order.direction == "LONG":
            fill_entry = order.entry + slip
        elif order.direction == "SHORT":
            fill_entry = order.entry - slip
        else:
            fill_entry = order.entry

        self._position = Position(
            instrument=order.instrument,
            direction=order.direction,
            entry_price=fill_entry,
            stop=order.stop,
            target=order.target,
            quantity=contracts,
            open=True,
        )

        # Phase 1: return OPEN fill — caller resolves with next bar
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=contracts,
            entry_price=fill_entry,
            exit_price=None,
            exit_reason=None,
            result="OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    def _entry_not_filled(self, order: BracketOrder, contracts: int) -> Fill:
        """The IOC entry self-cancelled: no position, no P&L, booked the way the
        live box books it (CANCELLED / ENTRY_NOT_FILLED) so daily-state
        reconstruction and fill-realism audits treat replay and live the same."""
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=contracts,
            entry_price=order.entry,
            exit_price=None,
            exit_reason="ENTRY_NOT_FILLED",
            result="CANCELLED",
            pnl_ticks=0.0,
            pnl_dollars=0.0,
        )

    def _cancel_pending_entry(self, reason: str = "ENTRY_NOT_TRIGGERED") -> Fill:
        pending = self._pending_stop_entry
        if pending is None:
            raise RuntimeError("PaperBroker: no pending stop-market entry to cancel.")
        self._pending_stop_entry = None
        return Fill(
            instrument=pending.order.instrument,
            direction=pending.order.direction,
            contracts=pending.contracts,
            entry_price=pending.order.entry,
            exit_price=None,
            exit_reason=reason,
            result="CANCELLED",
            pnl_ticks=0.0,
            pnl_dollars=0.0,
        )

    def _activate_pending_stop_entry(
        self, next_bar: NextBarOHLC, tick: float
    ) -> Optional[Fill]:
        """One-next-bar stop-market entry.

        Missing next-bar open fails closed so replay/paper never upgrades a
        signal into an assumed fill without the causal data needed to price a
        gap through the trigger.
        """
        pending = self._pending_stop_entry
        if pending is None:
            return None
        if next_bar.open is None:
            return self._cancel_pending_entry("ENTRY_OPEN_UNAVAILABLE")

        order = pending.order
        slip = self._slippage_ticks * tick
        fill_entry: float | None = None
        if order.direction == "LONG":
            if next_bar.open >= order.entry:
                fill_entry = next_bar.open + slip
            elif next_bar.high >= order.entry:
                fill_entry = order.entry + slip
        elif order.direction == "SHORT":
            if next_bar.open <= order.entry:
                fill_entry = next_bar.open - slip
            elif next_bar.low <= order.entry:
                fill_entry = order.entry - slip
        else:
            fill_entry = next_bar.open

        if fill_entry is None:
            return self._cancel_pending_entry("ENTRY_NOT_TRIGGERED")
        if order.direction == "LONG" and not (order.stop < fill_entry < order.target):
            return self._cancel_pending_entry("ENTRY_BRACKET_INVALID_AT_FILL")
        if order.direction == "SHORT" and not (order.target < fill_entry < order.stop):
            return self._cancel_pending_entry("ENTRY_BRACKET_INVALID_AT_FILL")

        self._pending_stop_entry = None
        self._position = Position(
            instrument=order.instrument,
            direction=order.direction,
            entry_price=fill_entry,
            stop=order.stop,
            target=order.target,
            quantity=pending.contracts,
            open=True,
        )
        return None

    def _resolve_runner(self, next_bar, pos, tick, tick_val) -> Optional[Fill]:
        """Runner exit: no fixed target; once +activation_R is reached, trail the
        stop trail_R behind the running favourable extreme. Favourable tracking
        uses only PRIOR bars (no intra-bar look-ahead); a straddle still exits on
        the trailed stop (worst case). 1-contract only."""
        is_long = pos.direction == "LONG"
        if self._runner_max_fav is None:
            self._runner_max_fav = pos.entry_price
        slip = self._slippage_ticks * tick

        active_stop, trailing = compute_trailed_stop(
            is_long=is_long, entry=pos.entry_price, original_stop=pos.stop,
            max_favorable=self._runner_max_fav,
            activation_r=self._runner_activation_r, trail_r=self._runner_trail_r,
        )

        stop_hit = (next_bar.low <= active_stop) if is_long else (next_bar.high >= active_stop)
        if stop_hit:
            exit_price = (active_stop - slip) if is_long else (active_stop + slip)
            pnl_ticks = ((exit_price - pos.entry_price) if is_long
                         else (pos.entry_price - exit_price)) / tick
            pnl_dollars = pnl_ticks * tick_val * pos.quantity
            self._balance += pnl_dollars
            self._position = None
            self._runner_max_fav = None
            result = "WIN" if pnl_dollars > 0 else ("BREAKEVEN" if pnl_dollars == 0 else "LOSS")
            return Fill(
                instrument=pos.instrument, direction=pos.direction, contracts=pos.quantity,
                entry_price=pos.entry_price, exit_price=round(exit_price, 4),
                exit_reason=("RUNNER_TRAIL" if trailing else "STOP_HIT"), result=result,
                pnl_ticks=round(pnl_ticks, 2), pnl_dollars=round(pnl_dollars, 2),
            )

        # update favourable extreme AFTER the exit check
        cur = next_bar.high if is_long else next_bar.low
        self._runner_max_fav = (max(self._runner_max_fav, cur) if is_long
                                else min(self._runner_max_fav, cur))
        return None

    def resolve_position(self, next_bar: NextBarOHLC) -> Optional[Fill]:
        """
        Attempt to resolve an open paper position using next-bar OHLC data.

        Returns:
            Fill with WIN/LOSS/BREAKEVEN result, or None if position not yet resolved.

        Logic:
            Long: target hit if next_bar.high >= target, stop hit if next_bar.low <= stop
            Short: target hit if next_bar.low <= target, stop hit if next_bar.high >= stop
            Both hit in one bar: resolved as STOP when pessimistic_both_hit=True,
            else target-priority (legacy optimistic).

        1-contract only — breakeven-at-1R:
            When 1R is reached without hitting target, the stop is moved to entry.
            If subsequently stopped at entry, result is BREAKEVEN.

        Multi-contract positions hold the full position until target or stop is hit.
        No partial exits, no 1R management for 2+ contracts.
        """
        instrument = (
            self._position.instrument
            if self._position is not None
            else self._pending_stop_entry.order.instrument
            if self._pending_stop_entry is not None
            else ""
        )
        tick = TICK_SIZE.get(instrument, 0.25)
        tick_val = TICK_VALUE.get(instrument, 1.0)

        pending_result = self._activate_pending_stop_entry(next_bar, tick)
        if pending_result is not None:
            return pending_result

        if self._position is None or not self._position.open:
            return None

        pos = self._position
        instrument = pos.instrument

        if self._runner_mode and pos.quantity == 1:
            return self._resolve_runner(next_bar, pos, tick, tick_val)

        target_hit = False
        stop_hit = False
        breakeven_hit = False

        if pos.direction == "LONG":
            initial_risk = pos.entry_price - pos.stop
            one_r = pos.entry_price + initial_risk if initial_risk > 0 else None
            target_hit = next_bar.high >= pos.target
            breakeven_hit = one_r is not None and next_bar.high >= one_r
            breakeven_active = (breakeven_hit or pos.stop == pos.entry_price) and pos.quantity == 1 and self._breakeven_at_1r
            active_stop = pos.entry_price if breakeven_active else pos.stop
            breakeven_stop_active = breakeven_active
            stop_hit = next_bar.low <= active_stop
        elif pos.direction == "SHORT":
            initial_risk = pos.stop - pos.entry_price
            one_r = pos.entry_price - initial_risk if initial_risk > 0 else None
            target_hit = next_bar.low <= pos.target
            breakeven_hit = one_r is not None and next_bar.low <= one_r
            breakeven_active = (breakeven_hit or pos.stop == pos.entry_price) and pos.quantity == 1 and self._breakeven_at_1r
            active_stop = pos.entry_price if breakeven_active else pos.stop
            breakeven_stop_active = breakeven_active
            stop_hit = next_bar.high >= active_stop
        else:
            return None

        # Did the bar trade through the ORIGINAL (ordered) stop — not the
        # breakeven-trailed stop? A straddle-bar worst case must use this, since
        # we cannot assume price reached 1R (and trailed the stop to entry)
        # before it reached the stop.
        if pos.direction == "LONG":
            original_stop_hit = next_bar.low <= pos.stop
        else:
            original_stop_hit = next_bar.high >= pos.stop

        slip = self._slippage_ticks * tick

        # When a single bar straddles BOTH the original stop and the target,
        # intrabar order is unknowable. pessimistic_both_hit=True resolves it as
        # a full stop loss (worst case), bypassing the breakeven trail; False
        # keeps the legacy optimistic target-priority.
        if target_hit and original_stop_hit and self._pessimistic_both_hit:
            exit_price = (pos.stop - slip) if pos.direction == "LONG" else (pos.stop + slip)
            exit_reason = "STOP_HIT"
            result = "LOSS"
        elif target_hit:
            # Target is a resting LIMIT order — fills clean at the target price.
            exit_price = pos.target
            exit_reason = "TARGET_HIT"
            result = "WIN"
        elif stop_hit:
            # Stop is a MARKET order — apply adverse slippage past the stop level.
            if pos.direction == "LONG":
                exit_price = active_stop - slip
            else:
                exit_price = active_stop + slip
            exit_reason = "BREAKEVEN_STOP" if breakeven_stop_active else "STOP_HIT"
            result = "BREAKEVEN" if breakeven_stop_active else "LOSS"
        else:
            # 1-contract only: move stop to breakeven when 1R is reached
            if breakeven_hit and pos.quantity == 1 and self._breakeven_at_1r:
                pos.stop = pos.entry_price
            # Position still open — no resolution yet
            return None

        # Calculate P&L in ticks and dollars
        if pos.direction == "LONG":
            pnl_ticks = (exit_price - pos.entry_price) / tick
        else:
            pnl_ticks = (pos.entry_price - exit_price) / tick

        pnl_dollars = pnl_ticks * tick_val * pos.quantity

        self._balance += pnl_dollars
        self._position = None  # Flat

        return Fill(
            instrument=instrument,
            direction=pos.direction,
            contracts=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=round(exit_price, 4),
            exit_reason=exit_reason,
            result=result,
            pnl_ticks=round(pnl_ticks, 2),
            pnl_dollars=round(pnl_dollars, 2),
        )

    def restore_position(
        self,
        instrument: str,
        direction: str,
        entry: float,
        stop: float,
        target: float,
        contracts: int = 1,
    ) -> None:
        """
        Rebuild internal position state from a persisted journal entry.
        Used by the webhook runner to carry open positions across process
        restarts — the broker is stateless between HTTP requests, so the
        journal is the source of truth.
        """
        if self._position is not None and self._position.open:
            raise RuntimeError(
                "PaperBroker.restore_position: a position is already loaded."
            )
        self._position = Position(
            instrument=instrument,
            direction=direction,
            entry_price=entry,
            stop=stop,
            target=target,
            quantity=max(1, int(contracts or 1)),
            open=True,
        )

    def get_position(self) -> Optional[Position]:
        return self._position if (self._position and self._position.open) else None

    def has_pending_entry(self) -> bool:
        return self._pending_stop_entry is not None

    def cancel_pending_entry(self, reason: str = "ENTRY_NOT_TRIGGERED") -> Optional[Fill]:
        if self._pending_stop_entry is None:
            return None
        return self._cancel_pending_entry(reason)

    def get_account_balance(self) -> Optional[float]:
        return round(self._balance, 2)

    def cancel_all(self) -> None:
        """Cancel (flatten) any open paper position without P&L."""
        if self._position:
            self._position.open = False
            self._position = None
        self._pending_stop_entry = None

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

        pnl_dollars = pnl_ticks * tick_val * pos.quantity
        self._balance += pnl_dollars
        self._position = None

        return Fill(
            instrument=instrument,
            direction=pos.direction,
            contracts=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=round(exit_price, 4),
            exit_reason="MANUAL_CANCEL",
            result=result,
            pnl_ticks=round(pnl_ticks, 2),
            pnl_dollars=round(pnl_dollars, 2),
        )
