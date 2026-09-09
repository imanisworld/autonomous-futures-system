"""Causal market context for the options advisory scanner.

Composes a session calendar, a bar provider and the shared setup authority
into the structural inputs the scanner needs: session VWAP, EMA20, Strat
candle classification, mechanical setup levels, causal structure targets, and
SPY/QQQ plus higher-timeframe alignment.

Every failure mode produces an explicit reason and an unavailable context.
Nothing here substitutes a default, falls back to a single-venue feed, or
carries a partially-built bar into a decision: a scan with no trustworthy
structure must WAIT, and must say why it waited.

Three rules are load-bearing:

* **A candle type is not a setup.** Candle and sequence classification remain
  context. The shared setup authority must first confirm the mechanical 2-1-2
  sequence and break before this module may promote anything.
* **Paper-evidence promotion is not trade approval.** A mechanically confirmed
  2-1-2 may be promoted to ``TRIGGERED`` for OPTIONS_PAPER_V1 evidence only
  after causal targets and SPY/QQQ + 1h/daily alignment are proven. The row is
  explicitly tagged ``trade_proof_status=INCOMPLETE`` because this data source
  does not prove event risk or flip context; Discord must therefore remain
  blocked until a separate authority proves those facts.
* **Every session used in a calculation must be complete**, not just today's.
  EMA20, previous-candle continuity, target levels and reconstructed daily
  candles all read historical sessions, so a missing trading day or absent
  30-minute bar fails the context closed rather than being silently bridged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from options_manager.levels import LevelFinderInputs, find_targets
from strategy.strat_classifier import TWO_DOWN, TWO_UP

from .bar_provider import CONSOLIDATED_FEED, BarProvider, BarProviderError
from .causal_bars import (
    HOUR_1,
    MINUTE_30,
    TIMEFRAMES,
    Bar,
    Timeframe,
    build_session_candle,
    build_session_timeframe,
    classify_last_bar,
    completed_bars,
    ema,
    missing_bar_starts,
    prior_high_low,
    session_bars,
    session_vwap,
)
from .session_calendar import EXCHANGE_TIMEZONE, SessionCalendar, SessionCalendarError, Session
from .setup_authority import SetupVerdict, evaluate_setup

__all__ = [
    "SymbolContext",
    "MarketContext",
    "BarContextBuilder",
    "INDEX_SYMBOLS",
    "EMA_PERIOD",
    "CANONICAL_TIMEFRAME",
]

INDEX_SYMBOLS = ("SPY", "QQQ")
EMA_PERIOD = 20

# The only timeframe whose native bars were proven to be valid regular-session
# Strat candles. Native hourly bars mix pre-market into the opening candle, so
# an hourly candle is rebuilt from session-aligned pairs of these and is never
# taken from the provider directly. This is not a default that a configured
# value overrides -- it is the only accepted value.
CANONICAL_TIMEFRAME = MINUTE_30


@dataclass(frozen=True)
class SymbolContext:
    """Structural context for one symbol, or an explicit reason it is absent."""

    symbol: str
    available: bool
    reason: str = ""
    timeframe: str = ""
    session_date: str | None = None
    session_open: str | None = None
    session_close: str | None = None
    is_early_close: bool | None = None
    bar_count: int = 0
    last_bar_start: str | None = None
    last_bar_close: str | None = None
    close: float | None = None
    vwap: float | None = None
    ema20: float | None = None
    prior_high: float | None = None
    prior_low: float | None = None
    candle_type: str | None = None
    previous_candle_type: str | None = None
    strat_sequence: str | None = None
    strat_direction: str | None = None
    hourly_bar_count: int = 0
    hourly_candle_type: str | None = None
    daily_candle_type: str | None = None
    daily_session_date: str | None = None
    # Which session failed the completeness check, and by how many bars.
    incomplete_session_date: str | None = None
    missing_bar_count: int = 0
    # Verdict of the shared setup authority. Context on its own is never a
    # setup, so these stay separate from the candle fields above.
    setup_status: str | None = None
    setup_reason_code: str | None = None
    setup_direction: str | None = None
    setup_entry_trigger: float | None = None
    setup_invalidation: float | None = None
    setup_sequence_confirmed: bool = False
    setup_suppression_reason: str | None = None
    # Causal target proof. These are derived only from structure known before
    # the breakout bar: the setup's directional parent plus prior session
    # extrema. No current-breakout high and no future bar can become a target.
    setup_target_1: float | None = None
    setup_target_2: float | None = None
    setup_rr_1: float | None = None
    setup_rr_2: float | None = None
    setup_target_reason: str | None = None
    setup_resistance_levels: tuple[float, ...] = ()
    setup_support_levels: tuple[float, ...] = ()
    # Evidence-level market proof. This is deliberately narrower than a trade
    # approval: SPY/QQQ trend plus ticker 1h/daily continuity only.
    setup_market_status: str | None = None
    setup_market_reason: str | None = None
    setup_proof_status: str | None = None
    # Event-risk and flip authorities do not exist in this causal bar source.
    # The field is explicit so downstream user-facing alerting fails closed.
    trade_proof_status: str | None = None
    trade_proof_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketContext:
    """Context for the scanned ticker plus its index backdrop."""

    available: bool
    reason: str
    feed: str
    requested_as_of: str
    information_cutoff: str
    delay_buffer_seconds: int
    timeframe: str
    ticker: SymbolContext | None = None
    spy: SymbolContext | None = None
    qqq: SymbolContext | None = None

    def to_scanner_fields(self) -> dict[str, Any]:
        """Flatten into the keys the scanner merges into a scan row.

        Telemetry is emitted whether or not the context is available, because
        the reason a scan had no structure is the evidence being collected.
        """
        fields: dict[str, Any] = {
            "bar_context_available": self.available,
            "bar_context_reason": self.reason or None,
            "bar_context_feed": self.feed,
            "bar_context_requested_as_of": self.requested_as_of,
            "bar_context_information_cutoff": self.information_cutoff,
            "bar_context_delay_buffer_seconds": self.delay_buffer_seconds,
            "bar_context_timeframe": self.timeframe,
            "spy_context_available": bool(self.spy and self.spy.available),
            "qqq_context_available": bool(self.qqq and self.qqq.available),
        }
        if self.ticker is not None:
            symbol = self.ticker
            fields.update(
                {
                    "session_date": symbol.session_date,
                    "session_open": symbol.session_open,
                    "session_close": symbol.session_close,
                    "session_is_early_close": symbol.is_early_close,
                    "bar_count": symbol.bar_count,
                    "latest_completed_bar_start": symbol.last_bar_start,
                    "latest_completed_bar_close": symbol.last_bar_close,
                    "prev_candle_high": symbol.prior_high,
                    "prev_candle_low": symbol.prior_low,
                    "candle_type": symbol.candle_type,
                    "previous_candle_type": symbol.previous_candle_type,
                    "strat_sequence": symbol.strat_sequence,
                    "hourly_candle_type": symbol.hourly_candle_type,
                    "daily_candle_type": symbol.daily_candle_type,
                    "incomplete_session_date": symbol.incomplete_session_date,
                    "missing_bar_count": symbol.missing_bar_count,
                    "setup_status": symbol.setup_status,
                    "setup_reason_code": symbol.setup_reason_code,
                    "setup_direction": symbol.setup_direction,
                    "setup_entry_trigger": symbol.setup_entry_trigger,
                    "setup_invalidation": symbol.setup_invalidation,
                    "setup_sequence_confirmed": symbol.setup_sequence_confirmed,
                    "setup_suppression_reason": symbol.setup_suppression_reason,
                    "target_1": symbol.setup_target_1,
                    "target_2": symbol.setup_target_2,
                    "setup_rr_1": symbol.setup_rr_1,
                    "setup_rr_2": symbol.setup_rr_2,
                    "setup_target_reason": symbol.setup_target_reason,
                    "setup_resistance_levels": list(symbol.setup_resistance_levels),
                    "setup_support_levels": list(symbol.setup_support_levels),
                    "setup_market_status": symbol.setup_market_status,
                    "setup_market_reason": symbol.setup_market_reason,
                    "setup_proof_status": symbol.setup_proof_status,
                    "trade_proof_status": symbol.trade_proof_status,
                    "trade_proof_reason": symbol.trade_proof_reason,
                }
            )
            if symbol.available:
                fields.update(
                    {
                        "vwap": symbol.vwap,
                        "ema20": symbol.ema20,
                        "timeframe": symbol.timeframe,
                    }
                )
                # `pattern` is the field the scorer credits. It is emitted only
                # after a shared-authority-confirmed 2-1-2 has passed the
                # evidence bridge's causal target and market proof. It is still
                # not trade permission: trade_proof_status remains separately
                # fail-closed until event/flip proof exists.
                if symbol.setup_status == "TRIGGERED" and symbol.strat_sequence:
                    fields["pattern"] = symbol.strat_sequence
        if self.spy is not None:
            fields["spy_context"] = self.spy.to_dict()
        if self.qqq is not None:
            fields["qqq_context"] = self.qqq.to_dict()
        return fields


@dataclass
class BarContextBuilder:
    """Builds causal context, failing closed on every incomplete input."""

    provider: BarProvider
    calendar: SessionCalendar
    timeframe: Timeframe = CANONICAL_TIMEFRAME
    delay_buffer: timedelta = timedelta(minutes=16)
    lookback_days: int = 10
    stale_multiple: int = 2
    exchange_timezone: str = EXCHANGE_TIMEZONE
    index_symbols: tuple[str, ...] = INDEX_SYMBOLS
    require_index_context: bool = True
    _cache: dict[tuple[str, str], Session | None] = field(default_factory=dict, init=False)

    async def build(self, ticker: str, now: datetime) -> MarketContext:
        symbol = (ticker or "").strip().upper()
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        cutoff = now.astimezone(timezone.utc) - self.delay_buffer
        base = {
            "feed": getattr(self.provider, "feed", ""),
            "requested_as_of": now.astimezone(timezone.utc).isoformat(),
            "information_cutoff": cutoff.isoformat(),
            "delay_buffer_seconds": int(self.delay_buffer.total_seconds()),
            "timeframe": self.timeframe.name,
        }

        # Setup decisions are only ever made on the consolidated tape. A
        # single-venue feed reproduces average prices but changes discrete
        # Strat classification and prior-high/low breaks, so it must never
        # silently stand in here.
        if base["feed"] != CONSOLIDATED_FEED:
            return MarketContext(False, "feed_not_consolidated", **base)
        # Refused before any fetch, so an unsafe configured timeframe cannot
        # reach the provider, let alone the Strat/EMA/level calculations.
        if self.timeframe.name != CANONICAL_TIMEFRAME.name:
            return MarketContext(False, "unsupported_timeframe", **base)
        if not symbol:
            return MarketContext(False, "missing_symbol", **base)

        wanted: list[str] = [symbol]
        if self.require_index_context:
            wanted.extend(s for s in self.index_symbols if s != symbol)

        try:
            session = await self._session_for(cutoff)
        except SessionCalendarError as exc:
            return MarketContext(False, exc.reason, **base)
        if session is None:
            return MarketContext(False, "no_session", **base)
        if cutoff < session.open:
            return MarketContext(False, "session_not_started", **base)

        start = cutoff - timedelta(days=self.lookback_days)
        try:
            required = await self._sessions_in_window(start, session)
        except SessionCalendarError as exc:
            return MarketContext(False, exc.reason, **base)

        try:
            raw = await self.provider.fetch_bars(wanted, self.timeframe, start, cutoff)
        except BarProviderError as exc:
            reason = exc.reason
            if reason == "missing_symbol" and exc.detail:
                reason = f"missing_symbol:{exc.detail.lower()}"
            return MarketContext(False, reason, **base)

        contexts: dict[str, SymbolContext] = {}
        for name in wanted:
            contexts[name] = self._symbol_context(
                name, raw.get(name, []), session, required, cutoff
            )

        ticker_context = contexts[symbol]
        # When the scanned ticker IS an index, its own context is the index
        # context; both are the same object rather than a second fetch.
        spy = contexts.get("SPY")
        qqq = contexts.get("QQQ")

        reason = ""
        if not ticker_context.available:
            reason = ticker_context.reason
        elif self.require_index_context and spy is not None and not spy.available:
            reason = "missing_context:spy"
        elif self.require_index_context and qqq is not None and not qqq.available:
            reason = "missing_context:qqq"

        if not reason:
            ticker_context = self._promote_paper_evidence_setup(ticker_context, spy, qqq)

        return MarketContext(
            available=not reason,
            reason=reason,
            ticker=ticker_context,
            spy=spy,
            qqq=qqq,
            **base,
        )

    async def _session_for(self, moment: datetime) -> Session | None:
        local_day = moment.astimezone(ZoneInfo(self.exchange_timezone)).date()
        return await self._session_for_day(local_day)

    async def _session_for_day(self, day: date) -> Session | None:
        key = (self.exchange_timezone, day.isoformat())
        if key not in self._cache:
            self._cache[key] = await self.calendar.session_for(day)
        return self._cache[key]

    async def _sessions_in_window(
        self, window_start: datetime, current: Session
    ) -> list[Session]:
        """Every session the calendar says falls wholly inside the request.

        The calendar is the authority on which sessions must be present, so a
        trading day that returned no bars at all is still expected and still
        fails the completeness check. A session that opened before the request
        window is excluded instead: that truncation is ours, not the
        provider's, and holding an unrequested session to a completeness bar
        would fail every scan.
        """
        tz = ZoneInfo(self.exchange_timezone)
        day = window_start.astimezone(tz).date()
        sessions: list[Session] = []
        while day <= current.date:
            session = await self._session_for_day(day)
            if session is not None and session.open >= window_start:
                sessions.append(session)
            day = day + timedelta(days=1)
        return sessions

    def _symbol_context(
        self,
        symbol: str,
        bars: Sequence[Bar],
        session: Session,
        required: Sequence[Session],
        cutoff: datetime,
    ) -> SymbolContext:
        base = {
            "symbol": symbol,
            "timeframe": self.timeframe.name,
            "session_date": session.date.isoformat(),
            "session_open": session.open.isoformat(),
            "session_close": session.close.isoformat(),
            "is_early_close": session.is_early_close,
        }

        closed = completed_bars(bars, self.timeframe, cutoff)
        if not closed:
            return SymbolContext(available=False, reason="no_completed_bars", **base)

        tz = ZoneInfo(self.exchange_timezone)
        by_day: dict[date, list[Bar]] = {}
        for bar in closed:
            by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)

        # Walk the calendar's sessions, not the days that happen to have bars.
        # Bars on a day with no session (a holiday can still print) are simply
        # never looked at: a holiday's prints are not session structure.
        regular: list[Bar] = []
        session_series: list[Bar] = []
        prior_sessions: list[tuple[date, list[Bar]]] = []
        for day_session in required:
            is_current = day_session.date == session.date
            kept = session_bars(
                by_day.get(day_session.date, []),
                self.timeframe,
                day_session.open,
                day_session.close,
            )
            if is_current and not kept:
                return SymbolContext(available=False, reason="no_session_bars", **base)
            gaps = missing_bar_starts(
                kept,
                self.timeframe,
                day_session.open,
                day_session.close,
                # A closed session is required to be whole. Only the session
                # still in progress is measured against the cutoff.
                through=cutoff if is_current else None,
            )
            if gaps:
                return SymbolContext(
                    available=False,
                    reason="missing_bars",
                    bar_count=len(kept),
                    incomplete_session_date=day_session.date.isoformat(),
                    missing_bar_count=len(gaps),
                    **base,
                )
            regular.extend(kept)
            if is_current:
                session_series = kept
            else:
                prior_sessions.append((day_session.date, kept))

        if not session_series:
            return SymbolContext(available=False, reason="no_session_bars", **base)

        latest = regular[-1]
        latest_close = latest.start_utc + self.timeframe.delta
        if cutoff - latest_close > self.timeframe.delta * self.stale_multiple:
            return SymbolContext(
                available=False,
                reason="stale_market_data",
                bar_count=len(regular),
                last_bar_start=latest.start_utc.isoformat(),
                last_bar_close=latest_close.isoformat(),
                **base,
            )

        vwap = session_vwap(session_series)
        if vwap is None:
            return SymbolContext(available=False, reason="missing_inputs:vwap", **base)

        ema20 = ema([bar.close for bar in regular], EMA_PERIOD)
        if ema20 is None:
            return SymbolContext(
                available=False,
                reason="insufficient_history",
                bar_count=len(regular),
                **base,
            )

        levels = prior_high_low(regular)
        if levels is None:
            return SymbolContext(available=False, reason="insufficient_history", **base)

        strat = classify_last_bar(regular)
        verdict = self._setup_verdict(symbol, regular, latest)
        target_1 = None
        target_2 = None
        rr_1 = None
        rr_2 = None
        target_reason = None
        resistance_levels: tuple[float, ...] = ()
        support_levels: tuple[float, ...] = ()
        if verdict.sequence_confirmed and verdict.entry_trigger is not None and verdict.invalidation is not None:
            resistance_levels, support_levels = self._causal_structure_levels(regular)
            target_result = find_targets(
                LevelFinderInputs(
                    direction=verdict.direction or "CALL",
                    entry=verdict.entry_trigger,
                    underlying_invalidation=verdict.invalidation,
                    resistance_levels=resistance_levels,
                    support_levels=support_levels,
                )
            )
            if target_result.status == "VALID":
                target_1 = target_result.target_1
                target_2 = target_result.target_2
                rr_1 = target_result.rr_1
                rr_2 = target_result.rr_2
                target_reason = target_result.reason_code
            else:
                target_reason = target_result.reason_code

        # Session-aligned hourly candles, rebuilt from the session's own bars
        # so the opening hour cannot inherit pre-market range the way a
        # clock-aligned vendor hourly bar does.
        hourly: list[Bar] = []
        if self.timeframe.seconds < HOUR_1.seconds:
            hourly = build_session_timeframe(
                session_series, self.timeframe, HOUR_1, session.open
            )
        hourly_strat = classify_last_bar(hourly) if len(hourly) >= 2 else {}

        # Daily Strat context is reconstructed from completed regular sessions
        # rather than taken from the vendor daily bar, whose open, close and
        # volume include extended-hours activity. Every session feeding this
        # has already passed the completeness check above.
        daily_type: str | None = None
        daily_date: str | None = None
        candles = [
            candle
            for candle in (build_session_candle(day_bars) for _, day_bars in prior_sessions)
            if candle is not None
        ]
        if len(candles) >= 2:
            daily_type = classify_last_bar(candles).get("candle_type")
            daily_date = prior_sessions[-1][0].isoformat()

        return SymbolContext(
            available=True,
            reason="",
            bar_count=len(regular),
            last_bar_start=latest.start_utc.isoformat(),
            last_bar_close=latest_close.isoformat(),
            close=latest.close,
            vwap=vwap,
            ema20=ema20,
            prior_high=levels[0],
            prior_low=levels[1],
            candle_type=strat.get("candle_type"),
            previous_candle_type=strat.get("previous_candle_type"),
            strat_sequence=strat.get("strat_sequence"),
            strat_direction=strat.get("strat_direction"),
            hourly_bar_count=len(hourly),
            hourly_candle_type=hourly_strat.get("candle_type"),
            daily_candle_type=daily_type,
            daily_session_date=daily_date,
            setup_status=verdict.status,
            setup_reason_code=verdict.reason_code,
            setup_direction=verdict.direction,
            setup_entry_trigger=verdict.entry_trigger,
            setup_invalidation=verdict.invalidation,
            setup_sequence_confirmed=verdict.sequence_confirmed,
            setup_suppression_reason=verdict.suppression_reason or None,
            setup_target_1=target_1,
            setup_target_2=target_2,
            setup_rr_1=rr_1,
            setup_rr_2=rr_2,
            setup_target_reason=target_reason,
            setup_resistance_levels=resistance_levels,
            setup_support_levels=support_levels,
            **base,
        )

    def _causal_structure_levels(
        self, bars: Sequence[Bar]
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Strong, already-known structure levels for target selection.

        The breakout bar and its inside trigger bar are excluded. The opening
        directional bar of the 2-1-2 is already known at entry and is retained,
        because its high/low is a legitimate Strat target/support reference.
        Earlier history is reduced to regular-session daily extrema so every
        30-minute wiggle does not become a fake target.
        """
        if len(bars) < 3:
            return (), ()
        known = list(bars[:-2])
        if not known:
            return (), ()
        tz = ZoneInfo(self.exchange_timezone)
        by_day: dict[date, list[Bar]] = {}
        for bar in known:
            by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)
        resistance = {max(day_bars, key=lambda item: item.high).high for day_bars in by_day.values()}
        support = {min(day_bars, key=lambda item: item.low).low for day_bars in by_day.values()}
        directional = bars[-3]
        resistance.add(directional.high)
        support.add(directional.low)
        return tuple(sorted(resistance)), tuple(sorted(support, reverse=True))

    @staticmethod
    def _trend(context: SymbolContext | None) -> str | None:
        if (
            context is None
            or not context.available
            or context.close is None
            or context.vwap is None
            or context.ema20 is None
        ):
            return None
        if context.close > context.vwap and context.close > context.ema20:
            return "bullish"
        if context.close < context.vwap and context.close < context.ema20:
            return "bearish"
        return "neutral"

    def _promote_paper_evidence_setup(
        self,
        ticker: SymbolContext,
        spy: SymbolContext | None,
        qqq: SymbolContext | None,
    ) -> SymbolContext:
        """Promote only a proven evidence candidate, never a trade approval.

        The shared strategy authority has already confirmed the exact 2-1-2
        sequence and breakout when ``sequence_confirmed`` is true and its first
        missing proof is ``missing_target_1``. We then require two causal
        structure targets, SPY and QQQ aligned with the direction, and matching
        1h + prior completed daily Strat direction. Contract/risk proof occurs
        later in OPTIONS_PAPER_V1. Event-risk and flip proof are intentionally
        absent here, so the resulting row remains blocked from Discord.
        """
        if not ticker.setup_sequence_confirmed:
            return ticker
        if ticker.setup_reason_code != "missing_target_1":
            return ticker
        if ticker.setup_direction not in {"CALL", "PUT"}:
            return replace(
                ticker,
                setup_proof_status="INVALID",
                setup_market_status="INVALID",
                setup_market_reason="direction_unresolved",
            )
        if ticker.setup_target_1 is None or ticker.setup_target_2 is None:
            return replace(
                ticker,
                setup_proof_status="INCOMPLETE",
                setup_market_status="UNRESOLVED",
                setup_suppression_reason=(
                    f"setup_proof_incomplete:target_finder_{ticker.setup_target_reason or 'missing'}"
                ),
            )

        desired_trend = "bullish" if ticker.setup_direction == "CALL" else "bearish"
        desired_candle = TWO_UP if ticker.setup_direction == "CALL" else TWO_DOWN
        spy_trend = self._trend(spy)
        qqq_trend = self._trend(qqq)
        market_reason = (
            f"spy={spy_trend or 'missing'};qqq={qqq_trend or 'missing'};"
            f"hourly={ticker.hourly_candle_type or 'missing'};"
            f"daily={ticker.daily_candle_type or 'missing'}"
        )
        market_aligned = (
            spy_trend == desired_trend
            and qqq_trend == desired_trend
            and ticker.hourly_candle_type == desired_candle
            and ticker.daily_candle_type == desired_candle
        )
        if not market_aligned:
            return replace(
                ticker,
                setup_proof_status="INCOMPLETE",
                setup_market_status="NOT_ALIGNED",
                setup_market_reason=market_reason,
                setup_suppression_reason="setup_proof_incomplete:market_not_aligned",
            )

        return replace(
            ticker,
            setup_status="TRIGGERED",
            setup_reason_code="paper_evidence_setup_proven",
            setup_suppression_reason=None,
            setup_market_status="VALID",
            setup_market_reason=market_reason,
            setup_proof_status="VALID",
            trade_proof_status="INCOMPLETE",
            trade_proof_reason="event_risk_unavailable;flip_context_unavailable",
        )

    def _setup_verdict(
        self, symbol: str, bars: Sequence[Bar], latest: Bar
    ) -> SetupVerdict:
        """Delegate the mechanical setup question to the shared authority."""
        return evaluate_setup(
            bars, ticker=symbol, timestamp=latest.start_utc.isoformat()
        )


