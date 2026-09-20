from datetime import date, timedelta
from pathlib import Path

from alert_ranker.config import ScannerConfig
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.signa_context_store import SignaContextStore
from scripts.options_signa_context_pull import build_symbols, market_is_open, pull_context
from sources.signa_discovery import SignaDiscoveryResponse


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
        signa_api_key_configured=True,
    )


def test_market_gate_uses_nyse_regular_session():
    session = nyse_session_for(date(2026, 9, 18))
    assert session is not None
    assert market_is_open(session.open + timedelta(minutes=1)) is True
    assert market_is_open(session.close + timedelta(minutes=1)) is False
    assert market_is_open(nyse_session_for(date(2026, 9, 21)).open - timedelta(days=1)) is False


def test_build_symbols_adds_shared_proxy_symbols_once():
    symbols = build_symbols(["SPY", "NVDA", "qqq"], include_shared_proxies=True, limit=20)
    assert symbols[:3] == ["SPY", "NVDA", "QQQ"]
    assert "IWM" in symbols
    assert "TLT" in symbols
    assert symbols.count("SPY") == 1


def test_pull_context_writes_observation_only_rows_and_reuses_shared_cache(tmp_path):
    class FakeClient:
        def scan(self, *, symbols, timeframe):
            return SignaDiscoveryResponse(
                True,
                "/api/v1/scan",
                payload={"data_as_of": "2026-09-20T00:00:00Z", "results": [{"ticker": "SPY", "signal": "BUY", "score": 71}]},
                status_code=200,
                retrieved_at="now",
            )

        def market_tide(self):
            return SignaDiscoveryResponse(True, "/api/options-flow/tide", payload={"server_time": "2026-09-20T13:00:00Z", "call_pct": "60%"}, status_code=200, retrieved_at="now")

        def action_card(self, symbol, timeframe="1d"):
            return SignaDiscoveryResponse(
                True,
                f"/api/v1/signals/{symbol}",
                payload={"data_as_of": "2026-09-20T00:00:00Z", "data": {"signal": {"symbol": symbol, "timeframe": timeframe, "direction": "bullish", "score": 80}}},
                status_code=200,
                retrieved_at="now",
            )

    cfg = _config(tmp_path)
    session = nyse_session_for(date(2026, 9, 18))
    now = session.open + timedelta(minutes=5)
    include = {"scan", "action_card", "market_tide"}
    first = pull_context(cfg=cfg, symbols=["SPY"], include=include, timeframe="1d", now=now, client=FakeClient())
    second = pull_context(cfg=cfg, symbols=["SPY"], include=include, timeframe="1d", now=now + timedelta(minutes=5), client=FakeClient())

    assert first["ok"] is True
    assert second["ok"] is True
    store = SignaContextStore(cfg.sqlite_path)
    latest = store.latest(limit=20, ticker="SPY")
    assert len(latest) == 2  # scan candidate + action card; second pull deduped same provider snapshots
    assert all(item.trade_authority is False for item in latest)
    assert any(set(item.consumers) == {"options", "shared_proxy", "futures"} for item in latest)


def test_signa_context_pull_config_defaults_off():
    from alert_ranker.config import load_config

    cfg = load_config(environ=[])

    assert cfg.signa_context_pull_enabled is False
    assert cfg.signa_context_pull_interval_minutes == 15
    assert cfg.signa_context_pull_timeframe == "1d"
    assert cfg.signa_context_pull_symbols == []
    assert "options_flow" in cfg.signa_context_pull_include
    assert cfg.signa_context_pull_include_shared_proxies is True
    assert cfg.signa_context_pull_symbol_limit == 50


def test_signa_context_pull_config_is_explicit(monkeypatch):
    from alert_ranker.config import load_config

    cfg = load_config(environ=[
        ("OPTIONS_SIGNA_CONTEXT_PULL_ENABLED", "true"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_INTERVAL_MINUTES", "12"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_TIMEFRAME", "1h"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_SYMBOLS", "SPY,NVDA"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE", "scan,options_flow"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_INCLUDE_SHARED_PROXIES", "false"),
        ("OPTIONS_SIGNA_CONTEXT_PULL_SYMBOL_LIMIT", "7"),
    ])

    assert cfg.signa_context_pull_enabled is True
    assert cfg.signa_context_pull_interval_minutes == 12
    assert cfg.signa_context_pull_timeframe == "1h"
    assert cfg.signa_context_pull_symbols == ["SPY", "NVDA"]
    assert cfg.signa_context_pull_include == ["scan", "options_flow"]
    assert cfg.signa_context_pull_include_shared_proxies is False
    assert cfg.signa_context_pull_symbol_limit == 7


def test_scheduled_signa_context_pull_disabled_never_pulls(tmp_path, monkeypatch):
    import alert_ranker.app as app_module

    called = False

    def fake_pull_context(**_kwargs):
        nonlocal called
        called = True
        return {"ok": False}

    monkeypatch.setattr(app_module, "pull_context", fake_pull_context)
    cfg = _config(tmp_path)
    cfg = cfg.__class__(**{**cfg.__dict__, "signa_context_pull_enabled": False})

    result = app_module.run_scheduled_signa_context_pull(cfg)

    assert result == {"ok": True, "skipped": "disabled", "stored_rows": 0}
    assert called is False


def test_scheduled_signa_context_pull_market_closed_never_pulls(tmp_path, monkeypatch):
    import alert_ranker.app as app_module

    called = False
    def fake_pull_context(**_kwargs):
        nonlocal called
        called = True
        return {"ok": False}

    monkeypatch.setattr(app_module, "pull_context", fake_pull_context)
    monkeypatch.setattr(app_module, "market_is_open", lambda _now: False)
    cfg = _config(tmp_path)
    cfg = cfg.__class__(**{**cfg.__dict__, "signa_context_pull_enabled": True})

    result = app_module.run_scheduled_signa_context_pull(cfg)

    assert result == {"ok": True, "skipped": "market_closed", "stored_rows": 0}
    assert called is False


def test_scheduled_signa_context_pull_uses_configured_read_only_inputs(tmp_path, monkeypatch):
    import alert_ranker.app as app_module

    captured = {}

    def fake_pull_context(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "trade_authority": False, "stored_rows": 2}

    monkeypatch.setattr(app_module, "market_is_open", lambda _now: True)
    monkeypatch.setattr(app_module, "pull_context", fake_pull_context)
    cfg = _config(tmp_path)
    cfg = cfg.__class__(**{
        **cfg.__dict__,
        "watchlist": ["AAPL"],
        "signa_context_pull_enabled": True,
        "signa_context_pull_symbols": ["SPY", "NVDA"],
        "signa_context_pull_include": ["scan", "options_flow"],
        "signa_context_pull_timeframe": "1h",
        "signa_context_pull_include_shared_proxies": True,
        "signa_context_pull_symbol_limit": 20,
    })

    result = app_module.run_scheduled_signa_context_pull(cfg)

    assert result["ok"] is True
    assert result["trade_authority"] is False
    assert captured["cfg"] is cfg
    assert captured["timeframe"] == "1h"
    assert captured["include"] == {"scan", "options_flow"}
    assert captured["symbols"][:2] == ["SPY", "NVDA"]
    assert "TLT" in captured["symbols"]
