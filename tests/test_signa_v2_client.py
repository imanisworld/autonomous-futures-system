from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sources.signa_snapshot_store import SignaSnapshotStore
from sources.signa_v2_client import SignaV2Client


def test_v2_client_calls_only_current_documented_signal_endpoint() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "signal": {
                        "symbol": "AAPL",
                        "timeframe": "4h",
                        "direction": "LONG",
                        "confidence": 88,
                        "entry_zone": {"low": 200, "high": 201},
                        "stop_loss": 195,
                        "targets": [210],
                        "reward_to_risk": 2.0,
                    }
                },
            },
        )

    http = httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler))
    try:
        obs = SignaV2Client(api_key="test-key", client=http).fetch_action_card("aapl", "4h")
    finally:
        http.close()

    assert obs.ok is True
    assert obs.symbol == "AAPL"
    assert obs.direction == "LONG"
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/v1/signals/AAPL"
    assert seen[0].url.params["timeframe"] == "4h"
    assert seen[0].headers["Authorization"] == "Bearer test-key"


def test_v2_client_never_falls_back_to_legacy_signal() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": "not found"})

    http = httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler))
    try:
        obs = SignaV2Client(api_key="test-key", client=http).fetch_action_card("QQQ")
    finally:
        http.close()

    assert obs.ok is False
    assert obs.error == "http_404"
    assert paths == ["/api/v1/signals/QQQ"]
    assert "/api/v1/signal" not in paths


def test_v2_client_missing_key_and_symbol_fail_soft() -> None:
    no_key = SignaV2Client(api_key="").fetch_action_card("AAPL")
    assert no_key.ok is False
    assert no_key.error == "missing_api_key"

    no_symbol = SignaV2Client(api_key="x").fetch_action_card("")
    assert no_symbol.ok is False
    assert no_symbol.error == "missing_symbol"


def test_v2_client_http_and_json_failures_are_observations_not_exceptions() -> None:
    def forbidden(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    http = httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(forbidden))
    try:
        obs = SignaV2Client(api_key="test-key", client=http).fetch_action_card("AAPL")
    finally:
        http.close()
    assert obs.ok is False
    assert obs.error == "http_403"

    def bad_json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    http2 = httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(bad_json))
    try:
        obs2 = SignaV2Client(api_key="test-key", client=http2).fetch_action_card("AAPL")
    finally:
        http2.close()
    assert obs2.ok is False
    assert obs2.error == "JSONDecodeError"


def test_v2_client_has_no_trading_runtime_imports_or_mutating_http_calls() -> None:
    source = Path("sources/signa_v2_client.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    forbidden = {"execution", "webhook", "risk", "strategy", "options_manager", "options_companion"}
    assert not any(name.split(".")[0] in forbidden for name in imported)
    assert ".post(" not in source
    assert ".put(" not in source
    assert ".delete(" not in source
    assert "LIVE_TRADING_ENABLED" not in source
    assert "SIGNA_GATE_ENFORCED" not in source


def _card(symbol: str, timeframe: str) -> dict:
    return {
        "success": True,
        "data_as_of": "2026-09-12T02:00:35.366+00:00",
        "data": {
            "signal": {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": "NEUTRAL",
                "grade": "F",
                "score": 40,
                "targets": [1.0],
            }
        },
    }


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _counting_client(seen: list[httpx.Request], status: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "x"})
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=_card(symbol, request.url.params.get("timeframe", "1d")))

    return httpx.Client(base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler))


def test_v2_client_serves_one_provider_call_per_symbol_timeframe_within_ttl() -> None:
    seen: list[httpx.Request] = []
    clock = _Clock()
    http = _counting_client(seen)
    try:
        client = SignaV2Client(api_key="k", client=http, cache_ttl_seconds=1800, clock=clock)
        first = client.fetch_action_card("AAPL", "1d")
        clock.now += 300  # one scanner interval later
        second = client.fetch_action_card("AAPL", "1d")
        clock.now += 300
        third = client.fetch_action_card("aapl", "1d")
    finally:
        http.close()

    assert len(seen) == 1
    assert first.cached is False
    assert second.cached is True and third.cached is True
    # Provenance is the ORIGINAL retrieval, not the cache read.
    assert second.retrieved_at == first.retrieved_at
    assert second.telemetry_fields()["signa_v2_cached"] is True
    assert first.telemetry_fields()["signa_v2_cached"] is False


def test_v2_client_refetches_after_ttl_and_keys_cache_by_symbol_and_timeframe() -> None:
    seen: list[httpx.Request] = []
    clock = _Clock()
    http = _counting_client(seen)
    try:
        client = SignaV2Client(api_key="k", client=http, cache_ttl_seconds=1800, clock=clock)
        client.fetch_action_card("AAPL", "1d")
        client.fetch_action_card("AAPL", "1h")  # different timeframe -> separate entry
        client.fetch_action_card("NVDA", "1d")  # different symbol -> separate entry
        assert len(seen) == 3
        clock.now += 1800  # exactly TTL -> expired
        refreshed = client.fetch_action_card("AAPL", "1d")
    finally:
        http.close()

    assert len(seen) == 4
    assert refreshed.cached is False


