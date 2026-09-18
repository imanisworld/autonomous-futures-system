from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from options_manager.config import OptionsManagerConfig
from options_manager.contract_quality import ContractMarketSnapshot, ContractQualityResult
from options_manager.fill_stress import (
    FillStressPolicy,
    simulate_round_trip_slippage_stress,
    validate_fill_stress_policy,
)
from options_manager.models import OptionTradePacket
from options_manager.paper_sim import simulate_round_trip
from options_manager.risk_gate import RiskGateResult


def _packet():
    return OptionTradePacket(
        ticker="SPY",
        direction="CALL",
        entry_price=550.0,
        price_target=560.0,
        signa_score=80,
        signa_grade="A",
        signa_bias="BULLISH",
        gex_regime="NEUTRAL",
        gex_wall_above=None,
        gex_wall_below=None,
        contract_strike=550.0,
        contract_expiry=date(2026, 11, 20),
        max_premium=10.0,
        max_contracts=1,
        account_tag="stress_fixture",
        source="frozen_stress_fixture",
        created_at=datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc),
        status="PENDING",
        rejection_reason=None,
    )


def _snapshot(*, bid, ask):
    return ContractMarketSnapshot(
        ticker="SPY",
        contract_symbol="SPY261120C00550000",
        bid=bid,
        ask=ask,
        last=(bid + ask) / 2 if bid is not None and ask is not None else None,
        volume=500,
        open_interest=1500,
        implied_volatility=0.25,
        delta=0.5,
        theta=-0.02,
        underlying_price=550.0,
        quote_timestamp=datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc),
        provider="fixture",
        is_snapshot_complete=True,
    )


def _risk():
    return RiskGateResult(
        approved=True,
        status="APPROVED",
        failed_rule=None,
        reason="",
        warnings=[],
    )


def _quality():
    return ContractQualityResult(
        approved=True,
        status="APPROVED",
        failed_rule=None,
        reason="",
        warnings=[],
    )


def _config(**overrides):
    return replace(
        OptionsManagerConfig(),
        paper_sim_require_approved_risk=False,
        paper_sim_require_approved_quality=False,
        **overrides,
    )


def test_base_and_stress_reuse_exact_canonical_fill_consumer():
    policy = FillStressPolicy(
        base_slippage_percent=0.5,
        stress_slippage_percent=1.0,
        per_contract_fee=0.65,
    )
    config = _config()
    entry = _snapshot(bid=4.8, ask=5.0)
    exit_quote = _snapshot(bid=5.8, ask=6.0)

    result = simulate_round_trip_slippage_stress(
        _packet(), entry, exit_quote, _risk(), _quality(), config, policy=policy
    )

    expected_base = simulate_round_trip(
        _packet(),
        entry,
        exit_quote,
        _risk(),
        _quality(),
        replace(config, paper_sim_slippage_percent=0.5, paper_sim_per_contract_fee=0.65),
    )
    expected_stress = simulate_round_trip(
        _packet(),
        entry,
        exit_quote,
        _risk(),
        _quality(),
        replace(config, paper_sim_slippage_percent=1.0, paper_sim_per_contract_fee=0.65),
    )

    assert result.base == expected_base
    assert result.stress == expected_stress
    assert result.stress.simulated_entry_price > result.base.simulated_entry_price
    assert result.stress.simulated_exit_price < result.base.simulated_exit_price
    assert result.stress.simulated_net_pnl < result.base.simulated_net_pnl
    assert config.paper_sim_slippage_percent == 0.0
    assert config.paper_sim_per_contract_fee == 0.0


def test_missing_executable_quote_blocks_both_scenarios_identically():
    result = simulate_round_trip_slippage_stress(
        _packet(),
        _snapshot(bid=4.8, ask=None),
        _snapshot(bid=5.8, ask=6.0),
        _risk(),
        _quality(),
        _config(),
        policy=FillStressPolicy(
            base_slippage_percent=0.5,
            stress_slippage_percent=1.0,
            per_contract_fee=0.65,
        ),
    )
    assert result.base.status == "DATA_BLOCKED"
    assert result.stress.status == "DATA_BLOCKED"
    assert result.base.failed_stage == result.stress.failed_stage == "entry_snapshot"


@pytest.mark.parametrize(
    "policy",
    [
        FillStressPolicy(base_slippage_percent=-0.1, stress_slippage_percent=1.0, per_contract_fee=0.65),
        FillStressPolicy(base_slippage_percent=0.5, stress_slippage_percent=0.5, per_contract_fee=0.65),
        FillStressPolicy(base_slippage_percent=1.0, stress_slippage_percent=0.5, per_contract_fee=0.65),
        FillStressPolicy(base_slippage_percent=0.5, stress_slippage_percent=float("nan"), per_contract_fee=0.65),
        FillStressPolicy(base_slippage_percent=0.5, stress_slippage_percent=1.0, per_contract_fee=-0.01),
    ],
)
def test_invalid_stress_policy_fails_closed(policy):
    with pytest.raises(ValueError):
        validate_fill_stress_policy(policy)


@pytest.mark.parametrize(
    "entry_mode,exit_mode",
    [("MID", "BID"), ("ASK", "MID"), ("LAST", "LAST")],
)
def test_stress_qualification_refuses_non_executable_fill_basis(entry_mode, exit_mode):
    with pytest.raises(ValueError):
        simulate_round_trip_slippage_stress(
            _packet(),
            _snapshot(bid=4.8, ask=5.0),
            _snapshot(bid=5.8, ask=6.0),
            _risk(),
            _quality(),
            _config(paper_sim_entry_fill=entry_mode, paper_sim_exit_fill=exit_mode),
            policy=FillStressPolicy(
                base_slippage_percent=0.5,
                stress_slippage_percent=1.0,
                per_contract_fee=0.65,
            ),
        )
