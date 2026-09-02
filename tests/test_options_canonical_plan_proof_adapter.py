"""Canonical proof bridge tests for the Phase-1 options thesis manager.

These tests prove a thesis cannot become actionable by combining unrelated or
caller-asserted booleans. Mechanical setup, structural targets, market context,
contract facts, canonical proof/contract intake, and portfolio risk must all
reconcile. No network, broker, alert, storage, or execution path is exercised.
"""

from __future__ import annotations

import ast
import math
from dataclasses import replace
from pathlib import Path

import options_manager.plans.proof_adapter as proof_adapter_module
from options_manager.context import MarketContextInputs
from options_manager.contracts import ContractConstraintsInputs
from options_manager.levels import LevelFinderInputs
from options_manager.plans import (
    SignaObservation,
    StructuralLevel,
    update_trade_thesis_from_authorities,
)
from options_manager.scanner import WatchlistRow, scan_watchlist_strat_212
from options_manager.strategies import Strat212Bars

MAX_TRADE_RISK = 300.0
MAX_AGGREGATE_RISK = 1000.0


def _bars() -> Strat212Bars:
    return Strat212Bars(
        two_bars_back_type="two_up",
        two_bars_back_high=100.0,
        two_bars_back_low=95.0,
        previous_high=99.0,
        previous_low=96.0,
        current_high=100.5,
        current_low=97.0,
    )


def _levels() -> tuple[StructuralLevel, ...]:
    return (
        StructuralLevel(price=103.0, side="RESISTANCE", source="PDH"),
        StructuralLevel(price=106.0, side="RESISTANCE", source="WEEKLY_HIGH"),
    )


def _market_context(**overrides) -> MarketContextInputs:
    fields = dict(
        direction="CALL",
        ticker="AAPL",
        underlying_price=100.0,
        spy_trend="bullish",
        qqq_trend="bullish",
        spy_above_flip=True,
        qqq_above_flip=True,
        gex_regime=None,
        price_above_gex_flip=None,
        signa_direction=None,
        signa_grade=None,
        signa_score=None,
        higher_timeframe_alignment="aligned",
        gap_direction="none",
        event_risk="none",
    )
    fields.update(overrides)
    return MarketContextInputs(**fields)


def _scanner_contract(**overrides) -> ContractConstraintsInputs:
    fields = dict(
        direction="CALL",
        ticker="AAPL",
        expiration="2026-10-16",
        dte=45,
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        spread_percent=5.0,
        volume=1000,
        open_interest=2000,
        delta=0.55,
        theta=-0.03,
        iv=0.40,
        max_premium=3.0,
        max_spread_percent=10.0,
        min_volume=100,
        min_open_interest=500,
        min_dte=14,
        max_theta_abs=0.10,
        earnings_risk="NONE",
        event_risk="NONE",
    )
    fields.update(overrides)
    return ContractConstraintsInputs(**fields)


def _row(**overrides) -> WatchlistRow:
    fields = dict(
        ticker="AAPL",
        timestamp="2026-09-01T10:30:00-04:00",
        direction="CALL",
        bars=_bars(),
        timeframe="30m",
        entry_trigger=99.0,
        underlying_invalidation=96.0,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=99.0,
            underlying_invalidation=96.0,
            resistance_levels=(103.0, 106.0),
        ),
        market_context_inputs=_market_context(),
        contract_constraints_inputs=_scanner_contract(),
    )
    fields.update(overrides)
    return WatchlistRow(**fields)


def _payload(**proof_overrides):
    proof = dict(
        ticker="AAPL",
        created_at="2026-09-01T10:30:00-04:00",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        entry_trigger="99.0",
        underlying_invalidation="96.0",
        premium_stop="1.60",
        target_1="103.0",
        target_2="106.0",
        expiration="2026-10-16",
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        spread_percent=5.0,
        volume=1000,
        open_interest=2000,
        max_contracts=1,
        max_dollar_risk=40.0,
        spy_context="SPY bullish / above flip",
        qqq_context="QQQ bullish / above flip",
        gex_context="unavailable; not fabricated",
        signa_context="observational only",
        source_references=("scan:abc123", "bars:abc123"),
        status="triggered",
    )
    proof.update(proof_overrides)
    return {
        "proof_packet": proof,
        "contract_quality": {
            "ticker": "AAPL",
            "direction": "CALL",
            "expiration": "2026-10-16",
            "dte": 45,
            "strike": 100.0,
            "premium": 2.0,
            "bid": 1.95,
            "ask": 2.05,
            "spread_percent": 5.0,
            "volume": 1000,
            "open_interest": 2000,
            "max_contracts": 1,
            "max_dollar_risk": 40.0,
            "distance_to_target": 4.0,
            "iv_event_risk": "none",
            "theta_risk": "none",
            "premium_stop": 1.60,
            "trade_style": "swing",
        },
        "portfolio_risk": {
            "open_positions": [],
            "candidate_correlation_group": "mega_cap_tech",
        },
    }


