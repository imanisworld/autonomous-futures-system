"""Public.com market-data client tests against the documented API shapes.

Covers the Phase-1 required matrix: successful underlying quote, successful
option chain, unauthorized, rate-limited, timeout, schema change, empty chain,
stale quote — plus a regression test for the legacy-path 302 root cause and
the read-only path guards.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from alert_ranker.config import ScannerConfig, load_config
from alert_ranker.market_data import (
    PUBLIC_AUTH_TOKEN_PATH,
    PublicMarketDataClient,
    build_provider_capabilities,
)

ACCOUNT_ID = "ACC12345"
AUTH_PATH = PUBLIC_AUTH_TOKEN_PATH
QUOTES_PATH = f"/userapigateway/marketdata/{ACCOUNT_ID}/quotes"
EXPIRATIONS_PATH = f"/userapigateway/marketdata/{ACCOUNT_ID}/option-expirations"
CHAIN_PATH = f"/userapigateway/marketdata/{ACCOUNT_ID}/option-chain"


def public_config(tmp_path: Path) -> ScannerConfig:
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
        discord_webhook_url="",
        watchlist=["SPY"],
        interval_minutes=5,
        sqlite_path=tmp_path / "options_scanner.sqlite",
        public_account_id=ACCOUNT_ID,
    )


def fresh_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def quote_payload(**overrides) -> dict:
    quote = {
        "instrument": {"symbol": "SPY", "type": "EQUITY"},
        "outcome": "SUCCESS",
        "last": "501.25",
        "lastTimestamp": fresh_ts(),
        "bid": "501.20",
        "bidSize": 4,
        "ask": "501.30",
        "askSize": 6,
        "volume": 1234567,
    }
    quote.update(overrides)
    return {"quotes": [quote]}


def make_client(cfg: ScannerConfig, handler) -> PublicMarketDataClient:
    transport = httpx.MockTransport(handler)
    return PublicMarketDataClient(
        cfg,
        client=httpx.AsyncClient(transport=transport, base_url="https://api.public.com"),
    )


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_API_SECRET_KEY", "test-secret")
    monkeypatch.delenv("PUBLIC_API_KEY", raising=False)


def auth_ok(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == AUTH_PATH:
        assert request.method == "POST"
        return httpx.Response(200, json={"accessToken": "short-lived-token"})
    return None


def test_successful_underlying_quote(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        seen.append((request.url.path, request.headers.get("authorization")))
        assert request.url.path == QUOTES_PATH
        return httpx.Response(200, json=quote_payload())

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("spy")
        assert snapshot.error is None
        assert snapshot.price == 501.25
        assert snapshot.bid == 501.20
        assert snapshot.ask == 501.30
        assert snapshot.volume == 1234567
        assert snapshot.stale is False
        # Token exchanged once and cached across calls.
        await public.fetch_market_snapshot("SPY")
        assert seen == [
            (QUOTES_PATH, "Bearer short-lived-token"),
            (QUOTES_PATH, "Bearer short-lived-token"),
        ]

    asyncio.run(run())


def test_successful_option_chain(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        if request.url.path == EXPIRATIONS_PATH:
            return httpx.Response(
                200, json={"baseSymbol": "SPY", "expirations": ["2099-01-16", "2099-02-20"]}
            )
        assert request.url.path == CHAIN_PATH
        contract = {
            "instrument": {"symbol": "SPY990116C00500000", "type": "OPTION"},
            "outcome": "SUCCESS",
            "last": "2.10",
            "bid": "2.05",
            "ask": "2.15",
            "volume": 350,
            "openInterest": 1500,
            "optionDetails": {
                "greeks": {"delta": "0.52", "impliedVolatility": "0.19"},
                "strikePrice": "500",
                "midPrice": "2.10",
            },
        }
        return httpx.Response(200, json={"baseSymbol": "SPY", "calls": [contract], "puts": []})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        chain = await public.fetch_option_chain("SPY")
        assert chain.error is None
        assert chain.expiration == "2099-01-16"
        assert len(chain.calls) == 1 and not chain.puts
        call = chain.calls[0]
        assert call.symbol == "SPY990116C00500000"
        assert call.strike == 500.0
        assert call.bid == 2.05 and call.ask == 2.15 and call.mid == 2.10
        assert call.open_interest == 1500
        assert call.delta == 0.52
        assert call.implied_volatility == 0.19

    asyncio.run(run())


def test_unauthorized_marketdata_response(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        return httpx.Response(401, json={"message": "unauthorized"})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "authentication_failed"
        assert snapshot.price is None

    asyncio.run(run())


def test_unauthorized_token_exchange(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == AUTH_PATH
        return httpx.Response(401, json={"message": "bad secret"})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "authentication_failed"

    asyncio.run(run())


def test_rate_limited_response(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        return httpx.Response(429, json={"message": "slow down"})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "rate_limited"

    asyncio.run(run())


def test_timeout_is_categorised(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        raise httpx.ConnectTimeout("timed out")

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "timeout"

    asyncio.run(run())


def test_schema_change_is_categorised(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        return httpx.Response(200, json={"totally": "different"})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "unsupported_response_shape"
        assert snapshot.price is None

    asyncio.run(run())


def test_empty_chain_is_categorised(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        if request.url.path == EXPIRATIONS_PATH:
            return httpx.Response(200, json={"baseSymbol": "SPY", "expirations": ["2099-01-16"]})
        return httpx.Response(200, json={"baseSymbol": "SPY", "calls": [], "puts": []})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        chain = await public.fetch_option_chain("SPY")
        assert chain.error == "empty_chain"
        assert not chain.calls and not chain.puts

    asyncio.run(run())


def test_stale_quote_is_flagged(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        return httpx.Response(200, json=quote_payload(lastTimestamp=old))

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "stale_quote"
        assert snapshot.stale is True
        # Price is preserved for observability but the error marks it unusable.
        assert snapshot.price == 501.25

    asyncio.run(run())


def test_legacy_redirect_root_cause_is_categorised_not_crashed(tmp_path):
    """Regression: unknown gateway paths 302-redirect to the docs page.

    The legacy placeholder path did exactly this and surfaced as a bare
    HTTPStatusError. The rewritten client must categorise any unexpected
    status (including redirects) instead.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        auth = auth_ok(request)
        if auth:
            return auth
        return httpx.Response(302, headers={"location": "https://public.com/api/docs"})

    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, handler)
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "http_status_302"
        assert snapshot.price is None

    asyncio.run(run())


