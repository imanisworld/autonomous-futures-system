"""Canonical Phase 1 /options/packet advisory integration tests."""

from __future__ import annotations

from datetime import date, timedelta
import json

import pytest

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
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
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
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
    payload = _payload()
    payload["proof_packet"]["underlying_invalidation"] = ""

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    assert response.json()["verdict"] == "AVOID"
    assert response.json()["actionable"] is False


def test_missing_portfolio_snapshot_is_avoid(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
    payload = _payload()
    del payload["portfolio_risk"]

    response = client.post("/options/packet", json=payload)
    assert response.status_code == 200
    assert response.json()["verdict"] == "AVOID"
    assert response.json()["portfolio_verdict"] == "block"


def test_position_count_is_observed_not_an_api_rejection_gate(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
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


def test_aggregate_risk_over_configured_cap_is_avoid(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
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
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
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
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")
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
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "1000")

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


def test_unconfigured_budget_can_never_return_take(monkeypatch, tmp_path):
    """No env var, otherwise perfect payload: the answer is AVOID, by name."""
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.delenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", raising=False)

    response = client.post("/options/packet", json=_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "AVOID"
    assert body["actionable"] is False
    assert body["portfolio_verdict"] == "block"
    assert any(r.startswith("portfolio risk: aggregate_risk_budget_missing") for r in body["blocking_reasons"])
    assert any("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS" in r for r in body["blocking_reasons"])
    # It is a configuration gap, not a risk verdict.
    assert "risk_too_high" not in body["no_trade_reasons"]


@pytest.mark.parametrize("raw", ("garbage", "one thousand", "1,000", "$1000", "NaN", "nan",
                                 "inf", "+inf", "-inf", "Infinity"))
def test_malformed_or_non_finite_budget_is_invalid_not_missing_and_never_take(
    monkeypatch, tmp_path, raw
):
    """The operator configured *something*; the fix is to correct it, not to set it.
    And none of these may read as an unlimited budget."""
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", raw)

    body = client.post("/options/packet", json=_payload()).json()
    assert body["verdict"] == "AVOID"
    assert body["actionable"] is False
    assert body["portfolio_verdict"] == "block"
    assert any("aggregate_risk_budget_invalid" in r for r in body["blocking_reasons"]), raw
    assert not any("aggregate_risk_budget_missing" in r for r in body["blocking_reasons"]), raw


@pytest.mark.parametrize("raw", ("", "   "))
def test_blank_budget_is_missing_not_invalid(monkeypatch, tmp_path, raw):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", raw)

    body = client.post("/options/packet", json=_payload()).json()
    assert body["verdict"] == "AVOID"
    assert any("aggregate_risk_budget_missing" in r for r in body["blocking_reasons"])
    assert not any("aggregate_risk_budget_invalid" in r for r in body["blocking_reasons"])


def test_zero_or_negative_budget_is_invalid_not_unlimited(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "0")

    body = client.post("/options/packet", json=_payload()).json()
    assert body["verdict"] == "AVOID"
    assert any("aggregate_risk_budget_invalid" in r for r in body["blocking_reasons"])
    assert not any("aggregate_risk_budget_missing" in r for r in body["blocking_reasons"])


def test_negative_budget_is_invalid_not_unlimited(monkeypatch, tmp_path):
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "-500")

    body = client.post("/options/packet", json=_payload()).json()
    assert body["verdict"] == "AVOID"
    assert body["actionable"] is False
    assert any("aggregate_risk_budget_invalid" in r for r in body["blocking_reasons"])


def test_exact_budget_boundary_is_deterministic_at_the_api(monkeypatch, tmp_path):
    """Candidate risk is (2.10 - 1.60) * 100 * 1 = $50. Budget exactly $50 passes; $49.99 blocks."""
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))

    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "50")
    assert client.post("/options/packet", json=_payload()).json()["verdict"] == "TAKE"

    monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", "49.99")
    body = client.post("/options/packet", json=_payload()).json()
    assert body["verdict"] == "AVOID"
    assert any("exceeds cap $49.99" in r for r in body["blocking_reasons"])



# ─── legacy compatibility lane cannot bypass the canonical budget ─────────────


def _legacy_flat_packet() -> dict:
    return {
        "ticker": "BAC",
        "direction": "CALL",
        "entry_price": 60.11,
        "price_target": 62.50,
        "signa_score": 78,
        "signa_grade": "B",
        "signa_bias": "BULLISH",
        "gex_regime": "LOW_PINNING",
        "gex_wall_above": None,
        "gex_wall_below": None,
        "contract_strike": 60.00,
        "contract_expiry": (date.today() + timedelta(days=30)).isoformat(),
        "max_premium": 2.00,
        "max_contracts": 1,
    }


@pytest.mark.parametrize("budget", (None, "1000"))
def test_legacy_flat_packet_is_never_actionable_regardless_of_budget(monkeypatch, tmp_path, budget):
    """The old flat-packet lane is not the advisory authority. With or without a
    configured budget it cannot return TAKE, so it offers no route around the
    canonical requirement."""
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.delenv("OPTIONS_MANAGER_DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("OPTIONS_MANAGER_JOURNAL_DIR", str(tmp_path))
    if budget is None:
        monkeypatch.delenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", raising=False)
    else:
        monkeypatch.setenv("OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS", budget)

    response = client.post("/options/packet", json=_legacy_flat_packet())
    assert response.status_code == 200
    body = response.json()
    assert body["legacy_compatibility"] is True
    assert body["actionable"] is False
    assert "verdict" not in body
    assert body.get("status") != "TAKE"
