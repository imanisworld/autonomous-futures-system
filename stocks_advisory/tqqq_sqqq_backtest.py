"""stocks_advisory/tqqq_sqqq_backtest.py

QQQ -> TQQQ/SQQQ opening-range backtest -- Stock/ETF Backtest v1
(refined). QQQ opening-range structure (30 or 60 minutes, configurable)
decides whether TQQQ, SQQQ, or NO_TRADE is the day's candidate.
Percent-based gap/range thresholds (so a fixed dollar range means
something consistent whether QQQ is trading at $300 or $450), a
computed intraday VWAP, configurable stop/target models, and simulated
fill/stop/target/exit-before-close resolution against the vehicle's
own bars.

Backtest/research only. No live execution, no paper execution loop, no
scheduler, no API endpoint, no order of any kind. Never calls a
broker, Robinhood, a market-data feed, or the live system clock --
every timestamp comparison is against a caller-supplied bar timestamp,
never the current wall-clock time. Does not import `execution/`,
`webhook/`, `strategy/`, `risk/`, `options_manager`, or any broker
package. This module decides nothing by discretion -- every rule below
is deterministic; the only "judgment" surface is the config a caller
builds before running it.

No-lookahead rules enforced by construction:
- The opening-range high/low is built only from bars whose timestamp
  falls within `opening_range_minutes` of the day's first bar.
- A breakout is only evaluated on bars *after* the opening-range window.
- VWAP at any bar is the cumulative volume-weighted price using only
  that bar and every bar before it in the same session -- never a
  later bar.
- `qqq_previous_close`/`qqq_previous_high`/`qqq_previous_low` come from
  the caller's `DaySession`, never derived by peeking at another
  session in the same run.
- Entry, stop, and target resolution for a TQQQ or SQQQ trade use only
  `DaySession.tqqq_bars`/`sqqq_bars` -- the QQQ bars never price a fill.
- A trade is entered at the bar *after* the signal bar's close (or, if
  no bar follows, the signal bar's own close as a same-bar fallback for
  the last bar of the day) -- never at a price only knowable from a
  bar further in the future than that.

Decision order per day (first match wins):

1. Missing `qqq_bars`, or fewer than one bar in the opening-range
   window, or a non-positive `qqq_previous_close` -> skip (`SkippedDay`,
   not a NO_TRADE trade-log entry).
2. Gap percent (day's first QQQ bar open vs. `qqq_previous_close`)
   above `max_gap_percent` -> NO_TRADE ("gap too large").
3. Opening-range percent (of the day's open) below
   `min_opening_range_percent` -> NO_TRADE ("range too small").
4. Opening-range percent above `max_opening_range_percent` -> NO_TRADE
   ("range too large").
5. If `relative_volume_filter_enabled` and `qqq_relative_volume` is
   missing or below `min_relative_volume` -> NO_TRADE
   ("relative volume ...").
6. Scan every bar after the opening range: a bar whose close is above
   the opening-range high (and, when `vwap_required`, also above that
   bar's own VWAP) qualifies a TQQQ signal; a bar whose close is below
   the opening-range low (and, when required, below VWAP) qualifies an
   SQQQ signal.
   - Both directions qualify somewhere in the day -> CONFLICT, unless
     `same_day_conflict_priority` is explicitly `"TQQQ"` or `"SQQQ"`.
   - Only one direction qualifies -> enter at the *first* qualifying
     bar, chronologically.
   - Neither direction's close condition is ever met -> NO_TRADE
     ("no breakout").
   - (`vwap_required` only) A close beyond the opening-range high/low
     occurs but VWAP never confirms it -> NO_TRADE ("VWAP conflict").

Both TQQQ and SQQQ trades are always entered LONG on the vehicle itself
(SQQQ is the bearish product; going long SQQQ is how this system
expresses a bearish QQQ view) -- profit is always price appreciation
on the vehicle, so stop is always below entry and target always above.

Stop models (`BacktestConfig.stop_model`):
- `OPPOSITE_RANGE_EDGE` (default): the QQQ-side percent distance from
  entry to the *opposite* edge of the opening range, scaled by
  `LEVERAGED_ETF_FACTOR`.
- `PERCENT`: a fixed percent of the vehicle's own entry price
  (`stop_percent`), no QQQ-side translation.
- `ATR_RANGE`: the average `high - low` of the opening-range bars, as a
  percent of the QQQ entry price, times `stop_atr_multiple`, scaled by
  `LEVERAGED_ETF_FACTOR`.
- `trailing_stop_enabled` is an overlay on top of any of the above: once
  price has moved `trailing_stop_activation_r` R in favor, the stop
  ratchets up (never down) to lock `trailing_stop_trail_r` R of that
  move.

Target models (`BacktestConfig.target_model`):
- `FIXED_R_MULTIPLE` (default): `target_r_multiple` times the stop
  distance.
- `PRIOR_HIGH_LOW`: the QQQ-side percent distance from entry to
  `qqq_previous_high` (TQQQ) or `qqq_previous_low` (SQQQ), scaled by
  `LEVERAGED_ETF_FACTOR`.
- `END_OF_DAY` / `TRAILING_STOP_EXIT`: no fixed target price at all --
  the trade only exits via a stop (trailing, if enabled) or the
  exit-before-close cutoff.

Resolution walks the vehicle's bars forward from the fill bar, checking
the (possibly-trailing) stop *before* the target on every bar -- a bar
that would touch both is scored as a stop, the same pessimistic,
conservative-by-default convention used throughout this repo's
shadow-evaluation code. If neither is touched by `exit_cutoff_time`,
the position exits at that bar's close ("exit_before_close"); if the
day's bars run out first, it exits at the last available close
("data_ended").
"""

