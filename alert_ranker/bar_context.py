"""Causal market context for the options advisory scanner.

Composes a session calendar and a bar provider into the structural inputs the
scanner has been missing: session VWAP, EMA20, Strat candle classification and
the prior candle's high and low, for the scanned ticker and for SPY and QQQ
under the same rules.

Every failure mode produces an explicit reason and an unavailable context.
Nothing here substitutes a default, falls back to a single-venue feed, or
carries a partially-built bar into a decision: a scan with no trustworthy
structure must WAIT, and must say why it waited.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

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

__all__ = [
    "SymbolContext",
    "MarketContext",
    "BarContextBuilder",
    "INDEX_SYMBOLS",
    "EMA_PERIOD",
]

INDEX_SYMBOLS = ("SPY", "QQQ")
EMA_PERIOD = 20


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
                if symbol.strat_sequence:
                    fields["pattern"] = symbol.strat_sequence
                elif symbol.candle_type:
                    fields["pattern"] = symbol.candle_type
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
    timeframe: Timeframe = MINUTE_30
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
            raw = await self.provider.fetch_bars(wanted, self.timeframe, start, cutoff)
        except BarProviderError as exc:
            reason = exc.reason
            if reason == "missing_symbol" and exc.detail:
                reason = f"missing_symbol:{exc.detail.lower()}"
            return MarketContext(False, reason, **base)

        contexts: dict[str, SymbolContext] = {}
        for name in wanted:
            contexts[name] = await self._symbol_context(
                name, raw.get(name, []), session, cutoff
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

    async def _symbol_context(
        self,
        symbol: str,
        bars: Sequence[Bar],
        session: Session,
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

        # Restrict to regular sessions, session by session, so pre-market and
        # post-close intervals never enter Strat context or the EMA series.
        by_day: dict[date, list[Bar]] = {}
        tz = ZoneInfo(self.exchange_timezone)
        for bar in closed:
            by_day.setdefault(bar.start_utc.astimezone(tz).date(), []).append(bar)

        regular: list[Bar] = []
        session_series: list[Bar] = []
        prior_sessions: list[tuple[date, list[Bar]]] = []
        try:
            for day in sorted(by_day):
                day_session = await self._session_for_day(day)
                if day_session is None:
                    # Bars exist on a day the calendar says had no session.
                    # Drop them: a holiday's prints are not session structure.
                    continue
                kept = session_bars(
                    by_day[day], self.timeframe, day_session.open, day_session.close
                )
                if not kept:
                    continue
                regular.extend(kept)
                if day == session.date:
                    session_series = kept
                else:
                    prior_sessions.append((day, kept))
        except SessionCalendarError as exc:
            return SymbolContext(available=False, reason=exc.reason, **base)

        if not session_series:
            return SymbolContext(available=False, reason="no_session_bars", **base)

        gaps = missing_bar_starts(
            session_series, self.timeframe, session.open, session.close, through=cutoff
        )
        if gaps:
            return SymbolContext(
                available=False,
                reason="missing_bars",
                bar_count=len(session_series),
                **base,
            )

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
        # volume include extended-hours activity.
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
            **base,
        )


def timeframe_from_name(name: str) -> Timeframe:
    """Look up a supported timeframe, defaulting to 30 minutes."""
    return TIMEFRAMES.get((name or "").strip(), MINUTE_30)


def create_bar_context(config: Any) -> BarContextBuilder | None:
    """Build a context builder from scanner config, or ``None`` when off.

    Returns ``None`` unless the lane is explicitly enabled AND credentialed,
    so a missing key degrades to the previous behaviour rather than to a
    half-configured context that fails on every scan.
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
