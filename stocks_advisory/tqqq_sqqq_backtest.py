"""stocks_advisory/tqqq_sqqq_backtest.py

QQQ -> TQQQ/SQQQ first-hour backtest -- Stock/ETF Backtest v1. Same
core hypothesis as the paper lane (`tqqq_sqqq_decision.py`): QQQ
first-hour structure decides whether TQQQ, SQQQ, or NO_TRADE is the
day's candidate. This module is a separate, self-contained
implementation built for multi-day/multi-year historical replay rather
than a single manual payload -- percent-based range/gap thresholds
(so a fixed dollar range means something consistent whether QQQ is
trading at $300 or $450), a computed intraday VWAP, and simulated
fill/stop/target/exit-before-close resolution against the vehicle's
own bars.

Backtest/research only. No live execution, no paper execution loop, no
scheduler, no API endpoint, no order of any kind. Never calls a
broker, Robinhood, a market-data feed, or the live system clock --
every timestamp comparison is against a caller-supplied bar timestamp,
never the current wall-clock time. Does not import `execution/`, `webhook/`,
`strategy/`, `risk/`, `options_manager`, or any broker package.

No-lookahead rules enforced by construction:
- The first-hour high/low/range is built only from bars whose
  timestamp falls within `first_hour_minutes` of the day's first bar.
- A breakout is only evaluated on bars *after* the first-hour window.
- VWAP at any bar is the cumulative volume-weighted price using only
  that bar and every bar before it in the same session -- never a
  later bar.
- `qqq_previous_close` comes from the caller's `DaySession`, never
  derived by peeking at another session in the same run.
- Entry, stop, and target resolution for a TQQQ or SQQQ trade use only
  `DaySession.tqqq_bars`/`sqqq_bars` -- the QQQ bars never price a fill.
- A trade is entered at the bar *after* the signal bar's close (or, if
  no bar follows, the signal bar's own close as a same-bar fallback for
  the last bar of the day) -- never at a price only knowable from a
  bar further in the future than that.

Decision order per day (first match wins):

1. Missing `qqq_bars`, `tqqq_bars`, `sqqq_bars`, or fewer than one bar
   in the first-hour window -> skip (`SkippedDay`, not a NO_TRADE
   trade-log entry).
2. Gap percent (day's first QQQ bar open vs. `qqq_previous_close`)
   above `max_gap_percent` -> NO_TRADE ("gap too large").
3. First-hour range percent (of the day's open) below
   `min_first_hour_range_percent` -> NO_TRADE ("range too small").
4. First-hour range percent above `max_first_hour_range_percent` ->
   NO_TRADE ("range too large").
5. Scan every bar after the first hour: a bar whose close is above the
   first-hour high AND above that bar's own VWAP qualifies a TQQQ
   signal; a bar whose close is below the first-hour low AND below
   VWAP qualifies an SQQQ signal.
   - Both directions qualify somewhere in the day -> CONFLICT, unless
     `same_day_conflict_priority` is explicitly set to `"TQQQ"` or
     `"SQQQ"`, in which case that direction is taken and the other is
     ignored.
   - Only one direction qualifies -> enter at the *first* qualifying
     bar, chronologically.
   - Neither direction's close condition is ever met -> NO_TRADE
     ("no breakout").
   - A close beyond the first-hour high/low occurs but VWAP never
     confirms it in either direction -> NO_TRADE ("VWAP conflict").

Stop/target: the stop distance is the QQQ-side percent distance from
entry to VWAP at the entry bar, scaled by `LEVERAGED_ETF_FACTOR` and
applied to the vehicle's own entry price (a documented v1
approximation, not a claim that TQQQ/SQQQ track QQQ at exactly 3x
intraday). Target is `target_r_multiple` times that same distance.
Resolution walks the vehicle's bars forward from the fill bar: a bar
touching both stop and target is scored as a stop (pessimistic
same-bar tie-break, the same convention used throughout this repo's
shadow-evaluation code). If neither is touched by `exit_cutoff_time`,
the position exits at that bar's close ("exit_before_close"); if the
day's bars run out first, it exits at the last available close
("data_ended").
"""

from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Optional, Sequence

from .backtest_models import (
    Bar,
    BacktestConfig,
    BacktestSummary,
    BacktestTradeResult,
    DaySession,
    LEVERAGED_ETF_FACTOR,
    DEFAULT_SLIPPAGE_STRESS_LEVELS,
    SkippedDay,
    SlippageStressPoint,
    SlippageStressReport,
    TradeDirection,
)

