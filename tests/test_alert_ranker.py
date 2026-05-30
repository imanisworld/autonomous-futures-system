from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient

from alert_ranker.app import create_app
from alert_ranker.config import ScannerConfig
from alert_ranker.discord import DiscordAlerter, build_discord_payload
from alert_ranker.scanner import OptionsScanner
from alert_ranker.scorer import score_setup
from alert_ranker.storage import ScanStorage
from alert_ranker.tastytrade_client import TastytradeClient, parse_iv_rank


def scanner_config(tmp_path: Path, webhook_url: str = "") -> ScannerConfig:
    return ScannerConfig(
        tastytrade_username="user",
        tastytrade_password="pass",
        tastytrade_base_url="https://api.tastyworks.com",
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
        status = client.get("/status").json()
        assert status["advisory_only"] is True
        assert status["latest"][0]["ticker"] == "AAPL"


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
