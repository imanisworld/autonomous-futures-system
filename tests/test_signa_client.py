from __future__ import annotations

import httpx

from sources.signa_client import (
    SignaClient,
    enrich_payload_with_signa,
    parse_signa_signal,
)
from webhook.payload import AlertPayload


def test_parse_signa_signal_normalizes_grade_and_direction():
    signal = parse_signa_signal(
        "AAPL",
        {
            "ok": True,
            "engine": {"grade": "A", "score": 92, "direction": "BULLISH"},
            "signa": {"action": "BUY", "riskRating": "MODERATE"},
            "data": {"direction": "LONG"},
        },
    )

    assert signal.ok is True
    assert signal.grade == "A"
    assert signal.score == 92
    assert signal.daily_direction == "UP"
    assert signal.weekly_direction is None
    assert signal.to_payload_fields() == {
        "signa_grade": "A",
        "signa_score": 92.0,
        "signa_daily_direction": "UP",
        "signa_weekly_direction": None,
    }


def test_signa_client_uses_bearer_auth_and_signal_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = dict(request.url.params)
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "ok": True,
            "engine": {"grade": "B+", "score": 74, "direction": "BEARISH"},
        })

    http_client = httpx.Client(
        base_url="https://app.getsigna.ai",
        transport=httpx.MockTransport(handler),
    )
    client = SignaClient(api_key="test-key", client=http_client)

    signal = client.fetch_signal("QQQ")

    assert signal.ok is True
    assert signal.grade == "B"
    assert signal.daily_direction == "DOWN"
    assert seen == {
        "path": "/api/v1/signal",
        "query": {"sym": "QQQ", "timeframe": "1d"},
        "authorization": "Bearer test-key",
    }


def test_signa_client_missing_key_returns_neutral_error():
    client = SignaClient(api_key="")

    signal = client.fetch_signal("AAPL")

    assert signal.ok is False
    assert signal.error == "missing_api_key"


def test_signa_enrichment_updates_missing_payload_fields(config):
    payload = AlertPayload(
        ticker="MES1!",
        timestamp="2026-05-31T14:30:00Z",
        open=5580,
        high=5585,
        low=5575,
        close=5582,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params)["sym"] == "SPY"
        return httpx.Response(200, json={
            "ok": True,
            "engine": {"grade": "A", "score": 91, "direction": "BULLISH"},
        })

    object.__setattr__(config, "signa_api_enabled", True)
    object.__setattr__(config, "signa_symbol_map", {"MES": "SPY"})
    client = SignaClient(
        api_key="test-key",
        client=httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler)),
    )

    signal = enrich_payload_with_signa(payload, config, client=client)

    assert signal is not None
    assert signal.ok is True
    assert payload.signa_grade == "A"
    assert payload.signa_score == 91
    assert payload.signa_daily_direction == "UP"


def test_signa_enrichment_disabled_is_noop(config):
    payload = AlertPayload(
        ticker="MES1!",
        timestamp="2026-05-31T14:30:00Z",
        open=5580,
        high=5585,
        low=5575,
        close=5582,
    )

    result = enrich_payload_with_signa(payload, config)

    assert result is None
    assert payload.signa_grade is None


# Intended advisory FLOW watchlist — the liquid index/ETF option underlyings
# (plus VIX) we'd surface in the FLOW tab if/when Signa API access is sorted.
# Kept as a TEST-ONLY fixture; nothing here is wired into the live path.
SIGNA_FLOW_WATCHLIST = ("SPY", "QQQ", "SPX", "SPXW", "VIX")


def test_signa_flow_watchlist_advisory_fetch():
    """Each FLOW watchlist symbol fetches + parses a directional signal."""
    seen_syms = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_syms.append(dict(request.url.params)["sym"])
        return httpx.Response(200, json={
            "ok": True,
            "engine": {"grade": "B", "score": 70, "direction": "BULLISH"},
        })

    client = SignaClient(
        api_key="test-key",
        client=httpx.Client(
            base_url="https://app.getsigna.ai",
            transport=httpx.MockTransport(handler),
        ),
    )

    for sym in SIGNA_FLOW_WATCHLIST:
        signal = client.fetch_signal(sym)
        assert signal.ok is True
        assert signal.symbol == sym
        assert signal.daily_direction == "UP"

    assert seen_syms == list(SIGNA_FLOW_WATCHLIST)