MIN_DAYS_FOR_ANNUALIZED = 252
MIN_TRADES_FOR_SHARPE = 10


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _bar_time(bar: Bar) -> dt_time:
    return _parse_ts(bar.timestamp).time()


def _first_hour_bars(bars: Sequence[Bar], first_hour_minutes: int) -> list[Bar]:
    if not bars:
        return []
    start = _parse_ts(bars[0].timestamp)
    cutoff_seconds = first_hour_minutes * 60
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
    bars_after_first_hour: Sequence[Bar],
    vwap_after_first_hour: Sequence[float],
    first_hour_high: float,
    first_hour_low: float,
) -> tuple[list[int], list[int], bool, bool]:
    """Returns (tqqq_signal_indices, sqqq_signal_indices, any_above_high,
    any_below_low) -- indices are into `bars_after_first_hour`."""
    tqqq_indices: list[int] = []
    sqqq_indices: list[int] = []
    any_above_high = False
    any_below_low = False
    for i, (bar, vwap) in enumerate(zip(bars_after_first_hour, vwap_after_first_hour)):
        above_high = bar.close > first_hour_high
        below_low = bar.close < first_hour_low
        any_above_high = any_above_high or above_high
        any_below_low = any_below_low or below_low
        if above_high and bar.close > vwap:
            tqqq_indices.append(i)
        elif below_low and bar.close < vwap:
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


def _resolve_vehicle_trade(
    *,
    day: DaySession,
    vehicle_bars: Sequence[Bar],
    signal_index: int,
    direction: TradeDirection,
    vehicle_symbol: str,
    stop_distance_percent: float,
    config: BacktestConfig,
) -> BacktestTradeResult:
    fill_index = signal_index + 1 if signal_index + 1 < len(vehicle_bars) else signal_index
    fill_bar = vehicle_bars[fill_index]
    raw_entry_price = fill_bar.open if fill_index != signal_index else fill_bar.close

    slip = config.slippage_percent / 100.0
    entry_price = raw_entry_price * (1 + slip)
    stop_price = entry_price * (1 - stop_distance_percent)
    target_price = entry_price * (1 + stop_distance_percent * config.target_r_multiple)
    cutoff = _parse_cutoff(config.exit_cutoff_time)

    exit_price: Optional[float] = None
    exit_reason = "data_ended"
    exit_time: Optional[str] = None

    for bar in vehicle_bars[fill_index:]:
        stop_hit = bar.low <= stop_price
        target_hit = bar.high >= target_price
        if stop_hit and target_hit:
            exit_price = stop_price
            exit_reason = "stop"
            exit_time = bar.timestamp
            break
        if stop_hit:
            exit_price = stop_price
            exit_reason = "stop"
            exit_time = bar.timestamp
            break
        if target_hit:
            exit_price = target_price
            exit_reason = "target"
            exit_time = bar.timestamp
            break
        if _bar_time(bar) >= cutoff:
            exit_price = bar.close
            exit_reason = "exit_before_close"
            exit_time = bar.timestamp
            break

    if exit_price is None:
        last_bar = vehicle_bars[-1]
        exit_price = last_bar.close
        exit_time = last_bar.timestamp

    exit_price_after_slippage = exit_price * (1 - slip)

    risk_per_share = entry_price - stop_price
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
        stop_price=stop_price,
        target_price=target_price,
        exit_time=exit_time,
        exit_price=exit_price_after_slippage,
        exit_reason=exit_reason,
        r_result=r_result,
        dollar_result=dollar_result,
        percent_result=percent_result,
    )


def _parse_cutoff(exit_cutoff_time: str) -> dt_time:
    hour, minute = exit_cutoff_time.split(":")
    return dt_time(int(hour), int(minute))