from __future__ import annotations

from dataclasses import replace as dataclasses_replace
from datetime import datetime, time as dt_time
from typing import Optional, Sequence

from .backtest_models import (
    Bar,
    BacktestConfig,
    BacktestSummary,
    BacktestTradeResult,
    DaySession,
    EquityPoint,
    LEVERAGED_ETF_FACTOR,
    DEFAULT_SLIPPAGE_STRESS_LEVELS,
    InSampleOutOfSampleResult,
    SkippedDay,
    SlippageStressPoint,
    SlippageStressReport,
    StopModel,
    TargetModel,
    TradeDirection,
    WalkForwardFold,
    WalkForwardResult,
)

MIN_DAYS_FOR_ANNUALIZED = 252
MIN_TRADES_FOR_SHARPE = 10

_SKIP_REASON_BUCKETS = (
    "gap too large",
    "range too small",
    "range too large",
    "relative volume",
    "vwap conflict",
    "conflict",
    "no breakout",
    "missing",
)


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _bar_time(bar: Bar) -> dt_time:
    return _parse_ts(bar.timestamp).time()


def _parse_cutoff(exit_cutoff_time: str) -> dt_time:
    hour, minute = exit_cutoff_time.split(":")
    return dt_time(int(hour), int(minute))


def _opening_range_bars(bars: Sequence[Bar], opening_range_minutes: int) -> list[Bar]:
    if not bars:
        return []
    start = _parse_ts(bars[0].timestamp)
    cutoff_seconds = opening_range_minutes * 60
    return [b for b in bars if (_parse_ts(b.timestamp) - start).total_seconds() < cutoff_seconds]


