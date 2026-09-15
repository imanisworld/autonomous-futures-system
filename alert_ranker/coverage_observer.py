"""Read-only 30m Strat coverage observer for the options lane.

This module answers one question per completed 30-minute bar: *what
mechanical structure existed here, and at which V1 gate would it have died?*
It is an evidence lane, not a trade lane.  It never selects a contract, never
promotes anything to ACTIVE, never alerts, never consumes risk, never writes
an episode block, and never touches the V1 scanner's database or its
``V1-EPOCH-1`` cohort.  Its rows carry their own identity
(:data:`OBSERVER_ID` / :data:`OBSERVER_VERSION`) so they cannot be confused
with official V1 evidence.

Every definition below is borrowed from the modules V1 already uses, so the
observer reports the same setup V1 would have seen rather than a second,
looser opinion of what a setup is:

* candle types and 3-bar sequences -- :mod:`strategy.strat_classifier`;
* 2-2-2 / 3-2-2 / 3-2 families -- :func:`alert_ranker.daily_strat.evaluate_daily_setup`
  applied to the 30m window exactly as the 1H/4H observers apply it;
* trigger / invalidation -- previous bar high/low, the same levels
  :func:`options_manager.strategies.strat_212_mechanical_levels` and
  ``daily_strat._directional_fields`` produce;
* target pool -- the same reduction as
  ``BarContextBuilder._causal_structure_levels``;
* targets -- :func:`options_manager.levels.find_targets`, run twice: the V1
  30m way (nearest level, no floor) and the Daily way (``min_target_rr=1.0``);
* market alignment -- the same SPY/QQQ trend + hourly + daily candle test as
  ``BarContextBuilder._promote_paper_evidence_setup``;
* first sight -- the bar's close plus the SIP delay buffer, rounded up to the
  scanner's 5-minute grid, and the late-entry arithmetic from
  :mod:`alert_ranker.paper_v1`.

Nothing here is a trade.  A row says "this structure existed".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from options_manager.levels import LevelFinderInputs, find_targets
from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
    classify_sequence,
)

from .causal_bars import (
    HOUR_1,
    MINUTE_30,
    Bar,
    Timeframe,
    build_session_candle,
    build_session_timeframe,
    classify_last_bar,
    ema,
    session_bars,
    session_vwap,
)
from .daily_strat import evaluate_daily_setup
from .paper_v1 import remaining_reward_to_risk
from .session_calendar import Session

__all__ = [
    "OBSERVER_ID",
    "OBSERVER_VERSION",
    "OBSERVED_TIMEFRAME",
    "V1_SUPPORTED_FAMILIES",
    "REQUESTED_FAMILIES",
    "CoverageEvent",
    "SymbolSeries",
    "build_symbol_series",
    "classify_window",
    "structure_levels",
    "first_sight",
    "observe_symbol",
    "funnel",
]

OBSERVER_ID = "OPTIONS_COVERAGE_OBSERVER"
OBSERVER_VERSION = "cov-v0.1"
OBSERVED_TIMEFRAME = "30m"

EXCHANGE_TIMEZONE = "America/New_York"
EMA_PERIOD = 20
MIN_TARGET_RR_FLOOR = 1.0
MIN_REMAINING_RR = 1.0
# The scanner runs every 5 minutes at hh:mm:57 for mm in {02,07,...,57}
# (observed on the box); a bar becomes visible once ``now - delay_buffer``
# reaches its close.
SCANNER_GRID_MINUTES = 5
SCANNER_GRID_OFFSET = timedelta(minutes=2, seconds=57)
DEFAULT_DELAY_BUFFER = timedelta(seconds=960)

# The only 30m family the V1 ACTIVE lane evaluates (setup_authority.py).
V1_SUPPORTED_FAMILIES = frozenset({"STRAT_212_CONTINUATION"})
# The populations the operator asked to be measured on 30m.
REQUESTED_FAMILIES = (
    "STRAT_212_CONTINUATION",
    "STRAT_212_REVERSAL",
    "STRAT_222_CONTINUATION",
    "STRAT_222_REVERSAL",
    "STRAT_312",
    "STRAT_322_CONTINUATION",
    "STRAT_322_REVERSAL",
)
_CLASSIFIER_FAMILY = {
    "strat_212": "STRAT_212_CONTINUATION",
    "strat_212_reversal": "STRAT_212_REVERSAL",
    "strat_312": "STRAT_312",
}


@dataclass(frozen=True)
class SymbolSeries:
    """Session-only 30m bars for one symbol, in session order."""

    symbol: str
    sessions: tuple[Session, ...]
    by_session: dict[date, list[Bar]]
    series: list[Bar]
    expected_current_bars: int
    missing_sessions: tuple[str, ...]
    incomplete_sessions: tuple[str, ...]
    vwap_missing: bool

    @property
    def observable(self) -> bool:
        return not self.missing_sessions and not self.incomplete_sessions and not self.vwap_missing

    @property
    def reason(self) -> str:
        if self.missing_sessions:
            return "missing_sessions:" + ",".join(self.missing_sessions)
        if self.incomplete_sessions:
            return "incomplete_sessions:" + ",".join(self.incomplete_sessions)
        if self.vwap_missing:
            return "missing_inputs:vwap"
        return ""


@dataclass
class CoverageEvent:
    """One completed 30m bar that carried a mechanical directional structure."""

    observer_id: str
    observer_version: str
    symbol: str
    timeframe: str
    session_date: str
    bar_start: str
    bar_close: str
    family: str
    sequence: str
    requested_family: bool
    v1_supported: bool
    direction: str
    two_back_type: str
    previous_type: str
    current_type: str
    entry_trigger: float
    invalidation: float
    risk: float
    # geometry -- V1 30m rule (nearest level)
    nearest_target_1: float | None
    nearest_target_2: float | None
    nearest_rr_1: float | None
    nearest_reason: str
    nearest_geometry_ok: bool
    # geometry -- Daily rule (>=1R floor)
    floor_target_1: float | None
    floor_target_2: float | None
    floor_rr_1: float | None
    floor_reason: str
    floor_geometry_ok: bool
    floor_rescued: bool
    # market alignment -- V1 30m promotion test
    spy_trend: str | None
    qqq_trend: str | None
    hourly_candle_type: str | None
    daily_candle_type: str | None
    alignment_ok: bool
    alignment_failures: str
    # first sight / late entry
    first_sight_at: str
    first_sight_after_close: bool
    first_sight_price: float | None
    first_sight_price_source: str
    nearest_remaining_rr: float | None
    floor_remaining_rr: float | None
    late_nearest: bool | None
    late_floor: bool | None
    # cumulative verdicts
    would_qualify_v1_rule: bool | None
    would_qualify_floor_rule: bool | None
    resistance_levels: list[float] = field(default_factory=list)
    support_levels: list[float] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# series construction
# --------------------------------------------------------------------------- #


def _expected_starts(session: Session, timeframe: Timeframe) -> list[datetime]:
    starts: list[datetime] = []
    cursor = session.open.astimezone(timezone.utc)
    close = session.close.astimezone(timezone.utc)
    while cursor + timeframe.delta <= close:
        starts.append(cursor)
        cursor += timeframe.delta
    return starts


def build_symbol_series(
    symbol: str,
    bars: Sequence[Bar],
    sessions: Sequence[Session],
    *,
    timeframe: Timeframe = MINUTE_30,
) -> SymbolSeries:
    """Reduce provider bars to whole regular sessions, in session order.

    Every prior session must be whole, and the current (last) session must
    have every bar up to the last one it has -- the observer runs after the
    close, so a short current session is data loss, not "in progress".  A gap
    anywhere fails the symbol closed, matching ``_symbol_context``'s
    ``missing_bars`` refusal.
    """
    by_day: dict[date, list[Bar]] = {}
    tz = ZoneInfo(EXCHANGE_TIMEZONE)
    for bar in bars:
        by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)

    by_session: dict[date, list[Bar]] = {}
    series: list[Bar] = []
    missing: list[str] = []
    incomplete: list[str] = []
    current = sessions[-1] if sessions else None
    expected_current = len(_expected_starts(current, timeframe)) if current else 0
    for session in sessions:
        kept = session_bars(by_day.get(session.date, []), timeframe, session.open, session.close)
        if not kept:
            missing.append(session.date.isoformat())
            continue
        expected = _expected_starts(session, timeframe)
        have = {bar.start_utc for bar in kept}
        if current is not None and session.date == current.date:
            # The last session only needs to be contiguous through its last bar.
            last = max(have)
            expected = [start for start in expected if start <= last]
        if any(start not in have for start in expected):
            incomplete.append(session.date.isoformat())
            continue
        by_session[session.date] = kept
        series.extend(kept)

    vwap_missing = False
    if current is not None and current.date in by_session:
        vwap_missing = session_vwap(by_session[current.date]) is None

    return SymbolSeries(
        symbol=symbol.strip().upper(),
        sessions=tuple(sessions),
        by_session=by_session,
        series=series,
        expected_current_bars=expected_current,
        missing_sessions=tuple(missing),
        incomplete_sessions=tuple(incomplete),
        vwap_missing=vwap_missing,
    )


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def _bar_type(current: Bar, previous: Bar) -> str:
    return classify_bar(StratBar(high=current.high, low=current.low), StratBar(high=previous.high, low=previous.low))


def classify_window(three_back: Bar, two_back: Bar, previous: Bar, current: Bar) -> dict[str, Any] | None:
    """Name the directional structure completed by ``current``, or ``None``.

    Families come from the Daily detector first (2-1-2C, 2-2-2C/R, 3-2-2C/R),
    then from the shared classifier for the shapes the Daily detector refuses
    (2-1-2R, 3-1-2).  Anything else directional (1-2-2, inside-bar break,
    outside-bar follow-through, 2-2 with an unclassified opener) is kept as
    ``OTHER:<classifier label>`` so "all structural events" is a real total.
    Non-directional bars (1, 3) return ``None``.
    """
    two_back_type = _bar_type(two_back, three_back)
    previous_type = _bar_type(previous, two_back)
    current_type = _bar_type(current, previous)
    if current_type not in (TWO_UP, TWO_DOWN):
        return None

    direction = "LONG" if current_type == TWO_UP else "SHORT"
    entry, invalidation = (previous.high, previous.low) if direction == "LONG" else (previous.low, previous.high)
    base = {
        "two_back_type": two_back_type,
        "previous_type": previous_type,
        "current_type": current_type,
        "direction": direction,
        "entry_trigger": entry,
        "invalidation": invalidation,
    }

    verdict = evaluate_daily_setup([three_back, two_back, previous], current)
    if verdict.triggered and verdict.setup_type:
        family = verdict.setup_type.replace("DAILY_", "STRAT_", 1)
        return {**base, "family": family, "sequence": verdict.sequence or ""}

    context = classify_sequence(two_back_type, previous_type, current_type)
    sequence = context.strat_sequence or ""
    family = _CLASSIFIER_FAMILY.get(sequence)
    if family is None:
        if not sequence:
            return None
        family = f"OTHER:{sequence}"
    return {**base, "family": family, "sequence": sequence}


def structure_levels(series: Sequence[Bar], index: int) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """The V1 target pool for the bar at ``index`` (mirrors ``_causal_structure_levels``).

    The breakout bar and its trigger bar are excluded; earlier history is
    reduced to per-day regular-session extrema plus the opening directional
    bar's high/low.
    """
    bars = series[: index + 1]
    if len(bars) < 3:
        return (), ()
    known = list(bars[:-2])
    tz = ZoneInfo(EXCHANGE_TIMEZONE)
    by_day: dict[date, list[Bar]] = {}
    for bar in known:
        by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)
    resistance = {max(day_bars, key=lambda item: item.high).high for day_bars in by_day.values()}
    support = {min(day_bars, key=lambda item: item.low).low for day_bars in by_day.values()}
    directional = bars[-3]
    resistance.add(directional.high)
    support.add(directional.low)
    return tuple(sorted(resistance)), tuple(sorted(support, reverse=True))


def _targets(direction: str, entry: float, invalidation: float, resistance, support, floor: float | None):
    kwargs: dict[str, Any] = {}
    if floor is not None:
        kwargs["min_target_rr"] = floor
    result = find_targets(
        LevelFinderInputs(
            direction="CALL" if direction == "LONG" else "PUT",
            entry=entry,
            underlying_invalidation=invalidation,
            resistance_levels=resistance,
            support_levels=support,
            **kwargs,
        )
    )
    ok = result.status == "VALID"
    return {
        "target_1": result.target_1 if ok else None,
        "target_2": result.target_2 if ok else None,
        "rr_1": result.rr_1 if ok else None,
        "reason": result.reason_code,
        "ok": ok,
    }


# --------------------------------------------------------------------------- #
# market alignment (same inputs as BarContextBuilder._promote_paper_evidence_setup)
# --------------------------------------------------------------------------- #


def _trend(series: Sequence[Bar], session_series: Sequence[Bar]) -> str | None:
    if not series or not session_series:
        return None
    close = series[-1].close
    vwap = session_vwap(session_series)
    ema20 = ema([bar.close for bar in series], EMA_PERIOD)
    if vwap is None or ema20 is None:
        return None
    if close > vwap and close > ema20:
        return "bullish"
    if close < vwap and close < ema20:
        return "bearish"
    return "neutral"


def _index_trend(index_series: SymbolSeries | None, session: Session, bar_close: datetime) -> str | None:
    """Index trend at ``bar_close`` using only bars closed by then."""
    if index_series is None or not index_series.observable:
        return None
    through = [bar for bar in index_series.series if bar.start_utc + MINUTE_30.delta <= bar_close]
    session_part = [bar for bar in index_series.by_session.get(session.date, []) if bar.start_utc + MINUTE_30.delta <= bar_close]
    return _trend(through, session_part)


def _hourly_type(session_series: Sequence[Bar], session: Session) -> str | None:
    hourly = build_session_timeframe(session_series, MINUTE_30, HOUR_1, session.open)
    if len(hourly) < 2:
        return None
    return classify_last_bar(hourly).get("candle_type")


def _daily_type(symbol: SymbolSeries, session: Session) -> str | None:
    prior = [symbol.by_session[s.date] for s in symbol.sessions if s.date < session.date and s.date in symbol.by_session]
    candles = [c for c in (build_session_candle(day) for day in prior) if c is not None]
    if len(candles) < 2:
        return None
    return classify_last_bar(candles).get("candle_type")


# --------------------------------------------------------------------------- #
# first sight
# --------------------------------------------------------------------------- #


def first_sight(bar_close: datetime, delay_buffer: timedelta = DEFAULT_DELAY_BUFFER) -> datetime:
    """The first scanner tick at which the bar's close is behind the cutoff."""
    earliest = bar_close.astimezone(timezone.utc) + delay_buffer
    grid = timedelta(minutes=SCANNER_GRID_MINUTES)
    anchor = earliest.replace(minute=0, second=0, microsecond=0) + SCANNER_GRID_OFFSET
    while anchor < earliest:
        anchor += grid
    return anchor