def evaluate_day(day: DaySession, config: BacktestConfig) -> BacktestTradeResult | SkippedDay:
    """Evaluates one `DaySession`. Returns a `SkippedDay` when required
    bar data is missing, otherwise a `BacktestTradeResult` (which itself
    carries `skipped=True` for a legitimate NO_TRADE/CONFLICT read)."""
    if not day.qqq_bars:
        return SkippedDay(date=day.date, reason="missing qqq_bars")

    first_hour = _first_hour_bars(day.qqq_bars, config.first_hour_minutes)
    if not first_hour:
        return SkippedDay(date=day.date, reason="no bars within first-hour window")

    day_open = day.qqq_bars[0].open
    if day.qqq_previous_close <= 0:
        return SkippedDay(date=day.date, reason="missing qqq_previous_close")

    gap_percent = (day_open - day.qqq_previous_close) / day.qqq_previous_close * 100.0
    if abs(gap_percent) > config.max_gap_percent:
        return _skipped_trade(day, f"gap too large: {gap_percent:.2f}% exceeds {config.max_gap_percent:.2f}%")

    first_hour_high = max(b.high for b in first_hour)
    first_hour_low = min(b.low for b in first_hour)
    first_hour_range_percent = (first_hour_high - first_hour_low) / day_open * 100.0 if day_open > 0 else 0.0

    if first_hour_range_percent < config.min_first_hour_range_percent:
        return _skipped_trade(
            day,
            f"range too small: {first_hour_range_percent:.3f}% below {config.min_first_hour_range_percent:.3f}%",
        )
    if first_hour_range_percent > config.max_first_hour_range_percent:
        return _skipped_trade(
            day,
            f"range too large: {first_hour_range_percent:.3f}% exceeds {config.max_first_hour_range_percent:.3f}%",
        )

    bars_after = list(day.qqq_bars[len(first_hour):])
    if not bars_after:
        return _skipped_trade(day, "no breakout: no bars after first-hour window")

    vwap_series = _intraday_vwap_series(day.qqq_bars)
    vwap_after = vwap_series[len(first_hour):]

    tqqq_indices, sqqq_indices, any_above_high, any_below_low = _find_signal_bars(
        bars_after, vwap_after, first_hour_high, first_hour_low
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
        reason = "VWAP conflict: breakout occurred but VWAP never confirmed" if (
            any_above_high or any_below_low
        ) else "no breakout"
        return _skipped_trade(day, reason)

    if not vehicle_bars:
        return SkippedDay(date=day.date, reason=f"missing {vehicle_symbol.lower()}_bars")

    relative_signal_index = indices[0]
    # `vehicle_bars` is index-aligned to the FULL day's qqq_bars (first-hour
    # bars included), but `indices`/`bars_after`/`vwap_after` are relative to
    # only the post-first-hour subset -- convert back to an absolute index
    # before touching the vehicle's own bar list.
    absolute_signal_index = len(first_hour) + relative_signal_index
    if absolute_signal_index >= len(vehicle_bars):
        return SkippedDay(date=day.date, reason=f"{vehicle_symbol.lower()}_bars shorter than qqq_bars at signal index")

    entry_qqq_bar = bars_after[relative_signal_index]
    entry_vwap = vwap_after[relative_signal_index]
    stop_distance_percent = abs(entry_qqq_bar.close - entry_vwap) / entry_qqq_bar.close * LEVERAGED_ETF_FACTOR

    return _resolve_vehicle_trade(
        day=day,
        vehicle_bars=vehicle_bars,
        signal_index=absolute_signal_index,
        direction=direction,
        vehicle_symbol=vehicle_symbol,
        stop_distance_percent=stop_distance_percent,
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


def summarize_trades(
    trade_log: Sequence[BacktestTradeResult],
    skipped_days: Sequence[SkippedDay],
) -> BacktestSummary:
    """Pure rollup of an already-computed trade log. Never fabricates a
    number on a thin sample -- returns `None` for any metric that needs
    more data than is present."""
    taken = [t for t in trade_log if not t.skipped and t.dollar_result is not None]
    dollar_results = [t.dollar_result for t in taken]
    percent_results = [t.percent_result for t in taken if t.percent_result is not None]

    total_trades = len(taken)
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

    return BacktestSummary(
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
        trade_log=tuple(trade_log),
        skipped_days=tuple(skipped_days),
    )


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
    return summarize_trades(trade_log, skipped_days)


def run_slippage_stress(
    sessions: Sequence[DaySession],
    config: BacktestConfig,
    levels: Sequence[float] = DEFAULT_SLIPPAGE_STRESS_LEVELS,
) -> SlippageStressReport:
    """Re-runs the same sessions once per slippage level, varying only
    `slippage_percent`. Every other config field is held fixed."""
    points = []
    for level in levels:
        level_config = BacktestConfig(
            max_gap_percent=config.max_gap_percent,
            min_first_hour_range_percent=config.min_first_hour_range_percent,
            max_first_hour_range_percent=config.max_first_hour_range_percent,
            first_hour_minutes=config.first_hour_minutes,
            exit_cutoff_time=config.exit_cutoff_time,
            slippage_percent=level,
            commission_per_trade=config.commission_per_trade,
            max_trades_per_day=config.max_trades_per_day,
            target_r_multiple=config.target_r_multiple,
            position_dollar_size=config.position_dollar_size,
            same_day_conflict_priority=config.same_day_conflict_priority,
        )
        points.append(SlippageStressPoint(slippage_percent=level, summary=run_backtest(sessions, level_config)))
    return SlippageStressReport(points=tuple(points))