def test_v2_client_failure_backoff_prevents_retry_storm_and_expires() -> None:
    seen: list[httpx.Request] = []
    clock = _Clock()
    http = _counting_client(seen, status=500)
    try:
        client = SignaV2Client(
            api_key="failure-cache-test",
            client=http,
            cache_ttl_seconds=1800,
            failure_backoff_seconds=900,
            clock=clock,
        )
        first = client.fetch_action_card("AAPL", "1d")
        clock.now += 300
        second = client.fetch_action_card("AAPL", "1d")
        clock.now += 601
        third = client.fetch_action_card("AAPL", "1d")
    finally:
        http.close()

    assert first.ok is False and first.error == "http_500"
    assert second.ok is False and second.cached is True
    assert third.ok is False and third.cached is False
    assert len(seen) == 2


def test_v2_client_ttl_zero_disables_cache_and_env_sets_default(monkeypatch) -> None:
    seen: list[httpx.Request] = []
    http = _counting_client(seen)
    try:
        client = SignaV2Client(api_key="k", client=http, cache_ttl_seconds=0)
        client.fetch_action_card("AAPL", "1d")
        client.fetch_action_card("AAPL", "1d")
    finally:
        http.close()
    assert len(seen) == 2

    monkeypatch.delenv("OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS", raising=False)
    assert SignaV2Client(api_key="k").cache_ttl_seconds == 1800.0
    monkeypatch.setenv("OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS", "600")
    assert SignaV2Client(api_key="k").cache_ttl_seconds == 600.0
    monkeypatch.setenv("OPTIONS_SIGNA_V2_CACHE_TTL_SECONDS", "garbage")
    assert SignaV2Client(api_key="k").cache_ttl_seconds == 1800.0


def test_v2_client_reuses_fresh_shared_action_card_snapshot(tmp_path) -> None:
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    retrieved = datetime.now(timezone.utc).isoformat()
    store.record_snapshot(
        endpoint="/api/v1/signals/AAPL",
        symbol="AAPL",
        timeframe="1d",
        params={"symbol": "AAPL", "timeframe": "1d"},
        retrieved_at=retrieved,
        payload=_card("AAPL", "1d"),
        status="OK",
        http_status=200,
    )
    seen: list[httpx.Request] = []
    http = _counting_client(seen)
    try:
        client = SignaV2Client(
            api_key="snapshot-reuse-test",
            client=http,
            snapshot_store=store,
            cache_ttl_seconds=1800,
        )
        obs = client.fetch_action_card("AAPL", "1d")
    finally:
        http.close()

    assert obs.ok is True
    assert obs.cached is True
    assert obs.symbol == "AAPL"
    assert seen == []


def test_v2_client_failure_backoff_env_default(monkeypatch) -> None:
    monkeypatch.delenv("OPTIONS_SIGNA_V2_FAILURE_BACKOFF_SECONDS", raising=False)
    assert SignaV2Client(api_key="failure-env-test").failure_backoff_seconds == 900.0
    monkeypatch.setenv("OPTIONS_SIGNA_V2_FAILURE_BACKOFF_SECONDS", "1200")
    assert SignaV2Client(api_key="failure-env-test").failure_backoff_seconds == 1200.0


def test_fresh_shared_snapshot_overrides_cached_failure(tmp_path) -> None:
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    seen: list[httpx.Request] = []
    http = _counting_client(seen, status=500)
    try:
        client = SignaV2Client(
            api_key="recovery-test",
            client=http,
            snapshot_store=store,
            failure_backoff_seconds=900,
        )
        failed = client.fetch_action_card("AAPL", "1d")
        store.record_snapshot(
            endpoint="/api/v1/signals/AAPL",
            symbol="AAPL",
            timeframe="1d",
            params={"symbol": "AAPL", "timeframe": "1d"},
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            payload=_card("AAPL", "1d"),
            status="OK",
            http_status=200,
        )
        recovered = client.fetch_action_card("AAPL", "1d")
    finally:
        http.close()

    assert failed.ok is False
    assert recovered.ok is True
    assert recovered.cached is True
    assert len(seen) == 1


def test_discovery_429_blocks_v2_provider_call_across_clients() -> None:
    from sources.signa_discovery import SignaDiscoveryClient
    from sources.signa_request_budget import clear_account_backoff

    base_url = "https://app.getsigna.ai"
    api_key = "cross-client-budget-test"
    clock = _Clock()
    discovery_calls: list[httpx.Request] = []
    v2_calls: list[httpx.Request] = []

    def discovery_handler(request: httpx.Request) -> httpx.Response:
        discovery_calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"}, json={"error": "rate"})

    def v2_handler(request: httpx.Request) -> httpx.Response:
        v2_calls.append(request)
        return httpx.Response(200, json=_card("AAPL", "1d"))

    clear_account_backoff(base_url, api_key)
    discovery_http = httpx.Client(base_url=base_url, transport=httpx.MockTransport(discovery_handler))
    v2_http = httpx.Client(base_url=base_url, transport=httpx.MockTransport(v2_handler))
    try:
        discovery = SignaDiscoveryClient(
            api_key=api_key,
            base_url=base_url,
            client=discovery_http,
            cache_ttl_seconds=0,
            clock=clock,
        )
        v2 = SignaV2Client(
            api_key=api_key,
            base_url=base_url,
            client=v2_http,
            cache_ttl_seconds=0,
            failure_backoff_seconds=0,
            clock=clock,
        )

        limited = discovery.signal_index()
        blocked = v2.fetch_action_card("AAPL", "1d")
    finally:
        discovery_http.close()
        v2_http.close()
        clear_account_backoff(base_url, api_key)

    assert limited.error == "http_429"
    assert blocked.ok is False
    assert blocked.error == "account_backoff_active"
    assert len(discovery_calls) == 1
    assert v2_calls == []
