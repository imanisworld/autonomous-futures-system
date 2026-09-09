from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from alert_ranker.config import ScannerConfig
from alert_ranker.contract_marks import contract_marks
from alert_ranker.discord import DiscordAlerter
from alert_ranker.market_data import OptionChain, OptionContractQuote
from alert_ranker.paper_v1 import (
    POLICY_ID,
    build_v1_contract_fields,
    choose_contract,
    choose_expiration,
)
from alert_ranker.scanner import OptionsScanner
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import MarketSnapshot

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 8, 10, 0, tzinfo=NY)
GOOD_EXPIRY = "2026-10-30"


def cfg(tmp_path: Path, webhook: str = "") -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=True,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url=webhook,
        watchlist=["SPY"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        public_account_id="ACC12345",
    )


def quote(*, bid: float = 4.80, ask: float = 5.00) -> OptionContractQuote:
    return OptionContractQuote(
        symbol="SPY261030C00505000",
        option_type="CALL",
        strike=505.0,
        bid=bid,
        ask=ask,
        mid=round((bid + ask) / 2.0, 4),
        last=4.90,
        volume=1200,
        open_interest=5000,
        delta=0.40,
        implied_volatility=0.30,
    )


class V1Market:
    provider_name = "public"
    last_error = None

    def __init__(self):
        self.contract = quote()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def fetch_market_snapshot(self, ticker: str) -> MarketSnapshot:
        return MarketSnapshot(ticker.upper(), price=500.0, volume=10_000_000)

    async def fetch_option_expirations(self, ticker: str) -> list[str]:
        # 2099 is deliberately present.  It must never be selected or alerted.
        return ["2099-01-16", "2026-09-25", GOOD_EXPIRY]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None) -> OptionChain:
        assert expiration == GOOD_EXPIRY
        return OptionChain(ticker.upper(), expiration, calls=(self.contract,), puts=())


def triggered_context(**overrides):
    data = {
        "ticker": "SPY",
        "pattern": "2-1-2",
        "price": 500.0,
        "vwap": 499.0,
        "ema20": 498.0,
        "volume_ratio": 1.4,
        "setup_status": "TRIGGERED",
        "stop": 497.0,
        "target": 510.0,
        # This simulates the bad fixture-like contract context that previously
        # could leak through to Discord.  V1 must replace it from the real chain.
        "expiry": "2099-01-16",
        "strike": 9999,
        "option_type": "PUT",
        "option_mark": 0.01,
        "option_bid": 0.01,
        "option_ask": 0.02,
        "open_interest": 1,
        "risk_cap": 1,
    }
    data.update(overrides)
    return data


def test_expiration_prefers_45_plus_and_ignores_2099():
    decision = choose_expiration(["2099-01-16", "2026-09-25", GOOD_EXPIRY], NOW)
    assert decision.valid
    assert decision.expiry.expiration == GOOD_EXPIRY
    assert decision.expiry.dte >= 45
    assert decision.expiry.bucket == "45_PLUS"


def test_14_44_dte_is_allowed_but_tagged_exception():
    decision = choose_expiration(["2026-10-02"], NOW)
    assert decision.valid
    assert decision.expiry.bucket == "14_44"
    assert decision.expiry.warning == "DTE_EXCEPTION"


def test_less_than_14_dte_has_no_normal_population_contract():
    decision = choose_expiration(["2026-09-09", "2026-09-18"], NOW)
    assert not decision.valid
    assert decision.status == "DATA_INVALID"


def test_contract_quality_requires_liquidity_and_real_quotes():
    decision = choose_contract((quote(),), option_type="CALL", underlying_price=500.0)
    assert decision.valid
    assert decision.contract.symbol == "SPY261030C00505000"

    bad = quote(bid=1.0, ask=2.0)
    rejected = choose_contract((bad,), option_type="CALL", underlying_price=500.0)
    assert not rejected.valid
    assert "no_liquid_contract" in rejected.reason


