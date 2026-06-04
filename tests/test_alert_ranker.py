from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from alert_ranker.app import create_app
from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.discord import DiscordAlerter, build_discord_payload
from alert_ranker.market_data import (
    AlpacaMarketDataClient,
    PublicMarketDataClient,
    build_provider_capabilities,
    create_market_data_client,
)
from alert_ranker.scanner import OptionsScanner
from alert_ranker.scorer import score_setup
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import TastytradeClient, parse_iv_rank
from sources.signa_client import SignaSignal


def scanner_config(tmp_path: Path, webhook_url: str = "") -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="tastytrade",
        tastytrade_username="user",
        tastytrade_password="pass",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url=webhook_url,
        watchlist=["AAPL"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
    )


def setup_payload(**overrides):
    data = {
        "ticker": "AAPL",
        "pattern": "2-2 reversal",
        "price": 101.0,
        "vwap": 100.0,
        "ema20": 99.0,
        "volume_ratio": 1.3,
        "iv_rank": 25.0,
    }
    data.update(overrides)
    return data


def test_tastytrade_auth_mock_returns_session_token(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/sessions"
        body = request.read().decode()
        assert "pass" in body
        return httpx.Response(201, json={"data": {"session-token": "token-123"}})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.tastyworks.com",
    )

    async def run():
        tasty = TastytradeClient(scanner_config(tmp_path), client=client)
        assert await tasty.authenticate() == "token-123"

    asyncio.run(run())


def test_iv_rank_parsing_supports_normal_and_missing_fields():
    assert parse_iv_rank({"implied-volatility-index-rank": "24.5"}) == 24.5
    assert parse_iv_rank({"iv-rank": 31}) == 31.0
    assert parse_iv_rank({}) is None


