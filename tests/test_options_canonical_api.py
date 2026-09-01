"""Canonical Phase 1 /options/packet advisory integration tests."""

from __future__ import annotations

from datetime import date, timedelta
import json

from fastapi.testclient import TestClient

from options_manager.app import SECRET_HEADER, app

client = TestClient(app)


def _payload(**overrides) -> dict:
    expiry = (date.today() + timedelta(days=50)).isoformat()
    proof = {
        "ticker": "ORCL",
        "created_at": "2026-09-01T10:00:00-04:00",
        "direction": "CALL",
        "setup_type": "2-1-2 continuation",
        "timeframe": "30m",
        "entry_trigger": "break above prior 30m high",
        "underlying_invalidation": "close below 102",
        "premium_stop": "1.60",
        "target_1": "108",
        "target_2": "112",
        "expiration": expiry,
        "strike": 110.0,
        "premium": 2.10,
        "bid": 2.05,
        "ask": 2.15,
        "spread_percent": 4.8,
        "volume": 800,
        "open_interest": 3000,
        "max_contracts": 1,
        "max_dollar_risk": 100.0,
        "spy_context": "aligned",
        "qqq_context": "aligned",
        "gex_context": "GEX_UNAVAILABLE",
        "signa_context": "observational only",
        "source_references": ["discord-alert-id-123"],
        "status": "triggered",
    }
    contract = {
        "ticker": "ORCL",
        "direction": "CALL",
        "expiration": expiry,
        "strike": 110.0,
        "premium": 2.10,
        "premium_stop": 1.60,
        "bid": 2.05,
        "ask": 2.15,
        "spread_percent": 4.8,
        "volume": 800,
        "open_interest": 3000,
        "dte": 50,
        "max_contracts": 1,
        "max_dollar_risk": 100.0,
        "distance_to_target": 5.0,
        "iv_event_risk": "none",
        "theta_risk": "low",
        "trade_style": "swing",
    }
    payload = {
        "proof_packet": proof,
        "contract_quality": contract,
        "portfolio_risk": {
            "open_positions": [],
            "candidate_correlation_group": "mega_cap_tech",
        },
    }
    payload.update(overrides)
    return payload


def test_canonical_endpoint_returns_take_and_journals(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.delenv("OPTIONS_MANAGER_DISCORD_WEBHOOK_URL", raising=False)

    response = client.post("/options/packet", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "TAKE"
    assert body["proof_valid"] is True
    assert body["portfolio_verdict"] == "pass"
    assert body["actionable"] is True

    journal_files = list(tmp_path.glob("options_journal_*.jsonl"))
    assert len(journal_files) == 1
    record = json.loads(journal_files[0].read_text().splitlines()[-1])
    assert record["record_type"] == "advisory_decision"
    assert record["decision"]["verdict"] == "take"


def test_missing_invalidation_is_advisory_avoid_not_http_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    payload["proof_packet"]["underlying_invalidation"] = ""

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    assert response.json()["verdict"] == "AVOID"
    assert response.json()["actionable"] is False


def test_missing_portfolio_snapshot_is_avoid(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    del payload["portfolio_risk"]

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    assert response.json()["verdict"] == "AVOID"
    assert response.json()["portfolio_verdict"] == "block"


def test_position_count_is_observed_not_an_api_rejection_gate(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    payload["portfolio_risk"]["open_positions"] = [
        {
            "ticker": f"T{i}",
            "direction": "CALL",
            "planned_dollar_risk": 40.0,
            "capital_deployed": 150.0,
            "correlation_group": "",
        }
        for i in range(12)
    ]

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    assert response.json()["verdict"] == "TAKE"


def test_aggregate_risk_over_cap_is_avoid(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    payload["portfolio_risk"]["open_positions"] = [
        {
            "ticker": "MSFT",
            "direction": "CALL",
            "planned_dollar_risk": 980.0,
            "capital_deployed": 1500.0,
            "correlation_group": "mega_cap_tech",
        }
    ]

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "AVOID"
    assert any("aggregate open risk" in reason for reason in body["blocking_reasons"])


def test_proof_contract_mismatch_fails_closed(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    payload["contract_quality"]["strike"] = 115.0

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "AVOID"
    assert any("proof/contract mismatch for strike" in reason for reason in body["blocking_reasons"])


def test_numeric_premium_stop_is_required_for_canonical_intake(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    payload = _payload()
    del payload["contract_quality"]["premium_stop"]

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "AVOID"
    assert any("numeric premium_stop" in reason for reason in body["blocking_reasons"])


def test_endpoint_still_requires_secret_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("OPTIONS_MANAGER_INGEST_SECRET", "s3cr3t")
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))

    response = client.post("/options/packet", json=_payload())
    assert response.status_code == 401

    response = client.post(
        "/options/packet",
        json=_payload(),
        headers={SECRET_HEADER: "s3cr3t"},
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "TAKE"


def test_invalid_json_stays_http_400(monkeypatch):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    response = client.post(
        "/options/packet",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
