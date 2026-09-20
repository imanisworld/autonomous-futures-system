import httpx

from sources.signa_discovery import (
    SignaDiscoveryClient,
    candidates_from_action_card,
    candidates_from_scan,
    dataset_candidate_tags,
    normalize_direction,
)


def test_normalize_direction_conservatively():
    assert normalize_direction("BUY") == "LONG"
    assert normalize_direction("bearish") == "SHORT"
    assert normalize_direction("avoid") == "AVOID"
    assert normalize_direction(None) is None


def test_action_card_candidate_keeps_levels_and_observation_flags():
    payload = {
        "data_as_of": "2026-09-20T00:00:00Z",
        "data": {
            "signal": {
                "symbol": "NVDA",
                "timeframe": "1d",
                "direction": "bullish",
                "grade": "A",
                "score": 81,
                "confidence": 72,
                "entry_zone": {"low": 170, "high": 172},
                "stop_loss": 165,
                "targets": [180, 190],
                "reward_to_risk": 2.4,
                "component_scores": {"trend": 90, "volume": 55},
            }
        },
    }
    [candidate] = candidates_from_action_card(payload, retrieved_at="now")
    row = candidate.to_record()
    assert row["ticker"] == "NVDA"
    assert row["direction"] == "LONG"
    assert row["status"] == "SIGNA_CANDIDATE"
    assert row["trade_authority"] is False
    assert row["targets"] == (180.0, 190.0)
    assert row["component_scores"] == {"trend": 90.0, "volume": 55.0}


def test_scan_candidates_are_deduped_and_flexible():
    payload = {
        "data_as_of": "2026-09-20T12:00:00Z",
        "results": [
            {"ticker": "RCL", "signal": "SHORT", "agent": "TrendEngine", "score": 66},
            {"ticker": "RCL", "signal": "SHORT", "agent": "TrendEngine", "score": 66},
            {"symbol": "MSFT", "bias": "BUY", "source_agent": "52W Momentum", "target_1": 540},
        ],
    }
    rows = candidates_from_scan(payload)
    assert [r.ticker for r in rows] == ["RCL", "MSFT"]
    assert rows[0].source_agent == "TrendEngine"
    assert rows[0].direction == "SHORT"
    assert rows[1].targets == (540.0,)


def test_dataset_tags_are_context_not_trade_authority():
    tag = dataset_candidate_tags("options_flow", {"count": 3, "direction": "calls", "sentiment": "bullish"}, symbol="QQQ")
    assert tag["dataset"] == "options_flow"
    assert tag["symbol"] == "QQQ"
    assert tag["direction"] == "LONG"
    assert tag["trade_authority"] is False


def test_client_caches_ok_responses_and_backs_off_429():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"error": "rate"})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.getsigna.ai")
    client = SignaDiscoveryClient(api_key="k", client=http, cache_ttl_seconds=1800, clock=lambda: 1000.0)
    first = client.scan(["SPY", "QQQ"], limit=10)
    second = client.scan(["SPY", "QQQ"], limit=10)
    assert first.ok is True
    assert second.cached is True
    assert len(calls) == 1

    client_no_cache = SignaDiscoveryClient(api_key="k", client=http, cache_ttl_seconds=0, clock=lambda: 1000.0)
    third = client_no_cache.scan("SPY,QQQ", limit=20)
    assert third.ok is False
    assert third.error == "http_429"
    fourth = client_no_cache.scan("SPY,QQQ", limit=20)
    assert fourth.backoff_active is True


def test_client_signal_index_endpoint():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"ok": True, "value": 55})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.getsigna.ai")
    client = SignaDiscoveryClient(api_key="k", client=http, cache_ttl_seconds=0)
    response = client.signal_index(universe="SPY,QQQ")
    assert response.ok is True
    assert seen == ["/api/v1/signal-index"]


def test_manual_context_records_from_discord_style_text():
    from sources.signa_discovery import manual_context_records_from_text

    text = """
    ticker: SPY
    source: options_flow
    direction: bullish
    count: 4
    callPremium: $1,250,000
    notes: calls dominant

    ticker: QQQ
    source: gex
    gamma_wall: 490
    flip: 485
    """
    rows = manual_context_records_from_text(text)
    assert len(rows) == 2
    assert rows[0]["status"] == "SIGNA_CONTEXT"
    assert rows[0]["trade_authority"] is False
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["direction"] == "LONG"
    assert rows[0]["callPremium"] == 1250000
    assert rows[1]["dataset"] == "gex"
    assert rows[1]["flip"] == 485


def test_client_uses_confirmed_options_flow_paths_and_gex_unresolved():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"count": 0})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.getsigna.ai")
    client = SignaDiscoveryClient(api_key="k", client=http, cache_ttl_seconds=0)
    assert client.options_flow("SPY").ok is True
    assert client.dark_pool("SPY").ok is True
    assert client.market_tide().ok is True
    assert client.congress_flow("SPY").ok is True
    gex = client.gex("SPY")
    assert gex.ok is False
    assert gex.error == "standalone_gex_endpoint_unresolved"
    assert seen == [
        "/api/options-flow/SPY",
        "/api/options-flow/darkpool/SPY",
        "/api/options-flow/tide",
        "/api/options-flow/congress",
    ]
