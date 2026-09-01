"""Canonical portfolio-risk intake tests."""

from options_manager.validation.contract_quality_gate import ContractQualityInput
from options_manager.validation.portfolio_risk_gate import (
    PortfolioRiskVerdict,
    check_portfolio_risk_intake,
)
from options_manager.validation.proof_packet import ProofPacket, ProofPacketStatus


def _proof(**overrides):
    data = dict(
        ticker="ORCL",
        created_at="2026-09-01T10:00:00-04:00",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        entry_trigger="break above prior 30m high",
        underlying_invalidation="close below 102",
        premium_stop="1.60",
        target_1="108",
        target_2="112",
        expiration="2026-10-23",
        strike=110.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        max_contracts=2,
        max_dollar_risk=150.0,
        spy_context="aligned",
        qqq_context="aligned",
        gex_context="GEX_UNAVAILABLE",
        signa_context="observational only",
        source_references=("alert-1",),
        status=ProofPacketStatus.TRIGGERED,
    )
    data.update(overrides)
    return ProofPacket(**data)


def _contract(**overrides):
    data = dict(
        ticker="ORCL",
        direction="CALL",
        expiration="2026-10-23",
        strike=110.0,
        premium=2.10,
        bid=2.05,
        ask=2.15,
        spread_percent=4.8,
        volume=800,
        open_interest=3000,
        dte=52,
        max_contracts=2,
        max_dollar_risk=150.0,
        distance_to_target=5.0,
        iv_event_risk="none",
        theta_risk="low",
        premium_stop=1.60,
        trade_style="swing",
    )
    data.update(overrides)
    return ContractQualityInput(**data)


def test_candidate_risk_and_capital_are_derived_not_caller_supplied():
    result = check_portfolio_risk_intake(
        {"open_positions": [], "candidate_correlation_group": "tech"},
        proof_packet=_proof(),
        contract=_contract(),
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.candidate_risk == 100.0
    assert result.projected_capital_deployed == 420.0


def test_missing_flat_snapshot_does_not_silently_assume_zero_positions():
    result = check_portfolio_risk_intake(
        {},
        proof_packet=_proof(),
        contract=_contract(),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("open_positions" in reason for reason in result.blocking_reasons)


def test_missing_numeric_premium_stop_blocks():
    result = check_portfolio_risk_intake(
        {"open_positions": []},
        proof_packet=_proof(),
        contract=_contract(premium_stop=None),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("numeric premium_stop" in reason for reason in result.blocking_reasons)


def test_proof_contract_mismatch_blocks():
    result = check_portfolio_risk_intake(
        {"open_positions": []},
        proof_packet=_proof(),
        contract=_contract(strike=115.0),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any("mismatch for strike" in reason for reason in result.blocking_reasons)


def test_many_positions_are_allowed_when_projected_risk_is_under_budget():
    open_positions = [
        {
            "ticker": f"T{i}",
            "direction": "CALL",
            "planned_dollar_risk": 50.0,
            "capital_deployed": 100.0,
        }
        for i in range(12)
    ]
    result = check_portfolio_risk_intake(
        {"open_positions": open_positions},
        proof_packet=_proof(max_contracts=1, max_dollar_risk=100.0),
        contract=_contract(max_contracts=1, max_dollar_risk=100.0),
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.open_position_count == 12
    assert result.projected_open_risk == 650.0