def _scan(row: WatchlistRow):
    result = scan_watchlist_strat_212([row]).results[0]
    assert result.scan_status == "TRIGGERED", result
    return result


def _evaluate(row=None, payload=None, levels=None, **kwargs):
    row = row or _row()
    payload = payload or _payload()
    levels = levels or _levels()
    return update_trade_thesis_from_authorities(
        None,
        row=row,
        scan_result=_scan(row),
        canonical_payload=payload,
        structural_levels=levels,
        max_trade_risk_dollars=kwargs.pop("max_trade_risk_dollars", MAX_TRADE_RISK),
        max_aggregate_open_risk_dollars=kwargs.pop(
            "max_aggregate_open_risk_dollars", MAX_AGGREGATE_RISK
        ),
        **kwargs,
    )


def test_matching_authorities_produce_actionable_triggered_thesis():
    result = _evaluate()
    assert result.valid is True
    assert result.blocking_reasons == ()
    assert result.plan_update is not None
    snapshot = result.plan_update.snapshot
    assert snapshot.actionable is True
    assert snapshot.status.value == "triggered"
    assert snapshot.entry_trigger == 99.0
    assert snapshot.underlying_invalidation == 96.0
    assert snapshot.target_1 == 103.0
    assert snapshot.target_2 == 106.0
    assert snapshot.target_1_source == "PDH"
    assert snapshot.target_2_source == "WEEKLY_HIGH"
    assert snapshot.source_references == ("scan:abc123", "bars:abc123")
    assert snapshot.contract_plan is not None
    assert snapshot.contract_plan.expiration == "2026-10-16"
    assert snapshot.contract_plan.strike == 100.0
    assert snapshot.contract_plan.premium == 2.0
    assert snapshot.contract_plan.premium_stop == 1.60
    assert snapshot.contract_plan.max_contracts == 1
    assert snapshot.risk_plan is not None
    assert snapshot.risk_plan.planned_dollar_risk == 40.0
    assert snapshot.risk_plan.capital_deployed == 200.0
    assert snapshot.risk_plan.max_trade_risk_dollars == MAX_TRADE_RISK
    assert snapshot.risk_plan.aggregate_open_risk == 0.0
    assert snapshot.risk_plan.projected_open_risk == 40.0
    assert snapshot.risk_plan.max_aggregate_open_risk_dollars == MAX_AGGREGATE_RISK
    assert snapshot.risk_plan.open_position_count == 0
    assert snapshot.risk_plan.correlation_risk == (("mega_cap_tech", 40.0),)


def test_gex_unavailable_can_still_pass_when_required_market_proof_is_aligned():
    result = _evaluate()
    assert result.valid is True
    assert any("GEX_UNAVAILABLE" in warning for warning in result.warnings)


def test_opposed_signa_is_telemetry_only_and_does_not_change_promotion():
    result = _evaluate(
        signa=SignaObservation(direction="bearish", grade="A", score=99.0)
    )
    assert result.valid is True
    assert result.plan_update.snapshot.actionable is True
    assert result.plan_update.snapshot.signa_event_count == 1


def test_manual_market_context_override_is_rejected_even_if_scanner_triggers():
    row = replace(_row(), market_context=replace(_row().market_context, confirmed=True))
    result = _evaluate(row=row)
    assert result.valid is False
    assert "manual_market_context_override_not_allowed" in result.blocking_reasons


def test_manual_contract_constraint_override_is_rejected():
    row = replace(
        _row(),
        contract_constraints=replace(_row().contract_constraints, constraints_met=True),
    )
    result = _evaluate(row=row)
    assert result.valid is False
    assert "manual_contract_constraint_override_not_allowed" in result.blocking_reasons


def test_explicit_scanner_targets_cannot_bypass_structural_target_authority():
    row = replace(_row(), target_1=103.0, target_2=106.0)
    result = _evaluate(row=row)
    assert result.valid is False
    assert "explicit_scanner_targets_not_allowed" in result.blocking_reasons


