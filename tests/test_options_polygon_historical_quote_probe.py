from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import httpx

from scripts.options_polygon_historical_quote_probe import (
    normalize_option_ticker,
    probe_historical_option_quotes,
)

TEST_KEY = "secret-test-key-never-print"


def _client(status_code: int, payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/quotes/O:SPY261120C00770000"
        assert request.url.params["timestamp"] == "2026-09-18"
        assert request.url.params["limit"] == "1"
        assert request.headers["Authorization"] == f"Bearer {TEST_KEY}"
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_normalizes_polygon_option_prefix():
    assert normalize_option_ticker("spy261120c00770000") == "O:SPY261120C00770000"
    assert normalize_option_ticker("O:SPY261120C00770000") == "O:SPY261120C00770000"


def test_missing_key_fails_closed():
    result = probe_historical_option_quotes(
        option_ticker="SPY261120C00770000",
        date="2026-09-18",
        api_key="",
    )
    assert result.verdict == "BLOCKED"
    assert result.configured is False
    assert result.reason == "POLYGON_API_KEY not configured"


def test_not_authorized_is_explicit_entitlement_block():
    with _client(
        403,
        {
            "status": "NOT_AUTHORIZED",
            "message": "You are not entitled to this data.",
        },
    ) as client:
        result = probe_historical_option_quotes(
            option_ticker="SPY261120C00770000",
            date="2026-09-18",
            api_key=TEST_KEY,
            client=client,
        )
    assert result.verdict == "BLOCKED"
    assert result.configured is True
    assert result.http_status == 403
    assert result.api_status == "NOT_AUTHORIZED"
    assert result.reason == "historical_options_quotes_not_entitled"
    assert TEST_KEY not in json.dumps(result.__dict__)


def test_success_proves_entitlement_even_when_specific_query_has_zero_rows():
    with _client(200, {"status": "OK", "results": []}) as client:
        result = probe_historical_option_quotes(
            option_ticker="SPY261120C00770000",
            date="2026-09-18",
            api_key=TEST_KEY,
            client=client,
        )
    assert result.verdict == "ENTITLED"
    assert result.http_status == 200
    assert result.api_status == "OK"
    assert result.result_count == 0


def test_success_counts_returned_quote_rows():
    with _client(
        200,
        {
            "status": "OK",
            "results": [
                {"bid_price": 4.8, "ask_price": 5.0, "sip_timestamp": 1},
            ],
        },
    ) as client:
        result = probe_historical_option_quotes(
            option_ticker="SPY261120C00770000",
            date="2026-09-18",
            api_key=TEST_KEY,
            client=client,
        )
    assert result.verdict == "ENTITLED"
    assert result.result_count == 1


def test_unauthorized_key_is_not_misreported_as_entitlement():
    with _client(401, {"status": "ERROR", "error": "API Key was not provided"}) as client:
        result = probe_historical_option_quotes(
            option_ticker="SPY261120C00770000",
            date="2026-09-18",
            api_key=TEST_KEY,
            client=client,
        )
    assert result.verdict == "BLOCKED"
    assert result.reason == "polygon_auth_failed"


def test_direct_help_works_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/options_polygon_historical_quote_probe.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "historical option-quote entitlement probe" in result.stdout
