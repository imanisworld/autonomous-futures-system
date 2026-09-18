"""Causal market-context snapshots for Strat trigger-time evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from strategy.strat_classifier import TWO_DOWN, TWO_UP, StratBar, classify_bar
from .causal_bars import HOUR_1, MINUTE_30, Bar, build_session_candle, build_session_timeframe, classify_last_bar, ema, session_vwap
from .coverage_observer import EMA_PERIOD, SymbolSeries
from .session_calendar import Session


@dataclass(frozen=True)
class TriggerAlignmentSnapshot:
    cutoff: str
    direction: str
    desired_trend: str
    desired_candle: str
    spy_trend: str | None
    qqq_trend: str | None
    completed_hourly_type: str | None
    prior_daily_type: str | None
    developing_hourly_type: str | None
    developing_daily_type: str | None
    completed_alignment_ok: bool
    developing_alignment_ok: bool
    completed_failures: tuple[str, ...]
    developing_failures: tuple[str, ...]


def _closed_through(bars: Sequence[Bar], cutoff: datetime) -> list[Bar]:
    point = cutoff.astimezone(timezone.utc)
    return [bar for bar in bars if bar.start_utc + MINUTE_30.delta <= point]


def _trend(series: Sequence[Bar], session_series: Sequence[Bar]) -> str | None:
    if not series or not session_series:
        return None
    vwap = session_vwap(session_series)
    ema20 = ema([bar.close for bar in series], EMA_PERIOD)
    if vwap is None or ema20 is None:
        return None
    close = series[-1].close
    if close > vwap and close > ema20:
        return "bullish"
    if close < vwap and close < ema20:
        return "bearish"
    return "neutral"


def _index_trend_at(series: SymbolSeries | None, session: Session, cutoff: datetime) -> str | None:
    if series is None or not series.observable:
        return None
    closed = _closed_through(series.series, cutoff)
    current = [bar for bar in series.by_session.get(session.date, ()) if bar.start_utc + MINUTE_30.delta <= cutoff.astimezone(timezone.utc)]
    return _trend(closed, current)


def _completed_hourly_type(series: SymbolSeries, session: Session, cutoff: datetime) -> str | None:
    current = [bar for bar in series.by_session.get(session.date, ()) if bar.start_utc + MINUTE_30.delta <= cutoff.astimezone(timezone.utc)]
    hourly = build_session_timeframe(current, MINUTE_30, HOUR_1, session.open)
    if len(hourly) < 2:
        return None
    return classify_last_bar(hourly).get("candle_type")


def _prior_daily_type(series: SymbolSeries, session: Session) -> str | None:
    prior = [series.by_session[item.date] for item in series.sessions if item.date < session.date and item.date in series.by_session]
    candles = [bar for bar in (build_session_candle(day) for day in prior) if bar]
    if len(candles) < 2:
        return None
    return classify_last_bar(candles).get("candle_type")


def _developing_daily_type(series: SymbolSeries, session: Session, cutoff: datetime) -> str | None:
    prior_days = [series.by_session[item.date] for item in series.sessions if item.date < session.date and item.date in series.by_session]
    previous_candles = [candle for candle in (build_session_candle(day) for day in prior_days) if candle is not None]
    if not previous_candles:
        return None
    current_bars = [bar for bar in series.by_session.get(session.date, ()) if bar.start_utc + MINUTE_30.delta <= cutoff.astimezone(timezone.utc)]
    current = build_session_candle(current_bars)
    if current is None:
        return None
    previous = previous_candles[-1]
    return classify_bar(StratBar(high=current.high, low=current.low), StratBar(high=previous.high, low=previous.low))


def _developing_hourly_type(series: SymbolSeries, session: Session, cutoff: datetime) -> str | None:
    point = cutoff.astimezone(timezone.utc)
    current = [bar for bar in series.by_session.get(session.date, ()) if bar.start_utc + MINUTE_30.delta <= point]
    if len(current) < 3:
        return None
    open_utc = session.open.astimezone(timezone.utc)
    elapsed = max(0.0, (point - open_utc).total_seconds())
    hour_index = int(elapsed // HOUR_1.seconds)
    bucket_start = open_utc + HOUR_1.delta * hour_index
    partial = [bar for bar in current if bucket_start <= bar.start_utc < bucket_start + HOUR_1.delta]
    completed = build_session_timeframe(current, MINUTE_30, HOUR_1, session.open)
    previous_hour = None
    for bar in completed:
        if bar.start_utc < bucket_start:
            previous_hour = bar
    if previous_hour is None:
        return None
    developing = build_session_candle(partial)
    if developing is None:
        if len(completed) < 2:
            return None
        return classify_last_bar(completed).get("candle_type")
    return classify_bar(StratBar(high=developing.high, low=developing.low), StratBar(high=previous_hour.high, low=previous_hour.low))


def alignment_at_trigger(ticker: SymbolSeries, session: Session, cutoff: datetime, *, direction: str, spy: SymbolSeries | None, qqq: SymbolSeries | None) -> TriggerAlignmentSnapshot:
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must be timezone-aware")
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    desired_trend = "bullish" if direction == "LONG" else "bearish"
    desired_candle = TWO_UP if direction == "LONG" else TWO_DOWN
    spy_trend = _index_trend_at(spy, session, cutoff)
    qqq_trend = _index_trend_at(qqq, session, cutoff)
    completed_hourly = _completed_hourly_type(ticker, session, cutoff)
    prior_daily = _prior_daily_type(ticker, session)
    developing_hourly = _developing_hourly_type(ticker, session, cutoff)
    developing_daily = _developing_daily_type(ticker, session, cutoff)
    completed_checks = (("spy", spy_trend == desired_trend), ("qqq", qqq_trend == desired_trend), ("hourly", completed_hourly == desired_candle), ("daily", prior_daily == desired_candle))
    developing_checks = (("spy", spy_trend == desired_trend), ("qqq", qqq_trend == desired_trend), ("hourly", developing_hourly == desired_candle), ("daily", developing_daily == desired_candle))
    completed_failures = tuple(name for name, ok in completed_checks if not ok)
    developing_failures = tuple(name for name, ok in developing_checks if not ok)
    return TriggerAlignmentSnapshot(
        cutoff=cutoff.astimezone(timezone.utc).isoformat(), direction=direction,
        desired_trend=desired_trend, desired_candle=desired_candle,
        spy_trend=spy_trend, qqq_trend=qqq_trend,
        completed_hourly_type=completed_hourly, prior_daily_type=prior_daily,
        developing_hourly_type=developing_hourly, developing_daily_type=developing_daily,
        completed_alignment_ok=not completed_failures, developing_alignment_ok=not developing_failures,
        completed_failures=completed_failures, developing_failures=developing_failures,
    )
