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
