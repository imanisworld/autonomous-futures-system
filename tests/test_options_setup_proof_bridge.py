"""Regression tests for the OPTIONS_PAPER_V1 causal setup-proof bridge.

The bridge is deliberately split in two:
- causal structure/targets/SPY-QQQ/HTF proof may open an evidence row;
- user-facing Discord remains blocked until separate event-risk/flip proof
  exists.

Nothing in this file touches a network or broker.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from alert_ranker.bar_context import BarContextBuilder
from alert_ranker.causal_bars import MINUTE_30, Bar
from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.scanner import OptionsScanner
from alert_ranker.session_calendar import StaticSessionCalendar, parse_session
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

UTC = timezone.utc
NOW = datetime(2026, 9, 1, 17, 16, tzinfo=UTC)
SESSIONS = [date(2026, 8, 27), date(2026, 8, 28), date(2026, 8, 31), date(2026, 9, 1)]


def _session(day: date):
    return parse_session({"date": day.isoformat(), "open": "09:30", "close": "16:00"})


def _bar(start: datetime, low: float, high: float) -> Bar:
    return Bar(
        start=start,
        open=low + 0.1,
        high=high,
        low=low,
        close=high - 0.1,
        volume=1000.0,
        vwap=(high + low) / 2.0,
    )


def _rising(session, count: int, base: float) -> list[Bar]:
    return [
        _bar(session.open + MINUTE_30.delta * index, base + index, base + index + 1.0)
        for index in range(count)
    ]


# Last three bars are: 2U parent (128-129), inside (128.2-128.8), 2U break.
# The parent high 129 is a causal T1. The prior completed session high 133 is
# the next strong historical level and therefore T2. The current breakout high
# 129.5 is deliberately NOT eligible as a target because it was not known at
# the trigger.
_PROMOTABLE = [
    (124.0, 125.0),
    (125.0, 126.0),
    (126.0, 127.0),
    (127.0, 128.0),
    (128.0, 129.0),
    (128.2, 128.8),
    (128.5, 129.5),
]


class FakeBarProvider:
    feed = "sip"

    def __init__(self, *, qqq_bearish: bool = False):
        self.bars = self._bars(qqq_bearish=qqq_bearish)

    @staticmethod
    def _bars(*, qqq_bearish: bool) -> dict[str, list[Bar]]:
        out: dict[str, list[Bar]] = {}
        for symbol, base in (("AAPL", 100.0), ("SPY", 500.0), ("QQQ", 400.0)):
            rows: list[Bar] = []
            for index, day in enumerate(SESSIONS[:-1]):
                rows.extend(_rising(_session(day), 13, base + index * 10.0))
            current = _session(SESSIONS[-1])
            if symbol == "AAPL":
                rows.extend(
                    _bar(current.open + MINUTE_30.delta * index, low, high)
                    for index, (low, high) in enumerate(_PROMOTABLE)
                )
            elif symbol == "QQQ" and qqq_bearish:
                # Complete current session bars are beneath both VWAP and the
                # rising-history EMA, so QQQ explicitly opposes the CALL setup.
                rows.extend(
                    _bar(
                        current.open + MINUTE_30.delta * index,
                        300.0 - index,
                        301.0 - index,
                    )
                    for index in range(7)
                )
            else:
                rows.extend(_rising(current, 7, base + 30.0))
            out[symbol] = rows
        return out

    async def fetch_bars(self, symbols, timeframe, start, end):
        return {symbol: list(self.bars[symbol]) for symbol in symbols}


def _builder(*, qqq_bearish: bool = False) -> BarContextBuilder:
    return BarContextBuilder(
        provider=FakeBarProvider(qqq_bearish=qqq_bearish),
        calendar=StaticSessionCalendar.from_sessions(_session(day) for day in SESSIONS),
        timeframe=MINUTE_30,
    )


def _config(tmp_path: Path) -> ScannerConfig:
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
        discord_webhook_url="https://discord.invalid/webhook",
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        bar_context_enabled=True,
    )


class PaperMarketData:
    provider_name = "public"
    last_error = None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=129.4, volume=1_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        return ["2026-10-23", "2026-11-20"]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        assert expiration == "2026-10-23"
        contract = OptionContractQuote(
            symbol="AAPL261023C00130000",
            option_type="CALL",
            strike=130.0,
            bid=1.90,
            ask=2.00,
            mid=1.95,
            last=1.95,
            volume=500.0,
            open_interest=1500.0,
            delta=0.45,
            implied_volatility=0.30,
        )
        return OptionChain("AAPL", expiration, calls=(contract,), puts=(), error=None)


def test_causal_bridge_promotes_only_evidence_and_preserves_target_provenance():
    context = asyncio.run(_builder().build("AAPL", NOW))
    assert context.available, context.reason
    ticker = context.ticker
    assert ticker is not None
    assert ticker.setup_sequence_confirmed is True
    assert ticker.setup_direction == "CALL"
    assert ticker.setup_entry_trigger == 128.8
    assert ticker.setup_invalidation == 128.2
    assert ticker.setup_target_1 == 129.0
    assert ticker.setup_target_2 == 133.0
    assert 129.5 not in ticker.setup_resistance_levels
    assert ticker.setup_market_status == "VALID"
    assert ticker.setup_proof_status == "VALID"
    assert ticker.setup_status == "TRIGGERED"
    assert ticker.trade_proof_status == "INCOMPLETE"
    assert "event_risk_unavailable" in (ticker.trade_proof_reason or "")
    assert "flip_context_unavailable" in (ticker.trade_proof_reason or "")

    fields = context.to_scanner_fields()
    assert fields["pattern"] == "strat_212"
    assert fields["target_1"] == 129.0
    assert fields["target_2"] == 133.0
    assert fields["trade_proof_status"] == "INCOMPLETE"


def test_market_conflict_keeps_mechanical_setup_non_triggered():
    context = asyncio.run(_builder(qqq_bearish=True).build("AAPL", NOW))
    assert context.available, context.reason
    ticker = context.ticker
    assert ticker is not None
    assert ticker.setup_sequence_confirmed is True
    assert ticker.setup_status == "INVALID"
    assert ticker.setup_proof_status == "INCOMPLETE"
    assert ticker.setup_market_status == "NOT_ALIGNED"
    assert ticker.setup_suppression_reason == "setup_proof_incomplete:market_not_aligned"
    assert "pattern" not in context.to_scanner_fields()


def test_paper_v1_opens_evidence_row_but_discord_stays_fail_closed(tmp_path):
    cfg = _config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    posts: list[httpx.Request] = []

    def discord_handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(204)

    discord = DiscordAlerter(
        cfg,
        storage,
        client=httpx.AsyncClient(transport=httpx.MockTransport(discord_handler)),
    )
    scanner = OptionsScanner(
        cfg,
        PaperMarketData(),
        storage,
        discord,
        bar_context=_builder(),
    )

    outcome = asyncio.run(
        scanner.scan_ticker(
            "AAPL",
            source="scheduled",
            context={"volume_ratio": 1.5, "iv_rank": 20.0},
            now=NOW,
        )
    )

    assert outcome.result.raw["setup_proof_status"] == "VALID"
    assert outcome.result.raw["paper_policy_status"] == "VALID"
    assert outcome.result.raw["contract"] == "AAPL261023C00130000"
    assert outcome.result.raw["dte"] >= 45
    assert outcome.result.raw["target_1"] == 129.0
    assert outcome.result.raw["target_2"] == 133.0
    assert outcome.alert_sent is False
    assert outcome.alert_suppression_reason.startswith("trade_proof_incomplete:")
    # Critical split: missing trade proof suppresses the user-facing alert but
    # does not discard the fully specified paper evidence candidate.
    assert outcome.shadow_id > 0
    stored = storage.get_shadow_setup(outcome.shadow_id)
    assert stored is not None
    assert stored.status == "OPEN"
    assert stored.selected_contract["paper_policy_id"] == "OPTIONS_PAPER_V1"
    assert stored.selected_contract["target"] == 129.0
    assert posts == []
