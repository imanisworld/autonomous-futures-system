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


def test_options_app_direct_signa_pull_route_is_read_only(tmp_path, monkeypatch):
    from sources.signa_discovery import SignaDiscoveryResponse
    import alert_ranker.app as app_module

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def scan(self, *, symbols, timeframe):
            return SignaDiscoveryResponse(
                True,
                "/api/v1/scan",
                payload={"results": [{"ticker": "SPY", "signal": "BUY", "agent": "TrendEngine", "score": 71}]},
                status_code=200,
                retrieved_at="now",
            )

        def signal_index(self):
            return SignaDiscoveryResponse(True, "/api/v1/signal-index", payload={"sentiment": "bullish", "value": 55}, status_code=200, retrieved_at="now")

        def market_tide(self):
            return SignaDiscoveryResponse(True, "/api/options-flow/tide", payload={"call_pct": "60%", "put_pct": "40%", "row_count": 10}, status_code=200, retrieved_at="now")

        def action_card(self, symbol, timeframe="1d"):
            return SignaDiscoveryResponse(
                True,
                f"/api/v1/signals/{symbol}",
                payload={"data": {"signal": {"symbol": symbol, "timeframe": timeframe, "direction": "bullish", "score": 80}}},
                status_code=200,
                retrieved_at="now",
            )

        def enhanced_signal(self, symbol, **params):
            return SignaDiscoveryResponse(True, "/api/v1/enhanced-signal", payload={"symbol": symbol, "signa": {"action": "BUY"}}, status_code=200, retrieved_at="now")

        def options_flow(self, symbol):
            return SignaDiscoveryResponse(True, f"/api/options-flow/{symbol}", payload={"symbol": symbol, "count": 2, "callPremium": 1000, "sentiment": "bullish"}, status_code=200, retrieved_at="now")

        def dark_pool(self, symbol):
            return SignaDiscoveryResponse(True, f"/api/options-flow/darkpool/{symbol}", payload={"items": [{"sentiment": "bullish"}]}, status_code=200, retrieved_at="now")

        def congress_flow(self, symbol):
            return SignaDiscoveryResponse(True, "/api/options-flow/congress", payload={"symbol": symbol, "signal": "neutral", "trade_count": 0}, status_code=200, retrieved_at="now")

        def gex(self, symbol):
            return SignaDiscoveryResponse(False, "gex", error="standalone_gex_endpoint_unresolved", retrieved_at="now")

    monkeypatch.setattr(app_module, "SignaDiscoveryClient", FakeClient)
    cfg = _config(tmp_path)
    cfg = cfg.__class__(**{**cfg.__dict__, "signa_api_key_configured": True})
    app = create_app(config=cfg)
    client = TestClient(app)
    response = client.post("/signa/context/pull", json={"symbols": ["SPY"], "include": ["scan", "action_card", "options_flow", "dark_pool", "market_tide"]})
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["trade_authority"] is False
    assert body["inserted"] >= 5

    recent = client.get("/signa/context/recent", params={"ticker": "SPY", "limit": 20})
    items = recent.json()["items"]
    assert {item["source"] for item in items} >= {"scan", "action_card", "options_flow", "dark_pool"}
    assert all(item["trade_authority"] is False for item in items)


def test_direct_signa_pull_requires_key(tmp_path):
    app = create_app(config=_config(tmp_path))
    client = TestClient(app)
    response = client.post("/signa/context/pull", json={"symbols": ["SPY"]})
    assert response.status_code == 503


def test_context_store_dedupes_shared_provider_snapshot_across_consumers(tmp_path):
    store = SignaContextStore(tmp_path / "ctx.sqlite")
    first = store.record({
        "ticker": "SPY",
        "source": "action_card",
        "endpoint": "/api/v1/signals/SPY",
        "timeframe": "1d",
        "data_as_of": "2026-09-20T00:00:00Z",
        "retrieved_at": "2026-09-20T13:00:00Z",
        "direction": "LONG",
    })
    second = store.record({
        "ticker": "SPY",
        "source": "action_card",
        "endpoint": "/api/v1/signals/SPY",
        "timeframe": "1d",
        "data_as_of": "2026-09-20T00:00:00Z",
        "retrieved_at": "2026-09-20T13:05:00Z",
        "direction": "LONG",
    })
    latest = store.latest(limit=10, ticker="SPY")
    assert second == first
    assert len(latest) == 1
    assert latest[0].timeframe == "1d"
    assert latest[0].data_as_of == "2026-09-20T00:00:00Z"
    assert set(latest[0].consumers) == {"options", "shared_proxy", "futures"}
    assert latest[0].trade_authority is False


def test_context_store_records_new_provider_snapshot_when_data_changes(tmp_path):
    store = SignaContextStore(tmp_path / "ctx.sqlite")
    a = store.record({
        "ticker": "NVDA",
        "source": "enhanced_signal",
        "endpoint": "/api/v1/enhanced-signal",
        "timeframe": "1d",
        "data_as_of": "2026-09-20T00:00:00Z",
        "score": 70,
    })
    b = store.record({
        "ticker": "NVDA",
        "source": "enhanced_signal",
        "endpoint": "/api/v1/enhanced-signal",
        "timeframe": "1d",
        "data_as_of": "2026-09-21T00:00:00Z",
        "score": 72,
    })
    latest = store.latest(limit=10, ticker="NVDA")
    assert a != b
    assert len(latest) == 2
    assert {item.data_as_of for item in latest} == {"2026-09-20T00:00:00Z", "2026-09-21T00:00:00Z"}
    assert all(item.consumers == ("options",) for item in latest)