def test_account_id_missing_is_a_distinct_config_error(tmp_path):
    cfg = public_config(tmp_path)
    object.__setattr__(cfg, "public_account_id", "")

    async def run():
        public = make_client(cfg, lambda request: httpx.Response(500))
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "account_id_missing"
        chain = await public.fetch_option_chain("SPY")
        assert chain.error == "account_id_missing"

    asyncio.run(run())


def test_credentials_missing_when_secret_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLIC_API_SECRET_KEY", raising=False)
    cfg = public_config(tmp_path)
    object.__setattr__(cfg, "public_api_key_configured", False)

    async def run():
        public = make_client(cfg, lambda request: httpx.Response(500))
        snapshot = await public.fetch_market_snapshot("SPY")
        assert snapshot.error == "credentials_missing"

    asyncio.run(run())


def test_read_only_guard_blocks_trading_and_account_paths(tmp_path):
    cfg = public_config(tmp_path)

    async def run():
        public = make_client(cfg, lambda request: httpx.Response(200, json={}))
        for path in (
            "/userapigateway/trading/account",
            "/userapigateway/marketdata/ACC12345/orders",
            "/quotes",
        ):
            with pytest.raises(ValueError):
                await public._post_marketdata(path, {})

    asyncio.run(run())


def test_capabilities_reflect_new_prefixes_and_account_pin(tmp_path):
    cfg = public_config(tmp_path)
    profile = build_provider_capabilities(cfg).to_dict()
    assert profile["configured"] is True
    assert profile["read_only"] is True
    assert profile["allowed_prefixes"] == ["/userapigateway/marketdata"] or profile[
        "allowed_prefixes"
    ] == ("/userapigateway/marketdata",)
    assert "/trading" in profile["forbidden_path_parts"]

    object.__setattr__(cfg, "public_account_id", "")
    assert build_provider_capabilities(cfg).configured is False


def test_load_config_reads_public_pins(monkeypatch, tmp_path):
    monkeypatch.setenv("PUBLIC_API_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("PUBLIC_ACCOUNT_ID", "ACC99999")
    monkeypatch.setenv("OPTIONS_SCANNER_SQLITE_PATH", str(tmp_path / "s.sqlite"))
    cfg = load_config()
    assert cfg.public_api_key_configured is True
    assert cfg.public_account_id == "ACC99999"
    assert cfg.market_data_provider in {"public", "tastytrade", "alpaca"}
    if cfg.market_data_provider == "public":
        assert cfg.market_data_configured is True
