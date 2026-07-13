"""stocks_advisory/tqqq_sqqq_backtest_v2.py

QQQ -> TQQQ/SQQQ two-lane backtest -- Stock/ETF Strategy v2.0
(operator-selected parameters, not yet validated). Adds a second,
independent entry lane on top of the unchanged v1 paper-harness
decision engine, so the strategy can also recognize sustained
continuation after the first hour (failed reclaims, lower-high/
higher-low structure, midday breakdowns/breakouts) rather than only
the opening first hour.

Lane 1 is the REAL, LIVE paper-advisory decision engine -- not a
look-alike. It is built by calling, unmodified, exactly the same
functions `paper_runner.run_paper_session()` calls: `qqq_signal_builder
.build_qqq_signal()` (with the identical "opening range + 1
confirmation bar" decision-cutoff slicing) -> `tqqq_sqqq_decision
.evaluate_tqqq_sqqq_decision()` -> `paper_simulator.advance_lifecycle()`
for lifecycle resolution against the day's remaining bars. (An earlier
draft of this module built Lane 1 on `tqqq_sqqq_backtest.evaluate_day()`
instead -- a separate, independently-implemented research/backtest
module that happens to encode a similarly-shaped idea with different
thresholds and units. That draft was corrected before being tested
against real data, because it silently was NOT the engine that produced
the actual 2026-07-10/2026-07-13 paper-proof records. This module
calls the real engine so Lane 1's results are provably identical to
what the live harness already produced.)

Nothing here is a broker, execution, futures, or options_manager
import; no live execution, no paper execution loop, no scheduler, no
API endpoint, no order of any kind, no system-clock read -- every
timestamp comparison is against a caller-supplied bar timestamp.

Lane interaction (operator-specified):
- Lane 1 is evaluated first. If it produces a real TAKE_PAPER trade,
  that trade wins for the day and Lane 2 is never evaluated.
- If Lane 1 is a legitimate NO_TRADE read (not a data-missing skip),
  Lane 2 evaluates completed bars from 11:00 through 15:00 ET.
- At most one position per day, from whichever lane produces it first.
- No averaging, re-entry, or second attempt after an invalidated/closed
  Lane 2 position.

Lane 2 rules (operator-selected v2.0 parameters -- not proven; this
module exists to backtest them, not to assert they work):

1. Eligibility window: 11:00-15:00 ET. Per the operator's own stated
   reason ("Lane 2 remains clearly separate from the opening-range
   setup"), this module restricts ALL of Lane 2's own evaluation --
   pivot candidacy, trend-reference check, entry break -- to bars in
   this window, not just the final entry trigger. A pivot candidate's
   two comparison bars may fall just before 11:00 (needed for the very
   first eligible bars to have any left-side context at all) -- an
   implementation default, not literal spec text.
2. Trend reference: QQQ close below (bearish) or above (bullish)
   session VWAP AND below/above the first-hour midpoint (a separate
   directional filter, not the trend reference itself).
3. Confirmed lower-high (bearish) / higher-low (bullish): causal,
   left-side-only pivot detection -- see `_pivot_candidates()` /
   `_confirm_pivots_through()`. No bar after the candidate bar is ever
   consulted to CREATE a candidate, only to confirm one already
   proposed, which happens no earlier than the confirming bar itself
   is "current" in the forward walk -- this is what makes the whole
   scan causal.
4. Entry break: a completed bar's CLOSE (never its intrabar low/high)
   below/above the immediately preceding completed bar's low/high.
5. Room to target: at least 0.30% QQQ distance from the entry-trigger
   close to the CAUSAL running session low/high so far (never the
   eventual full-day low/high -- that would be lookahead).
6. Vertical-extension filter: the traded vehicle's own close must sit
   within 1.25% of its own session VWAP. If every other Lane 2
   condition holds at some bar but only this one blocks it, the day's
   final reason (if Lane 2 never triggers) is reported as
   `NO_TRADE: CONTINUATION_EXTENDED` rather than a generic no-signal
   reason.
7. Invalidation: a single completed QQQ close above the latest
   confirmed lower-high pivot OR above session VWAP (bearish; inverted
   for bullish) -- no two-bar confirmation required.

Position management: BOTH lanes are backtested under the identical,
real friction/sizing model already locked for the live harness --
`paper_simulator.MODELED_SLIPPAGE_PERCENT_PER_SIDE` (0.15% per side),
`paper_simulator._robinhood_regulatory_fee_dollars()` (SEC + FINRA
fees), and floor-share sizing (`floor(position_dollar_size /
raw_entry_price)`, minimum 1 share) -- reused directly, not
reimplemented, so a day where Lane 2 fires is priced exactly as it
would be if it were live. Lane 1's resolution reuses
`paper_simulator.advance_lifecycle()` outright (same bar-walk, same
same-bar-stop-wins tie-break, same force-EXPIRE-at-day-end rule).
Lane 2's resolution (`_resolve_lane2_trade()`) mirrors that same
friction math for its own QQQ-side stop/target rules (implementation
defaults where the operator's spec is mechanical-but-silent, flagged
below):
- Invalidation is a QQQ-side signal (checked on QQQ's own close, not a
  static vehicle-side price level), so resolution walks QQQ's and the
  vehicle's index-aligned bars together. Once QQQ's bar `j` close
  confirms invalidation, the position exits at that SAME index's
  vehicle bar's (slippage-adjusted) close -- no extra lag.
- Target 1 (QQQ session low/high) is translated to a vehicle-side RAW
  price once at entry using the same QQQ-side-distance,
  `LEVERAGED_ETF_FACTOR`-scaled translation
  `tqqq_sqqq_backtest._compute_target_price()`'s `PRIOR_HIGH_LOW` model
  already uses, then checked against the vehicle's own raw bar high
  each bar; the actual fill still slips against that raw level.
- Same-bar stop-and-target: invalidation checked before target every
  bar (stop wins), matching `advance_lifecycle()`'s own tie-break.
- Time exit: `V2Config.exit_cutoff_time` (default "15:55") -- Lane 2's
  own construction, since the real harness's `advance_lifecycle()` has
  no separate early-cutoff concept (it force-resolves only at the
  actual end of the supplied bars).
- No Target 2, no averaging, no re-entry -- the operator's spec is
  explicit and there is nothing further to implement.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime, time as dt_time
from typing import Optional, Sequence

from .backtest_models import Bar, BacktestTradeResult, DaySession, LEVERAGED_ETF_FACTOR, SkippedDay, TradeDirection
from .paper_simulator import (
    DEFAULT_POSITION_DOLLAR_SIZE,
    MODELED_SLIPPAGE_PERCENT_PER_SIDE,
    LifecycleState,
    _robinhood_regulatory_fee_dollars,
    advance_lifecycle,
)
from .qqq_signal_builder import _opening_range_bars, build_qqq_signal
from .tqqq_sqqq_backtest import summarize_trades
from .tqqq_sqqq_decision import evaluate_tqqq_sqqq_decision
from .tqqq_sqqq_models import TqqqSqqqDirection, TqqqSqqqVerdict

STRATEGY_VERSION = "tqqq_sqqq_decision_v2"

LANE2_ELIGIBILITY_START = dt_time(11, 0)
LANE2_ELIGIBILITY_END = dt_time(15, 0)
"""No NEW Lane 2 entries are considered outside this window. An
already-open Lane 2 position still manages (invalidation/target/time
exit) using the day's full remaining bars, per
`V2Config.exit_cutoff_time`."""

PIVOT_LOOKBACK_BARS = 2
""""Previous two completed bars", per the operator's exact spec."""

