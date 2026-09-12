from __future__ import annotations

import ast
from pathlib import Path

import httpx

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