def test_signa_flow_watchlist_degrades_safely_on_auth_failure():
    """A rejected/expired key (live state: HTTP 401) must degrade to a neutral
    signal for every watchlist symbol — never raise, never block."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    client = SignaClient(
        api_key="bad-key",
        client=httpx.Client(
            base_url="https://app.getsigna.ai",
            transport=httpx.MockTransport(handler),
        ),
    )

    for sym in SIGNA_FLOW_WATCHLIST:
        signal = client.fetch_signal(sym)
        assert signal.ok is False
        assert signal.error == "http_401"
        assert signal.grade is None
        assert signal.to_payload_fields()["signa_grade"] is None


# --- quota guards (opt-in TTL cache + shared account backoff) ---------------

from sources.signa_request_budget import account_backoff_remaining, clear_account_backoff


def _counting_client(status=200, headers=None, **kwargs):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params)["sym"])
        if status != 200:
            return httpx.Response(status, headers=headers or {}, json={"error": "x"})
        return httpx.Response(200, json={"ok": True, "engine": {"grade": "B", "score": 70, "direction": "BULLISH"}})

    client = SignaClient(
        api_key=kwargs.pop("api_key", "quota-test-key"),
        client=httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler)),
        **kwargs,
    )
    return client, calls


def test_default_client_has_no_cache_and_ignores_backoff():
    clear_account_backoff("https://app.getsigna.ai", "quota-default-key")
    client, calls = _counting_client(api_key="quota-default-key")
    client.fetch_signal("SPY")
    client.fetch_signal("SPY")
    assert calls == ["SPY", "SPY"]

    limited, limited_calls = _counting_client(status=429, api_key="quota-default-key")
    assert limited.fetch_signal("SPY").error == "http_429"
    assert account_backoff_remaining("https://app.getsigna.ai", "quota-default-key") == 0.0
    assert limited.fetch_signal("SPY").error == "http_429"
    assert limited_calls == ["SPY", "SPY"]


def test_ttl_cache_serves_repeat_requests_until_expiry():
    now = [1000.0]
    client, calls = _counting_client(cache_ttl_seconds=3600, clock=lambda: now[0])

    first = client.fetch_signal("SPY")
    second = client.fetch_signal("SPY")
    assert calls == ["SPY"]
    assert first.client_cached is None and second.client_cached is True
    assert second.retrieved_at == first.retrieved_at
    assert second.provenance_fields()["signa_client_cached"] is True
    assert second.grade == "B"

    client.fetch_signal("QQQ")
    assert calls == ["SPY", "QQQ"]

    now[0] += 3600
    third = client.fetch_signal("SPY")
    assert calls == ["SPY", "QQQ", "SPY"]
    assert third.client_cached is None


def test_ttl_cache_does_not_store_failures():
    client, calls = _counting_client(status=500, cache_ttl_seconds=3600)
    assert client.fetch_signal("SPY").ok is False
    assert client.fetch_signal("SPY").ok is False
    assert calls == ["SPY", "SPY"]


def test_429_opens_shared_account_backoff_and_later_calls_skip_the_request():
    key = "quota-backoff-key"
    clear_account_backoff("https://app.getsigna.ai", key)
    try:
        limited, calls = _counting_client(
            status=429, headers={"retry-after": "120"}, api_key=key, respect_account_backoff=True,
        )
        assert limited.fetch_signal("SPY").error == "http_429"
        remaining = account_backoff_remaining("https://app.getsigna.ai", key)
        assert 0 < remaining <= 120

        skipped = limited.fetch_signal("QQQ")
        assert skipped.ok is False and skipped.error == "account_backoff_active"
        assert calls == ["SPY"]

        # The circuit is per account, shared across client instances in the process.
        other, other_calls = _counting_client(api_key=key, respect_account_backoff=True)
        assert other.fetch_signal("SPY").error == "account_backoff_active"
        assert other_calls == []
    finally:
        clear_account_backoff("https://app.getsigna.ai", key)


def test_cache_hit_is_served_even_while_backoff_is_active():
    key = "quota-cache-backoff-key"
    clear_account_backoff("https://app.getsigna.ai", key)
    try:
        client, calls = _counting_client(api_key=key, cache_ttl_seconds=3600, respect_account_backoff=True)
        client.fetch_signal("SPY")
        from sources.signa_request_budget import mark_account_rate_limited
        mark_account_rate_limited("https://app.getsigna.ai", key, retry_after="600")
        hit = client.fetch_signal("SPY")
        assert hit.ok is True and hit.client_cached is True
        assert client.fetch_signal("QQQ").error == "account_backoff_active"
        assert calls == ["SPY"]
    finally:
        clear_account_backoff("https://app.getsigna.ai", key)