def test_non_mechanical_entry_is_rejected_even_when_strategy_layer_accepts_it():
    row = replace(
        _row(),
        entry_trigger=98.5,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=98.5,
            underlying_invalidation=96.0,
            resistance_levels=(103.0, 106.0),
        ),
    )
    payload = _payload(entry_trigger="98.5")
    result = _evaluate(row=row, payload=payload)
    assert result.valid is False
    assert "entry_not_mechanical_previous_candle_break" in result.blocking_reasons


def test_invalidation_must_be_inside_bar_opposite_extreme():
    row = replace(
        _row(),
        underlying_invalidation=95.5,
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=99.0,
            underlying_invalidation=95.5,
            resistance_levels=(103.0, 106.0),
        ),
    )
    payload = _payload(underlying_invalidation="95.5")
    result = _evaluate(row=row, payload=payload)
    assert result.valid is False
    assert "invalidation_not_inside_bar_opposite_extreme" in result.blocking_reasons


def test_scanner_and_canonical_contract_must_be_same_contract():
    row = replace(_row(), contract_constraints_inputs=_scanner_contract(premium=2.10))
    result = _evaluate(row=row)
    assert result.valid is False
    assert "scanner_canonical_contract_mismatch:premium" in result.blocking_reasons


def test_proof_packet_timeframe_must_match_scanner_row():
    result = _evaluate(payload=_payload(timeframe="1h"))
    assert result.valid is False
    assert "proof_packet_timeframe_mismatch" in result.blocking_reasons


def test_proof_packet_setup_type_must_match_canonical_strategy_name():
    result = _evaluate(payload=_payload(setup_type="2-1-2"))
    assert result.valid is False
    assert "proof_packet_setup_type_mismatch" in result.blocking_reasons


def test_mixed_spy_qqq_context_can_trigger_scanner_but_cannot_promote_plan():
    row = replace(_row(), market_context_inputs=_market_context(qqq_trend="neutral"))
    result = _evaluate(row=row)
    assert result.valid is False
    assert "spy_qqq_not_aligned" in result.blocking_reasons


def test_opposite_side_level_labels_are_rejected_before_plan_promotion():
    row = replace(
        _row(),
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=99.0,
            underlying_invalidation=96.0,
            resistance_levels=(103.0, 106.0),
            support_levels=(104.0,),
        ),
    )
    result = _evaluate(row=row)
    assert result.valid is False
    assert "opposite_side_target_levels_present" in result.blocking_reasons


def test_gamma_target_requires_verified_gamma_provenance():
    row = replace(
        _row(),
        level_inputs=LevelFinderInputs(
            direction="CALL",
            entry=99.0,
            underlying_invalidation=96.0,
            resistance_levels=(103.0,),
            gamma_resistance=106.0,
        ),
    )
    levels = (
        StructuralLevel(price=103.0, side="RESISTANCE", source="PDH"),
        StructuralLevel(
            price=106.0,
            side="RESISTANCE",
            source="unverified_vendor_pivot",
            is_gamma=True,
            verified_gamma=False,
        ),
    )
    result = _evaluate(row=row, levels=levels)
    assert result.valid is False
    assert "scanner_gamma_level_not_verified" in result.blocking_reasons


def test_nonfinite_aggregate_risk_budget_cannot_become_unlimited():
    result = _evaluate(max_aggregate_open_risk_dollars=math.inf)
    assert result.valid is False
    assert "canonical_portfolio_risk_not_pass" in result.blocking_reasons
    assert any("aggregate_risk_budget_invalid" in reason for reason in result.blocking_reasons)


def test_plan_target_authority_must_match_scanner_and_proof_targets():
    levels = (
        StructuralLevel(price=104.0, side="RESISTANCE", source="WRONG_LEVEL_1"),
        StructuralLevel(price=107.0, side="RESISTANCE", source="WRONG_LEVEL_2"),
    )
    result = _evaluate(levels=levels)
    assert result.valid is False
    assert "scanner_plan_structural_levels_mismatch" in result.blocking_reasons


def test_market_authority_exposes_independent_proof_components_without_signa():
    from options_manager.context import evaluate_market_context

    result = evaluate_market_context(_market_context())
    assert result.confirmed is True
    assert result.spy_qqq_aligned is True
    assert result.htf_aligned is True
    assert result.event_risk_clear is True


def test_bridge_module_has_no_network_broker_alert_or_execution_imports():
    tree = ast.parse(Path(proof_adapter_module.__file__).read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = (
        "httpx",
        "requests",
        "socket",
        "webhook",
        "discord",
        "broker",
        "execution",
        "robinhood",
        "tradovate",
        "ibkr",
        "storage",
    )
    for module in imported:
        lowered = module.lower()
        assert not any(fragment in lowered for fragment in forbidden), module
