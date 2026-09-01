"""Pure, source-neutral bar math for causal options market context.

No network, no provider SDK, no clock reads: every function here is a
deterministic transform of bars the caller already fetched. Provider
transport lives in :mod:`alert_ranker.bar_provider`; session authority lives
in :mod:`alert_ranker.session_calendar`.

The rules encoded here come from measured provider behaviour (VPS evidence,
2026-09-01), not from assumption:

* The provider's ``end`` parameter filters on bar START, so a bar whose
  interval is still open at the information cutoff is still returned, fully
  populated. Trusting ``end`` alone leaks future information into a decision.
  :func:`completed_bars` is the client-side guard.
* Native clock-aligned hourly bars mix pre-market into the opening candle,
  so a regular-session hourly candle must be rebuilt from session-aligned
  30-minute bars (:func:`build_session_timeframe`).
* The vendor daily bar is a hybrid: its high/low track the regular session
  but its open, close and volume include extended hours. A regular-session
  daily candle must likewise be reconstructed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from strategy.strat_classifier import classify_from_ohlc

__all__ = [
    "Bar",
    "Timeframe",
    "MINUTE_1",
    "MINUTE_5",
    "MINUTE_15",
    "MINUTE_30",
    "HOUR_1",
    "TIMEFRAMES",
    "bar_close",
    "completed_bars",
    "session_bars",
    "expected_bar_starts",
    "missing_bar_starts",
    "build_session_timeframe",
    "build_session_candle",
    "session_vwap",
    "ema",
    "classify_last_bar",
    "prior_high_low",
]


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar, timestamped by the START of its interval (UTC)."""

    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("Bar.start must be timezone-aware")

    @property
    def start_utc(self) -> datetime:
        return self.start.astimezone(timezone.utc)


@dataclass(frozen=True)
class Timeframe:
    """A bar interval, with the provider's spelling for it."""

    name: str
    delta: timedelta

    @property
    def seconds(self) -> int:
        return int(self.delta.total_seconds())


MINUTE_1 = Timeframe("1Min", timedelta(minutes=1))
MINUTE_5 = Timeframe("5Min", timedelta(minutes=5))
MINUTE_15 = Timeframe("15Min", timedelta(minutes=15))
MINUTE_30 = Timeframe("30Min", timedelta(minutes=30))
HOUR_1 = Timeframe("1Hour", timedelta(hours=1))

TIMEFRAMES: dict[str, Timeframe] = {
    tf.name: tf for tf in (MINUTE_1, MINUTE_5, MINUTE_15, MINUTE_30, HOUR_1)
}


def bar_close(bar: Bar, timeframe: Timeframe) -> datetime:
    """The instant the bar's interval ends (exclusive)."""
    return bar.start_utc + timeframe.delta


def completed_bars(
    bars: Iterable[Bar], timeframe: Timeframe, information_cutoff: datetime
) -> list[Bar]:
    """Bars whose FULL interval finished at or before ``information_cutoff``.

    This is the no-future-leakage guard. The provider returns a bar whose
    start precedes the request's ``end`` even when that bar was still being
    built, so a bar must be judged by its close, never by its start.
    """
    if information_cutoff.tzinfo is None:
        raise ValueError("information_cutoff must be timezone-aware")
    cutoff = information_cutoff.astimezone(timezone.utc)
    return sorted(
        (bar for bar in bars if bar_close(bar, timeframe) <= cutoff),
        key=lambda bar: bar.start_utc,
    )


def session_bars(
    bars: Iterable[Bar],
    timeframe: Timeframe,
    session_open: datetime,
    session_close: datetime,
) -> list[Bar]:
    """Bars fully contained in the regular session.

    A bar qualifies when it starts at or after the session open and its
    interval ends at or before the session close, which keeps pre-market and
    post-close intervals out and correctly truncates an early-close session.
    Membership is deliberately NOT decided by the bar's start alone: the
    provider's inclusive ``end`` bound would otherwise admit the first
    post-close interval.
    """
    open_utc = session_open.astimezone(timezone.utc)
    close_utc = session_close.astimezone(timezone.utc)
    return sorted(
        (
            bar
            for bar in bars
            if bar.start_utc >= open_utc and bar_close(bar, timeframe) <= close_utc
        ),
        key=lambda bar: bar.start_utc,
    )


def expected_bar_starts(
    timeframe: Timeframe, session_open: datetime, session_close: datetime
) -> list[datetime]:
    """Every bar start a complete regular session should contain."""
    open_utc = session_open.astimezone(timezone.utc)
    close_utc = session_close.astimezone(timezone.utc)
    starts: list[datetime] = []
    cursor = open_utc
    while cursor + timeframe.delta <= close_utc:
        starts.append(cursor)
        cursor = cursor + timeframe.delta
    return starts


def missing_bar_starts(
    bars: Sequence[Bar],
    timeframe: Timeframe,
    session_open: datetime,
    session_close: datetime,
    *,
    through: datetime | None = None,
) -> list[datetime]:
    """Expected bar starts absent from ``bars``.

    ``through`` limits the expectation to intervals that had already closed by
    that instant, so an in-progress session is not reported as full of holes.
    A non-empty result means the series has gaps and must not be aggregated:
    silently pairing across a hole would fabricate a candle.
    """
    have = {bar.start_utc for bar in bars}
    expected = expected_bar_starts(timeframe, session_open, session_close)
    if through is not None:
        limit = through.astimezone(timezone.utc)
        expected = [start for start in expected if start + timeframe.delta <= limit]
    return [start for start in expected if start not in have]


