"""stocks_advisory/paper_simulator.py

Forward lifecycle simulator for the TQQQ/SQQQ Paper Advisory Bot v1
paper-proof harness. A `tqqq_sqqq_decision.evaluate_tqqq_sqqq_decision()`
verdict of TAKE_PAPER is only a plan -- this module is what decides,
using later completed bars (never the same bar the plan was formed on),
whether that plan actually becomes a filled paper position, and then
tracks it through to an exit. Nothing here places, prepares, or queues
an order; nothing here imports a broker, `execution/`, `futures`, or
`options_manager` module of any kind.

No-lookahead rule: a plan can become ACTIVE only at a vehicle bar
strictly AFTER the bar the decision was evaluated on. Entry fills at
that next vehicle bar's OPEN; a stop exit fills at the vehicle bar's
CLOSE that confirms the QQQ VWAP breach (the same "closes back
through VWAP" condition `tqqq_sqqq_decision.py`'s own `invalidation`
field already describes). No target-based exit is reachable through
v1's real decision output (`target_1`/`target_2` are always `None`
there) -- the target-handling branch below exists only so a same-bar
stop/target ambiguity has an explicit, tested, conservative rule ready
if a later increment ever populates a target.

Friction: the locked Robinhood real cost model -- identical constants
and formula to `scripts/stocks_advisory_robustness_audit.py`'s
`robinhood_regulatory_fee_dollars()`, duplicated here (that script is a
one-time research tool, not an importable library) rather than
imported, so both stay independently reviewable while representing the
exact same assumption. $0 commission; the only per-trade cost is the
SEC Section 31 fee + FINRA TAF, both charged on the SELL leg only
(every trade here is buy-to-open/sell-to-close, never a short sale).
Not tunable by this module -- these are fixed, locked constants.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Optional, Sequence

from .backtest_models import Bar

SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS = 0.0000080
FINRA_TAF_RATE_PER_SHARE_SOLD = 0.000166
FINRA_TAF_MAX_PER_TRADE_DOLLARS = 8.30

DEFAULT_POSITION_DOLLAR_SIZE = 1000.0
"""Matches `BacktestConfig.position_dollar_size`'s default in
`backtest_models.py` -- the same locked per-trade paper size shared
with the backtest lane, not independently tuned here."""

_OPEN_STATUSES = ("watching", "active")
_LONG_TQQQ = "long_tqqq"
_LONG_SQQQ = "long_sqqq"


def _robinhood_regulatory_fee_dollars(shares_sold: float, sell_proceeds_dollars: float) -> float:
    """Mirrors scripts/stocks_advisory_robustness_audit.py's
    robinhood_regulatory_fee_dollars() exactly."""
    sec_fee = max(0.0, sell_proceeds_dollars) * SEC_FEE_RATE_PER_DOLLAR_OF_SELL_PROCEEDS
    taf = min(max(0.0, shares_sold) * FINRA_TAF_RATE_PER_SHARE_SOLD, FINRA_TAF_MAX_PER_TRADE_DOLLARS)
    return sec_fee + taf


@dataclass(frozen=True, kw_only=True)
class LifecycleState:
    """One paper trade's simulated lifecycle state. `direction` and
    `stop_price_qqq` are read directly off the `PaperTradeRecord` the
    decision engine produced (`stop_price` there is a QQQ price -- the
    VWAP level whose breach both invalidates a WATCHING plan and stops
    out an ACTIVE one). `target_1` is accepted only so the same-bar
    stop/target ambiguity rule below has something to exercise; v1's
    real decision output never sets it."""

    trade_date: str
    direction: str  # "long_tqqq" | "long_sqqq"
    vehicle_symbol: str
    stop_price_qqq: float
    status: str  # "watching" | "active" | "exited" | "invalidated" | "expired"
    target_1: Optional[float] = None
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    exit_reason: str = ""
    shares: Optional[float] = None
    gross_pnl_dollars: Optional[float] = None
    friction_dollars: Optional[float] = None
    net_pnl_dollars: Optional[float] = None
    notes: str = ""


@dataclass(frozen=True, kw_only=True)
class LifecycleAdvanceResult:
    ok: bool
    state: Optional[LifecycleState] = None
    reject_reason: str = ""


def _invalidated_or_stopped(direction: str, qqq_close: float, stop_price_qqq: float) -> bool:
    if direction == _LONG_TQQQ:
        return qqq_close <= stop_price_qqq
    if direction == _LONG_SQQQ:
        return qqq_close >= stop_price_qqq
    return False


def _resolve_exit(
    state: LifecycleState, *, exit_price: float, exit_time: str, exit_reason: str, position_dollar_size: float
) -> LifecycleState:
    shares = state.shares
    if shares is None:
        shares = position_dollar_size / state.entry_price if state.entry_price else 0.0
    gross = shares * (exit_price - state.entry_price) if state.entry_price is not None else 0.0
    sell_proceeds = shares * exit_price
    friction = _robinhood_regulatory_fee_dollars(shares_sold=shares, sell_proceeds_dollars=sell_proceeds)
    net = gross - friction
    return dataclasses.replace(
        state,
        status="exited",
        exit_price=exit_price,
        exit_time=exit_time,
        exit_reason=exit_reason,
        shares=shares,
        gross_pnl_dollars=gross,
        friction_dollars=friction,
        net_pnl_dollars=net,
    )


def advance_lifecycle(
    state: LifecycleState,
    *,
    qqq_bars: Sequence[Bar],
    vehicle_bars: Sequence[Bar],
    session_closed: bool,
    position_dollar_size: float = DEFAULT_POSITION_DOLLAR_SIZE,
) -> LifecycleAdvanceResult:
    """Advances one trade's lifecycle using bars that occurred strictly
    after the bar its decision was evaluated on (or after the last bar
    already consumed by a previous call). `qqq_bars`/`vehicle_bars`
    must be the same length and index-aligned by timestamp -- the
    caller's responsibility, same convention `DaySession` already
    establishes. `session_closed=True` means these are the last bars
    available for this trade's own session; a still-open position gets
    force-resolved (no overnight hold) rather than silently carried as
    if it could still trigger tomorrow.

    Fails closed (ok=False, state=None) on:

    - mismatched qqq_bars/vehicle_bars length
    - any non-positive OHLC in a supplied bar
    - a WATCHING/ACTIVE state whose `direction` is not `long_tqqq` or
      `long_sqqq` (should never happen upstream -- NO_TRADE records
      never reach WATCHING/ACTIVE -- but this module never assumes it)

    A record whose status is already EXITED/INVALIDATED/EXPIRED (or
    NO_TRADE) is returned unchanged with ok=True -- resolved trades are
    never re-touched.
    """
    if len(qqq_bars) != len(vehicle_bars):
        return LifecycleAdvanceResult(ok=False, reject_reason="qqq_bars and vehicle_bars are not the same length")

    for bar in list(qqq_bars) + list(vehicle_bars):
        if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
            return LifecycleAdvanceResult(ok=False, reject_reason=f"non-positive OHLC in bar at {bar.timestamp}")

    if state.status not in _OPEN_STATUSES:
        return LifecycleAdvanceResult(ok=True, state=state)

    if state.direction not in (_LONG_TQQQ, _LONG_SQQQ):
        return LifecycleAdvanceResult(
            ok=False, reject_reason=f"cannot advance an open position with direction={state.direction!r}"
        )

    current = state
    for qqq_bar, vehicle_bar in zip(qqq_bars, vehicle_bars):
        if current.status == "watching":
            if _invalidated_or_stopped(current.direction, qqq_bar.close, current.stop_price_qqq):
                current = dataclasses.replace(
                    current,
                    status="invalidated",
                    exit_reason="QQQ closed back through VWAP before entry confirmed",
                    gross_pnl_dollars=0.0,
                    friction_dollars=0.0,
                    net_pnl_dollars=0.0,
                )
                break
            current = dataclasses.replace(
                current,
                status="active",
                entry_price=vehicle_bar.open,
                entry_time=vehicle_bar.timestamp,
                shares=position_dollar_size / vehicle_bar.open,
            )
            continue

        if current.status == "active":
            stop_hit = _invalidated_or_stopped(current.direction, qqq_bar.close, current.stop_price_qqq)
            target_hit = current.target_1 is not None and vehicle_bar.high >= current.target_1
            if stop_hit and target_hit:
                current = _resolve_exit(
                    current,
                    exit_price=vehicle_bar.close,
                    exit_time=vehicle_bar.timestamp,
                    exit_reason="stop hit (same-bar stop+target ambiguity resolved conservatively as stop)",
                    position_dollar_size=position_dollar_size,
                )
                break
            if stop_hit:
                current = _resolve_exit(
                    current,
                    exit_price=vehicle_bar.close,
                    exit_time=vehicle_bar.timestamp,
                    exit_reason="stop hit: QQQ closed back through VWAP",
                    position_dollar_size=position_dollar_size,
                )
                break
            if target_hit:
                current = _resolve_exit(
                    current,
                    exit_price=current.target_1,
                    exit_time=vehicle_bar.timestamp,
                    exit_reason="target hit",
                    position_dollar_size=position_dollar_size,
                )
                break

    if session_closed and current.status == "watching":
        current = dataclasses.replace(
            current,
            status="expired",
            exit_reason="session ended before entry confirmed or invalidated",
            gross_pnl_dollars=0.0,
            friction_dollars=0.0,
            net_pnl_dollars=0.0,
        )
    elif session_closed and current.status == "active" and vehicle_bars:
        last_qqq, last_vehicle = qqq_bars[-1], vehicle_bars[-1]
        current = _resolve_exit(
            current,
            exit_price=last_vehicle.close,
            exit_time=last_vehicle.timestamp,
            exit_reason="forced session-end exit (no overnight hold)",
            position_dollar_size=position_dollar_size,
        )

    return LifecycleAdvanceResult(ok=True, state=current)
