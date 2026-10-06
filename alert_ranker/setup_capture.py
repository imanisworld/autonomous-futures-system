"""Observation-only Strat setup-capture: arm completed structure before the break.

Pending 2-2 is every two_back/previous pair in {2U, 2D} (four combos). Each
structure is two-sided: a high break is continuation-or-reversal LONG/CALL,
a low break is the opposite SHORT/PUT. Family names follow
``daily_strat.evaluate_daily_setup`` (H1_222_CONTINUATION on a same-direction
high break). This module does **not** call ``trigger_time.arm_trigger_setup``
or ``_family_for_break``, which cancel same-direction 2-2-2.

The watcher covers the full next 1H candle, including the 15:30 and 12:30
stubs as watch candles. It does not reconstruct a 960 s-delayed current
partial (that path only ever contains the first 30m bar).

No Signa, GEX, chain, paper_v1, Discord, risk, or broker imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo

from strategy.strat_classifier import (
    INSIDE_BAR,
    OUTSIDE_BAR,
    TWO_DOWN,
    TWO_UP,
    StratBar,
    classify_bar,
)

from .causal_bars import MINUTE_30, Bar, session_bars
from .session_calendar import Session, nyse_session_for

CAPTURE_ID = "OPTIONS_SETUP_CAPTURE"
CAPTURE_VERSION = "capture-v0.2"
OBSERVATION_LANE = "SETUP_CAPTURE_V0"
PLUMBING_REPLAY_LANE = "PLUMBING_REPLAY"
LEVEL_QUANTUM = 1e-4
NY = ZoneInfo("America/New_York")

STATUS_WATCHING = "WATCHING"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_INVALIDATED = "INVALIDATED"
STATUS_EXPIRED = "EXPIRED"
STATUS_MISSED_LATE = "MISSED_LATE"
STATUS_GAP_THROUGH_OPEN = "GAP_THROUGH_OPEN"
STATUS_DATA_BLOCKED = "DATA_BLOCKED"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_NO_TRIGGER = "NO_TRIGGER"
STATUS_LOCKED = "LOCKED"

TERMINAL_STATUSES = frozenset(
    {
        STATUS_TRIGGERED,
        STATUS_INVALIDATED,
        STATUS_EXPIRED,
        STATUS_MISSED_LATE,
        STATUS_GAP_THROUGH_OPEN,
        STATUS_DATA_BLOCKED,
        STATUS_AMBIGUOUS,
        STATUS_NO_TRIGGER,
    }
)

WATCHER_UNIVERSE = ("SPY", "QQQ", "SPX")
OPTION_ROOT_SYMBOLS = frozenset({"SPXW"})
INDEX_QUOTE_SYMBOLS = frozenset({"SPX", "VIX", "NDX", "RUT", "DJI"})
MAX_CAPTURE_LAG_SECONDS = 120.0
SPX_MAX_BAR_AGE_SECONDS = 120.0
CLOCK_SKEW_LIMIT_SECONDS = 30.0
SIP_RECONCILE_DELAY = timedelta(minutes=16)
DEFAULT_JOURNAL = "/root/afs-shared/logs/options_setup_capture.jsonl"
DEFAULT_RAW_TRADE_DIR = "/root/afs-shared/logs/options_setup_capture_source_trades"

TIMEFRAME_PREFIXES = {"30m": "M30_", "1H": "H1_", "1D": "DAILY_"}
TIMEFRAME_DELTAS = {
    "30m": timedelta(minutes=30),
    "1H": timedelta(hours=1),
    "1D": timedelta(hours=24),
}

# CTA/UTDF minute-price eligibility (same table as the 212R trade-timestamp
# audit). Form T (extended hours) is GREEN — only the RTH watch window excludes
# it. Opening print 'O' is GREEN; 'Q' is RED.
_MINUTE_PRICE_GREEN = {
    "A": frozenset({" ", "E", "F", "K", "L", "O", "T", "X", "5", "6"}),
    "B": frozenset({" ", "E", "F", "K", "L", "O", "T", "X", "5", "6"}),
    "C": frozenset({"@", "A", "B", "D", "F", "K", "L", "O", "T", "X", "Y", "5", "6"}),
    "O": frozenset({"@", "T"}),
}
_MINUTE_PRICE_RED = {
    "A": frozenset({"B", "C", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "Z", "4", "7", "9"}),
    "B": frozenset({"B", "C", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "Z", "4", "7", "9"}),
    "C": frozenset({"C", "G", "H", "I", "M", "N", "P", "Q", "R", "U", "V", "W", "Z", "4", "7", "9"}),
    "O": frozenset({"C", "I", "N", "P", "R", "U", "W"}),
}

SessionLookup = Callable[[date], Session | None]


def quote_instrument_type(ticker: str) -> str:
    symbol = (ticker or "").strip().upper()
    if symbol in OPTION_ROOT_SYMBOLS:
        raise ValueError("spxw_is_option_root_not_underlying")
    if symbol in INDEX_QUOTE_SYMBOLS:
        return "INDEX"
    return "EQUITY"


def watcher_universe() -> tuple[str, ...]:
    """Fixed observer list. Never OPTIONS_SCANNER_WATCHLIST, never SPXW."""
    return WATCHER_UNIVERSE


def quantize_level(value: float) -> float:
    return round(float(value) / LEVEL_QUANTUM) * LEVEL_QUANTUM


def format_level(value: float) -> str:
    return f"{quantize_level(value):.4f}"


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _bar_type(current: Bar, previous: Bar) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


_PATTERN_TOKEN = {
    TWO_UP: "2U",
    TWO_DOWN: "2D",
    INSIDE_BAR: "1",
    OUTSIDE_BAR: "3",
}


def pattern_label(two_back_type: str, previous_type: str) -> str:
    left = _PATTERN_TOKEN.get(two_back_type, two_back_type)
    right = _PATTERN_TOKEN.get(previous_type, previous_type)
    return f"222:{left}:{right}"


def structure_key(
    *,
    ticker: str,
    timeframe: str,
    structure_close: datetime,
    pattern: str,
    trigger: float | None = None,
    invalidation: float | None = None,
) -> str:
    """Identity of one pending structure.

    Levels are attributes (and part of fingerprint), not part of the key, so a
    completed-bar revision updates the same structure via SOURCE_DRIFT instead
    of opening a duplicate WATCHING row. ``trigger`` / ``invalidation`` remain
    accepted for call-site compatibility and scanner linking only.
    """
    del trigger, invalidation
    close = _aware(structure_close).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "|".join(
        (
            ticker.strip().upper(),
            timeframe,
            close,
            pattern,
        )
    )


def link_scanner_first_sight(
    *,
    ticker: str,
    timeframe: str,
    structure_close: datetime,
    pattern: str,
    trigger: float,
    invalidation: float,
    known: Sequence["ArmedStructure"] = (),
    level_tolerance: float = 0.01,
) -> str:
    """Map a scanner first-sight (9925/9933) onto one watcher structure key.

    Levels may disagree by a cent across Public vs Alpaca; the match is
    ticker + timeframe + structure_close + pattern with a 0.01 level window.
    """
    wanted = structure_key(
        ticker=ticker,
        timeframe=timeframe,
        structure_close=structure_close,
        pattern=pattern,
        trigger=trigger,
        invalidation=invalidation,
    )
    if not known:
        return wanted
    close = _aware(structure_close)
    for item in known:
        if item.ticker != ticker.strip().upper() or item.timeframe != timeframe:
            continue
        if item.pattern != pattern:
            continue
        if abs((_aware(item.structure_close) - close).total_seconds()) > 1:
            continue
        if (
            abs(item.boundary_high - float(trigger)) <= level_tolerance
            and abs(item.boundary_low - float(invalidation)) <= level_tolerance
        ):
            return item.structure_key
    return wanted


def next_session_on_or_after(moment: datetime, lookup: SessionLookup = nyse_session_for) -> Session:
    local = _aware(moment).astimezone(NY)
    day = local.date()
    for _ in range(14):
        session = lookup(day)
        if session is not None and moment < session.close.astimezone(timezone.utc):
            return session
        day += timedelta(days=1)
    raise RuntimeError("no_nyse_session_in_horizon")


def next_session_after_close(moment: datetime, lookup: SessionLookup = nyse_session_for) -> Session:
    local = _aware(moment).astimezone(NY)
    day = local.date() + timedelta(days=1)
    for _ in range(14):
        session = lookup(day)
        if session is not None:
            return session
        day += timedelta(days=1)
    raise RuntimeError("no_nyse_session_in_horizon")


def watch_window_after_structure(
    *,
    structure_close: datetime,
    timeframe: str,
    lookup: SessionLookup = nyse_session_for,
) -> tuple[datetime, datetime]:
    """Next RTH watch window. After the session close this is the next open.

    After-hours and pre-market prints therefore cannot fire. A Friday 16:00
    structure watches Monday 09:30; a 15:30 close still in Friday RTH watches
    the 15:30-16:00 stub.
    """
    close = _aware(structure_close)
    if timeframe not in TIMEFRAME_DELTAS:
        raise ValueError(f"unsupported capture timeframe: {timeframe}")
    local = close.astimezone(NY)
    session = lookup(local.date())
    if session is None or close >= session.close.astimezone(timezone.utc):
        nxt = next_session_after_close(close, lookup)
        start = nxt.open.astimezone(timezone.utc)
        if timeframe == "1D":
            return start, nxt.close.astimezone(timezone.utc)
        end = min(start + TIMEFRAME_DELTAS[timeframe], nxt.close.astimezone(timezone.utc))
        return start, end
    if timeframe == "1D":
        nxt = next_session_after_close(session.close, lookup)
        return nxt.open.astimezone(timezone.utc), nxt.close.astimezone(timezone.utc)
    start = close
    end = min(start + TIMEFRAME_DELTAS[timeframe], session.close.astimezone(timezone.utc))
    return start, end


def structure_close_for_bar(bar: Bar, *, timeframe: str, session: Session) -> datetime:
    """Bar timestamps are interval START. Close is start+delta, clipped to session."""
    start = bar.start_utc
    session_close = session.close.astimezone(timezone.utc)
    delta = TIMEFRAME_DELTAS[timeframe]
    raw_end = start + delta
    if raw_end > session_close:
        return session_close
    return raw_end


def rebuild_session_hours(
    bars_30m: Sequence[Bar],
    *,
    sessions: Sequence[Session],
    cutoff: datetime,
) -> list[Bar]:
    """9:30-anchored 1H candles from 30m, keeping the 15:30/12:30 stub.

    Vendor ONE_HOUR bars are rejected by the caller. Partial groups whose end
    is still after ``cutoff`` are omitted (no 15:45 stub leakage).
    """
    point = _aware(cutoff)
    prior: list[Bar] = []
    by_start = {bar.start_utc: bar for bar in bars_30m}
    span = timedelta(hours=1)
    source = MINUTE_30
    for session in sessions:
        session_open = session.open.astimezone(timezone.utc)
        session_close = session.close.astimezone(timezone.utc)
        anchor = session_open
        while anchor < session_close:
            group_end = min(anchor + span, session_close)
            expected: list[datetime] = []
            cursor = anchor
            while cursor + source.delta <= group_end:
                expected.append(cursor)
                cursor += source.delta
            available = [by_start[start] for start in expected if start in by_start]
            complete = bool(expected) and len(available) == len(expected)
            end_known = group_end <= point
            if complete and end_known:
                prior.append(
                    Bar(
                        start=anchor,
                        open=available[0].open,
                        high=max(item.high for item in available),
                        low=min(item.low for item in available),
                        close=available[-1].close,
                        volume=sum(item.volume for item in available),
                        vwap=None,
                    )
                )
            anchor += span
    return prior


def rebuild_session_dailies(
    bars_30m: Sequence[Bar],
    *,
    sessions: Sequence[Session],
    cutoff: datetime,
) -> list[Bar]:
    point = _aware(cutoff)
    out: list[Bar] = []
    for session in sessions:
        if session.close.astimezone(timezone.utc) > point:
            continue
        kept = session_bars(bars_30m, MINUTE_30, session.open, session.close)
        if not kept:
            continue
        out.append(
            Bar(
                start=session.open.astimezone(timezone.utc),
                open=kept[0].open,
                high=max(item.high for item in kept),
                low=min(item.low for item in kept),
                close=kept[-1].close,
                volume=sum(item.volume for item in kept),
                vwap=None,
            )
        )
    return out


def reject_vendor_hour_bars(bars: Sequence[Bar]) -> None:
    """1H must be rebuilt from 30m. Native clock-hour bars start on :00 ET."""
    for bar in bars:
        local = bar.start_utc.astimezone(NY)
        if local.minute == 0:
            raise ValueError("vendor_hour_bars_unused")


@dataclass(frozen=True)
class TapePrint:
    timestamp: datetime
    timestamp_ns: int
    price: float
    trade_id: str
    feed: str
    conditions: tuple[str, ...] = ()
    tape: str = "A"
    exchange: str = ""

    @property
    def ts_utc(self) -> datetime:
        return _aware(self.timestamp)


@dataclass(frozen=True)
class IndexMinuteBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    available_at: datetime | None = None

    @property
    def window_end(self) -> datetime:
        return _aware(self.start) + timedelta(minutes=1)


@dataclass(frozen=True)
class ArmedStructure:
    ticker: str
    timeframe: str
    pattern: str
    two_back_type: str
    previous_type: str
    boundary_high: float
    boundary_low: float
    structure_close: datetime
    knowable_at: datetime
    watch_start: datetime
    watch_until: datetime
    setup_bar_start: datetime
    level_source: str = "public_regular_30m"
    revision: int = 0

    @property
    def structure_key(self) -> str:
        return structure_key(
            ticker=self.ticker,
            timeframe=self.timeframe,
            structure_close=self.structure_close,
            pattern=self.pattern,
        )

    def long_setup_type(self) -> str:
        prefix = TIMEFRAME_PREFIXES[self.timeframe]
        family = "222"
        if self.pattern.startswith("212"):
            family = "212"
        elif self.pattern.startswith("322"):
            family = "322"
        subtype = "CONTINUATION" if self.previous_type == TWO_UP else "REVERSAL"
        return f"{prefix}{family}_{subtype}"

    def short_setup_type(self) -> str:
        prefix = TIMEFRAME_PREFIXES[self.timeframe]
        family = "222"
        if self.pattern.startswith("212"):
            family = "212"
        elif self.pattern.startswith("322"):
            family = "322"
        subtype = "CONTINUATION" if self.previous_type == TWO_DOWN else "REVERSAL"
        return f"{prefix}{family}_{subtype}"

    def fingerprint(self) -> str:
        return "|".join(
            (
                self.structure_key,
                format_level(self.boundary_high),
                format_level(self.boundary_low),
                str(self.revision),
            )
        )


@dataclass
class CaptureRecord:
    structure_key: str
    capture_id: str = CAPTURE_ID
    capture_version: str = CAPTURE_VERSION
    ticker: str = ""
    timeframe: str = ""
    pattern: str = ""
    status: str = STATUS_WATCHING
    boundary_high: float = 0.0
    boundary_low: float = 0.0
    level_source: str = "public_regular_30m"
    structure_close: str = ""
    knowable_at: str = ""
    persisted_at: str = ""
    first_seen_at: str = ""
    watch_start: str = ""
    watch_until: str = ""
    direction: str | None = None
    setup_type: str | None = None
    observation_only: bool = True
    execution_authority: bool = False
    trade_authority: bool = False
    prospective_catch: bool = False
    capture_late: bool = False
    status_reason: str = ""
    geometry: str | None = None
    trigger_crossed_at: str | None = None
    trigger_crossed_at_ns: int | None = None
    trigger_trade_price: float | None = None
    trigger_trade_id: str | None = None
    trigger_feed: str | None = None
    trigger_source: str | None = None
    trigger_resolution: str | None = None
    crossed_window_start: str | None = None
    crossed_window_end: str | None = None
    detected_at: str | None = None
    sip_crossed_at: str | None = None
    true_lag_seconds: float | None = None
    iex_lag_seconds: float | None = None
    gap_through: bool = False
    first_print_price: float | None = None
    clock_offset_s: float | None = None
    data_delayed: bool = False
    revision: int = 0
    high_water_ts: str | None = None
    sibling_invalidated: bool = False
    observation_lane: str = OBSERVATION_LANE
    extra: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return "|".join(
            (
                self.structure_key,
                format_level(self.boundary_high),
                format_level(self.boundary_low),
                str(self.revision),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["advisory_only"] = True
        payload["spxw_execution_enabled"] = False
        return payload


def arm_pending_structures(
    completed_bars: Sequence[Bar],
    *,
    ticker: str,
    timeframe: str,
    now: datetime,
    session: Session,
    lookup: SessionLookup = nyse_session_for,
    level_source: str = "public_regular_30m",
) -> list[ArmedStructure]:
    """Arm from completed precursor bars only. Partial stubs are not used."""
    if timeframe not in TIMEFRAME_PREFIXES:
        raise ValueError(f"unsupported capture timeframe: {timeframe}")
    if len(completed_bars) < 3:
        return []
    point = _aware(now)
    three_back, two_back, previous = completed_bars[-3:]
    two_back_type = _bar_type(two_back, three_back)
    previous_type = _bar_type(previous, two_back)
    close = structure_close_for_bar(previous, timeframe=timeframe, session=session)
    if close > point:
        return []
    watch_start, watch_until = watch_window_after_structure(
        structure_close=close, timeframe=timeframe, lookup=lookup
    )
    symbol = ticker.strip().upper()
    common = dict(
        ticker=symbol,
        timeframe=timeframe,
        two_back_type=two_back_type,
        previous_type=previous_type,
        boundary_high=quantize_level(previous.high),
        boundary_low=quantize_level(previous.low),
        structure_close=close,
        knowable_at=close,
        watch_start=watch_start,
        watch_until=watch_until,
        setup_bar_start=previous.start_utc,
        level_source=level_source,
    )
    if two_back_type in {TWO_UP, TWO_DOWN} and previous_type in {TWO_UP, TWO_DOWN}:
        return [ArmedStructure(pattern=pattern_label(two_back_type, previous_type), **common)]
    if two_back_type in {TWO_UP, TWO_DOWN} and previous_type == INSIDE_BAR:
        return [
            ArmedStructure(
                pattern=f"212:{two_back_type}:{previous_type}",
                **common,
            )
        ]
    if two_back_type == OUTSIDE_BAR and previous_type in {TWO_UP, TWO_DOWN}:
        return [ArmedStructure(pattern=f"322:{two_back_type}:{previous_type}", **common)]
    return []


def record_watching(armed: ArmedStructure, *, now: datetime, clock_offset_s: float | None = None) -> CaptureRecord:
    persist = _aware(now)
    return CaptureRecord(
        structure_key=armed.structure_key,
        ticker=armed.ticker,
        timeframe=armed.timeframe,
        pattern=armed.pattern,
        status=STATUS_WATCHING,
        boundary_high=armed.boundary_high,
        boundary_low=armed.boundary_low,
        level_source=armed.level_source,
        structure_close=_iso(armed.structure_close) or "",
        knowable_at=_iso(armed.knowable_at) or "",
        persisted_at=_iso(persist) or "",
        first_seen_at=_iso(persist) or "",
        watch_start=_iso(armed.watch_start) or "",
        watch_until=_iso(armed.watch_until) or "",
        status_reason="structure_ready_pre_trigger",
        clock_offset_s=clock_offset_s,
        revision=armed.revision,
        extra={
            "long_setup_type": armed.long_setup_type(),
            "short_setup_type": armed.short_setup_type(),
            "setup_bar_start": _iso(armed.setup_bar_start),
        },
    )


def minute_price_eligible(*, tape: str, conditions: Sequence[str]) -> bool:
    tape_code = str(tape or "").strip().upper()
    if tape_code not in _MINUTE_PRICE_GREEN:
        raise ValueError(f"unknown_tape:{tape_code}")
    normalized = tuple(str(item) for item in conditions)
    if not normalized:
        if tape_code in {"A", "B"}:
            return True
        raise ValueError(f"undocumented_empty_condition_set:{tape_code}")
    green = _MINUTE_PRICE_GREEN[tape_code]
    red = _MINUTE_PRICE_RED[tape_code]
    for condition in normalized:
        if condition not in green and condition not in red:
            raise ValueError(f"unknown_trade_condition:{condition}")
    return not any(condition in red for condition in normalized)


def _in_watch(ts: datetime, start: datetime, until: datetime) -> bool:
    point = _aware(ts)
    return _aware(start) <= point < _aware(until)


def first_boundary_from_prints(
    prints: Sequence[TapePrint],
    *,
    armed: ArmedStructure,
    window_start: datetime,
    window_end: datetime,
) -> dict[str, Any]:
    eligible: list[TapePrint] = []
    for item in prints:
        if not _in_watch(item.ts_utc, window_start, window_end):
            continue
        try:
            if not minute_price_eligible(tape=item.tape, conditions=item.conditions):
                continue
        except ValueError as exc:
            return {"status": STATUS_DATA_BLOCKED, "reason_code": str(exc), "feed": item.feed}
        eligible.append(item)
    eligible.sort(key=lambda row: (row.timestamp_ns, row.trade_id))
    high_hit = next((row for row in eligible if row.price > armed.boundary_high), None)
    low_hit = next((row for row in eligible if row.price < armed.boundary_low), None)
    if high_hit is not None and low_hit is not None and high_hit.timestamp_ns == low_hit.timestamp_ns:
        return {
            "status": STATUS_AMBIGUOUS,
            "reason_code": "simultaneous_boundary_break",
            "feed": high_hit.feed,
        }
    candidates = []
    if high_hit is not None:
        candidates.append(("HIGH", "LONG", high_hit))
    if low_hit is not None:
        candidates.append(("LOW", "SHORT", low_hit))
    if not candidates:
        return {"status": "NO_BREAK", "eligible": len(eligible)}
    candidates.sort(key=lambda item: item[2].timestamp_ns)
    side, direction, trade = candidates[0]
    first = eligible[0] if eligible else trade
    gap = first.trade_id == trade.trade_id and first.timestamp_ns == trade.timestamp_ns
    at_open = abs((_aware(trade.ts_utc) - _aware(armed.watch_start)).total_seconds()) < 1.0
    return {
        "status": "PROVEN",
        "break_side": side,
        "direction": direction,
        "print": trade,
        "gap_through": bool(gap and at_open),
        "first_print_price": first.price,
        "feed": trade.feed,
    }


def first_boundary_from_index_minutes(
    bars: Sequence[IndexMinuteBar],
    *,
    armed: ArmedStructure,
    window_start: datetime,
    window_end: datetime,
    now: datetime,
    max_age_seconds: float = SPX_MAX_BAR_AGE_SECONDS,
) -> dict[str, Any]:
    """SPX trigger source: Public INDEX ONE_MINUTE high/low, bar resolution."""
    start = _aware(window_start)
    until = _aware(window_end)
    point = _aware(now)
    admitted: list[IndexMinuteBar] = []
    for bar in bars:
        bar_start = _aware(bar.start)
        if not (start <= bar_start < until):
            continue
        if bar.window_end > point:
            continue
        admitted.append(bar)
    if not admitted:
        newest_age = None
        delayed = True
    else:
        newest = max(admitted, key=lambda item: _aware(item.start))
        newest_age = (point - newest.window_end).total_seconds()
        delayed = newest_age > max_age_seconds
    high_hit = next((bar for bar in admitted if bar.high > armed.boundary_high), None)
    low_hit = next((bar for bar in admitted if bar.low < armed.boundary_low), None)
    if high_hit is not None and low_hit is not None and high_hit.start == low_hit.start:
        return {
            "status": STATUS_AMBIGUOUS,
            "reason_code": "simultaneous_boundary_break",
            "trigger_source": "public_index_1m_bar",
            "data_delayed": delayed,
        }
    hit = None
    side = direction = None
    if high_hit is not None and (low_hit is None or _aware(high_hit.start) <= _aware(low_hit.start)):
        hit, side, direction = high_hit, "HIGH", "LONG"
    elif low_hit is not None:
        hit, side, direction = low_hit, "LOW", "SHORT"
    if hit is None:
        return {
            "status": "NO_BREAK",
            "trigger_source": "public_index_1m_bar",
            "data_delayed": delayed,
            "newest_bar_age_seconds": newest_age,
        }
    first = admitted[0]
    gap = hit.start == first.start and (
        (direction == "LONG" and first.open > armed.boundary_high)
        or (direction == "SHORT" and first.open < armed.boundary_low)
    )
    return {
        "status": "PROVEN",
        "break_side": side,
        "direction": direction,
        "index_bar": hit,
        "gap_through": gap,
        "first_print_price": first.open,
        "trigger_source": "public_index_1m_bar",
        "trigger_resolution": "BAR",
        "crossed_window_start": _iso(_aware(hit.start)),
        "crossed_window_end": _iso(hit.window_end),
        "data_delayed": delayed,
        "newest_bar_age_seconds": newest_age,
    }


def _setup_type_for(armed: ArmedStructure, direction: str) -> str:
    return armed.long_setup_type() if direction == "LONG" else armed.short_setup_type()


def resolve_break(
    watching: CaptureRecord,
    armed: ArmedStructure,
    *,
    break_result: Mapping[str, Any],
    now: datetime,
    detected_at: datetime,
    sip_result: Mapping[str, Any] | None = None,
    had_pre_cross_watching: bool,
) -> CaptureRecord:
    persist = _aware(now)
    detected = _aware(detected_at)
    status = str(break_result.get("status") or "")
    if status == STATUS_DATA_BLOCKED:
        return replace(
            watching,
            status=STATUS_DATA_BLOCKED,
            status_reason=str(break_result.get("reason_code") or "source_blocked"),
            detected_at=_iso(detected),
            persisted_at=_iso(persist),
            prospective_catch=False,
        )
    if status == STATUS_AMBIGUOUS:
        return replace(
            watching,
            status=STATUS_AMBIGUOUS,
            status_reason="simultaneous_boundary_break",
            detected_at=_iso(detected),
            persisted_at=_iso(persist),
            prospective_catch=False,
        )
    if status != "PROVEN":
        if persist >= _aware(armed.watch_until):
            return replace(
                watching,
                status=STATUS_EXPIRED,
                status_reason="watch_window_elapsed_without_trigger",
                detected_at=_iso(detected),
                persisted_at=_iso(persist),
                prospective_catch=False,
            )
        return watching

    direction = str(break_result["direction"])
    print = break_result.get("print")
    index_bar = break_result.get("index_bar")
    if print is not None:
        crossed = print.ts_utc
        crossed_ns = print.timestamp_ns
        price = print.price
        trade_id = print.trade_id
        feed = print.feed
        source = f"alpaca_{feed}"
        resolution = "TRADE"
        window_start = window_end = None
    elif index_bar is not None:
        crossed = _aware(index_bar.start)
        crossed_ns = None
        price = float(index_bar.high if direction == "LONG" else index_bar.low)
        trade_id = None
        feed = "public_index"
        source = "public_index_1m_bar"
        resolution = "BAR"
        window_start = break_result.get("crossed_window_start")
        window_end = break_result.get("crossed_window_end")
    else:
        raise ValueError("proven_break_missing_source")

    sip_crossed = None
    if sip_result and sip_result.get("status") == "PROVEN" and sip_result.get("print") is not None:
        sip_crossed = sip_result["print"].ts_utc
    true_cross = sip_crossed or crossed
    true_lag = (detected - _aware(true_cross)).total_seconds()
    iex_lag = (detected - _aware(crossed)).total_seconds() if print is not None else None
    pre_armed = had_pre_cross_watching and datetime.fromisoformat(watching.persisted_at) < _aware(true_cross)
    delayed = bool(break_result.get("data_delayed"))
    capture_late = (not pre_armed) or true_lag > MAX_CAPTURE_LAG_SECONDS or delayed
    gap = bool(break_result.get("gap_through"))

    if not pre_armed:
        final_status = STATUS_MISSED_LATE
        reason = "first_sight_after_trigger"
        if sip_result and sip_result.get("status") == "PROVEN" and break_result.get("status") != "PROVEN":
            reason = "iex_no_cross_sip_cross"
        catch = False
    else:
        final_status = STATUS_TRIGGERED
        reason = "first_observable_cross"
        if capture_late:
            reason = "sip_reconciled_capture_late" if sip_crossed else "capture_late"
        catch = not capture_late and not gap

    if gap and pre_armed:
        final_status = STATUS_GAP_THROUGH_OPEN
        reason = "opening_print_gapped_through"
        catch = False

    setup = _setup_type_for(armed, direction)
    return replace(
        watching,
        status=final_status,
        direction=direction,
        setup_type=setup,
        status_reason=reason,
        prospective_catch=catch,
        capture_late=capture_late,
        gap_through=gap,
        first_print_price=break_result.get("first_print_price"),
        trigger_crossed_at=_iso(crossed),
        trigger_crossed_at_ns=crossed_ns,
        trigger_trade_price=price,
        trigger_trade_id=trade_id,
        trigger_feed=feed,
        trigger_source=source,
        trigger_resolution=resolution,
        crossed_window_start=window_start,
        crossed_window_end=window_end,
        detected_at=_iso(detected),
        persisted_at=_iso(persist),
        sip_crossed_at=_iso(sip_crossed) if sip_crossed else None,
        true_lag_seconds=true_lag,
        iex_lag_seconds=iex_lag,
        data_delayed=delayed,
        sibling_invalidated=True,
        geometry="GAP_THROUGH" if gap else "AT_TRIGGER",
    )


def classify_iex_sip_pair(
    *,
    iex: Mapping[str, Any],
    sip: Mapping[str, Any],
    watch_elapsed: bool,
) -> str:
    """Do not reuse 122 reconcile_pair (continuation = MISS_NO_PROVISIONAL)."""
    if iex.get("status") == "NO_BREAK" and sip.get("status") == "PROVEN":
        return "iex_no_cross_sip_cross"
    if iex.get("status") == "NO_BREAK" and sip.get("status") == "NO_BREAK":
        return "expired_or_no_trigger" if watch_elapsed else "still_watching"
    if iex.get("status") == "PROVEN":
        return "iex_proven"
    return "pending"


def is_prospective_catch(record: CaptureRecord | Mapping[str, Any]) -> bool:
    if isinstance(record, CaptureRecord):
        return bool(
            record.prospective_catch
            and record.status == STATUS_TRIGGERED
            and not record.capture_late
            and not record.gap_through
        )
    return bool(
        record.get("prospective_catch")
        and str(record.get("status") or "") == STATUS_TRIGGERED
        and not record.get("capture_late")
        and not record.get("gap_through")
    )


def catch_count(records: Iterable[CaptureRecord | Mapping[str, Any]]) -> int:
    return sum(1 for row in records if is_prospective_catch(row))


def submit_broker_order(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("setup_capture_has_no_execution_authority")


def consume_risk_budget(*_args: Any, **_kwargs: Any) -> bool:
    return False
