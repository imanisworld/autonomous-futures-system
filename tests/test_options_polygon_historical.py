"""
tests/test_options_polygon_historical.py

options_manager/adapters/polygon_historical.py tests. Proves the
read-only Polygon historical stock-candle adapter fails closed without
an API key, builds the correct request for a real target (AMD 5-minute
bars, 2026-05-27), maps a mocked response into AdapterCandle without
fabricating missing fields, never leaks the API key, and never touches
the scanner, broker/execution/config, or the filesystem. No real network
call is made anywhere in this file -- every HTTP call is faked via
httpx.MockTransport.
"""

from __future__ import annotations

import ast
from pathlib import Path

import httpx
import pytest

import options_manager.adapters.polygon_historical as polygon_historical_module
from options_manager.adapters.base import AdapterCandle
from options_manager.adapters.polygon_historical import (
    PolygonHistoricalClient,
    PolygonHistoricalError,
    fetch_stock_aggregates,
)

_SCANNED_MODULES = (polygon_historical_module,)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "options_manager.scanner",
    "options_manager.strategies",
    "execution",
    "webhook",
    "alert_ranker",
    "options_companion",
    "risk_engine",
    "robin_stocks",
    "ib_insync",
    "ibapi",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
)

_TEST_KEY = "test-polygon-key-do-not-leak"


def _module_source() -> str:
    return Path(polygon_historical_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _amd_2026_05_27_payload():
    return {
        "ticker": "AMD",
        "results": [
            {"t": 1780000000000, "o": 190.0, "h": 191.5, "l": 189.8, "c": 191.0, "v": 12000},
            {"t": 1780000300000, "o": 191.0, "h": 192.0, "l": 190.5, "c": 191.8, "v": 9000},
        ],
        "status": "OK",
    }


# --- 1. missing POLYGON_API_KEY fails closed --------------------------------------------------------


def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    client = PolygonHistoricalClient()
    assert client.configured is False
    with pytest.raises(PolygonHistoricalError):
        client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")


def test_module_level_wrapper_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    with pytest.raises(PolygonHistoricalError):
        fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")


# --- 2. request URL/params for AMD 5-minute 2026-05-27 are correct -----------------------------------


def test_request_url_and_params_for_amd_5min_are_correct():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        captured["auth_header"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_amd_2026_05_27_payload())

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")

    assert captured["path"] == "/v2/aggs/ticker/AMD/range/5/minute/2026-05-27/2026-05-27"
    assert captured["params"]["adjusted"] == "true"
    assert captured["params"]["sort"] == "asc"
    assert captured["params"]["limit"] == "5000"
    assert captured["auth_header"] == f"Bearer {_TEST_KEY}"


def test_module_level_wrapper_delegates_to_supplied_client():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json=_amd_2026_05_27_payload())

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    candles = fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute", client=client)
    assert captured["path"] == "/v2/aggs/ticker/AMD/range/5/minute/2026-05-27/2026-05-27"
    assert len(candles) == 2


# --- 3. mocked response maps timestamp/OHLCV into AdapterCandle -------------------------------------


def test_mocked_response_maps_into_adapter_candle():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_amd_2026_05_27_payload())

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    candles = client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")

    assert len(candles) == 2
    assert all(isinstance(c, AdapterCandle) for c in candles)
    first = candles[0]
    assert first.open == 190.0
    assert first.high == 191.5
    assert first.low == 189.8
    assert first.close == 191.0
    assert first.volume == 12000
    assert first.timestamp  # non-empty ISO string derived from epoch ms


# --- 4. missing OHLC fields are rejected/skipped without fabrication --------------------------------


def test_malformed_bar_is_skipped_not_fabricated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"t": 1780000000000, "o": 190.0, "h": 191.5, "l": 189.8, "c": 191.0, "v": 12000},
                    {"t": 1780000300000, "o": 191.0, "l": 190.5, "c": 191.8, "v": 9000},  # missing "h"
                    {"t": 1780000600000, "o": "not_a_number", "h": 1, "l": 1, "c": 1},
                ]
            },
        )

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    candles = client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")
    # Only the one fully well-formed bar survives; the other two are skipped,
    # never patched with a guessed/fabricated value.
    assert len(candles) == 1
    assert candles[0].open == 190.0


def test_missing_volume_maps_to_none_not_zero():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"t": 1780000000000, "o": 190.0, "h": 191.5, "l": 189.8, "c": 191.0}]},
        )

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    candles = client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")
    assert len(candles) == 1
    assert candles[0].volume is None


def test_empty_results_returns_empty_list():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler))
    candles = client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")
    assert candles == []


# --- 5. API key is never present in repr/log/error output -------------------------------------------


def test_api_key_never_appears_in_repr():
    client = PolygonHistoricalClient(api_key=_TEST_KEY)
    assert _TEST_KEY not in repr(client)
    assert _TEST_KEY not in str(client)


def test_api_key_never_appears_in_error_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = PolygonHistoricalClient(api_key=_TEST_KEY, client=_mock_client(handler), max_retries=0)
    with pytest.raises(PolygonHistoricalError) as exc_info:
        client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")
    assert _TEST_KEY not in str(exc_info.value)


def test_api_key_never_appears_in_missing_key_error():
    client = PolygonHistoricalClient(api_key="")
    with pytest.raises(PolygonHistoricalError) as exc_info:
        client.fetch_stock_aggregates("AMD", "2026-05-27", "2026-05-27", 5, "minute")
    assert "POLYGON_API_KEY" in str(exc_info.value)
    assert _TEST_KEY not in str(exc_info.value)


# --- 6. no scanner imports ---------------------------------------------------------------------------


def test_no_scanner_or_strategy_imports():
    imported = _imported_modules(polygon_historical_module)
    assert not any("options_manager.scanner" in name for name in imported)
    assert not any("options_manager.strategies" in name for name in imported)


# --- 7. no broker/execution/config imports ------------------------------------------------------------


def test_no_forbidden_import_fragments():
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        for name in imported:
            for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
                assert forbidden not in name, f"{module.__name__} must not import {name!r}"


def test_no_cross_boundary_imports_outside_options_manager_or_stdlib():
    allowed_stdlib_and_third_party = (
        "__future__",
        "os",
        "time",
        "datetime",
        "typing",
        "httpx",
    )
    for module in _SCANNED_MODULES:
        imported = _imported_modules(module)
        outside = [
            name
            for name in imported
            if not name.startswith("options_manager")
            and name not in allowed_stdlib_and_third_party
        ]
        assert not outside, f"{module.__name__} has an unexpected import: {outside}"


# --- 8. no order/action verbs -------------------------------------------------------------------------


def test_no_order_action_verbs():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_does_not_mutate_live_options_flag():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        assert "LIVE_OPTIONS_TRADING_ENABLED" not in source


# --- 9. no file I/O -------------------------------------------------------------------------------------


def test_module_does_not_read_or_write_files():
    for module in _SCANNED_MODULES:
        source = Path(module.__file__).read_text()
        for forbidden in ("open(", ".write(", ".write_text(", ".write_bytes(", ".read_text("):
            assert forbidden not in source, f"{module.__name__} must not contain {forbidden!r}"


def test_module_never_calls_options_chain_or_quote_endpoints():
    # Scoped to stock aggregates only -- proves no options-chain-shaped
    # path string exists anywhere in this module.
    source = _module_source().lower()
    for forbidden in ("/v3/reference/options", "optionchain", "options/snapshot", "websocket"):
        assert forbidden not in source, f"module must not contain {forbidden!r}"
