"""
tests/test_options_causal_bar_context.py

PR C — causal options bar context.

Each test pins one behaviour that the 2026-09-01 VPS evidence pass proved the
provider does NOT give us for free: completed-bar filtering, calendar-driven
sessions across daylight saving and early closes, session-aligned hourly
reconstruction, regular-session daily reconstruction, and fail-closed handling
of every silent provider failure (dropped symbol, shared multi-symbol limit,
entitlement block, staleness).

Provider behaviour is mocked. Nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import ast
import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from alert_ranker import bar_context as bar_context_module
from alert_ranker import bar_provider as bar_provider_module
from alert_ranker import causal_bars as causal_bars_module
from alert_ranker import session_calendar as session_calendar_module
from alert_ranker.bar_context import BarContextBuilder, SymbolContext
from alert_ranker.bar_provider import AlpacaBarProvider, BarProviderError
from alert_ranker.causal_bars import (
    HOUR_1,
    MINUTE_30,
    Bar,
    build_session_candle,
    build_session_timeframe,
    completed_bars,
    ema,
    missing_bar_starts,
    prior_high_low,
    session_bars,
    session_vwap,
)
from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.scanner import OptionsScanner
from alert_ranker.session_calendar import (
    Session,
    SessionCalendarError,
    StaticSessionCalendar,
    calendar_url,
    parse_session,
)
from alert_ranker.storage import ScanStorage

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


# ─── helpers ──────────────────────────────────────────────────────────────────


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def make_bar(start: datetime, *, o: float, h: float, low: float, c: float, v: float = 1000.0,
             vw: float | None = None) -> Bar:
    return Bar(start=start, open=o, high=h, low=low, close=c, volume=v,
               vwap=vw if vw is not None else (h + low) / 2)


def full_session(day: date) -> Session:
    """Regular EDT/EST session built the way the calendar builds it."""
    return parse_session({"date": day.isoformat(), "open": "09:30", "close": "16:00"})


def early_session(day: date) -> Session:
    return parse_session({"date": day.isoformat(), "open": "09:30", "close": "13:00"})


def rising_series(session: Session, count: int, *, base: float = 100.0,
                  step: float = 1.0) -> list[Bar]:
    """Contiguous 30-minute bars, each making a higher high and higher low."""
    bars = []
    for index in range(count):
        low = base + index * step
        bars.append(
            make_bar(
                session.open + MINUTE_30.delta * index,
                o=low + 0.1,
                h=low + step,
                low=low,
                c=low + step - 0.1,
                v=1000.0 + index,
            )
        )
    return bars


def full_day_series(session: Session, *, base: float = 100.0) -> list[Bar]:
    count = len(
        [
            None
            for _ in range(1000)
        ][: int((session.close - session.open) / MINUTE_30.delta)]
    )
    return rising_series(session, count, base=base)


class FakeProvider:
    """Records requests and returns canned bars, or raises a canned error."""

    def __init__(self, bars_by_symbol: dict[str, list[Bar]] | None = None,
                 error: BarProviderError | None = None, feed: str = "sip") -> None:
        self.bars_by_symbol = bars_by_symbol or {}
        self.error = error
        self.feed = feed
        self.calls: list[dict] = []

    async def fetch_bars(self, symbols, timeframe, start, end):
        self.calls.append(
            {"symbols": list(symbols), "timeframe": timeframe, "start": start, "end": end}
        )
        if self.error is not None:
            raise self.error
        missing = [s for s in symbols if s not in self.bars_by_symbol]
        if missing:
            raise BarProviderError("missing_symbol", ",".join(sorted(missing)))
        return {s: list(self.bars_by_symbol[s]) for s in symbols}


def build_context(builder: BarContextBuilder, ticker: str, now: datetime):
    return asyncio.run(builder.build(ticker, now))


SESSIONS = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]


def standard_calendar() -> StaticSessionCalendar:
    return StaticSessionCalendar.from_sessions(full_session(d) for d in SESSIONS)


def standard_bars(symbol_base: dict[str, float]) -> dict[str, list[Bar]]:
    """Four full sessions of contiguous 30-minute bars per symbol."""
    out: dict[str, list[Bar]] = {}
    for symbol, base in symbol_base.items():
        bars: list[Bar] = []
        for index, day in enumerate(SESSIONS):
            bars.extend(rising_series(full_session(day), 13, base=base + index * 10))
        out[symbol] = bars
    return out


def standard_builder(**overrides) -> BarContextBuilder:
    provider = overrides.pop(
        "provider",
        FakeProvider(standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0})),
    )
    return BarContextBuilder(
        provider=provider,
        calendar=overrides.pop("calendar", standard_calendar()),
        timeframe=MINUTE_30,
        delay_buffer=timedelta(minutes=16),
        **overrides,
    )


# Mid-session moment: cutoff lands exactly on the 17:00Z close of the 16:30 bar.
NOW = utc(2026, 9, 1, 17, 16)
CUTOFF = utc(2026, 9, 1, 17, 0)


# ─── 1 & 2. completed-bar filtering / no future leakage ───────────────────────


def test_completed_bar_filtering_prevents_future_leakage():
    session = full_session(date(2026, 9, 1))
    bars = rising_series(session, 4)  # 13:30, 14:00, 14:30, 15:00
    kept = completed_bars(bars, MINUTE_30, utc(2026, 9, 1, 15, 0))
    assert [b.start for b in kept] == [
        utc(2026, 9, 1, 13, 30),
        utc(2026, 9, 1, 14, 0),
        utc(2026, 9, 1, 14, 30),
    ]


def test_bar_starting_before_cutoff_but_closing_after_it_is_rejected():
    """The provider returns this bar fully populated; only the client can reject it."""
    session = full_session(date(2026, 9, 1))
    bars = rising_series(session, 4)
    straddling = bars[3]  # starts 15:00, closes 15:30
    cutoff = utc(2026, 9, 1, 15, 29)
    assert straddling.start < cutoff < straddling.start + MINUTE_30.delta
    kept = completed_bars(bars, MINUTE_30, cutoff)
    assert straddling not in kept
    assert all(b.start + MINUTE_30.delta <= cutoff for b in kept)


def test_builder_never_requests_beyond_the_information_cutoff():
    provider = FakeProvider(standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0}))
    builder = standard_builder(provider=provider)
    context = build_context(builder, "AAPL", NOW)
    assert context.available, context.reason
    assert provider.calls[0]["end"] == CUTOFF
    assert context.information_cutoff == CUTOFF.isoformat()
    # The newest bar offered to the scorer closed at or before the cutoff.
    assert datetime.fromisoformat(context.ticker.last_bar_close) <= CUTOFF


# ─── 3. an undefined cutoff can never mean "latest data" ──────────────────────


def test_provider_requires_an_explicit_end_and_always_sends_one():
    """Omitting `end` returns 200 and silently clamps, so it must be impossible."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"bars": {"AAPL": [_wire_bar("2026-09-01T13:30:00Z")]}})

    provider = AlpacaBarProvider(
        base_url="https://data.example",
        api_key="k",
        secret_key="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(BarProviderError) as excinfo:
        asyncio.run(
            provider.fetch_bars(["AAPL"], MINUTE_30, utc(2026, 9, 1, 13, 0), None)  # type: ignore[arg-type]
        )
    assert excinfo.value.reason == "missing_information_cutoff"
    assert captured == []

    asyncio.run(
        provider.fetch_bars(["AAPL"], MINUTE_30, utc(2026, 9, 1, 13, 0), CUTOFF)
    )
    assert "end=2026-09-01T17%3A00%3A00Z" in str(captured[0].url)


def _wire_bar(t: str, price: float = 100.0) -> dict:
    return {"t": t, "o": price, "h": price + 1, "l": price - 1, "c": price + 0.5,
            "v": 1000, "vw": price, "n": 10}


# ─── 4, 5, 6, 7. calendar: normal, DST, early close, holiday ──────────────────


def test_normal_session_boundaries_come_from_the_calendar():
    session = full_session(date(2026, 9, 1))
    assert session.open == utc(2026, 9, 1, 13, 30)
    assert session.close == utc(2026, 9, 1, 20, 0)
    assert session.is_early_close is False
    assert session.minutes == 390


def test_daylight_saving_shifts_the_session_in_utc():
    """13:30-20:00Z under EDT, 14:30-21:00Z under EST — never a fixed UTC window."""
    summer = full_session(date(2026, 9, 1))
    winter = full_session(date(2025, 12, 23))
    assert (summer.open.hour, summer.close.hour) == (13, 20)
    assert (winter.open.hour, winter.close.hour) == (14, 21)
    assert summer.minutes == winter.minutes == 390


def test_early_close_session_is_shorter_and_flagged():
    session = early_session(date(2025, 11, 28))
    assert session.open == utc(2025, 11, 28, 14, 30)
    assert session.close == utc(2025, 11, 28, 18, 0)
    assert session.is_early_close is True
    assert session.minutes == 210
    # 7 thirty-minute bars, and no partial trailing interval.
    bars = rising_series(session, 8)  # one more than the session holds
    kept = session_bars(bars, MINUTE_30, session.open, session.close)
    assert len(kept) == 7
    assert kept[-1].start + MINUTE_30.delta == session.close


def test_holiday_has_no_session_even_when_bars_exist():
    """2025-11-27 returned intraday bars with no session and no daily bar."""
    calendar = StaticSessionCalendar.from_sessions([full_session(date(2025, 11, 26))])
    holiday = date(2025, 11, 27)
    assert asyncio.run(calendar.session_for(holiday)) is None

    builder = BarContextBuilder(
        provider=FakeProvider({"AAPL": rising_series(full_session(date(2025, 11, 26)), 13)}),
        calendar=calendar,
        timeframe=MINUTE_30,
        delay_buffer=timedelta(minutes=16),
        require_index_context=False,
    )
    context = build_context(builder, "AAPL", utc(2025, 11, 27, 17, 16))
    assert context.available is False
    assert context.reason == "no_session"


def test_calendar_url_tolerates_a_base_that_already_ends_in_v2():
    assert calendar_url("https://paper-api.example/v2") == "https://paper-api.example/v2/calendar"
    assert calendar_url("https://paper-api.example") == "https://paper-api.example/v2/calendar"


def test_malformed_calendar_entry_fails_closed():
    with pytest.raises(SessionCalendarError) as excinfo:
        parse_session({"date": "2026-09-01", "open": "09:30", "close": "09:00"})
    assert excinfo.value.reason == "calendar_malformed"


# ─── 8 & 9. session-aligned hourly construction ───────────────────────────────


def test_session_aligned_hourly_bars_are_built_from_thirty_minute_pairs():
    session = full_session(date(2026, 9, 1))
    bars = rising_series(session, 13)
    hourly = build_session_timeframe(bars, MINUTE_30, HOUR_1, session.open)
    # 13 half-hours = 6 whole session hours; the trailing stub is dropped, not
    # published as a completed candle.
    assert len(hourly) == 6
    assert hourly[0].start == session.open
    assert hourly[0].open == bars[0].open
    assert hourly[0].close == bars[1].close
    assert hourly[0].high == max(bars[0].high, bars[1].high)
    assert hourly[0].volume == bars[0].volume + bars[1].volume


def test_opening_hourly_candle_cannot_inherit_premarket_range():
    """A clock-aligned vendor 1h bar mixes 30 pre-market minutes into 13:00Z."""
    session = full_session(date(2026, 9, 1))
    premarket = make_bar(utc(2026, 9, 1, 13, 0), o=90.0, h=999.0, low=1.0, c=95.0)
    bars = [premarket] + rising_series(session, 13)

    kept = session_bars(bars, MINUTE_30, session.open, session.close)
    assert premarket not in kept

    hourly = build_session_timeframe(kept, MINUTE_30, HOUR_1, session.open)
    assert hourly[0].high < 999.0
    assert hourly[0].low > 1.0
    assert hourly[0].start == session.open


def test_hourly_construction_refuses_to_bridge_a_gap():
    session = full_session(date(2026, 9, 1))
    bars = rising_series(session, 13)
    del bars[1]  # 14:00 missing: pairing across it would fabricate a candle
    hourly = build_session_timeframe(bars, MINUTE_30, HOUR_1, session.open)
    assert all(candle.start != session.open for candle in hourly)


# ─── 10. regular-session daily reconstruction ─────────────────────────────────


def test_daily_candle_is_reconstructed_from_regular_session_bars_only():
    """The vendor daily bar carries extended-hours open/close/volume."""
    session = full_session(date(2026, 9, 1))
    rth = rising_series(session, 13)
    after_hours = make_bar(session.close, o=200.0, h=250.0, low=199.0, c=245.0, v=9_999.0)

    candle = build_session_candle(rth)
    assert candle is not None
    assert candle.open == rth[0].open
    assert candle.close == rth[-1].close
    assert candle.high == max(b.high for b in rth)
    assert candle.volume == sum(b.volume for b in rth)

    contaminated = build_session_candle(rth + [after_hours])
    assert contaminated is not None
    assert contaminated.close != candle.close
    assert contaminated.high > candle.high
    # Session filtering is what keeps the extended bar out in the real path.
    kept = session_bars(rth + [after_hours], MINUTE_30, session.open, session.close)
    assert build_session_candle(kept) == candle


# ─── 11. VWAP resets per session ──────────────────────────────────────────────


def test_session_vwap_resets_each_session():
    day_one = rising_series(full_session(date(2026, 8, 31)), 13, base=100.0)
    day_two = rising_series(full_session(date(2026, 9, 1)), 13, base=200.0)

    vwap_one = session_vwap(day_one)
    vwap_two = session_vwap(day_two)
    combined = session_vwap(day_one + day_two)
    assert vwap_one is not None and vwap_two is not None
    assert vwap_one != vwap_two
    assert combined != vwap_two  # carrying yesterday forward is a different number

    builder = standard_builder()
    context = build_context(builder, "AAPL", NOW)
    assert context.available, context.reason
    # Only the current session's completed bars feed VWAP.
    current = rising_series(full_session(date(2026, 9, 1)), 13, base=130.0)[:7]
    assert context.ticker.vwap == pytest.approx(session_vwap(current))


def test_session_vwap_requires_volume_weighted_prices():
    bars = [make_bar(utc(2026, 9, 1, 13, 30), o=1, h=2, low=1, c=1.5, vw=None)]
    object.__setattr__(bars[0], "vwap", None)
    assert session_vwap(bars) is None


# ─── 12. EMA20 needs real history ─────────────────────────────────────────────


def test_ema20_requires_sufficient_completed_history():
    assert ema([float(i) for i in range(19)], 20) is None
    assert ema([float(i) for i in range(20)], 20) is not None


def test_context_fails_closed_when_history_is_too_short_for_ema20():
    only_today = {
        symbol: rising_series(full_session(date(2026, 9, 1)), 13, base=base)
        for symbol, base in (("AAPL", 100.0), ("SPY", 500.0), ("QQQ", 400.0))
    }
    builder = standard_builder(provider=FakeProvider(only_today))
    context = build_context(builder, "AAPL", NOW)
    assert context.available is False
    assert context.reason == "insufficient_history"


# ─── 13, 14. multi-symbol integrity ───────────────────────────────────────────


def test_missing_requested_symbol_fails_closed():
    """An unknown symbol is omitted from the response with no error field."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bars": {"AAPL": [_wire_bar("2026-09-01T13:30:00Z")]}})

    provider = AlpacaBarProvider(
        base_url="https://data.example", api_key="k", secret_key="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(BarProviderError) as excinfo:
        asyncio.run(
            provider.fetch_bars(["AAPL", "ZZZZQQ"], MINUTE_30, utc(2026, 9, 1, 13, 0), CUTOFF)
        )
    assert excinfo.value.reason == "missing_symbol"
    assert "ZZZZQQ" in excinfo.value.detail


def test_shared_multi_symbol_limit_is_followed_through_pagination():
    """limit=10 across three symbols returned 10 bars of the first and none of the rest."""
    pages = [
        {"bars": {"AAPL": [_wire_bar("2026-09-01T13:30:00Z")]}, "next_page_token": "p2"},
        {"bars": {"SPY": [_wire_bar("2026-09-01T13:30:00Z", 500.0)],
                  "QQQ": [_wire_bar("2026-09-01T13:30:00Z", 400.0)]}, "next_page_token": None},
    ]
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = dict(request.url.params).get("page_token")
        seen.append(token)
        return httpx.Response(200, json=pages[0] if token is None else pages[1])

    provider = AlpacaBarProvider(
        base_url="https://data.example", api_key="k", secret_key="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    result = asyncio.run(
        provider.fetch_bars(["AAPL", "SPY", "QQQ"], MINUTE_30, utc(2026, 9, 1, 13, 0), CUTOFF)
    )
    assert seen == [None, "p2"]
    assert set(result) == {"AAPL", "SPY", "QQQ"}
    assert all(result[symbol] for symbol in result)


def test_endless_pagination_fails_closed_rather_than_truncating():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"bars": {"AAPL": [_wire_bar("2026-09-01T13:30:00Z")]}, "next_page_token": "more"},
        )

    provider = AlpacaBarProvider(
        base_url="https://data.example", api_key="k", secret_key="s", max_pages=3,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(BarProviderError) as excinfo:
        asyncio.run(provider.fetch_bars(["AAPL"], MINUTE_30, utc(2026, 9, 1, 13, 0), CUTOFF))
    assert excinfo.value.reason == "pagination_truncated"


# ─── 15, 16. index context is mandatory ───────────────────────────────────────


@pytest.mark.parametrize(("dropped", "expected"), (("SPY", "missing_context:spy"),
                                                   ("QQQ", "missing_context:qqq")))
def test_missing_index_context_fails_closed(dropped, expected):
    bars = standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0})
    # The index symbol resolves but has too little history to be trustworthy.
    bars[dropped] = bars[dropped][-3:]
    builder = standard_builder(provider=FakeProvider(bars))
    context = build_context(builder, "AAPL", NOW)
    assert context.available is False
    assert context.reason == expected


def test_index_symbols_are_requested_alongside_the_ticker():
    provider = FakeProvider(standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0}))
    build_context(standard_builder(provider=provider), "AAPL", NOW)
    assert provider.calls[0]["symbols"] == ["AAPL", "SPY", "QQQ"]


def test_scanning_an_index_does_not_request_it_twice():
    provider = FakeProvider(standard_bars({"SPY": 500.0, "QQQ": 400.0}))
    context = build_context(standard_builder(provider=provider), "SPY", NOW)
    assert provider.calls[0]["symbols"] == ["SPY", "QQQ"]
    assert context.available, context.reason
    assert context.spy is context.ticker


# ─── 17, 18. staleness and entitlement ────────────────────────────────────────


def test_stale_provider_data_fails_closed():
    """A complete but long-finished session must not be served as current structure."""
    bars = standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0})
    builder = standard_builder(provider=FakeProvider(bars))
    # 23:14Z cutoff: the session closed at 20:00Z, so the newest completed bar
    # is more than three hours old even though the series has no gaps.
    context = build_context(builder, "AAPL", utc(2026, 9, 1, 23, 30))
    assert context.available is False
    assert context.reason == "stale_market_data"
    assert context.ticker is not None
    assert context.ticker.last_bar_close == utc(2026, 9, 1, 20, 0).isoformat()


def test_a_session_that_stalls_mid_way_is_caught_as_missing_bars():
    """An intra-session stall is a hole, not staleness, and is reported as such."""
    bars = standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0})
    for symbol in bars:
        bars[symbol] = [b for b in bars[symbol] if b.start < utc(2026, 9, 1, 14, 30)]
    builder = standard_builder(provider=FakeProvider(bars))
    context = build_context(builder, "AAPL", NOW)
    assert context.available is False
    assert context.reason == "missing_bars"


def test_entitlement_error_fails_closed():
    builder = standard_builder(
        provider=FakeProvider(error=BarProviderError("provider_entitlement", "403"))
    )
    context = build_context(builder, "AAPL", NOW)
    assert context.available is False
    assert context.reason == "provider_entitlement"


def test_entitlement_http_403_is_classified_not_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "subscription does not permit querying recent SIP data"}
        )

    provider = AlpacaBarProvider(
        base_url="https://data.example", api_key="k", secret_key="s",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(BarProviderError) as excinfo:
        asyncio.run(provider.fetch_bars(["AAPL"], MINUTE_30, utc(2026, 9, 1, 13, 0), CUTOFF))
    assert excinfo.value.reason == "provider_entitlement"


def test_a_single_venue_feed_is_refused_for_setup_context():
    """IEX changes discrete Strat classification; it must never stand in."""
    provider = FakeProvider(standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0}))
    provider.feed = "iex"
    context = build_context(standard_builder(provider=provider), "AAPL", NOW)
    assert context.available is False
    assert context.reason == "feed_not_consolidated"
    assert provider.calls == []


def test_missing_bars_inside_the_session_fail_closed():
    bars = standard_bars({"AAPL": 100.0, "SPY": 500.0, "QQQ": 400.0})
    bars["AAPL"] = [b for b in bars["AAPL"] if b.start != utc(2026, 9, 1, 14, 30)]
    builder = standard_builder(provider=FakeProvider(bars))
    context = build_context(builder, "AAPL", NOW)
    assert context.available is False
    assert context.reason == "missing_bars"


# ─── context content ──────────────────────────────────────────────────────────


def test_available_context_carries_the_structure_the_scorer_needs():
    context = build_context(standard_builder(), "AAPL", NOW)
    assert context.available, context.reason
    fields = context.to_scanner_fields()
    for key in ("vwap", "ema20", "pattern", "prev_candle_high", "prev_candle_low",
                "candle_type", "session_date", "session_open", "session_close",
                "latest_completed_bar_close", "bar_context_feed", "timeframe"):
        assert fields.get(key) is not None, key
    assert fields["bar_context_feed"] == "sip"
    assert fields["session_date"] == "2026-09-01"
    assert fields["spy_context_available"] is True
    assert fields["qqq_context_available"] is True

    current = rising_series(full_session(date(2026, 9, 1)), 13, base=130.0)[:7]
    expected_levels = prior_high_low(current)
    assert expected_levels is not None
    assert fields["prev_candle_high"] == expected_levels[0]
    assert fields["prev_candle_low"] == expected_levels[1]
    assert fields["candle_type"] == "two_up"


def test_unavailable_context_still_emits_telemetry():
    builder = standard_builder(
        provider=FakeProvider(error=BarProviderError("provider_entitlement", "403"))
    )
    fields = build_context(builder, "AAPL", NOW).to_scanner_fields()
    assert fields["bar_context_available"] is False
    assert fields["bar_context_reason"] == "provider_entitlement"
    assert fields["bar_context_information_cutoff"] == CUTOFF.isoformat()
    assert fields["bar_context_delay_buffer_seconds"] == 960


# ─── 19, 20. scanner integration ──────────────────────────────────────────────


def scanner_config(tmp_path: Path) -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=True,
        alpaca_secret_key_configured=True,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
    )


class StubMarketData:
    """Snapshot provider that supplies price and volume but no structure."""

    def __init__(self, price: float | None = 137.0) -> None:
        self.price = price

    async def fetch_market_snapshot(self, ticker: str):
        from alert_ranker.tastytrade_client import MarketSnapshot

        return MarketSnapshot(ticker.upper(), price=self.price, volume=1_000_000)


def make_scanner(tmp_path: Path, builder: BarContextBuilder | None):
    cfg = scanner_config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    discord = DiscordAlerter(
        cfg, storage,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(204))),
    )
    scanner = OptionsScanner(cfg, StubMarketData(), storage, discord, bar_context=builder)
    return cfg, storage, scanner


def test_scheduled_scan_with_causal_context_is_no_longer_blind(tmp_path):
    """The old failure: snapshot price only, so VWAP/EMA20 absent and direction UNKNOWN."""
    _, _, blind = make_scanner(tmp_path, None)
    blind_outcome = asyncio.run(blind.scan_ticker("AAPL", source="scheduled", now=NOW))
    assert blind_outcome.result.direction == "UNKNOWN"
    assert blind_outcome.result.reason.startswith("missing_inputs")

    _, _, wired = make_scanner(tmp_path / "wired", standard_builder())
    outcome = asyncio.run(wired.scan_ticker("AAPL", source="scheduled", now=NOW))
    assert outcome.result.raw["vwap"] is not None
    assert outcome.result.raw["ema20"] is not None
    assert outcome.result.direction in {"LONG", "SHORT"}
    assert not outcome.result.reason.startswith("missing_inputs")


def test_structural_failure_is_named_not_logged_as_score_below_threshold(tmp_path):
    builder = standard_builder(
        provider=FakeProvider(error=BarProviderError("provider_entitlement", "403"))
    )
    _, storage, scanner = make_scanner(tmp_path, builder)
    outcome = asyncio.run(scanner.scan_ticker("AAPL", source="scheduled", now=NOW))
    assert outcome.alert_sent is False
    assert outcome.alert_suppression_reason == "provider_entitlement"
    assert outcome.alert_suppression_reason != "score_below_threshold"
    assert outcome.result.raw["bar_context_reason"] == "provider_entitlement"


@pytest.mark.parametrize(
    ("provider_kwargs", "expected"),
    (
        ({"error": BarProviderError("missing_symbol", "AAPL")}, "missing_symbol:aapl"),
        ({"error": BarProviderError("pagination_truncated", "")}, "pagination_truncated"),
        ({"error": BarProviderError("provider_unavailable", "")}, "provider_unavailable"),
    ),
)
def test_each_structural_failure_reaches_the_journal_by_name(tmp_path, provider_kwargs, expected):
    builder = standard_builder(provider=FakeProvider(**provider_kwargs))
    _, _, scanner = make_scanner(tmp_path, builder)
    outcome = asyncio.run(scanner.scan_ticker("AAPL", source="scheduled", now=NOW))
    assert outcome.alert_suppression_reason == expected


def test_caller_supplied_context_still_wins_over_bar_context(tmp_path):
    """The webhook path supplies its own structure and must not be overridden."""
    _, _, scanner = make_scanner(tmp_path, standard_builder())
    outcome = asyncio.run(
        scanner.scan_ticker(
            "AAPL", source="webhook",
            context={"price": 150.0, "vwap": 149.0, "ema20": 148.0, "pattern": "2-1-2"},
            now=NOW,
        )
    )
    assert outcome.result.raw["vwap"] == 149.0
    assert outcome.result.raw["ema20"] == 148.0
    assert outcome.result.raw["pattern"] == "2-1-2"


def test_scanner_without_a_builder_behaves_exactly_as_before(tmp_path):
    _, _, scanner = make_scanner(tmp_path, None)
    outcome = asyncio.run(scanner.scan_ticker("AAPL", source="scheduled", now=NOW))
    assert "bar_context_reason" not in outcome.result.raw
    assert outcome.alert_suppression_reason.startswith("missing_inputs")


# ─── 21. no execution surface ─────────────────────────────────────────────────


_PR_C_MODULES = (
    causal_bars_module,
    session_calendar_module,
    bar_provider_module,
    bar_context_module,
)

_FORBIDDEN_IDENTIFIERS = (
    "place_order", "submit_order", "cancel_order", "replace_order", "execute_order",
    "live_order", "review_option_order", "robin_stocks", "ib_insync", "ibapi",
    "placeOrder", "mcp__robinhood",
)

_FORBIDDEN_IMPORT_ROOTS = ("execution", "risk_engine", "options_manager.adapters")


def test_pr_c_modules_introduce_no_execution_or_broker_order_surface():
    for module in _PR_C_MODULES:
        source = inspect.getsource(module)
        lowered = source.lower()
        for identifier in _FORBIDDEN_IDENTIFIERS:
            assert identifier.lower() not in lowered, f"{module.__name__}: {identifier}"
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(
                    name == root or name.startswith(root + ".") for root in _FORBIDDEN_IMPORT_ROOTS
                ), f"{module.__name__} imports {name}"


def test_bar_provider_only_reads_the_historical_bars_path():
    source = inspect.getsource(bar_provider_module)
    assert "/v2/stocks/bars" in source
    for forbidden in ("/orders", "/positions", "/account"):
        assert forbidden not in source


def test_context_builder_never_declares_a_fallback_feed():
    source = inspect.getsource(bar_context_module)
    assert "iex" not in source.lower()
