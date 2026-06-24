"""Tests for the read-only GEXsniper client and adapters.

The sample payload below is the published ``GET /v1/context`` example from
``gexsniper-api-schema.json`` (SPY snapshot), trimmed to the fields the parser
reads. If GEXsniper changes its response shape, these tests should catch it.
"""

from __future__ import annotations

import httpx

from sources.gex_client import (
    DEFAULT_SYMBOL_MAP,
    GexClient,
    observe_gex,
    parse_gex_context,
)

# Published example response (GET /v1/context?ticker=SPY), trimmed.
SAMPLE_CONTEXT = {
    "success": True,
    "ticker": "SPY",
    "spotPrice": 598.42,
    "timestamp": "2026-06-23T15:30:00.000Z",
    "netGEX": 2847563210,
    "flipPoint": 594.00,
    "distToFlip": 0.74,
    "regime": "positive",
    "regimeLabel": "Dealers LONG gamma — expect mean reversion at walls",
    "topCallWall": {"strike": 605, "gex": 1243876543},
    "bottomPutWall": {"strike": 585, "gex": -987654321},
    "netDEX": -8743200,
    "chexRegime": "HIGH_PINNING",
    "snapshotAge": 34521,
    "source": "database",
}


def test_parse_maps_published_schema_onto_context_fields():
    ctx = parse_gex_context("SPY", SAMPLE_CONTEXT)

    assert ctx.ok is True
    assert ctx.ticker == "SPY"
    assert ctx.spot_price == 598.42
    assert ctx.flip_point == 594.00
    assert ctx.dist_to_flip == 0.74
    assert ctx.regime == "positive"
    assert ctx.call_wall == 605.0
    assert ctx.call_wall_gex == 1243876543.0
    assert ctx.put_wall == 585.0
    assert ctx.net_dex == -8743200.0
    assert ctx.delta_bias == "bearish"  # netDEX < 0
    assert ctx.chex_regime == "HIGH_PINNING"
    assert ctx.snapshot_age_ms == 34521


def test_to_gex_context_fields_is_drop_in_for_engine_gexcontext():
    """Mapped keys must match context.market_context.GEXContext field names."""
    from context.market_context import GEXContext

    fields = parse_gex_context("SPY", SAMPLE_CONTEXT).to_gex_context_fields()

    # Constructing GEXContext with these kwargs must not raise (names line up).
    gex = GEXContext(**fields)
    assert gex.gex_flip == 594.00
    assert gex.call_wall == 605.0
    assert gex.put_wall == 585.0
    assert gex.gex_regime == "positive"
    assert gex.delta_bias == "bearish"


def test_delta_bias_follows_netdex_sign():
    assert parse_gex_context("X", {**SAMPLE_CONTEXT, "netDEX": 5}).delta_bias == "bullish"
    assert parse_gex_context("X", {**SAMPLE_CONTEXT, "netDEX": 0}).delta_bias == "neutral"
    assert parse_gex_context("X", {**SAMPLE_CONTEXT, "netDEX": None}).delta_bias is None


def test_unsuccessful_payload_is_not_ok():
    ctx = parse_gex_context("SPY", {"success": False, "error": "no coverage"})
    assert ctx.ok is False
    assert ctx.error == "api_unsuccessful"


def test_client_uses_apikey_header_and_context_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["x_api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=SAMPLE_CONTEXT)

    http_client = httpx.Client(
        base_url="https://api.gexsniper.com/v1",
        transport=httpx.MockTransport(handler),
    )
    ctx = GexClient(api_key="test-key", client=http_client).fetch_context("NDX")

    assert seen["path"] == "/v1/context"
    assert seen["query"] == {"ticker": "NDX"}
    assert seen["x_api_key"] == "test-key"
    assert ctx.ok is True


def test_missing_api_key_fails_soft():
    ctx = GexClient(api_key="").fetch_context("NDX")
    assert ctx.ok is False
    assert ctx.error == "missing_api_key"


def test_http_error_fails_soft_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    http_client = httpx.Client(
        base_url="https://api.gexsniper.com/v1",
        transport=httpx.MockTransport(handler),
    )
    ctx = GexClient(api_key="k", client=http_client).fetch_context("NDX")
    assert ctx.ok is False
    assert ctx.error == "http_503"


def test_observe_gex_disabled_returns_none():
    class Cfg:
        gex_api_enabled = False

    assert observe_gex("MNQ", Cfg()) is None


def test_observe_gex_maps_future_to_index_and_fetches():
    def handler(request: httpx.Request) -> httpx.Response:
        # MNQ must resolve to NDX before the call.
        assert dict(request.url.params)["ticker"] == "NDX"
        return httpx.Response(200, json={**SAMPLE_CONTEXT, "ticker": "NDX"})

    http_client = httpx.Client(
        base_url="https://api.gexsniper.com/v1",
        transport=httpx.MockTransport(handler),
    )

    class Cfg:
        gex_api_enabled = True
        gex_symbol_map = DEFAULT_SYMBOL_MAP

    ctx = observe_gex("MNQ", Cfg(), client=GexClient(api_key="k", client=http_client))
    assert ctx is not None
    assert ctx.ok is True
    assert ctx.ticker == "NDX"


def test_observe_gex_unmapped_instrument_returns_none():
    class Cfg:
        gex_api_enabled = True
        gex_symbol_map = DEFAULT_SYMBOL_MAP

    # MCL (crude) has no index mapping -> nothing to observe.
    assert observe_gex("MCL", Cfg(), client=GexClient(api_key="k")) is None
