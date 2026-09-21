"""Observer-only non-Strat structural coverage for the options lane.

This module is deliberately isolated from scanner alerting, contract selection,
risk, broker, and execution code. It answers a narrower research question:
which mechanically-defined level interactions existed in completed 5-minute
regular-session bars, independent of Strat family classification?

Definitions are causal at bar close. Context fields (EMA20, relative volume,
SPY/QQQ trend) are recorded but do not promote an event into an alert or trade.
Generic support/resistance families are intentionally absent until a frozen
planned-level source exists.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any, Iterable, Sequence

from .causal_bars import Bar, MINUTE_5, ema, session_vwap

OBSERVER_ID = "OPTIONS_NON_STRAT_COVERAGE"
OBSERVER_VERSION = "ns-v0.1"
OBSERVED_TIMEFRAME = "5m"
EMA_PERIOD = 20
VOLUME_LOOKBACK = 20
SIP_DELAY_BUFFER = timedelta(seconds=960)

FAMILIES = (
    "VWAP_RECLAIM_LONG",
    "VWAP_FAILED_RECLAIM_SHORT",
    "VWAP_TEST_HOLD_LONG",
    "VWAP_TEST_HOLD_SHORT",
    "PDH_RECLAIM_LONG",
    "PDL_RECLAIM_SHORT",
    "PDH_REJECTION_SHORT",
    "PDL_REJECTION_LONG",
    "PDH_BREAK_RETEST_LONG",
    "PDL_BREAK_RETEST_SHORT",
    "ORB_BREAKOUT_LONG",
    "ORB_BREAKOUT_SHORT",
    "ORB_REJECTION_SHORT",
    "ORB_REJECTION_LONG",
    "ORB_BREAK_RETEST_LONG",
    "ORB_BREAK_RETEST_SHORT",
)


@dataclass(frozen=True)
class NonStratEvent:
    observer_id: str
    observer_version: str
    symbol: str
    timeframe: str
    session_date: str
    bar_start: str
    bar_close: str
    family: str
    direction: str
    level_name: str
    level_value: float
    trigger_price: float
    vwap: float | None
    ema20: float | None
    volume_ratio: float | None
    spy_trend: str | None
    qqq_trend: str | None
    market_aligned: bool | None
    earliest_sip_visibility: str
    source_rule: str
    episode_id: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutcomeView:
    observer_version: str
    symbol: str
    session_date: str
    family: str
    direction: str
    episode_id: str
    event_bar_start: str
    horizon: str
    bars_observed: int
    close_return_bps: float | None
    mfe_bps: float | None
    mae_bps: float | None

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _close_time(bar: Bar) -> datetime:
    return bar.start_utc + MINUTE_5.delta


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _volume_ratio(history: Sequence[Bar], current: Bar) -> float | None:
    prior = [float(bar.volume) for bar in history[-VOLUME_LOOKBACK:]]
    baseline = _mean(prior)
    if baseline is None or baseline <= 0:
        return None
    return round(float(current.volume) / baseline, 6)


def _trend(history: Sequence[Bar], session_so_far: Sequence[Bar]) -> str | None:
    if not session_so_far:
        return None
    vwap = session_vwap(session_so_far)
    ema20 = ema([bar.close for bar in history], EMA_PERIOD)
    if vwap is None or ema20 is None:
        return None
    close = session_so_far[-1].close
    if close > vwap and close > ema20:
        return "bullish"
    if close < vwap and close < ema20:
        return "bearish"
    return "neutral"


def _index_trend(
    history: Sequence[Bar] | None,
    session_bars: Sequence[Bar] | None,
    through: datetime,
) -> str | None:
    if history is None or session_bars is None:
        return None
    session_part = [bar for bar in session_bars if _close_time(bar) <= through]
    known = [bar for bar in history if _close_time(bar) <= through] + session_part
    return _trend(known, session_part)


def _market_aligned(direction: str, spy: str | None, qqq: str | None) -> bool | None:
    if spy is None or qqq is None:
        return None
    wanted = "bullish" if direction == "LONG" else "bearish"
    return spy == wanted and qqq == wanted


def _event(
    *,
    symbol: str,
    session_date: str,
    bar: Bar,
    family: str,
    direction: str,
    level_name: str,
    level_value: float,
    trigger_price: float,
    vwap: float | None,
    ema20_value: float | None,
    volume_ratio: float | None,
    spy_trend: str | None,
    qqq_trend: str | None,
    source_rule: str,
) -> NonStratEvent:
    close_at = _close_time(bar)
    return NonStratEvent(
        observer_id=OBSERVER_ID,
        observer_version=OBSERVER_VERSION,
        symbol=symbol.strip().upper(),
        timeframe=OBSERVED_TIMEFRAME,
        session_date=session_date,
        bar_start=_iso(bar.start_utc),
        bar_close=_iso(close_at),
        family=family,
        direction=direction,
        level_name=level_name,
        level_value=float(level_value),
        trigger_price=float(trigger_price),
        vwap=float(vwap) if vwap is not None else None,
        ema20=float(ema20_value) if ema20_value is not None else None,
        volume_ratio=volume_ratio,
        spy_trend=spy_trend,
        qqq_trend=qqq_trend,
        market_aligned=_market_aligned(direction, spy_trend, qqq_trend),
        earliest_sip_visibility=_iso(close_at + SIP_DELAY_BUFFER),
        source_rule=source_rule,
    )


def observe_session(
    *,
    symbol: str,
    session_date: str,
    prior_session_bars: Sequence[Bar],
    session_bars: Sequence[Bar],
    history_bars: Sequence[Bar] = (),
    spy_history: Sequence[Bar] | None = None,
    spy_session: Sequence[Bar] | None = None,
    qqq_history: Sequence[Bar] | None = None,
    qqq_session: Sequence[Bar] | None = None,
) -> list[NonStratEvent]:
    """Return raw, causal level-interaction events for one completed session.

    The opening range is the first six completed 5-minute bars (30 minutes).
    PDH/PDL are the previous regular session's extrema. VWAP is cumulative from
    completed bars in the current regular session. No event uses a later bar.
    """

    current = sorted(session_bars, key=lambda bar: bar.start_utc)
    prior = sorted(prior_session_bars, key=lambda bar: bar.start_utc)
    history = sorted(history_bars, key=lambda bar: bar.start_utc)
    if len(current) < 2 or not prior:
        return []

    pdh = max(bar.high for bar in prior)
    pdl = min(bar.low for bar in prior)
    orb_ready = len(current) >= 6
    orb_high = max(bar.high for bar in current[:6]) if orb_ready else None
    orb_low = min(bar.low for bar in current[:6]) if orb_ready else None

    events: list[NonStratEvent] = []
    retest_armed: dict[str, bool] = {
        "PDH_LONG": False,
        "PDL_SHORT": False,
        "ORB_LONG": False,
        "ORB_SHORT": False,
    }

    for idx in range(1, len(current)):
        bar = current[idx]
        prev = current[idx - 1]
        so_far = current[: idx + 1]
        prev_so_far = current[:idx]
        current_vwap = session_vwap(so_far)
        previous_vwap = session_vwap(prev_so_far)
        known_before = [*history, *current[:idx]]
        known_through = [*history, *so_far]
        ema20_value = ema([b.close for b in known_through], EMA_PERIOD)
        volume_ratio = _volume_ratio(known_before, bar)
        close_at = _close_time(bar)
        spy_trend = _index_trend(spy_history, spy_session, close_at)
        qqq_trend = _index_trend(qqq_history, qqq_session, close_at)

        def add(
            family: str,
            direction: str,
            level_name: str,
            level_value: float,
            source_rule: str,
        ) -> None:
            events.append(
                _event(
                    symbol=symbol,
                    session_date=session_date,
                    bar=bar,
                    family=family,
                    direction=direction,
                    level_name=level_name,
                    level_value=level_value,
                    trigger_price=bar.close,
                    vwap=current_vwap,
                    ema20_value=ema20_value,
                    volume_ratio=volume_ratio,
                    spy_trend=spy_trend,
                    qqq_trend=qqq_trend,
                    source_rule=source_rule,
                )
            )

        # VWAP: crossing, immediate failed reclaim, and touch/hold from the
        # already-correct side. These are distinct raw populations.
        if current_vwap is not None and previous_vwap is not None:
            reclaimed = prev.close <= previous_vwap and bar.close > current_vwap
            if reclaimed:
                add(
                    "VWAP_RECLAIM_LONG",
                    "LONG",
                    "VWAP",
                    current_vwap,
                    "prior_close<=prior_cum_vwap and close>current_cum_vwap",
                )

            if idx >= 2:
                two_back = current[idx - 2]
                two_back_vwap = session_vwap(current[: idx - 1])
                prior_was_reclaim = (
                    two_back_vwap is not None
                    and two_back.close <= two_back_vwap
                    and prev.close > previous_vwap
                )
                if prior_was_reclaim and bar.close < current_vwap:
                    add(
                        "VWAP_FAILED_RECLAIM_SHORT",
                        "SHORT",
                        "VWAP",
                        current_vwap,
                        "immediately_after_reclaim and close<current_cum_vwap",
                    )

            if (
                prev.close > previous_vwap
                and bar.low <= current_vwap <= bar.close
                and bar.close > current_vwap
                and not reclaimed
            ):
                add(
                    "VWAP_TEST_HOLD_LONG",
                    "LONG",
                    "VWAP",
                    current_vwap,
                    "prior_close>prior_cum_vwap and low<=vwap<=close",
                )
            if (
                prev.close < previous_vwap
                and bar.high >= current_vwap >= bar.close
                and bar.close < current_vwap
            ):
                add(
                    "VWAP_TEST_HOLD_SHORT",
                    "SHORT",
                    "VWAP",
                    current_vwap,
                    "prior_close<prior_cum_vwap and high>=vwap>=close",
                )

        # Previous-day levels. "Reclaim" keeps the repository's existing
        # strategy names: long through PDH, short through PDL.
        if prev.close <= pdh and bar.close > pdh:
            add(
                "PDH_RECLAIM_LONG",
                "LONG",
                "PDH",
                pdh,
                "prior_close<=pdh and close>pdh",
            )
            retest_armed["PDH_LONG"] = True
        elif retest_armed["PDH_LONG"] and bar.low <= pdh < bar.close:
            add(
                "PDH_BREAK_RETEST_LONG",
                "LONG",
                "PDH",
                pdh,
                "post_break low<=pdh and close>pdh",
            )
            retest_armed["PDH_LONG"] = False

        if prev.close >= pdl and bar.close < pdl:
            add(
                "PDL_RECLAIM_SHORT",
                "SHORT",
                "PDL",
                pdl,
                "prior_close>=pdl and close<pdl",
            )
            retest_armed["PDL_SHORT"] = True
        elif retest_armed["PDL_SHORT"] and bar.high >= pdl > bar.close:
            add(
                "PDL_BREAK_RETEST_SHORT",
                "SHORT",
                "PDL",
                pdl,
                "post_break high>=pdl and close<pdl",
            )
            retest_armed["PDL_SHORT"] = False

        if prev.close <= pdh and bar.high > pdh and bar.close < pdh:
            add(
                "PDH_REJECTION_SHORT",
                "SHORT",
                "PDH",
                pdh,
                "high>pdh and close<pdh from inside/below",
            )
        if prev.close >= pdl and bar.low < pdl and bar.close > pdl:
            add(
                "PDL_REJECTION_LONG",
                "LONG",
                "PDL",
                pdl,
                "low<pdl and close>pdl from inside/above",
            )

        # ORB becomes knowable only after the first 30 minutes are complete.
        if orb_high is not None and orb_low is not None and idx >= 6:
            if prev.close <= orb_high and bar.close > orb_high:
                add(
                    "ORB_BREAKOUT_LONG",
                    "LONG",
                    "ORB_HIGH",
                    orb_high,
                    "prior_close<=orb_high and close>orb_high",
                )
                retest_armed["ORB_LONG"] = True
            elif retest_armed["ORB_LONG"] and bar.low <= orb_high < bar.close:
                add(
                    "ORB_BREAK_RETEST_LONG",
                    "LONG",
                    "ORB_HIGH",
                    orb_high,
                    "post_break low<=orb_high and close>orb_high",
                )
                retest_armed["ORB_LONG"] = False

            if prev.close >= orb_low and bar.close < orb_low:
                add(
                    "ORB_BREAKOUT_SHORT",
                    "SHORT",
                    "ORB_LOW",
                    orb_low,
                    "prior_close>=orb_low and close<orb_low",
                )
                retest_armed["ORB_SHORT"] = True
            elif retest_armed["ORB_SHORT"] and bar.high >= orb_low > bar.close:
                add(
                    "ORB_BREAK_RETEST_SHORT",
                    "SHORT",
                    "ORB_LOW",
                    orb_low,
                    "post_break high>=orb_low and close<orb_low",
                )
                retest_armed["ORB_SHORT"] = False

            if prev.close <= orb_high and bar.high > orb_high and bar.close < orb_high:
                add(
                    "ORB_REJECTION_SHORT",
                    "SHORT",
                    "ORB_HIGH",
                    orb_high,
                    "high>orb_high and close<orb_high from inside/below",
                )
            if prev.close >= orb_low and bar.low < orb_low and bar.close > orb_low:
                add(
                    "ORB_REJECTION_LONG",
                    "LONG",
                    "ORB_LOW",
                    orb_low,
                    "low<orb_low and close>orb_low from inside/above",
                )

    return assign_episode_ids(events)


def assign_episode_ids(events: Iterable[NonStratEvent]) -> list[NonStratEvent]:
    """Group contiguous same-family/direction/level events into raw episodes."""

    ordered = sorted(
        list(events),
        key=lambda event: (event.symbol, event.family, event.direction, event.bar_start),
    )
    result: list[NonStratEvent] = []
    counters: dict[tuple[str, str, str, str], int] = {}
    previous: dict[tuple[str, str, str, str], NonStratEvent] = {}

    for event in ordered:
        key = (event.symbol, event.family, event.direction, event.level_name)
        prev = previous.get(key)
        contiguous = False
        if prev is not None:
            prev_start = datetime.fromisoformat(prev.bar_start.replace("Z", "+00:00"))
            cur_start = datetime.fromisoformat(event.bar_start.replace("Z", "+00:00"))
            contiguous = cur_start - prev_start == MINUTE_5.delta
        if not contiguous:
            counters[key] = counters.get(key, 0) + 1
        episode_id = (
            f"{event.session_date}:{event.symbol}:{event.family}:"
            f"{event.direction}:{counters[key]}"
        )
        stamped = replace(event, episode_id=episode_id)
        result.append(stamped)
        previous[key] = stamped

    return sorted(result, key=lambda event: (event.symbol, event.bar_start, event.family))


def measure_outcomes(
    events: Sequence[NonStratEvent],
    session_bars_by_symbol: dict[str, Sequence[Bar]],
    *,
    horizons_minutes: Sequence[int] = (15, 30, 60),
) -> list[OutcomeView]:
    """Measure forward underlying movement without inventing stop/target geometry.

    MFE/MAE and close return are measured from the event bar's close using only
    later completed 5-minute bars. EOD is always included as a separate view.
    """

    rows: list[OutcomeView] = []
    for event in events:
        bars = sorted(session_bars_by_symbol.get(event.symbol, ()), key=lambda b: b.start_utc)
        event_start = datetime.fromisoformat(event.bar_start.replace("Z", "+00:00"))
        event_close = datetime.fromisoformat(event.bar_close.replace("Z", "+00:00"))
        entry = float(event.trigger_price)
        if entry <= 0 or not isfinite(entry):
            continue

        future = [bar for bar in bars if bar.start_utc > event_start]
        views: list[tuple[str, list[Bar]]] = []
        for minutes in horizons_minutes:
            cutoff = event_close + timedelta(minutes=int(minutes))
            views.append(
                (
                    f"{int(minutes)}m",
                    [bar for bar in future if _close_time(bar) <= cutoff],
                )
            )
        views.append(("EOD", future))

        for horizon, path in views:
            if not path:
                rows.append(
                    OutcomeView(
                        observer_version=OBSERVER_VERSION,
                        symbol=event.symbol,
                        session_date=event.session_date,
                        family=event.family,
                        direction=event.direction,
                        episode_id=event.episode_id,
                        event_bar_start=event.bar_start,
                        horizon=horizon,
                        bars_observed=0,
                        close_return_bps=None,
                        mfe_bps=None,
                        mae_bps=None,
                    )
                )
                continue

            final_close = path[-1].close
            if event.direction == "LONG":
                close_return = ((final_close - entry) / entry) * 10000.0
                mfe = ((max(bar.high for bar in path) - entry) / entry) * 10000.0
                mae = ((entry - min(bar.low for bar in path)) / entry) * 10000.0
            else:
                close_return = ((entry - final_close) / entry) * 10000.0
                mfe = ((entry - min(bar.low for bar in path)) / entry) * 10000.0
                mae = ((max(bar.high for bar in path) - entry) / entry) * 10000.0

            rows.append(
                OutcomeView(
                    observer_version=OBSERVER_VERSION,
                    symbol=event.symbol,
                    session_date=event.session_date,
                    family=event.family,
                    direction=event.direction,
                    episode_id=event.episode_id,
                    event_bar_start=event.bar_start,
                    horizon=horizon,
                    bars_observed=len(path),
                    close_return_bps=round(close_return, 6),
                    mfe_bps=round(max(0.0, mfe), 6),
                    mae_bps=round(max(0.0, mae), 6),
                )
            )
    return rows


def summarize(
    events: Sequence[NonStratEvent],
    outcomes: Sequence[OutcomeView] = (),
) -> dict[str, Any]:
    by_family: dict[str, dict[str, Any]] = {}
    for event in events:
        row = by_family.setdefault(
            event.family,
            {"events": 0, "episodes": set(), "aligned": 0, "unaligned": 0, "unknown_alignment": 0},
        )
        row["events"] += 1
        row["episodes"].add(event.episode_id)
        if event.market_aligned is True:
            row["aligned"] += 1
        elif event.market_aligned is False:
            row["unaligned"] += 1
        else:
            row["unknown_alignment"] += 1

    outcome_map: dict[str, list[float]] = {}
    for outcome in outcomes:
        if outcome.horizon != "EOD" or outcome.close_return_bps is None:
            continue
        outcome_map.setdefault(outcome.family, []).append(outcome.close_return_bps)

    normalized: dict[str, Any] = {}
    for family, row in sorted(by_family.items()):
        returns = outcome_map.get(family, [])
        normalized[family] = {
            "events": row["events"],
            "episodes": len(row["episodes"]),
            "aligned": row["aligned"],
            "unaligned": row["unaligned"],
            "unknown_alignment": row["unknown_alignment"],
            "eod_mean_close_return_bps": (
                round(sum(returns) / len(returns), 6) if returns else None
            ),
            "eod_measured": len(returns),
        }

    return {
        "observer_id": OBSERVER_ID,
        "observer_version": OBSERVER_VERSION,
        "timeframe": OBSERVED_TIMEFRAME,
        "events": len(events),
        "episodes": len({event.episode_id for event in events}),
        "families": normalized,
        "blocked_by_design": [
            "generic_support_hold_requires_frozen_planned_level_source",
            "generic_resistance_rejection_requires_frozen_planned_level_source",
            "no_option_contract_expectancy_in_ns-v0.1",
            "no_alert_or_execution_path",
        ],
    }