def test_v1_risk_uses_premium_stop_not_full_debit():
    expiry = choose_expiration([GOOD_EXPIRY], NOW).expiry
    contract = choose_contract((quote(),), option_type="CALL", underlying_price=500.0).contract
    fields, reason = build_v1_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=497.0,
        target_1=510.0,
        aggregate_open_risk=0.0,
    )
    assert reason == ""
    assert fields["option_mark"] == 5.0  # entry at ask
    assert fields["premium_stop"] == 3.75
    assert fields["planned_risk_dollars"] == 125.0
    assert fields["planned_risk_dollars"] != 500.0
    assert fields["max_trade_planned_risk"] == 300.0
    assert fields["max_aggregate_open_planned_risk"] == 1000.0


def test_aggregate_risk_cap_blocks_candidate():
    expiry = choose_expiration([GOOD_EXPIRY], NOW).expiry
    contract = choose_contract((quote(),), option_type="CALL", underlying_price=500.0).contract
    fields, reason = build_v1_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=497.0,
        target_1=510.0,
        aggregate_open_risk=900.0,
    )
    assert fields is None
    assert reason == "aggregate_risk_cap_exceeded:1025.00"


def test_triggered_scanner_replaces_fake_2099_with_current_chain_and_journals_marks(tmp_path):
    config = cfg(tmp_path)
    storage = ScanStorage(config.sqlite_path)
    market = V1Market()
    scanner = OptionsScanner(config, market, storage, DiscordAlerter(config, storage))

    outcome = asyncio.run(
        scanner.scan_ticker("SPY", source="webhook", context=triggered_context(), now=NOW)
    )

    assert outcome.shadow_id > 0
    row = storage.get_shadow_setup(outcome.shadow_id)
    selected = row.selected_contract
    assert selected["paper_policy_id"] == POLICY_ID
    assert selected["paper_policy_status"] == "VALID"
    assert selected["expiry"] == GOOD_EXPIRY
    assert selected["contract"] == "SPY261030C00505000"
    assert selected["strike"] == 505.0
    assert selected["option_type"] == "CALL"
    assert selected["entry_quote"] == 5.0
    assert selected["premium_stop"] == 3.75
    assert selected["planned_risk_dollars"] == 125.0

    marks = contract_marks(storage, outcome.shadow_id)
    assert len(marks) == 1
    assert marks[0]["option_symbol"] == "SPY261030C00505000"
    assert marks[0]["ask"] == 5.0
    assert marks[0]["raw"]["event"] == "ENTRY"


def test_v1_resolver_reprices_exact_contract_and_exits_at_premium_stop(tmp_path):
    config = cfg(tmp_path)
    storage = ScanStorage(config.sqlite_path)
    market = V1Market()
    scanner = OptionsScanner(config, market, storage, DiscordAlerter(config, storage))
    outcome = asyncio.run(
        scanner.scan_ticker("SPY", source="webhook", context=triggered_context(), now=NOW)
    )

    market.contract = quote(bid=3.70, ask=3.80)
    counts = asyncio.run(scanner.resolve_open_candidates(now=NOW.replace(hour=11)))
    assert counts == {"checked": 1, "resolved": 1}
    row = storage.get_shadow_setup(outcome.shadow_id)
    assert row.status == "LOSS"
    assert row.outcome["closed_reason"] == "premium_stop_hit"
    assert row.outcome["exit_mark"] == 3.7
    assert row.outcome["pnl_dollars"] == -130.0

    marks = contract_marks(storage, outcome.shadow_id)
    assert len(marks) == 2
    assert marks[-1]["bid"] == 3.7
    assert marks[-1]["raw"]["reassessment_band_reached"] is True


def test_discord_never_sends_absurd_2099_expiration(tmp_path):
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    config = cfg(tmp_path, webhook="https://discord.test/webhook")
    storage = ScanStorage(config.sqlite_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    alerter = DiscordAlerter(config, storage, client=client)

    from alert_ranker.scorer import score_setup

    result = score_setup(
        {
            "ticker": "SPY",
            "pattern": "2-1-2",
            "price": 500.0,
            "vwap": 499.0,
            "ema20": 498.0,
            "volume_ratio": 1.4,
            "iv_rank": 25,
            "expiry": "2099-01-16",
            "strike": 505,
        },
        now=NOW,
    )

    decision = asyncio.run(alerter.send_if_eligible(result, now=NOW))
    assert decision.sent is False
    assert decision.reason == "DATA_INVALID:expiration_out_of_range"
    assert sent == []