def test_iv_rank_buckets_and_expensive_subtracts_three():
    morning = datetime(2026, 5, 26, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    low = score_setup(setup_payload(iv_rank=20), now=morning)
    middle = score_setup(setup_payload(iv_rank=40), now=morning)
    high = score_setup(setup_payload(iv_rank=60), now=morning)

    assert low.components["iv_rank"] == 2
    assert middle.components["iv_rank"] == 0
    assert high.components["iv_rank"] == -3
    assert sum(high.components.values()) == sum(low.components.values()) - 5


def test_against_trend_or_vwap_veto_returns_zero():
    against_vwap = score_setup(setup_payload(vwap=102.0, ema20=99.0, direction="LONG"))
    against_trend = score_setup(setup_payload(vwap=100.0, ema20=102.0, direction="LONG"))

    assert against_vwap.score == 0
    assert against_vwap.reason == "against_vwap"
    assert against_trend.score == 0
    assert against_trend.reason == "against_trend"


def test_discord_sends_only_when_score_is_at_least_seven(tmp_path):
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    cfg = scanner_config(tmp_path, webhook_url="https://discord.test/webhook")
    storage = ScanStorage(cfg.sqlite_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    alerter = DiscordAlerter(cfg, storage, client=client)
    high = score_setup(setup_payload())
    low = score_setup(setup_payload(pattern="N/A", volume_ratio=1.0, iv_rank=60))

    async def run():
        assert (await alerter.send_if_eligible(low)).sent is False
        assert (await alerter.send_if_eligible(high)).sent is True

    asyncio.run(run())
    assert len(sent) == 1


def test_duplicate_discord_alerts_suppressed_for_30_minutes(tmp_path):
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(204)

    cfg = scanner_config(tmp_path, webhook_url="https://discord.test/webhook")
    storage = ScanStorage(cfg.sqlite_path)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    alerter = DiscordAlerter(cfg, storage, client=client)
    now = datetime(2026, 5, 26, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    result = score_setup(setup_payload(), now=now)

    async def run():
        first = await alerter.send_if_eligible(result, now=now)
        storage.record_scan(
            result,
            source="test",
            alert_sent=first.sent,
            alert_suppression_reason="",
            timestamp=now,
        )
        second = await alerter.send_if_eligible(result, now=now + timedelta(minutes=10))
        assert first.sent is True
        assert second.sent is False
        assert second.reason == "duplicate_30m"

    asyncio.run(run())
    assert len(sent) == 1


def test_scanner_skips_after_4pm_and_weekends(tmp_path):
    cfg = scanner_config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    tasty = TastytradeClient(cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    discord = DiscordAlerter(cfg, storage, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(204))))
    scanner = OptionsScanner(cfg, tasty, storage, discord)

    after_close = datetime(2026, 5, 26, 16, 1, tzinfo=ZoneInfo("America/New_York"))
    weekend = datetime(2026, 5, 30, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    assert asyncio.run(scanner.scan_watchlist(now=after_close)) == []
    assert scanner.last_skip_reason == "outside_market_hours"
    assert asyncio.run(scanner.scan_watchlist(now=weekend)) == []


def test_sqlite_records_required_scan_fields(tmp_path):
    cfg = scanner_config(tmp_path)
    storage = ScanStorage(cfg.sqlite_path)
    result = score_setup(setup_payload())
    row_id = storage.record_scan(
        result,
        source="test",
        alert_sent=False,
        alert_suppression_reason="score_below_threshold",
    )

    latest = storage.latest(limit=1)[0]
    assert row_id == latest.id
    assert latest.timestamp
    assert latest.ticker == "AAPL"
    assert latest.score == result.score
    assert latest.direction == "LONG"
    assert latest.alert_sent is False
    assert latest.alert_suppression_reason == "score_below_threshold"


def test_health_status_watchlist_and_webhook_endpoints_work(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "healthy"
        assert client.get("/watchlist").json() == {"watchlist": ["AAPL"]}
        webhook = client.post("/webhook/alert", json=setup_payload())
        assert webhook.status_code == 200
        body = webhook.json()
        assert body["accepted"] is True
        assert body["results"][0]["ticker"] == "AAPL"
        assert body["results"][0]["shadow_id"]
        status = client.get("/status").json()
        assert status["advisory_only"] is True
        assert status["provider_profile"]["read_only"] is True
        assert status["provider_profile"]["order_supported"] is False
        assert status["latest"][0]["ticker"] == "AAPL"
        terminal = client.get("/terminal").json()
        assert terminal["shadow_journal"][0]["ticker"] == "AAPL"
        assert terminal["shadow_journal"][0]["scan_id"] == body["results"][0]["storage_id"]


def test_shadow_journal_endpoint_lists_and_updates_outcomes(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        webhook = client.post("/webhook/alert", json=setup_payload(ticker="SPY", option_mark=2.1))
        shadow_id = webhook.json()["results"][0]["shadow_id"]
        listed = client.get("/shadow-journal").json()
        assert listed["advisory_only"] is True
        assert listed["items"][0]["id"] == shadow_id
        assert listed["items"][0]["status"] == "OPEN"
        assert client.get("/shadow-journal?ticker=QQQ").json()["items"] == []
        assert client.get("/shadow-journal?ticker=SPY&status=OPEN").json()["items"][0]["id"] == shadow_id

        updated = client.patch(
            f"/shadow-journal/{shadow_id}/outcome",
            json={
                "status": "WIN",
                "outcome": {
                    "exit_mark": 3.2,
                    "closed_reason": "target_hit",
                },
            },
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["advisory_only"] is True
        assert body["item"]["status"] == "WIN"
        assert body["item"]["outcome"]["closed_reason"] == "target_hit"
        assert body["item"]["outcome"]["entry_mark"] == 2.1
        assert body["item"]["outcome"]["exit_mark"] == 3.2
        assert body["item"]["outcome"]["pnl_percent"] == 52.38
        assert body["item"]["outcome"]["pnl_dollars"] == 110.0
        assert client.get("/shadow-journal?status=WIN").json()["items"][0]["id"] == shadow_id


def test_shadow_journal_summary_reports_paper_stats(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        spy = client.post("/webhook/alert", json=setup_payload(ticker="SPY", option_mark=2.0))
        qqq = client.post("/webhook/alert", json=setup_payload(ticker="QQQ", option_mark=4.0))
        spy_shadow = spy.json()["results"][0]["shadow_id"]
        qqq_shadow = qqq.json()["results"][0]["shadow_id"]
        client.patch(
            f"/shadow-journal/{spy_shadow}/outcome",
            json={"status": "WIN", "outcome": {"exit_mark": 3.0}},
        )
        client.patch(
            f"/shadow-journal/{qqq_shadow}/outcome",
            json={"status": "LOSS", "outcome": {"exit_mark": 2.0}},
        )

        summary = client.get("/shadow-journal/summary").json()["summary"]
        assert summary["total"] == 2
        assert summary["closed"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["win_rate_percent"] == 50.0
        assert summary["total_pnl_dollars"] == -100.0
        assert summary["average_pnl_percent"] == 0.0

        spy_summary = client.get("/shadow-journal/summary?ticker=SPY").json()
        assert spy_summary["ticker"] == "SPY"
        assert spy_summary["summary"]["total"] == 1
        assert spy_summary["summary"]["wins"] == 1
        assert spy_summary["summary"]["total_pnl_dollars"] == 100.0

        terminal = client.get("/terminal").json()
        assert terminal["shadow_summary"]["total"] == 2


def test_shadow_outcome_update_rejects_invalid_status_and_missing_id(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        bad_status = client.patch(
            "/shadow-journal/1/outcome",
            json={"status": "FILLED", "outcome": {}},
        )
        assert bad_status.status_code == 400
        assert bad_status.json()["detail"] == "unsupported_shadow_status"

        missing = client.patch(
            "/shadow-journal/999/outcome",
            json={"status": "LOSS", "outcome": {"closed_reason": "stop_hit"}},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"] == "shadow_setup_not_found"

        bad_filter = client.get("/shadow-journal?status=FILLED")
        assert bad_filter.status_code == 400
        assert bad_filter.json()["detail"] == "unsupported_shadow_status"


def test_alert_ranker_does_not_import_futures_execution_or_risk_modules():
    import alert_ranker.app as app_module
    import alert_ranker.scanner as scanner_module

    source = inspect.getsource(app_module) + inspect.getsource(scanner_module)
    forbidden = ("execution.", "paper_broker", "broker_interface", "risk.", "risk_engine")
    assert not any(name in source for name in forbidden)


def test_war_room_discord_payload_uses_rich_confirmed_template():
    result = score_setup(
        setup_payload(
            ticker="NVDA",
            contract="NVDA $950 Call - Jun 20",
            price=950.0,
            strike=950,
            stop=940,
            target_1=965,
            target_2=975,
            why="Demand zone reclaim. Volume expanding. GEX flip at 950 cleared.",
            edge="Multi-timeframe alignment confirmed. All gates passed.",
            risk="Size for your account. Exit at stop - no exceptions.",
            strat_combo=["2U", "1", "2U"],
            timeframe="15m",
            ftfc=True,
            ftfc_direction="UP",
            option_type="call",
            underlying_price=950,
            dte=19,
            implied_volatility=25,
            option_mark=8.0,
        )
    )

    embed = build_discord_payload(result)["embeds"][0]
    field_map = {field["name"]: field["value"] for field in embed["fields"]}

    assert embed["title"] == "▲ NVDA CALL - A+ CONFIRMED ⭐ GOLDEN SETUP"
    assert "bullish structure" in embed["description"]
    assert field_map["Strat Combo"] == "2U-1-2U"
    assert field_map["Timeframe"] == "15m"
    assert field_map["FTFC"] == "Yes (UP)"
    assert field_map["Watch Contract"] == "NVDA $950 Call - Jun 20"
    assert field_map["Stop Level"] == "$940.00"
    assert field_map["Target 1"] == "$965.00"
    assert field_map["Target 2"] == "$975.00"
    assert field_map["Why"] == "Demand zone reclaim. Volume expanding. GEX flip at 950 cleared."
    assert field_map["Edge"] == "Multi-timeframe alignment confirmed. All gates passed."
    assert "Premium Value" in field_map
    assert "fair" in field_map["Premium Value"].lower() or "discount" in field_map["Premium Value"].lower() or "overpriced" in field_map["Premium Value"].lower()
    assert field_map["Risk"] == "Size for your account. Exit at stop - no exceptions."


def test_war_room_discord_payload_marks_forming_setup():
    result = score_setup(setup_payload(ticker="QQQ", status="forming", strike=490, expiry="Jun 20"))

    embed = build_discord_payload(result)["embeds"][0]
    field_map = {field["name"]: field["value"] for field in embed["fields"]}

    assert embed["title"] == "▲ QQQ CALL - SETUP FORMING ⭐"
    assert "do not enter early" in embed["description"]
    assert field_map["Risk"] == "Setup is developing - NOT confirmed. Wait for the A+ signal before entering."


def test_webhook_context_passes_rich_alert_fields_to_storage_status(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        body = setup_payload(
            ticker="NVDA",
            contract="NVDA $950 Call - Jun 20",
            stop=940,
            target_1=965,
            why="Demand reclaim",
            edge="All gates passed",
            strat_combo="3-1-2U",
            tf="30m",
            ftfc="UP",
        )
        webhook = client.post("/webhook/alert", json=body)
        assert webhook.status_code == 200
        status = client.get("/status").json()

    latest = status["latest"][0]
    assert latest["ticker"] == "NVDA"
    assert latest["score"] >= 7


def test_strat_context_fields_support_aliases():
    result = score_setup(
        setup_payload(
            combo="1-2-2 REV",
            tf_stack=["15m", "30m", "1h"],
            full_timeframe_continuity=True,
            ftfc_direction="UP",
        )
    )

    embed = build_discord_payload(result)["embeds"][0]
    field_map = {field["name"]: field["value"] for field in embed["fields"]}

    assert field_map["Strat Combo"] == "1-2-2 REV"
    assert field_map["Timeframe"] == "15m / 30m / 1h"
    assert field_map["FTFC"] == "Yes (UP)"




def test_black_scholes_option_value_sanity():
    from alert_ranker.options_valuation import black_scholes_price, evaluate_option_value

    call_value = black_scholes_price(
        option_type="call",
        underlying_price=100,
        strike=100,
        dte=30,
        implied_volatility=0.20,
        risk_free_rate=0.04,
    )
    assert 2.0 < call_value < 3.5

    valuation = evaluate_option_value({
        "option_type": "call",
        "underlying_price": 100,
        "strike": 100,
        "dte": 30,
        "implied_volatility": 20,
        "option_mark": 2.0,
        "risk_free_rate": 0.04,
    })
    assert valuation is not None
    assert valuation.verdict in {"discount", "fair"}
    assert valuation.theoretical_value == call_value


def test_option_premium_value_adjusts_advisory_score():
    morning = datetime(2026, 5, 26, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    base = setup_payload(
        option_type="call",
        underlying_price=100,
        strike=100,
        dte=30,
        implied_volatility=20,
        risk_free_rate=0.04,
    )

    discounted = score_setup({**base, "option_mark": 2.0}, now=morning)
    overpriced = score_setup({**base, "option_mark": 4.0}, now=morning)

    assert discounted.components["premium_value"] == 2
    assert discounted.raw["option_value_verdict"] == "discount"
    assert overpriced.components["premium_value"] == -3
    assert overpriced.raw["option_value_verdict"] == "overpriced"
    assert discounted.score > overpriced.score


def test_options_webhook_persists_valuation_context(tmp_path):
    cfg = scanner_config(tmp_path)
    app = create_app(cfg)

    with TestClient(app) as client:
        body = setup_payload(
            ticker="SPY",
            option_type="call",
            underlying_price=100,
            strike=100,
            dte=30,
            implied_volatility=20,
            option_mark=2.0,
            risk_free_rate=0.04,
        )
        webhook = client.post("/webhook/alert", json=body)
        assert webhook.status_code == 200
        status = client.get("/status").json()

    latest = status["latest"][0]
    assert latest["ticker"] == "SPY"
    assert latest["components"]["premium_value"] == 2
    assert latest["raw"]["option_value_verdict"] == "discount"
    import sqlite3, json

    with sqlite3.connect(cfg.sqlite_path) as conn:
        row = conn.execute("SELECT components_json, raw_json FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    components = json.loads(row[0])
    raw = json.loads(row[1])
    assert components["premium_value"] == 2
    assert raw["option_value_verdict"] == "discount"
    assert raw["option_theoretical_value"] > 0


class FakeSignaClient:
    def __init__(self):
        self.symbols = []

    def fetch_signal(self, symbol: str):
        self.symbols.append(symbol)
        return SignaSignal(
            symbol=symbol,
            ok=True,
            grade="A",
            score=88,
            daily_direction="UP",
            action="BUY",
            risk_rating="MODERATE",
        )


def test_options_scanner_enriches_with_signa_context(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "signa_api_enabled", True)
    object.__setattr__(cfg, "signa_api_key_configured", True)
    object.__setattr__(cfg, "signa_symbol_map", {"SPXW": "SPY", "AAPL": "AAPL"})
    storage = ScanStorage(cfg.sqlite_path)
    tasty = TastytradeClient(cfg, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    discord = DiscordAlerter(cfg, storage, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(204))))
    signa = FakeSignaClient()
    scanner = OptionsScanner(cfg, tasty, storage, discord, signa_client=signa)

    now = datetime(2026, 5, 29, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    outcome = asyncio.run(scanner.scan_ticker("SPXW", source="test", context=setup_payload(ticker="SPXW"), now=now))

    assert signa.symbols == ["SPY"]
    assert outcome.result.raw["signa_symbol"] == "SPY"
    assert outcome.result.raw["signa_grade"] == "A"
    assert outcome.result.raw["signa_daily_direction"] == "UP"
    assert outcome.result.components["signa"] == 2


def test_discord_payload_shows_signa_flow_field():
    result = score_setup(setup_payload(
        ticker="QQQ",
        signa_symbol="QQQ",
        signa_grade="B",
        signa_score=74,
        signa_daily_direction="UP",
        signa_action="BUY",
    ))

    embed = build_discord_payload(result)["embeds"][0]
    field_map = {field["name"]: field["value"] for field in embed["fields"]}

    assert "Signa Flow" in field_map
    assert "QQQ" in field_map["Signa Flow"]
    assert "grade B" in field_map["Signa Flow"]
    assert "score 74" in field_map["Signa Flow"]
    assert "UP" in field_map["Signa Flow"]
    assert result.components["signa"] == 2


def test_options_config_loads_signa_settings(tmp_path):
    cfg = ScannerConfig(
        market_data_provider="tastytrade",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://api.tastyworks.com",
        public_api_key_configured=False,
        public_base_url="https://api.public.com",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://data.alpaca.markets",
        port=8010,
        discord_webhook_url="",
        watchlist=["SPY"],
        interval_minutes=5,
        sqlite_path=tmp_path / "scanner.sqlite",
        signa_api_enabled=True,
        signa_api_key_configured=True,
        signa_symbol_map={"SPXW": "SPY"},
    )

    assert cfg.signa_api_enabled is True
    assert cfg.signa_symbol_map["SPXW"] == "SPY"


def test_market_data_provider_factory_selects_public_and_alpaca(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    assert isinstance(create_market_data_client(cfg), PublicMarketDataClient)

    object.__setattr__(cfg, "market_data_provider", "alpaca")
    assert isinstance(create_market_data_client(cfg), AlpacaMarketDataClient)


def test_options_market_data_defaults_to_public():
    cfg = load_config(environ=[])

    assert cfg.market_data_provider == "public"


def test_provider_capabilities_are_read_only_and_account_forbidden(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    object.__setattr__(cfg, "public_api_key_configured", True)

    profile = build_provider_capabilities(cfg).to_dict()

    assert profile["name"] == "public"
    assert profile["configured"] is True
    assert profile["read_only"] is True
    assert profile["options_supported"] is True
    assert profile["order_supported"] is False
    assert profile["account_endpoints_forbidden"] is True
    assert "/orders" in profile["forbidden_path_parts"]


def test_unsupported_market_data_provider_rejected(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "unknown-provider")

    try:
        create_market_data_client(cfg)
    except ValueError as exc:
        assert "Unsupported OPTIONS_MARKET_DATA_PROVIDER" in str(exc)
    else:
        raise AssertionError("unsupported provider should raise")


def test_public_provider_is_read_only_and_parses_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_KEY", "public-key")
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "data": {
                "iv_rank": 22,
                "underlying_price": 501.25,
                "volume": 12345,
            }
        })

    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    object.__setattr__(cfg, "public_api_key_configured", True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.public.com",
    )

    async def run():
        public = PublicMarketDataClient(cfg, client=client)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error is None
        assert snapshot.iv_rank == 22
        assert snapshot.price == 501.25
        assert snapshot.volume == 12345

    asyncio.run(run())
    assert seen == {
        "path": "/market-data/options/SPY",
        "auth": "Bearer public-key",
    }


def test_public_provider_missing_credentials_fails_soft(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    object.__setattr__(cfg, "public_api_key_configured", False)

    async def run():
        public = PublicMarketDataClient(cfg)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.ticker == "SPY"
        assert snapshot.error == "credentials_missing"

    asyncio.run(run())


def test_public_provider_blocks_forbidden_account_paths(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    object.__setattr__(cfg, "public_api_key_configured", True)

    async def run():
        public = PublicMarketDataClient(cfg)
        try:
            await public._get("/accounts/me")
        except ValueError as exc:
            assert "forbidden market-data path" in str(exc)
        else:
            raise AssertionError("account path should be blocked")

    asyncio.run(run())


def test_public_provider_marks_unsupported_response_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_KEY", "public-key")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        base_url="https://api.public.com",
    )
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "public")
    object.__setattr__(cfg, "public_api_key_configured", True)

    async def run():
        public = PublicMarketDataClient(cfg, client=client)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "unsupported_response_shape"

    asyncio.run(run())


def test_alpaca_provider_uses_market_data_only_and_midpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "alpaca-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "alpaca-secret")
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("apca-api-key-id")
        seen["secret"] = request.headers.get("apca-api-secret-key")
        return httpx.Response(200, json={
            "snapshot": {
                "latestQuote": {"bp": 2.1, "ap": 2.3},
                "volume": 250,
                "ivRank": 28,
            }
        })

    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "alpaca")
    object.__setattr__(cfg, "alpaca_api_key_configured", True)
    object.__setattr__(cfg, "alpaca_secret_key_configured", True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://data.alpaca.markets",
    )

    async def run():
        alpaca = AlpacaMarketDataClient(cfg, client=client)
        snapshot = await alpaca.fetch_market_snapshot("SPY")
        assert snapshot.error is None
        assert snapshot.iv_rank == 28
        assert snapshot.price == 2.2
        assert snapshot.volume == 250

    asyncio.run(run())
    assert seen == {
        "path": "/v1beta1/options/snapshots/SPY",
        "key": "alpaca-key",
        "secret": "alpaca-secret",
    }


def test_alpaca_provider_blocks_order_paths(tmp_path):
    cfg = scanner_config(tmp_path)
    object.__setattr__(cfg, "market_data_provider", "alpaca")

    async def run():
        alpaca = AlpacaMarketDataClient(cfg)
        try:
            await alpaca._get("/v1beta1/options/orders")
        except ValueError as exc:
            assert "forbidden market-data path" in str(exc)
        else:
            raise AssertionError("order path should be blocked")

    asyncio.run(run())
