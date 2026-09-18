"""Canonical portfolio-risk intake tests."""

import pytest

from options_manager.validation.contract_quality_gate import ContractQualityInput
from options_manager.validation.portfolio_risk_gate import (
    AGGREGATE_RISK_BUDGET_UNCONFIGURED,
    AVERAGING_DOWN_REJECTED_CODE,
    PLANNED_RISK_INVALID_CODE,
    PortfolioRiskVerdict,
    check_portfolio_risk_intake,
    planned_risk_from_premium_stop,
)
from options_manager.validation.proof_packet import ProofPacket, ProofPacketStatus

# Every PASS expectation states its budget. There is no implicit one.
BUDGET = 1000.0


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
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.candidate_risk == pytest.approx(110.0)
    assert result.projected_capital_deployed == pytest.approx(430.0)


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
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
    assert result.open_position_count == 12
    assert result.projected_open_risk == 650.0


def test_canonical_intake_without_a_budget_blocks_by_name():
    result = check_portfolio_risk_intake(
        {"open_positions": []},
        proof_packet=_proof(),
        contract=_contract(),
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert AGGREGATE_RISK_BUDGET_UNCONFIGURED in result.blocking_reasons
    assert result.candidate_risk == 100.0



def test_planned_risk_formula_uses_executable_entry_fill_and_premium_stop():
    risk, reason = planned_risk_from_premium_stop(
        entry_fill=2.15,
        premium_stop=1.60,
        contracts=2,
        max_trade_risk_dollars=300.0,
    )
    assert reason is None
    assert risk == pytest.approx(110.0)


@pytest.mark.parametrize(
    "entry_fill,premium_stop",
    [
        (2.15, 0.0),
        (2.15, 2.15),
        (2.15, 2.20),
        (float("nan"), 1.60),
        (2.15, float("inf")),
    ],
)
def test_planned_risk_invalid_stop_or_nonfinite_input_fails_closed(entry_fill, premium_stop):
    risk, reason = planned_risk_from_premium_stop(
        entry_fill=entry_fill,
        premium_stop=premium_stop,
        contracts=1,
        max_trade_risk_dollars=300.0,
    )
    assert risk is None
    assert reason is not None
    assert reason.startswith(PLANNED_RISK_INVALID_CODE)


def test_matching_open_position_rejects_averaging_down():
    result = check_portfolio_risk_intake(
        {
            "open_positions": [
                {
                    "ticker": "ORCL",
                    "direction": "CALL",
                    "planned_dollar_risk": 50.0,
                    "capital_deployed": 100.0,
                }
            ]
        },
        proof_packet=_proof(),
        contract=_contract(),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(AVERAGING_DOWN_REJECTED_CODE)
        for reason in result.blocking_reasons
    )


def test_matching_open_order_rejects_averaging_down():
    result = check_portfolio_risk_intake(
        {
            "open_positions": [],
            "open_orders": [{"ticker": "ORCL", "direction": "CALL"}],
        },
        proof_packet=_proof(),
        contract=_contract(),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.BLOCK
    assert any(
        reason.startswith(AVERAGING_DOWN_REJECTED_CODE)
        for reason in result.blocking_reasons
    )


def test_opposite_direction_open_position_does_not_trigger_averaging_guard():
    result = check_portfolio_risk_intake(
        {
            "open_positions": [
                {
                    "ticker": "ORCL",
                    "direction": "PUT",
                    "planned_dollar_risk": 50.0,
                    "capital_deployed": 100.0,
                }
            ]
        },
        proof_packet=_proof(),
        contract=_contract(),
        max_aggregate_open_risk_dollars=BUDGET,
    )
    assert result.verdict == PortfolioRiskVerdict.PASS