def build_session_timeframe(
    bars: Sequence[Bar],
    source_timeframe: Timeframe,
    target_timeframe: Timeframe,
    session_open: datetime,
) -> list[Bar]:
    """Aggregate session-aligned bars into a coarser session-aligned series.

    Grouping starts at the session open, which is what makes the resulting
    hourly candle a *session* hour rather than a clock hour. Only whole groups
    are emitted: a trailing partial group is dropped rather than published as
    a completed candle, and a group whose members are not contiguous is
    dropped rather than aggregated across a gap.
    """
    if target_timeframe.seconds % source_timeframe.seconds != 0:
        raise ValueError(
            f"{target_timeframe.name} is not a whole multiple of {source_timeframe.name}"
        )
    factor = target_timeframe.seconds // source_timeframe.seconds
    if factor < 1:
        raise ValueError("target timeframe must be coarser than the source timeframe")

    ordered = sorted(bars, key=lambda bar: bar.start_utc)
    open_utc = session_open.astimezone(timezone.utc)
    by_start = {bar.start_utc: bar for bar in ordered}

    built: list[Bar] = []
    if not ordered:
        return built
    cursor = open_utc
    last_start = ordered[-1].start_utc
    while cursor <= last_start:
        group_starts = [cursor + source_timeframe.delta * i for i in range(factor)]
        group = [by_start.get(start) for start in group_starts]
        if all(member is not None for member in group):
            members = [member for member in group if member is not None]
            built.append(
                Bar(
                    start=cursor,
                    open=members[0].open,
                    high=max(member.high for member in members),
                    low=min(member.low for member in members),
                    close=members[-1].close,
                    volume=sum(member.volume for member in members),
                    vwap=_weighted_vwap(members),
                )
            )
        cursor = cursor + target_timeframe.delta
    return built


def build_session_candle(bars: Sequence[Bar]) -> Bar | None:
    """Collapse one session's bars into a single regular-session candle.

    Used to reconstruct a daily Strat candle, because the vendor daily bar
    carries extended-hours open, close and volume.
    """
    ordered = sorted(bars, key=lambda bar: bar.start_utc)
    if not ordered:
        return None
    return Bar(
        start=ordered[0].start_utc,
        open=ordered[0].open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=ordered[-1].close,
        volume=sum(bar.volume for bar in ordered),
        vwap=_weighted_vwap(ordered),
    )


def _weighted_vwap(bars: Sequence[Bar]) -> float | None:
    total_volume = sum(bar.volume for bar in bars)
    if total_volume <= 0:
        return None
    if any(bar.vwap is None for bar in bars):
        return None
    return sum(float(bar.vwap) * bar.volume for bar in bars) / total_volume


def session_vwap(bars: Sequence[Bar]) -> float | None:
    """Cumulative session VWAP from per-bar volume-weighted prices.

    Returns ``None`` when any bar lacks a volume-weighted price or the session
    has no volume, so a caller can fail closed instead of substituting a
    close-price average. Verified on 2026-09-01 to reproduce the 1-minute
    construction within 0.2 basis points from 30-minute bars.
    """
    if not bars:
        return None
    return _weighted_vwap(bars)


def ema(values: Sequence[float], period: int) -> float | None:
    """Exponential moving average over completed values.

    Returns ``None`` when there is less than ``period`` history: a short
    series yields a number that looks valid but is dominated by its seed, and
    treating that as a real EMA is how missing history becomes a fake signal.
    """
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    average = sum(values[:period]) / period
    for value in values[period:]:
        average = value * multiplier + average * (1 - multiplier)
    return average


def prior_high_low(bars: Sequence[Bar]) -> tuple[float, float] | None:
    """High and low of the bar before the most recent completed bar."""
    if len(bars) < 2:
        return None
    prior = bars[-2]
    return prior.high, prior.low


def classify_last_bar(bars: Sequence[Bar]) -> dict[str, str | None]:
    """Strat classification of the most recent completed bar.

    Delegates to the shared futures classifier so the options lane and the
    futures lane cannot drift apart on what a 2-up is.
    """
    empty: dict[str, str | None] = {
        "candle_type": None,
        "previous_candle_type": None,
        "strat_sequence": None,
        "strat_direction": None,
    }
    if len(bars) < 2:
        return empty
    current = bars[-1]
    previous = bars[-2]
    two_back = bars[-3] if len(bars) >= 3 else None
    context = classify_from_ohlc(
        current_high=current.high,
        current_low=current.low,
        previous_high=previous.high,
        previous_low=previous.low,
        two_bars_back_high=two_back.high if two_back else None,
        two_bars_back_low=two_back.low if two_back else None,
    )
    return {
        "candle_type": context.current_bar_type,
        "previous_candle_type": context.previous_bar_type,
        "strat_sequence": context.strat_sequence,
        "strat_direction": context.strat_direction,
    }


def with_start(bar: Bar, start: datetime) -> Bar:
    """Copy of ``bar`` re-anchored to ``start`` (test and adapter helper)."""
    return replace(bar, start=start)
