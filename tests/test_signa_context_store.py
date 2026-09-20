from pathlib import Path

from fastapi.testclient import TestClient

from alert_ranker.app import create_app
from alert_ranker.config import ScannerConfig
from alert_ranker.signa_context_store import SignaContextStore
from sources.signa_discovery import manual_context_records_from_text


def _config(tmp_path: Path) -> ScannerConfig:
    return ScannerConfig(
        market_data_provider="public",
        tastytrade_username="",
        tastytrade_password="",
        tastytrade_base_url="https://example.invalid",
        public_api_key_configured=False,
        public_base_url="https://example.invalid",
        alpaca_api_key_configured=False,
        alpaca_secret_key_configured=False,
        alpaca_paper=True,
        alpaca_data_base_url="https://example.invalid",
        port=0,
        discord_webhook_url="",
        watchlist=["SPY"],
        interval_minutes=999,
        sqlite_path=tmp_path / "options.sqlite",
    )


def test_context_store_records_manual_rows_without_trade_authority(tmp_path):
    store = SignaContextStore(tmp_path / "ctx.sqlite")
    [row] = manual_context_records_from_text(
        "ticker: SPY\nsource: gex\nflip: 485\ngamma_wall: 490",
        default_source="manual_discord",
    )
    row_id = store.record(row)
    latest = store.latest(limit=5)
    assert row_id == latest[0].id
    assert latest[0].ticker == "SPY"
    assert latest[0].source == "gex"
    assert latest[0].status == "SIGNA_CONTEXT"
    assert latest[0].observation_only is True
    assert latest[0].trade_authority is False
    assert latest[0].payload["flip"] == 485


def test_options_app_manual_signa_context_ingest_route(tmp_path):
    app = create_app(config=_config(tmp_path))
    client = TestClient(app)
    response = client.post(
        "/signa/context/ingest",
        json={
            "source": "manual_discord",
            "text": "ticker: QQQ\nsource: options_flow\ndirection: bullish\ncallPremium: $1200000",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["trade_authority"] is False

    recent = client.get("/signa/context/recent", params={"ticker": "QQQ"})
    assert recent.status_code == 200
    items = recent.json()["items"]
    assert len(items) == 1
    assert items[0]["ticker"] == "QQQ"
    assert items[0]["source"] == "options_flow"
    assert items[0]["payload"]["callPremium"] == 1200000
    assert items[0]["trade_authority"] is False