def _intraday_vwap_series(bars: Sequence[Bar]) -> list[float]:
    """Cumulative, causal VWAP -- entry i uses only bars[0..i]."""
    vwaps: list[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_vol += bar.volume
        vwaps.append(cum_pv / cum_vol if cum_vol > 0 else typical)
    return vwaps


def _find_signal_bars(
    bars_after_range: Sequence[Bar],
    vwap_after_range: Sequence[float],
    range_high: float,
    range_low: float,
    vwap_required: bool,
) -> tuple[list[int], list[int], bool, bool]:
    """Returns (tqqq_signal_indices, sqqq_signal_indices, any_above_high,
    any_below_low) -- indices are into `bars_after_range`."""
    tqqq_indices: list[int] = []
    sqqq_indices: list[int] = []
    any_above_high = False
    any_below_low = False
    for i, (bar, vwap) in enumerate(zip(bars_after_range, vwap_after_range)):
        above_high = bar.close > range_high
        below_low = bar.close < range_low
        any_above_high = any_above_high or above_high
        any_below_low = any_below_low or below_low
        confirms_up = bar.close > vwap if vwap_required else True
        confirms_down = bar.close < vwap if vwap_required else True
        if above_high and confirms_up:
            tqqq_indices.append(i)
        elif below_low and confirms_down:
            sqqq_indices.append(i)
    return tqqq_indices, sqqq_indices, any_above_high, any_below_low


def _skipped_trade(day: DaySession, reason: str) -> BacktestTradeResult:
    return BacktestTradeResult(
        trade_date=day.date,
        vehicle_symbol="",
        direction=TradeDirection.NO_TRADE,
        exit_reason=reason,
        skipped=True,
        skipped_reason=reason,
    )


def _compute_stop_distance_percent(
    *,
    config: BacktestConfig,
    direction: TradeDirection,
    entry_qqq_price: float,
    range_high: float,
    range_low: float,
    opening_range_bars: Sequence[Bar],
) -> float:
    if config.stop_model == StopModel.PERCENT:
        return (config.stop_percent or 0.0) / 100.0
    if config.stop_model == StopModel.ATR_RANGE:
        avg_range = sum(b.high - b.low for b in opening_range_bars) / len(opening_range_bars)
        atr_percent = avg_range / entry_qqq_price if entry_qqq_price > 0 else 0.0
        return atr_percent * (config.stop_atr_multiple or 0.0) * LEVERAGED_ETF_FACTOR
    # OPPOSITE_RANGE_EDGE
    if direction == TradeDirection.LONG_TQQQ:
        distance = (entry_qqq_price - range_low) / entry_qqq_price if entry_qqq_price > 0 else 0.0
    else:
        distance = (range_high - entry_qqq_price) / entry_qqq_price if entry_qqq_price > 0 else 0.0
    return max(distance, 0.0) * LEVERAGED_ETF_FACTOR


def _compute_target_price(
    *,
    config: BacktestConfig,
    direction: TradeDirection,
    entry_vehicle_price: float,
    risk_per_share: float,
    entry_qqq_price: float,
    day: DaySession,
) -> Optional[float]:
    if config.target_model in (TargetModel.END_OF_DAY, TargetModel.TRAILING_STOP_EXIT):
        return None
    if config.target_model == TargetModel.PRIOR_HIGH_LOW:
        if direction == TradeDirection.LONG_TQQQ:
            distance = (day.qqq_previous_high - entry_qqq_price) / entry_qqq_price if entry_qqq_price > 0 else 0.0
        else:
            distance = (entry_qqq_price - day.qqq_previous_low) / entry_qqq_price if entry_qqq_price > 0 else 0.0
        distance = max(distance, 0.0) * LEVERAGED_ETF_FACTOR
        return entry_vehicle_price * (1 + distance)
    # FIXED_R_MULTIPLE
    return entry_vehicle_price + risk_per_share * config.target_r_multiple


def _resolve_vehicle_trade(
    *,
    day: DaySession,
    vehicle_bars: Sequence[Bar],
    signal_index: int,
    direction: TradeDirection,
    vehicle_symbol: str,
    entry_qqq_price: float,
    range_high: float,
    range_low: float,
    opening_range_bars: Sequence[Bar],
    config: BacktestConfig,
) -> BacktestTradeResult:
    fill_index = signal_index + 1 if signal_index + 1 < len(vehicle_bars) else signal_index
    fill_bar = vehicle_bars[fill_index]
    raw_entry_price = fill_bar.open if fill_index != signal_index else fill_bar.close

    slip = config.slippage_percent / 100.0
    entry_price = raw_entry_price * (1 + slip)

    stop_distance_percent = _compute_stop_distance_percent(
        config=config,
        direction=direction,
        entry_qqq_price=entry_qqq_price,
        range_high=range_high,
        range_low=range_low,
        opening_range_bars=opening_range_bars,
    )
    initial_stop_price = entry_price * (1 - stop_distance_percent)
    risk_per_share = entry_price - initial_stop_price

    target_price = _compute_target_price(
        config=config,
        direction=direction,
        entry_vehicle_price=entry_price,
        risk_per_share=risk_per_share,
        entry_qqq_price=entry_qqq_price,
        day=day,
    )

    cutoff = _parse_cutoff(config.exit_cutoff_time)

    current_stop = initial_stop_price
    peak_price = entry_price
    exit_price: Optional[float] = None
    exit_reason = "data_ended"
    exit_time: Optional[str] = None

    for bar in vehicle_bars[fill_index:]:
        # Checked against the stop/target level as it stood BEFORE this bar --
        # a bar's own high can tighten the trailing stop for *later* bars, but
        # must never be used to justify stopping out that same bar (that would
        # be circular: the bar's own favorable excursion producing the very
        # stop level used to judge its own low, with no real ordering between
        # the two within one bar).
        if bar.low <= current_stop:
            exit_price = current_stop
            exit_reason = "trailing_stop" if current_stop > initial_stop_price else "stop"
            exit_time = bar.timestamp
            break
        if target_price is not None and bar.high >= target_price:
            exit_price = target_price
            exit_reason = "target"
            exit_time = bar.timestamp
            break
        if _bar_time(bar) >= cutoff:
            exit_price = bar.close
            exit_reason = "exit_before_close"
            exit_time = bar.timestamp
            break

        peak_price = max(peak_price, bar.high)
        if config.trailing_stop_enabled and risk_per_share > 0:
            favorable_r = (peak_price - entry_price) / risk_per_share
            if favorable_r >= config.trailing_stop_activation_r:
                trailed_stop = peak_price - config.trailing_stop_trail_r * risk_per_share
                current_stop = max(current_stop, trailed_stop)

    if exit_price is None:
        last_bar = vehicle_bars[-1]
        exit_price = last_bar.close
        exit_time = last_bar.timestamp

    exit_price_after_slippage = exit_price * (1 - slip)

    r_result = (
        (exit_price_after_slippage - entry_price) / risk_per_share if risk_per_share > 0 else None
    )
    shares = config.position_dollar_size / entry_price if entry_price > 0 else 0.0
    dollar_result = shares * (exit_price_after_slippage - entry_price) - config.commission_per_trade
    percent_result = (
        (exit_price_after_slippage - entry_price) / entry_price * 100.0 if entry_price > 0 else None
    )

    return BacktestTradeResult(
        trade_date=day.date,
        vehicle_symbol=vehicle_symbol,
        direction=direction,
        entry_time=fill_bar.timestamp,
        entry_price=entry_price,
        stop_price=initial_stop_price,
        target_price=target_price,
        exit_time=exit_time,
        exit_price=exit_price_after_slippage,
        exit_reason=exit_reason,
        r_result=r_result,
        dollar_result=dollar_result,
        percent_result=percent_result,
    )


def evaluate_day(day: DaySession, config: BacktestConfig) -> BacktestTradeResult | SkippedDay:
    """Evaluates one `DaySession`. Returns a `SkippedDay` when required
    bar data is missing, otherwise a `BacktestTradeResult` (which itself
    carries `skipped=True` for a legitimate NO_TRADE/CONFLICT read)."""
    if not day.qqq_bars:
        return SkippedDay(date=day.date, reason="missing qqq_bars")

    opening_range = _opening_range_bars(day.qqq_bars, config.opening_range_minutes)
    if not opening_range:
        return SkippedDay(date=day.date, reason="no bars within opening-range window")

    day_open = day.qqq_bars[0].open
    if day.qqq_previous_close <= 0:
        return SkippedDay(date=day.date, reason="missing qqq_previous_close")

    gap_percent = (day_open - day.qqq_previous_close) / day.qqq_previous_close * 100.0
    if abs(gap_percent) > config.max_gap_percent:
        return _skipped_trade(day, f"gap too large: {gap_percent:.2f}% exceeds {config.max_gap_percent:.2f}%")

    range_high = max(b.high for b in opening_range)
    range_low = min(b.low for b in opening_range)
    range_percent = (range_high - range_low) / day_open * 100.0 if day_open > 0 else 0.0

    if range_percent < config.min_opening_range_percent:
        return _skipped_trade(
            day, f"range too small: {range_percent:.3f}% below {config.min_opening_range_percent:.3f}%"
        )
    if range_percent > config.max_opening_range_percent:
        return _skipped_trade(
            day, f"range too large: {range_percent:.3f}% exceeds {config.max_opening_range_percent:.3f}%"
        )

    if config.relative_volume_filter_enabled:
        if day.qqq_relative_volume is None:
            return _skipped_trade(day, "relative volume: missing qqq_relative_volume")
        if day.qqq_relative_volume < config.min_relative_volume:
            return _skipped_trade(
                day,
                f"relative volume {day.qqq_relative_volume:.2f} below minimum {config.min_relative_volume:.2f}",
            )

    bars_after = list(day.qqq_bars[len(opening_range):])
    if not bars_after:
        return _skipped_trade(day, "no breakout: no bars after opening-range window")

    vwap_series = _intraday_vwap_series(day.qqq_bars)
    vwap_after = vwap_series[len(opening_range):]

    tqqq_indices, sqqq_indices, any_above_high, any_below_low = _find_signal_bars(
        bars_after, vwap_after, range_high, range_low, config.vwap_required
    )

    if tqqq_indices and sqqq_indices:
        if config.same_day_conflict_priority == "TQQQ":
            direction, indices, vehicle_bars, vehicle_symbol = (
                TradeDirection.LONG_TQQQ, tqqq_indices, day.tqqq_bars, "TQQQ",
            )
        elif config.same_day_conflict_priority == "SQQQ":
            direction, indices, vehicle_bars, vehicle_symbol = (
                TradeDirection.LONG_SQQQ, sqqq_indices, day.sqqq_bars, "SQQQ",
            )
        else:
            return BacktestTradeResult(
                trade_date=day.date,
                vehicle_symbol="",
                direction=TradeDirection.CONFLICT,
                exit_reason="conflict: both TQQQ and SQQQ signals occurred the same day",
                skipped=True,
                skipped_reason="conflict: both TQQQ and SQQQ signals occurred the same day",
            )
    elif tqqq_indices:
        direction, indices, vehicle_bars, vehicle_symbol = (
            TradeDirection.LONG_TQQQ, tqqq_indices, day.tqqq_bars, "TQQQ",
        )
    elif sqqq_indices:
        direction, indices, vehicle_bars, vehicle_symbol = (
            TradeDirection.LONG_SQQQ, sqqq_indices, day.sqqq_bars, "SQQQ",
        )
    else:
        if config.vwap_required and (any_above_high or any_below_low):
            reason = "VWAP conflict: breakout occurred but VWAP never confirmed"
        else:
            reason = "no breakout"
        return _skipped_trade(day, reason)

    if not vehicle_bars:
        return SkippedDay(date=day.date, reason=f"missing {vehicle_symbol.lower()}_bars")

    relative_signal_index = indices[0]
    # `vehicle_bars` is index-aligned to the FULL day's qqq_bars (opening-range
    # bars included), but `indices`/`bars_after`/`vwap_after` are relative to
    # only the post-range subset -- convert back to an absolute index before
    # touching the vehicle's own bar list.
    absolute_signal_index = len(opening_range) + relative_signal_index
    if absolute_signal_index >= len(vehicle_bars):
        return SkippedDay(date=day.date, reason=f"{vehicle_symbol.lower()}_bars shorter than qqq_bars at signal index")

    entry_qqq_bar = bars_after[relative_signal_index]

    return _resolve_vehicle_trade(
        day=day,
        vehicle_bars=vehicle_bars,
        signal_index=absolute_signal_index,
        direction=direction,
        vehicle_symbol=vehicle_symbol,
        entry_qqq_price=entry_qqq_bar.close,
        range_high=range_high,
        range_low=range_low,
        opening_range_bars=opening_range,
        config=config,
    )


def _max_drawdown(cumulative: Sequence[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    running = 0.0
    for value in cumulative:
        running += value
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return max_dd


def _max_losing_streak(results: Sequence[float]) -> int:
    longest = 0
    current = 0
    for r in results:
        if r < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _days_span(trade_log: Sequence[BacktestTradeResult]) -> int:
    dated = [t.trade_date for t in trade_log if not t.skipped]
    if len(dated) < 2:
        return 0
    first = datetime.fromisoformat(min(dated))
    last = datetime.fromisoformat(max(dated))
    return (last - first).days


def _reason_bucket(reason: str) -> str:
    lowered = reason.lower()
    for bucket in _SKIP_REASON_BUCKETS:
        if bucket in lowered:
            return bucket
    return "other"


def _skipped_days_by_reason(
    trade_log: Sequence[BacktestTradeResult], skipped_days: Sequence[SkippedDay]
) -> dict:
    counts: dict[str, int] = {}
    for t in trade_log:
        if t.skipped:
            counts[_reason_bucket(t.skipped_reason)] = counts.get(_reason_bucket(t.skipped_reason), 0) + 1
    for s in skipped_days:
        counts[_reason_bucket(s.reason)] = counts.get(_reason_bucket(s.reason), 0) + 1
    return counts


def summarize_trades(
    trade_log: Sequence[BacktestTradeResult],
    skipped_days: Sequence[SkippedDay],
    *,
    buy_and_hold_qqq_return_percent: Optional[float] = None,
    buy_and_hold_tqqq_return_percent: Optional[float] = None,
) -> BacktestSummary:
    """Pure rollup of an already-computed trade log. Never fabricates a
    number on a thin sample -- returns `None` for any metric that needs
    more data than is present."""
    taken = [t for t in trade_log if not t.skipped and t.dollar_result is not None]
    dollar_results = [t.dollar_result for t in taken]
    percent_results = [t.percent_result for t in taken if t.percent_result is not None]

    total_trades = len(taken)
    total_days_tested = len(trade_log) + len(skipped_days)
    wins = [d for d in dollar_results if d > 0]
    losses = [d for d in dollar_results if d < 0]

    win_rate_percent = (len(wins) / total_trades * 100.0) if total_trades else None
    average_win_dollars = (sum(wins) / len(wins)) if wins else None
    average_loss_dollars = (sum(losses) / len(losses)) if losses else None
    expectancy_dollars = (sum(dollar_results) / total_trades) if total_trades else None
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    max_drawdown_dollars = _max_drawdown(dollar_results) if dollar_results else None
    max_losing_streak = _max_losing_streak(dollar_results)
    average_trade_return_percent = (sum(percent_results) / len(percent_results)) if percent_results else None
    worst_single_loss_dollars = min(dollar_results) if dollar_results else None
    best_single_win_dollars = max(dollar_results) if dollar_results else None
    exposure_percent = (total_trades / total_days_tested * 100.0) if total_days_tested else None

    days_span = _days_span(trade_log)
    annualized_return_percent = None
    if days_span >= MIN_DAYS_FOR_ANNUALIZED and total_trades > 0:
        # Annualized off cumulative percent-return compounding, not dollars,
        # so position sizing doesn't distort the annualization.
        cumulative_growth = 1.0
        for p in percent_results:
            cumulative_growth *= (1 + p / 100.0)
        years = days_span / 365.0
        if years > 0:
            annualized_return_percent = (cumulative_growth ** (1 / years) - 1) * 100.0

    sharpe_ratio = None
    if len(percent_results) >= MIN_TRADES_FOR_SHARPE:
        mean_r = sum(percent_results) / len(percent_results)
        variance = sum((p - mean_r) ** 2 for p in percent_results) / (len(percent_results) - 1)
        stdev = variance ** 0.5
        if stdev > 0:
            sharpe_ratio = mean_r / stdev

    equity_curve: list[EquityPoint] = []
    running_total = 0.0
    for t in sorted(taken, key=lambda t: t.trade_date):
        running_total += t.dollar_result
        equity_curve.append(EquityPoint(date=t.trade_date, cumulative_dollars=running_total))

    return BacktestSummary(
        total_days_tested=total_days_tested,
        total_trades=total_trades,
        win_rate_percent=win_rate_percent,
        average_win_dollars=average_win_dollars,
        average_loss_dollars=average_loss_dollars,
        expectancy_dollars=expectancy_dollars,
        profit_factor=profit_factor,
        max_drawdown_dollars=max_drawdown_dollars,
        max_losing_streak=max_losing_streak,
        average_trade_return_percent=average_trade_return_percent,
        annualized_return_percent=annualized_return_percent,
        sharpe_ratio=sharpe_ratio,
        worst_single_loss_dollars=worst_single_loss_dollars,
        best_single_win_dollars=best_single_win_dollars,
        exposure_percent=exposure_percent,
        buy_and_hold_qqq_return_percent=buy_and_hold_qqq_return_percent,
        buy_and_hold_tqqq_return_percent=buy_and_hold_tqqq_return_percent,
        trade_log=tuple(trade_log),
        skipped_days=tuple(skipped_days),
        equity_curve=tuple(equity_curve),
        skipped_days_by_reason=_skipped_days_by_reason(trade_log, skipped_days),
    )


def _buy_and_hold_return_percent(sessions: Sequence[DaySession], bar_attr: str) -> Optional[float]:
    with_bars = [s for s in sessions if getattr(s, bar_attr)]
    if not with_bars:
        return None
    first_bars = getattr(with_bars[0], bar_attr)
    last_bars = getattr(with_bars[-1], bar_attr)
    start_price = first_bars[0].open
    end_price = last_bars[-1].close
    if start_price <= 0:
        return None
    return (end_price - start_price) / start_price * 100.0


def run_backtest(sessions: Sequence[DaySession], config: BacktestConfig) -> BacktestSummary:
    """Runs every `DaySession` through `evaluate_day()` in order and
    rolls the results up with `summarize_trades()`. Deterministic: the
    same sessions and config always produce the same summary."""
    trade_log: list[BacktestTradeResult] = []
    skipped_days: list[SkippedDay] = []
    for day in sessions:
        outcome = evaluate_day(day, config)
        if isinstance(outcome, SkippedDay):
            skipped_days.append(outcome)
        else:
            trade_log.append(outcome)
    return summarize_trades(
        trade_log,
        skipped_days,
        buy_and_hold_qqq_return_percent=_buy_and_hold_return_percent(sessions, "qqq_bars"),
        buy_and_hold_tqqq_return_percent=_buy_and_hold_return_percent(sessions, "tqqq_bars"),
    )


def run_slippage_stress(
    sessions: Sequence[DaySession],
    config: BacktestConfig,
    levels: Sequence[float] = DEFAULT_SLIPPAGE_STRESS_LEVELS,
) -> SlippageStressReport:
    """Re-runs the same sessions once per slippage level, varying only
    `slippage_percent`. Every other config field is held fixed."""
    points = []
    for level in levels:
        level_config = dataclasses_replace(config, slippage_percent=level)
        points.append(SlippageStressPoint(slippage_percent=level, summary=run_backtest(sessions, level_config)))
    return SlippageStressReport(points=tuple(points))


def run_in_sample_out_of_sample(
    sessions: Sequence[DaySession],
    config: BacktestConfig,
    in_sample_fraction: float = 0.7,
) -> InSampleOutOfSampleResult:
    """Chronological split: the first `in_sample_fraction` of sessions
    (sorted by date) is in-sample, the remainder is out-of-sample."""
    ordered = sorted(sessions, key=lambda s: s.date)
    if len(ordered) < 2:
        split_index = len(ordered)
    else:
        split_index = min(max(1, round(len(ordered) * in_sample_fraction)), len(ordered) - 1)
    in_sample = ordered[:split_index]
    out_of_sample = ordered[split_index:]
    split_date = out_of_sample[0].date if out_of_sample else (in_sample[-1].date if in_sample else "")
    return InSampleOutOfSampleResult(
        in_sample_summary=run_backtest(in_sample, config),
        out_of_sample_summary=run_backtest(out_of_sample, config),
        in_sample_session_count=len(in_sample),
        out_of_sample_session_count=len(out_of_sample),
        split_date=split_date,
    )


def run_walk_forward(
    sessions: Sequence[DaySession],
    config: BacktestConfig,
    *,
    train_size: int,
    test_size: int,
) -> WalkForwardResult:
    """Rolling walk-forward: consecutive (train, test) windows advancing
    by `test_size` sessions each fold, sorted chronologically by date."""
    ordered = sorted(sessions, key=lambda s: s.date)
    folds: list[WalkForwardFold] = []
    fold_index = 0
    start = 0
    while start + train_size + test_size <= len(ordered):
        train = ordered[start : start + train_size]
        test = ordered[start + train_size : start + train_size + test_size]
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train_session_count=len(train),
                test_session_count=len(test),
                train_summary=run_backtest(train, config),
                test_summary=run_backtest(test, config),
                train_start_date=train[0].date,
                train_end_date=train[-1].date,
                test_start_date=test[0].date,
                test_end_date=test[-1].date,
            )
        )
        fold_index += 1
        start += test_size
    return WalkForwardResult(folds=tuple(folds))
