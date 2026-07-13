"""stocks_advisory/qqq_signal_builder.py

Derives a `QQQSignalInput` (see `tqqq_sqqq_models.py`) for the TQQQ/SQQQ
Paper Advisory Bot v1 decision engine (`tqqq_sqqq_decision.py`) from
completed QQQ bars only. This module performs no decisioning of its
own -- see `tqqq_sqqq_decision.py` for that -- and no I/O: every bar,
every threshold, and (mirroring the same convention
`backtest_models.DaySession` already establishes for
`qqq_relative_volume`) `qqq_relative_volume` itself are supplied by the
caller. Never reads the system clock; every "has the first hour
closed" judgment is made against the supplied bars' own timestamps,
never wall-clock time. No broker, order, execution, futures, or
options_manager import of any kind.

Opening-range slicing and the intraday VWAP formula mirror
`stocks_advisory/tqqq_sqqq_backtest._opening_range_bars()` /
`_intraday_vwap_series()` exactly (same math, duplicated here rather
than imported -- matching the precedent already established by
`scripts/stocks_advisory_robustness_audit.py`'s
`_session_gap_and_range_percent()` -- so a paper-advisory read and a
backtest read of the same bars can never silently diverge without both
being reviewed together).

Fail-closed: every rejection below returns a `SignalBuildResult` with
`ok=False` and a named `reject_reason` -- this function never raises on
bad input data and never fabricates a missing value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from .backtest_models import Bar
from .tqqq_sqqq_models import QQQSignalInput

OPENING_RANGE_MINUTES = 60
"""First trading hour, in minutes. Matches
`BacktestConfig.opening_range_minutes`'s default (60) -- a fixed
convention shared with the backtest lane, not a tunable parameter of
this module."""


def _parse_ts(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def _opening_range_bars(bars: Sequence[Bar], opening_range_minutes: int = OPENING_RANGE_MINUTES) -> list[Bar]:
    """Mirrors tqqq_sqqq_backtest._opening_range_bars() exactly."""
    if not bars:
        return []
    start = _parse_ts(bars[0].timestamp)
    cutoff_seconds = opening_range_minutes * 60
    return [b for b in bars if (_parse_ts(b.timestamp) - start).total_seconds() < cutoff_seconds]


def _intraday_vwap_series(bars: Sequence[Bar]) -> list[float]:
    """Mirrors tqqq_sqqq_backtest._intraday_vwap_series() exactly --
    cumulative, causal VWAP; entry i uses only bars[0..i]."""
    vwaps: list[float] = []
    cum_pv = 0.0
    cum_vol = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_vol += bar.volume
        vwaps.append(cum_pv / cum_vol if cum_vol > 0 else typical)
    return vwaps


@dataclass(frozen=True, kw_only=True)
class SignalBuildResult:
    """Outcome of attempting to build a `QQQSignalInput` from bars.
    Exactly one of `signal`/`reject_reason` is meaningful, selected by
    `ok`."""

    ok: bool
    signal: Optional[QQQSignalInput] = None
    reject_reason: str = ""


def build_qqq_signal(
    *,
    date: str,
    qqq_bars_today: Sequence[Bar],
    qqq_previous_day_close: float,
    qqq_previous_day_high: float,
    qqq_previous_day_low: float,
    qqq_relative_volume: float,
    allowed_max_gap_percent: float,
    allowed_min_first_hour_range: float,
    allowed_max_first_hour_range: float,
    market_regime_label: Optional[str] = None,
) -> SignalBuildResult:
    """Builds one day's `QQQSignalInput` from that day's completed QQQ
    bars, supplied in ascending timestamp order starting at the
    session open.

    Fails closed (returns `ok=False`) rather than raising or guessing
    whenever:

    - `qqq_bars_today` is empty
    - any bar timestamp is malformed, or bars are not strictly
      ascending (duplicate or out-of-order bar -- also the dedup guard
      against replaying the same bar twice)
    - any bar has a non-positive open/high/low/close or negative volume
    - `qqq_previous_day_close` is not positive (gap_percent would be
      undefined)
    - the first trading hour has not fully closed yet -- i.e. no bar in
      `qqq_bars_today` starts at or after the 60-minute mark. A caller
      that runs this before the first hour closes gets rejected here,
      not a guess from partial data.

    `qqq_current_price` is the close of the LAST bar in
    `qqq_bars_today`; `qqq_vwap`/`qqq_open`/`qqq_gap_percent` are
    computed from the full supplied sequence starting at the session
    open. The caller decides how much of the day to include -- this
    function only requires that the first hour is fully present.
    """
    if not qqq_bars_today:
        return SignalBuildResult(ok=False, reject_reason="no QQQ bars supplied for today")

    try:
        parsed = [_parse_ts(b.timestamp) for b in qqq_bars_today]
    except ValueError as exc:
        return SignalBuildResult(ok=False, reject_reason=f"malformed bar timestamp: {exc}")

    for prev, cur in zip(parsed, parsed[1:]):
        if cur <= prev:
            return SignalBuildResult(
                ok=False,
                reject_reason="bars are not strictly ascending by timestamp (duplicate or out-of-order bar)",
            )

    for b in qqq_bars_today:
        if b.open <= 0 or b.high <= 0 or b.low <= 0 or b.close <= 0:
            return SignalBuildResult(ok=False, reject_reason=f"non-positive OHLC in bar at {b.timestamp}")
        if b.volume < 0:
            return SignalBuildResult(ok=False, reject_reason=f"negative volume in bar at {b.timestamp}")

    if qqq_previous_day_close <= 0:
        return SignalBuildResult(ok=False, reject_reason="previous-day close must be positive")
    if qqq_previous_day_high <= 0 or qqq_previous_day_low <= 0:
        return SignalBuildResult(ok=False, reject_reason="previous-day high/low must be positive")

    opening_range = _opening_range_bars(qqq_bars_today)
    if not opening_range:
        return SignalBuildResult(ok=False, reject_reason="no bars within the first trading hour")
    if len(qqq_bars_today) <= len(opening_range):
        return SignalBuildResult(
            ok=False,
            reject_reason="first trading hour has not closed yet -- no completed bar at/after the 60-minute mark",
        )

    day_open = qqq_bars_today[0].open
    gap_percent = (day_open - qqq_previous_day_close) / qqq_previous_day_close * 100.0

    first_hour_high = max(b.high for b in opening_range)
    first_hour_low = min(b.low for b in opening_range)
    first_hour_close = opening_range[-1].close

    vwap_series = _intraday_vwap_series(qqq_bars_today)

    signal = QQQSignalInput(
        date=date,
        qqq_open=day_open,
        qqq_previous_day_high=qqq_previous_day_high,
        qqq_previous_day_low=qqq_previous_day_low,
        qqq_previous_day_close=qqq_previous_day_close,
        qqq_gap_percent=gap_percent,
        qqq_first_hour_high=first_hour_high,
        qqq_first_hour_low=first_hour_low,
        qqq_first_hour_close=first_hour_close,
        qqq_vwap=vwap_series[-1],
        qqq_current_price=qqq_bars_today[-1].close,
        relative_volume=qqq_relative_volume,
        allowed_max_gap_percent=allowed_max_gap_percent,
        allowed_min_first_hour_range=allowed_min_first_hour_range,
        allowed_max_first_hour_range=allowed_max_first_hour_range,
        market_regime_label=market_regime_label,
    )
    return SignalBuildResult(ok=True, signal=signal)