MIN_ROOM_TO_TARGET_PERCENT = 0.30
MAX_VEHICLE_VWAP_EXTENSION_PERCENT = 1.25

CONTINUATION_EXTENDED_REASON = "NO_TRADE: CONTINUATION_EXTENDED"


@dataclasses.dataclass(frozen=True, kw_only=True)
class V2Config:
    """Lane 1's thresholds are the ACTUAL frozen paper-harness values
    (see `data/stocks_advisory_paper_proof/PROOF_MANIFEST.md`) -- not a
    new invention. Lane 2's own thresholds are module-level constants
    above, not config fields, since the operator specified them as
    fixed v2.0 parameters to backtest, not knobs to sweep."""

    allowed_max_gap_percent: float
    allowed_min_first_hour_range: float
    allowed_max_first_hour_range: float
    position_dollar_size: float = DEFAULT_POSITION_DOLLAR_SIZE
    exit_cutoff_time: str = "15:55"


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _bar_time(bar: Bar) -> dt_time:
    return _parse_ts(bar.timestamp).time()


def _intraday_vwap_series(bars: Sequence[Bar]) -> list[float]:
    """Mirrors `tqqq_sqqq_backtest._intraday_vwap_series()` exactly --
    cumulative, causal VWAP; entry i uses only bars[0..i]. Duplicated
    rather than imported, per this repo's established cross-lane-math
    convention (see `qqq_signal_builder.py`)."""
    vwaps: list[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_vol += bar.volume
        vwaps.append(cum_pv / cum_vol if cum_vol > 0 else typical)
    return vwaps


def _running_extreme(bars: Sequence[Bar], *, low: bool) -> list[float]:
    """Causal running min(low) or max(high) through bar i inclusive --
    never the eventual full-day extreme, which would be lookahead."""
    out: list[float] = []
    current: Optional[float] = None
    for bar in bars:
        value = bar.low if low else bar.high
        current = value if current is None else (min(current, value) if low else max(current, value))
        out.append(current)
    return out


@dataclasses.dataclass(frozen=True, kw_only=True)
class _ConfirmedPivot:
    index: int
    price: float


def _pivot_candidates(bars: Sequence[Bar], *, eligible_from_index: int, high: bool) -> list[int]:
    """Indices of causal, left-side-only pivot-high (`high=True`) or
    pivot-low (`high=False`) CANDIDATES. A candidate at index i (i>=2)
    compares only to bars[i-1] and bars[i-2] -- no bar after i is ever
    consulted to create a candidate. `eligible_from_index` restricts
    which indices may become candidates (Lane 2's 11:00 eligibility
    window); the two comparison bars may fall before that index."""
    candidates: list[int] = []
    for i in range(max(PIVOT_LOOKBACK_BARS, eligible_from_index), len(bars)):
        if high:
            if bars[i].high > bars[i - 1].high and bars[i].high > bars[i - 2].high:
                candidates.append(i)
        else:
            if bars[i].low < bars[i - 1].low and bars[i].low < bars[i - 2].low:
                candidates.append(i)
    return candidates


def _confirm_pivots_through(
    bars: Sequence[Bar],
    candidate_indices: Sequence[int],
    *,
    through_index: int,
    high: bool,
) -> list[_ConfirmedPivot]:
    """Confirmed pivots (in confirmation order) using only
    bars[0..through_index] -- a candidate at index c is confirmed at
    the first index j (c < j <= through_index) where bars[j].close is
    below bars[c].low (pivot-high) or above bars[c].high (pivot-low).
    Never looks past `through_index`, which is what makes a scan that
    calls this once per bar (with through_index == that bar's own
    index) strictly causal."""
    confirmed: list[_ConfirmedPivot] = []
    for c in candidate_indices:
        if c > through_index:
            continue
        pivot_bar = bars[c]
        for j in range(c + 1, through_index + 1):
            if high and bars[j].close < pivot_bar.low:
                confirmed.append(_ConfirmedPivot(index=c, price=pivot_bar.high))
                break
            if not high and bars[j].close > pivot_bar.high:
                confirmed.append(_ConfirmedPivot(index=c, price=pivot_bar.low))
                break
    confirmed.sort(key=lambda p: p.index)
    return confirmed


def _is_confirmed_lower_high(
    confirmed_highs: Sequence[_ConfirmedPivot], vwap: Sequence[float]
) -> bool:
    if len(confirmed_highs) < 2:
        return False
    previous, latest = confirmed_highs[-2], confirmed_highs[-1]
    both_below_vwap = latest.price < vwap[latest.index] and previous.price < vwap[previous.index]
    return both_below_vwap and latest.price < previous.price


def _is_confirmed_higher_low(
    confirmed_lows: Sequence[_ConfirmedPivot], vwap: Sequence[float]
) -> bool:
    if len(confirmed_lows) < 2:
        return False
    previous, latest = confirmed_lows[-2], confirmed_lows[-1]
    both_above_vwap = latest.price > vwap[latest.index] and previous.price > vwap[previous.index]
    return both_above_vwap and latest.price > previous.price


@dataclasses.dataclass(frozen=True, kw_only=True)
class V2TradeResult(BacktestTradeResult):
    """`BacktestTradeResult` plus which lane produced it, so
    `summarize_trades()` (unmodified) keeps working on a sequence of
    these, while callers can filter by `.lane` for the separate Lane 1
    / Lane 2 reports the validation requirement calls for."""

    lane: str = ""
    strategy_version: str = STRATEGY_VERSION


def _no_trade_v2(*, lane: str, date: str, reason: str) -> V2TradeResult:
    return V2TradeResult(
        lane=lane,
        trade_date=date,
        vehicle_symbol="",
        direction=TradeDirection.NO_TRADE,
        exit_reason=reason,
        skipped=True,
        skipped_reason=reason,
    )


# --- Lane 1: the real, unmodified paper-harness decision + resolution ---------------------------------------


def _evaluate_lane1(day: DaySession, config: V2Config) -> V2TradeResult | SkippedDay:
    """Calls `qqq_signal_builder.build_qqq_signal()` ->
    `tqqq_sqqq_decision.evaluate_tqqq_sqqq_decision()` ->
    `paper_simulator.advance_lifecycle()`, exactly mirroring
    `paper_runner.run_paper_session()`'s own bar-slicing and resolution
    -- this IS the real, live v1 engine, not a look-alike."""
    opening_range = _opening_range_bars(day.qqq_bars)
    if not opening_range or len(day.qqq_bars) <= len(opening_range):
        return SkippedDay(date=day.date, reason="first trading hour has not closed yet")

    decision_cutoff = len(opening_range) + 1
    decision_qqq_bars = day.qqq_bars[:decision_cutoff]

    build_result = build_qqq_signal(
        date=day.date,
        qqq_bars_today=decision_qqq_bars,
        qqq_previous_day_close=day.qqq_previous_close,
        qqq_previous_day_high=day.qqq_previous_high,
        qqq_previous_day_low=day.qqq_previous_low,
        qqq_relative_volume=day.qqq_relative_volume if day.qqq_relative_volume is not None else 1.0,
        allowed_max_gap_percent=config.allowed_max_gap_percent,
        allowed_min_first_hour_range=config.allowed_min_first_hour_range,
        allowed_max_first_hour_range=config.allowed_max_first_hour_range,
    )
    if not build_result.ok or build_result.signal is None:
        return SkippedDay(date=day.date, reason=build_result.reject_reason)

    decision_result = evaluate_tqqq_sqqq_decision(build_result.signal)
    if decision_result.verdict == TqqqSqqqVerdict.INVALID or decision_result.trade is None:
        reason = "; ".join(decision_result.blocking_reasons) or "invalid"
        return SkippedDay(date=day.date, reason=reason)

    trade = decision_result.trade
    if decision_result.verdict != TqqqSqqqVerdict.TAKE_PAPER:
        return _no_trade_v2(lane="lane1", date=day.date, reason=trade.reason)

    remaining_qqq_bars = day.qqq_bars[decision_cutoff:]
    vehicle_bars_full = day.tqqq_bars if trade.direction == TqqqSqqqDirection.LONG_TQQQ else day.sqqq_bars
    remaining_vehicle_bars = (
        vehicle_bars_full[decision_cutoff:] if len(vehicle_bars_full) > decision_cutoff else ()
    )

    lifecycle_state = LifecycleState(
        trade_date=day.date,
        direction=trade.direction.value,
        vehicle_symbol=trade.vehicle_symbol,
        stop_price_qqq=trade.stop_price if trade.stop_price is not None else 0.0,
        status="watching",
        target_1=trade.target_1,
    )
    advanced = advance_lifecycle(
        lifecycle_state,
        qqq_bars=remaining_qqq_bars,
        vehicle_bars=remaining_vehicle_bars,
        session_closed=True,
        position_dollar_size=config.position_dollar_size,
    )
    if not advanced.ok or advanced.state is None:
        return SkippedDay(date=day.date, reason=f"lane1 lifecycle resolution failed: {advanced.reject_reason}")

    state = advanced.state
    direction = (
        TradeDirection.LONG_TQQQ if trade.direction == TqqqSqqqDirection.LONG_TQQQ else TradeDirection.LONG_SQQQ
    )
    percent_result = (
        (state.exit_price - state.entry_price) / state.entry_price * 100.0
        if state.entry_price and state.exit_price
        else None
    )
    return V2TradeResult(
        lane="lane1",
        trade_date=day.date,
        vehicle_symbol=trade.vehicle_symbol,
        direction=direction,
        entry_time=state.entry_time,
        entry_price=state.entry_price,
        stop_price=trade.stop_price,
        target_price=None,  # v1's decision engine never sets a target
        exit_time=state.exit_time,
        exit_price=state.exit_price,
        exit_reason=state.exit_reason,
        dollar_result=state.net_pnl_dollars,
        percent_result=percent_result,
    )


# --- Lane 2: the new operator-specified continuation lane ----------------------------------------------------


def _lane2_entry_gate(
    *,
    index: int,
    qqq_bars: Sequence[Bar],
    vehicle_bars: Sequence[Bar],
    qqq_vwap: Sequence[float],
    vehicle_vwap: Sequence[float],
    running_low: Sequence[float],
    running_high: Sequence[float],
    first_hour_midpoint: float,
    confirmed_pivots: Sequence[_ConfirmedPivot],
    direction: TradeDirection,
) -> tuple[bool, bool]:
    """Returns (all_conditions_pass, blocked_only_by_extension) for one
    candidate index."""
    bearish = direction == TradeDirection.LONG_SQQQ
    qqq_close = qqq_bars[index].close

    if bearish:
        trend_ok = qqq_close < qqq_vwap[index] and qqq_close < first_hour_midpoint
        pivot_ok = _is_confirmed_lower_high(confirmed_pivots, qqq_vwap)
        entry_break_ok = qqq_close < qqq_bars[index - 1].low
        room = (qqq_close - running_low[index]) / qqq_close if qqq_close > 0 else 0.0
    else:
        trend_ok = qqq_close > qqq_vwap[index] and qqq_close > first_hour_midpoint
        pivot_ok = _is_confirmed_higher_low(confirmed_pivots, qqq_vwap)
        entry_break_ok = qqq_close > qqq_bars[index - 1].high
        room = (running_high[index] - qqq_close) / qqq_close if qqq_close > 0 else 0.0

    room_ok = room >= MIN_ROOM_TO_TARGET_PERCENT / 100.0

    vehicle_close = vehicle_bars[index].close
    vehicle_vwap_at_index = vehicle_vwap[index]
    extension = (
        (vehicle_close - vehicle_vwap_at_index) / vehicle_vwap_at_index
        if vehicle_vwap_at_index > 0
        else 0.0
    )
    extension_ok = extension <= MAX_VEHICLE_VWAP_EXTENSION_PERCENT / 100.0

    structural_ok = trend_ok and pivot_ok and entry_break_ok and room_ok
    return (structural_ok and extension_ok, structural_ok and not extension_ok)


def _resolve_lane2_trade(
    *,
    day: DaySession,
    qqq_bars: Sequence[Bar],
    vehicle_bars: Sequence[Bar],
    signal_index: int,
    direction: TradeDirection,
    vehicle_symbol: str,
    qqq_vwap: Sequence[float],
    running_low: Sequence[float],
    running_high: Sequence[float],
    confirmed_pivots: Sequence[_ConfirmedPivot],
    config: V2Config,
) -> V2TradeResult:
    """Mirrors `paper_simulator`'s exact friction/sizing math (floor
    shares, 0.15%-per-side slippage, Robinhood regulatory fees) so both
    lanes are priced identically, and `_resolve_vehicle_trade`'s
    stop-before-target same-bar tie-break."""
    fill_index = signal_index + 1 if signal_index + 1 < len(vehicle_bars) else signal_index
    fill_bar = vehicle_bars[fill_index]
    raw_entry_price = fill_bar.open if fill_index != signal_index else fill_bar.close

    slip = MODELED_SLIPPAGE_PERCENT_PER_SIDE / 100.0
    entry_price = raw_entry_price * (1 + slip)
    shares = float(max(1, math.floor(config.position_dollar_size / raw_entry_price)))
    entry_slippage_dollars = shares * (entry_price - raw_entry_price)

    entry_qqq_price = qqq_bars[signal_index].close
    bearish = direction == TradeDirection.LONG_SQQQ
    latest_pivot = confirmed_pivots[-1]

    target_distance = (
        (entry_qqq_price - running_low[signal_index]) / entry_qqq_price
        if bearish
        else (running_high[signal_index] - entry_qqq_price) / entry_qqq_price
    )
    target_distance = max(target_distance, 0.0) * LEVERAGED_ETF_FACTOR
    raw_target_price = raw_entry_price * (1 + target_distance)

    cutoff_hour, cutoff_minute = config.exit_cutoff_time.split(":")
    cutoff = dt_time(int(cutoff_hour), int(cutoff_minute))

    raw_exit_price: Optional[float] = None
    exit_reason = "data_ended"
    exit_time: Optional[str] = None

    for j in range(fill_index, len(vehicle_bars)):
        qqq_bar = qqq_bars[j] if j < len(qqq_bars) else None
        invalidated = False
        if qqq_bar is not None:
            if bearish:
                invalidated = qqq_bar.close > latest_pivot.price or qqq_bar.close > qqq_vwap[j]
            else:
                invalidated = qqq_bar.close < latest_pivot.price or qqq_bar.close < qqq_vwap[j]

        if invalidated:
            raw_exit_price = vehicle_bars[j].close
            exit_reason = "invalidated"
            exit_time = vehicle_bars[j].timestamp
            break
        if vehicle_bars[j].high >= raw_target_price:
            raw_exit_price = raw_target_price
            exit_reason = "target"
            exit_time = vehicle_bars[j].timestamp
            break
        if _bar_time(vehicle_bars[j]) >= cutoff:
            raw_exit_price = vehicle_bars[j].close
            exit_reason = "exit_before_close"
            exit_time = vehicle_bars[j].timestamp
            break

    if raw_exit_price is None:
        last_bar = vehicle_bars[-1]
        raw_exit_price = last_bar.close
        exit_time = last_bar.timestamp
        exit_reason = "data_ended"

    modeled_exit_price = raw_exit_price * (1 - slip)
    exit_slippage_dollars = shares * (raw_exit_price - modeled_exit_price)
    sell_proceeds = shares * modeled_exit_price
    regulatory_fees_dollars = _robinhood_regulatory_fee_dollars(
        shares_sold=shares, sell_proceeds_dollars=sell_proceeds
    )
    total_friction = entry_slippage_dollars + exit_slippage_dollars + regulatory_fees_dollars
    gross_pnl = shares * (raw_exit_price - raw_entry_price)
    net_pnl = gross_pnl - total_friction
    percent_result = (modeled_exit_price - entry_price) / entry_price * 100.0 if entry_price > 0 else None

    return V2TradeResult(
        lane="lane2",
        trade_date=day.date,
        vehicle_symbol=vehicle_symbol,
        direction=direction,
        entry_time=fill_bar.timestamp,
        entry_price=entry_price,
        stop_price=latest_pivot.price,
        target_price=raw_target_price,
        exit_time=exit_time,
        exit_price=modeled_exit_price,
        exit_reason=exit_reason,
        dollar_result=net_pnl,
        percent_result=percent_result,
    )


def _evaluate_lane2(day: DaySession, config: V2Config) -> V2TradeResult:
    qqq_bars = day.qqq_bars
    opening_range = _opening_range_bars(qqq_bars)
    if not opening_range or not qqq_bars:
        return _no_trade_v2(lane="lane2", date=day.date, reason="no lane 2 signal: missing opening range")

    first_hour_high = max(b.high for b in opening_range)
    first_hour_low = min(b.low for b in opening_range)
    first_hour_midpoint = (first_hour_high + first_hour_low) / 2.0

    qqq_vwap = _intraday_vwap_series(qqq_bars)
    running_low = _running_extreme(qqq_bars, low=True)
    running_high = _running_extreme(qqq_bars, low=False)

    eligible_indices = [
        i for i, bar in enumerate(qqq_bars)
        if LANE2_ELIGIBILITY_START <= _bar_time(bar) <= LANE2_ELIGIBILITY_END
    ]
    if not eligible_indices:
        return _no_trade_v2(
            lane="lane2", date=day.date,
            reason="no lane 2 signal: no bars in the 11:00-15:00 eligibility window",
        )

    eligible_from_index = eligible_indices[0]
    high_candidates = _pivot_candidates(qqq_bars, eligible_from_index=eligible_from_index, high=True)
    low_candidates = _pivot_candidates(qqq_bars, eligible_from_index=eligible_from_index, high=False)

    any_extension_block = False
    for i in eligible_indices:
        if i < 1:
            continue
        confirmed_highs = _confirm_pivots_through(qqq_bars, high_candidates, through_index=i, high=True)
        confirmed_lows = _confirm_pivots_through(qqq_bars, low_candidates, through_index=i, high=False)

        for direction, vehicle_bars, vehicle_symbol, confirmed in (
            (TradeDirection.LONG_SQQQ, day.sqqq_bars, "SQQQ", confirmed_highs),
            (TradeDirection.LONG_TQQQ, day.tqqq_bars, "TQQQ", confirmed_lows),
        ):
            if not vehicle_bars or i >= len(vehicle_bars):
                continue
            vehicle_vwap = _intraday_vwap_series(vehicle_bars)
            passed, extension_blocked_only = _lane2_entry_gate(
                index=i,
                qqq_bars=qqq_bars,
                vehicle_bars=vehicle_bars,
                qqq_vwap=qqq_vwap,
                vehicle_vwap=vehicle_vwap,
                running_low=running_low,
                running_high=running_high,
                first_hour_midpoint=first_hour_midpoint,
                confirmed_pivots=confirmed,
                direction=direction,
            )
            any_extension_block = any_extension_block or extension_blocked_only
            if passed:
                return _resolve_lane2_trade(
                    day=day,
                    qqq_bars=qqq_bars,
                    vehicle_bars=vehicle_bars,
                    signal_index=i,
                    direction=direction,
                    vehicle_symbol=vehicle_symbol,
                    qqq_vwap=qqq_vwap,
                    running_low=running_low,
                    running_high=running_high,
                    confirmed_pivots=confirmed,
                    config=config,
                )

    reason = CONTINUATION_EXTENDED_REASON if any_extension_block else "no lane 2 signal"
    return _no_trade_v2(lane="lane2", date=day.date, reason=reason)


def evaluate_day_v2(day: DaySession, config: V2Config) -> V2TradeResult | SkippedDay:
    """Lane 1 (the real, unmodified paper-harness engine) first; if it
    produced a real TAKE_PAPER trade, that wins and Lane 2 never runs.
    If Lane 1 was a legitimate NO_TRADE read, Lane 2 evaluates. A
    `SkippedDay` (missing required bar data, or the first hour hasn't
    closed) short-circuits both -- neither lane can be evaluated
    without the day's bars."""
    lane1_result = _evaluate_lane1(day, config)
    if isinstance(lane1_result, SkippedDay):
        return lane1_result
    if not lane1_result.skipped:
        return lane1_result
    return _evaluate_lane2(day, config)


def run_backtest_v2(sessions: Sequence[DaySession], config: V2Config) -> dict:
    """Runs every `DaySession` through `evaluate_day_v2()` and rolls up
    THREE summaries with the existing, unmodified `summarize_trades()`
    (from `tqqq_sqqq_backtest.py`, a pure rollup function with no lane
    awareness needed): combined (both lanes), lane1-only, and
    lane2-only -- the separate Lane 1 / Lane 2 reporting the validation
    requirement calls for."""
    trade_log: list[V2TradeResult] = []
    skipped_days: list[SkippedDay] = []
    for day in sessions:
        outcome = evaluate_day_v2(day, config)
        if isinstance(outcome, SkippedDay):
            skipped_days.append(outcome)
        else:
            trade_log.append(outcome)

    lane1_log = [t for t in trade_log if t.lane == "lane1"]
    lane2_log = [t for t in trade_log if t.lane == "lane2"]

    return {
        "combined": summarize_trades(trade_log, skipped_days),
        "lane1": summarize_trades(lane1_log, skipped_days),
        "lane2": summarize_trades(lane2_log, skipped_days),
        "trade_log": tuple(trade_log),
        "skipped_days": tuple(skipped_days),
    }
