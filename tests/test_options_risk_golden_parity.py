"""Golden replay/forward parity for canonical options portfolio-risk intake.

The fixtures are frozen JSON bytes. "Replay" and "forward" independently
parse the same bytes into the same canonical ProofPacket / ContractQualityInput
boundary, then run the production risk intake. No provider, broker, env, or I/O
is involved beyond the test fixture bytes themselves.
"""

from __future__ import annotations

import json

from options_manager.validation.contract_quality_gate import ContractQualityInput
from options_manager.validation.portfolio_risk_gate import (
    AVERAGING_DOWN_REJECTED_CODE,
    PLANNED_RISK_EXCEEDS_CAP_CODE,
    PLANNED_RISK_INVALID_CODE,
    PortfolioRiskResult,
    PortfolioRiskVerdict,
    check_portfolio_risk_intake,
)
from options_manager.validation.proof_packet import ProofPacket, ProofPacketStatus


def _base_fixture() -> dict:
    return {
        "budget": 1000.0,
        "proof": {
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
            "expiration": "2026-10-23",
            "strike": 110.0,
            "premium": 2.10,
            "bid": 2.05,
            "ask": 2.15,
            "spread_percent": 4.8,
            "volume": 800,
            "open_interest": 3000,
            "max_contracts": 2,
            "max_dollar_risk": 150.0,
            "spy_context": "aligned",
            "qqq_context": "aligned",
            "gex_context": "GEX_UNAVAILABLE",
            "signa_context": "observational only",
            "source_references": ["alert-1"],
            "status": "triggered",
        },
        "contract": {
            "ticker": "ORCL",
            "direction": "CALL",
            "expiration": "2026-10-23",
            "strike": 110.0,
            "premium": 2.10,
            "bid": 2.05,
            "ask": 2.15,

            "spread_percent": 4.8,
            "volume": 800,
            "open_interest": 3000,
            "dte": 52,
            "max_contracts": 2,
            "max_dollar_risk": 150.0,
            "distance_to_target": 5.0,
            "iv_event_risk": "none",
            "theta_risk": "low",
            "premium_stop": 1.60,
            "trade_style": "swing",
        },
        "portfolio": {
            "open_positions": [],
            "open_orders": [],
            "candidate_correlation_group": "tech",
        },
    }


def _freeze(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _run_frozen(payload: bytes) -> PortfolioRiskResult:
    raw = json.loads(payload)
    proof_raw = dict(raw["proof"])
    proof_raw["source_references"] = tuple(proof_raw["source_references"])
    proof_raw["status"] = ProofPacketStatus(proof_raw["status"])
    proof = ProofPacket(**proof_raw)
    contract = ContractQualityInput(**raw["contract"])
    return check_portfolio_risk_intake(
        raw["portfolio"],
        proof_packet=proof,
        contract=contract,
        max_aggregate_open_risk_dollars=float(raw["budget"]),
    )


def _decision_projection(result: PortfolioRiskResult) -> bytes:
    projection = {
        "verdict": result.verdict.value,
        "open_position_count": result.open_position_count,
        "aggregate_open_risk": result.aggregate_open_risk,
        "candidate_risk": result.candidate_risk,
        "projected_open_risk": result.projected_open_risk,
        "aggregate_capital_deployed": result.aggregate_capital_deployed,
        "projected_capital_deployed": result.projected_capital_deployed,
        "correlation_risk": [list(item) for item in result.correlation_risk],
        "blocking_reasons": list(result.blocking_reasons),
    }
    return _freeze(projection)


def _assert_replay_forward_parity(payload: bytes) -> PortfolioRiskResult:
    replay = _run_frozen(payload)
    forward = _run_frozen(payload)
    assert replay == forward
    assert _decision_projection(replay) == _decision_projection(forward)
    return replay


def test_valid_premium_stop_risk_has_golden_parity():
    payload = _freeze(_base_fixture())
    result = _assert_replay_forward_parity(payload)

    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.candidate_risk == 100.0
    assert result.projected_open_risk == 100.0
    assert result.blocking_reasons == ()


def test_invalid_premium_stop_has_same_fail_closed_reason():
    fixture = _base_fixture()
    fixture["proof"]["premium_stop"] = "2.20"
    fixture["contract"]["premium_stop"] = 2.20
    result = _assert_replay_forward_parity(_freeze(fixture))

    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(PLANNED_RISK_INVALID_CODE)
        for reason in result.blocking_reasons
    )


def test_per_trade_cap_breach_has_same_reason_code():
    fixture = _base_fixture()
    fixture["proof"]["premium_stop"] = "0.10"
    fixture["contract"]["premium_stop"] = 0.10
    result = _assert_replay_forward_parity(_freeze(fixture))

    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(PLANNED_RISK_EXCEEDS_CAP_CODE)
        for reason in result.blocking_reasons
    )


def test_aggregate_budget_breach_has_golden_parity():
    fixture = _base_fixture()
    fixture["portfolio"]["open_positions"] = [
        {
            "ticker": "MSFT",
            "direction": "CALL",
            "planned_dollar_risk": 950.0,
            "capital_deployed": 400.0,
        }
    ]
    result = _assert_replay_forward_parity(_freeze(fixture))

    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert result.projected_open_risk == 1050.0
    assert any(
        reason.startswith("projected aggregate open risk")
        for reason in result.blocking_reasons
    )


def test_matching_open_position_averaging_guard_has_golden_parity():
    fixture = _base_fixture()
    fixture["portfolio"]["open_positions"] = [
        {
            "ticker": "ORCL",
            "direction": "CALL",
            "planned_dollar_risk": 50.0,
            "capital_deployed": 100.0,
        }
    ]
    result = _assert_replay_forward_parity(_freeze(fixture))

    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(AVERAGING_DOWN_REJECTED_CODE)
        for reason in result.blocking_reasons
    )


def test_matching_open_order_averaging_guard_has_golden_parity():
    fixture = _base_fixture()
    fixture["portfolio"]["open_orders"] = [
        {"ticker": "ORCL", "direction": "CALL"}
    ]
    result = _assert_replay_forward_parity(_freeze(fixture))

    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(AVERAGING_DOWN_REJECTED_CODE)
        for reason in result.blocking_reasons
    )


def test_fixture_serialization_is_byte_stable():
    first = _freeze(_base_fixture())
    second = _freeze(_base_fixture())
    assert first == second