def timeframe_from_name(name: str) -> Timeframe:
    """Look up a configured timeframe WITHOUT silently correcting it.

    An unknown or unsafe name is carried through under its own name so
    :meth:`BarContextBuilder.build` can refuse it and say which value it
    refused. Quietly substituting the canonical timeframe would hide a
    misconfiguration behind correct-looking output.
    """
    cleaned = (name or "").strip()
    known = TIMEFRAMES.get(cleaned)
    if known is not None:
        return known
    return Timeframe(cleaned or "unset", CANONICAL_TIMEFRAME.delta)


def create_bar_context(config: Any) -> BarContextBuilder | None:
    """Build a context builder from scanner config, or ``None`` when off.

    Returns ``None`` unless the lane is explicitly enabled AND credentialed.
    A ``None`` return with the lane switched on is reported by the scanner as
    ``bar_context_unconfigured`` rather than being treated as an intentional
    OFF, so a half-configured deployment is visible instead of silent.
    """
    from .bar_provider import AlpacaBarProvider
    from .config import resolve_alpaca_credentials
    from .session_calendar import AlpacaSessionCalendar

    if not getattr(config, "bar_context_configured", False):
        return None
    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        return None
    provider = AlpacaBarProvider(
        base_url=config.alpaca_data_base_url,
        api_key=api_key,
        secret_key=secret_key,
        feed=config.bar_context_feed,
    )
    calendar = AlpacaSessionCalendar(
        base_url=config.alpaca_trading_base_url,
        api_key=api_key,
        secret_key=secret_key,
    )
    return BarContextBuilder(
        provider=provider,
        calendar=calendar,
        timeframe=timeframe_from_name(config.bar_context_timeframe),
        delay_buffer=timedelta(seconds=config.sip_delay_buffer_seconds),
        lookback_days=config.bar_context_lookback_days,
        exchange_timezone=config.timezone,
    )