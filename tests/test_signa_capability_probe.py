from __future__ import annotations

import ast
from pathlib import Path

import httpx

from scripts.signa_capability_probe import (
    build_probe_requests,
    classify_http_status,
    probe_signa_capabilities,
    response_shape,
)


def test_probe_request_allowlist_is_get_only_and_non_execution() -> None:
    requests = build_probe_requests("AAPL", "1d")
    assert requests
    names = {request.name for request in requests}
    assert {
        "health",
        "quote",
        "signal_current",
        "signal_legacy",
        "analysis",
        "earnings",
        "options_flow",
        "darkpool_symbol",
        "darkpool_prints",
        "market_tide",
        "political_trades",
        "congress_raw",
        "congress_aggregate",
    }.issubset(names)
    for request in requests:
        assert request.path.startswith("/api/")
        assert "/broker/" not in request.path
        assert "execute" not in request.path.lower()
        assert "order" not in request.path.lower()


def test_probe_includes_current_and_legacy_signal_contracts() -> None:
    by_name = {request.name: request for request in build_probe_requests("QQQ", "4h")}
    assert by_name["signal_current"].path == "/api/v1/signals/QQQ"
    assert by_name["signal_current"].params == {"timeframe": "4h"}
    assert by_name["signal_legacy"].path == "/api/v1/signal"
    assert by_name["signal_legacy"].params == {"sym": "QQQ", "timeframe": "4h"}


def test_http_status_classification() -> None:
    assert classify_http_status(200) == "available"
    assert classify_http_status(401) == "auth_failed"
    assert classify_http_status(403) == "forbidden_or_not_entitled"
    assert classify_http_status(404) == "not_found"
    assert classify_http_status(429) == "rate_limited"
    assert classify_http_status(503) == "provider_error"


def test_response_shape_never_copies_provider_values() -> None:
    raw = {
        "success": True,
        "data": {
            "signal": {
                "symbol": "AAPL",
                "confidence": 87,
                "targets": [185.9, 190.0],
            }
        },
    }
    shape = response_shape(raw)
    text = repr(shape)
    assert "AAPL" not in text
    assert "185.9" not in text
    assert "190.0" not in text
    assert shape["success"] == "bool"
    assert shape["data"]["signal"] == {
        "confidence": "int",
        "symbol": "str",
        "targets": "list",
    }


def test_probe_reports_entitlement_without_dumping_raw_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/health":
            return httpx.Response(200, json={"ok": True, "status": "healthy"})
        if path == "/api/v1/signals/AAPL":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "signal": {
                            "symbol": "AAPL",
                            "direction": "LONG",
                            "confidence": 87,
                        }
                    },
                },
            )
        if path == "/api/v1/signal":
            return httpx.Response(404, json={"error": "not found"})
        if path == "/api/options-flow/AAPL":
            return httpx.Response(403, json={"error": "upgrade required"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(
        base_url="https://app.getsigna.ai",
        transport=httpx.MockTransport(handler),
    )
    try:
        report = probe_signa_capabilities(api_key="secret-test-key", client=client)
    finally:
        client.close()

    by_name = {row["name"]: row for row in report["results"]}
    assert by_name["signal_current"]["status"] == "available"
    assert by_name["signal_legacy"]["status"] == "not_found"
    assert by_name["options_flow"]["status"] == "forbidden_or_not_entitled"
    rendered = repr(report)
    assert "secret-test-key" not in rendered
    assert "upgrade required" not in rendered
    assert "LONG" not in rendered
    assert "AAPL" in rendered  # request symbol metadata is expected


def test_probe_module_has_no_trading_path_imports_or_writes() -> None:
    source = Path("scripts/signa_capability_probe.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"execution", "webhook", "risk", "strategy", "options_manager", "options_companion"}
    assert not any(name.split(".")[0] in forbidden for name in imported)
    assert "open(" not in source
    assert ".write(" not in source
    assert ".post(" not in source
    assert ".put(" not in source
    assert ".delete(" not in source