def _late(price: float | None, target: float | None, remaining: float | None) -> bool | None:
    if price is None or target is None:
        return None
    if remaining is None:
        return True
    return remaining < MIN_REMAINING_RR


def _price_at(fine_bars: Sequence[Bar] | None, fine_timeframe: Timeframe | None, moment: datetime) -> tuple[float | None, str]:
    """Close of the last fine bar that had closed by ``moment``."""
    if not fine_bars or fine_timeframe is None:
        return None, "unavailable"
    closed = [bar for bar in fine_bars if bar.start_utc + fine_timeframe.delta <= moment]
    if not closed:
        return None, "unavailable"
    last = max(closed, key=lambda bar: bar.start_utc)
    return last.close, f"{fine_timeframe.name}_close"


# --------------------------------------------------------------------------- #
# main entry
# --------------------------------------------------------------------------- #


def observe_symbol(
    symbol: SymbolSeries,
    session: Session,
    *,
    spy: SymbolSeries | None,
    qqq: SymbolSeries | None,
    fine_bars: Sequence[Bar] | None = None,
    fine_timeframe: Timeframe | None = None,
    delay_buffer: timedelta = DEFAULT_DELAY_BUFFER,
) -> list[CoverageEvent]:
    """Every structural event completed inside ``session`` for one symbol.

    ``fine_bars`` (e.g. 5Min) are used ONLY to price the first-sight moment
    for the late-entry arithmetic; detection uses the 30m series alone.
    """
    if not symbol.observable or session.date not in symbol.by_session:
        return []
    series = symbol.series
    events: list[CoverageEvent] = []
    for index, current in enumerate(series):
        if index < 3:
            continue
        tz_date = current.start_utc.astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date()
        if tz_date != session.date:
            continue
        window = classify_window(series[index - 3], series[index - 2], series[index - 1], current)
        if window is None:
            continue

        direction = window["direction"]
        entry = float(window["entry_trigger"])
        invalidation = float(window["invalidation"])
        risk = abs(entry - invalidation)
        resistance, support = structure_levels(series, index)
        nearest = _targets(direction, entry, invalidation, resistance, support, None)
        floor = _targets(direction, entry, invalidation, resistance, support, MIN_TARGET_RR_FLOOR)
        nearest_ok = bool(nearest["ok"] and nearest["rr_1"] is not None and nearest["rr_1"] >= MIN_REMAINING_RR)
        floor_ok = bool(floor["ok"])

        bar_close = current.start_utc + MINUTE_30.delta
        session_part = [bar for bar in symbol.by_session[session.date] if bar.start_utc + MINUTE_30.delta <= bar_close]
        desired_trend = "bullish" if direction == "LONG" else "bearish"
        desired_candle = TWO_UP if direction == "LONG" else TWO_DOWN
        spy_trend = _index_trend(spy, session, bar_close)
        qqq_trend = _index_trend(qqq, session, bar_close)
        hourly = _hourly_type(session_part, session)
        daily = _daily_type(symbol, session)
        failures = [
            name
            for name, ok in (
                ("spy", spy_trend == desired_trend),
                ("qqq", qqq_trend == desired_trend),
                ("hourly", hourly == desired_candle),
                ("daily", daily == desired_candle),
            )
            if not ok
        ]
        aligned = not failures

        sight = first_sight(bar_close, delay_buffer)
        # The scanner only runs during the session, so a bar whose first
        # sight lands after the close is never seen by V1 at all.
        after_close = sight >= session.close.astimezone(timezone.utc)
        price, source = _price_at(fine_bars, fine_timeframe, sight)
        rem_nearest = remaining_reward_to_risk(direction, price, invalidation, nearest["target_1"]) if price is not None and nearest["target_1"] is not None else None
        rem_floor = remaining_reward_to_risk(direction, price, invalidation, floor["target_1"]) if price is not None and floor["target_1"] is not None else None
        # A priced first sight with an undefined ratio means price is already
        # at or through the stop: that is late (paper_v1 reports it as
        # ``price_past_stop``), not "unpriced".
        late_nearest = _late(price, nearest["target_1"], rem_nearest)
        late_floor = _late(price, floor["target_1"], rem_floor)

        def _qualify(geometry_ok: bool, late: bool | None) -> bool | None:
            if not geometry_ok or not aligned or after_close:
                return False
            if late is None:
                return None
            return not late

        events.append(
            CoverageEvent(
                observer_id=OBSERVER_ID,
                observer_version=OBSERVER_VERSION,
                symbol=symbol.symbol,
                timeframe=OBSERVED_TIMEFRAME,
                session_date=session.date.isoformat(),
                bar_start=current.start_utc.isoformat(),
                bar_close=bar_close.isoformat(),
                family=window["family"],
                sequence=window["sequence"],
                requested_family=window["family"] in REQUESTED_FAMILIES,
                v1_supported=window["family"] in V1_SUPPORTED_FAMILIES,
                direction=direction,
                two_back_type=window["two_back_type"],
                previous_type=window["previous_type"],
                current_type=window["current_type"],
                entry_trigger=entry,
                invalidation=invalidation,
                risk=risk,
                nearest_target_1=nearest["target_1"],
                nearest_target_2=nearest["target_2"],
                nearest_rr_1=nearest["rr_1"],
                nearest_reason=nearest["reason"],
                nearest_geometry_ok=nearest_ok,
                floor_target_1=floor["target_1"],
                floor_target_2=floor["target_2"],
                floor_rr_1=floor["rr_1"],
                floor_reason=floor["reason"],
                floor_geometry_ok=floor_ok,
                floor_rescued=bool(floor_ok and not nearest_ok),
                spy_trend=spy_trend,
                qqq_trend=qqq_trend,
                hourly_candle_type=hourly,
                daily_candle_type=daily,
                alignment_ok=aligned,
                alignment_failures=",".join(failures),
                first_sight_at=sight.isoformat(),
                first_sight_after_close=after_close,
                first_sight_price=price,
                first_sight_price_source=source,
                nearest_remaining_rr=rem_nearest,
                floor_remaining_rr=rem_floor,
                late_nearest=late_nearest,
                late_floor=late_floor,
                would_qualify_v1_rule=_qualify(nearest_ok, late_nearest),
                would_qualify_floor_rule=_qualify(floor_ok, late_floor),
                resistance_levels=list(resistance),
                support_levels=list(support),
            )
        )
    return events


# --------------------------------------------------------------------------- #
# daily funnel
# --------------------------------------------------------------------------- #


def funnel(events: Sequence[CoverageEvent | dict[str, Any]]) -> dict[str, Any]:
    """The operator's per-day funnel, in gate order, for one rule set each.

    ``v1_rule`` walks the gates as V1 applies them today (nearest target).
    ``floor_rule`` walks the same gates with the Daily >=1R target floor.
    Each stage counts events that survived every earlier stage, so the
    numbers read top to bottom as a funnel; ``by_family`` breaks the same
    walk out per setup family so the missing populations can be compared.
    """
    rows = [e.to_row() if isinstance(e, CoverageEvent) else dict(e) for e in events]

    def walk(subset: Sequence[dict[str, Any]], geometry_key: str, late_key: str) -> dict[str, int]:
        out = {
            "all_structural_events": len(subset),
            "v1_supported": 0,
            "unsupported_setup_family": 0,
            "unsupported_timeframe": 0,
            "target_geometry_failure": 0,
            "market_alignment_failure": 0,
            "first_sight_after_close": 0,
            "late_at_first_sight": 0,
            "first_sight_unpriced": 0,
            "would_otherwise_qualify": 0,
        }
        for row in subset:
            if row.get("timeframe") != OBSERVED_TIMEFRAME:
                out["unsupported_timeframe"] += 1
                continue
            if not row.get("v1_supported"):
                out["unsupported_setup_family"] += 1
                continue
            out["v1_supported"] += 1
            if not row.get(geometry_key):
                out["target_geometry_failure"] += 1
                continue
            if not row.get("alignment_ok"):
                out["market_alignment_failure"] += 1
                continue
            if row.get("first_sight_after_close"):
                out["first_sight_after_close"] += 1
                continue
            late = row.get(late_key)
            if late is None:
                out["first_sight_unpriced"] += 1
                continue
            if late:
                out["late_at_first_sight"] += 1
                continue
            out["would_otherwise_qualify"] += 1
        return out

    def walk_as_if_supported(subset: Sequence[dict[str, Any]], geometry_key: str, late_key: str) -> dict[str, int]:
        """Same gates, pretending the family were V1-supported."""
        pretend = [{**row, "v1_supported": True} for row in subset]
        return walk(pretend, geometry_key, late_key)

    families = sorted({row["family"] for row in rows})
    by_family = {}
    for family in families:
        subset = [row for row in rows if row["family"] == family]
        by_family[family] = {
            "count": len(subset),
            "requested": bool(subset and subset[0].get("requested_family")),
            "v1_supported": bool(subset and subset[0].get("v1_supported")),
            "as_if_supported_v1_rule": walk_as_if_supported(subset, "nearest_geometry_ok", "late_nearest"),
            "as_if_supported_floor_rule": walk_as_if_supported(subset, "floor_geometry_ok", "late_floor"),
        }

    return {
        "observer_id": OBSERVER_ID,
        "observer_version": OBSERVER_VERSION,
        "timeframe": OBSERVED_TIMEFRAME,
        "v1_rule": walk(rows, "nearest_geometry_ok", "late_nearest"),
        "floor_rule": walk(rows, "floor_geometry_ok", "late_floor"),
        "by_family": by_family,
        "symbols_with_events": len({row["symbol"] for row in rows}),
    }
