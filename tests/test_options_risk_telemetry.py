from __future__ import annotations

from dataclasses import replace

import pytest

from options_manager.plans import (
    ContractPlanSnapshot,
    ConvictionBand,
    PlanStatus,
    RiskPlanSnapshot,
    TradePlanSnapshot,
)
from options_manager.risk import RiskTelemetryStatus, measure_risk_telemetry


def _contract(**overrides: object) -> ContractPlanSnapshot:
    values = dict(
        expiration="2026-10-16",
        strike=100.0,
        premium=2.50,
        bid=2.45,
        ask=2.55,
        spread_percent=4.0,
        volume=500,
        open_interest=1500,
        dte=44,
        max_contracts=1,
        premium_stop=2.00,
        distance_to_target=4.0,
        iv_event_risk="LOW",
        theta_risk="LOW",
        trade_style="SWING",
    )
    values.update(overrides)
    return ContractPlanSnapshot(**values)


def _risk(**overrides: object) -> RiskPlanSnapshot:
    values = dict(
        planned_dollar_risk=50.0,
        capital_deployed=250.0,
        stated_max_dollar_risk=50.0,
        max_trade_risk_dollars=300.0,
        aggregate_open_risk=300.0,
        projected_open_risk=350.0,
        max_aggregate_open_risk_dollars=1000.0,
        aggregate_capital_deployed=1000.0,
        projected_capital_deployed=1250.0,
        open_position_count=12,
        correlation_risk=(("MEGA_CAP_TECH", 200.0),),
    )
    values.update(overrides)
    return RiskPlanSnapshot(**values)


def _plan(*, contract_plan=None, risk_plan=None, **overrides) -> TradePlanSnapshot:
    values = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        observed_at="2026-09-02T10:03:00-04:00",
        status=PlanStatus.TRIGGERED,
        actionable=True,
        conviction=ConvictionBand.STANDARD,
        conviction_confirmation_count=2,
        entry_trigger=100.0,
        underlying_invalidation=98.0,
        target_1=104.0,
        target_2=106.0,
        target_1_source="PDH",
        target_2_source="WEEKLY_HIGH",
        rr_1=2.0,
        rr_2=3.0,
        target_status="VALID",
        target_reason_code="targets_confirmed",
        blocking_reasons=(),
        warnings=(),
        contract_plan=_contract() if contract_plan is None else contract_plan,
        risk_plan=_risk() if risk_plan is None else risk_plan,
    )
    values.update(overrides)
    return TradePlanSnapshot(**values)


def test_complete_snapshot_keeps_planned_risk_and_full_debit_separate():
    result = measure_risk_telemetry(_plan())
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot is not None
    assert result.snapshot.planned_stop_risk_per_contract == pytest.approx(50.0)
    assert result.snapshot.full_debit_per_contract == pytest.approx(250.0)


def test_complete_snapshot_keeps_current_and_projected_portfolio_risk_separate():
    snapshot = measure_risk_telemetry(_plan()).snapshot
    assert snapshot is not None
    assert snapshot.aggregate_planned_open_risk == pytest.approx(300.0)
    assert snapshot.projected_aggregate_planned_open_risk == pytest.approx(350.0)
    assert snapshot.aggregate_full_debit == pytest.approx(1000.0)
    assert snapshot.projected_aggregate_full_debit == pytest.approx(1250.0)


def test_position_count_is_telemetry_only_not_a_cap():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(open_position_count=25)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.open_position_count == 25


def test_recorded_caps_are_preserved_without_selecting_new_policy():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(max_trade_risk_dollars=275.0, max_aggregate_open_risk_dollars=875.0)))
    assert result.snapshot.max_trade_risk_dollars == pytest.approx(275.0)
    assert result.snapshot.max_aggregate_open_risk_dollars == pytest.approx(875.0)


def test_correlation_risk_is_reported_not_converted_to_position_cap():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(correlation_risk=(("SPY_BETA", 120.0), ("TECH", 180.0)))))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.correlation_risk == (("SPY_BETA", 120.0), ("TECH", 180.0))


def test_distance_to_invalidation_is_measured_from_underlying_levels():
    result = measure_risk_telemetry(_plan())
    assert result.snapshot.distance_to_invalidation == pytest.approx(2.0)


def test_missing_risk_plan_is_incomplete_not_invented():
    plan = _plan()
    result = measure_risk_telemetry(replace(plan, risk_plan=None))
    assert result.status == RiskTelemetryStatus.INCOMPLETE
    assert result.reason_codes == ("risk_plan_missing",)


def test_missing_contract_plan_is_incomplete_not_invented():
    plan = _plan()
    result = measure_risk_telemetry(replace(plan, contract_plan=None))
    assert result.status == RiskTelemetryStatus.INCOMPLETE
    assert result.reason_codes == ("contract_plan_missing",)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_planned_risk_fails_closed(bad):
    result = measure_risk_telemetry(_plan(risk_plan=_risk(planned_dollar_risk=bad)))
    assert result.status == RiskTelemetryStatus.INVALID
    assert "planned_dollar_risk_not_finite" in result.reason_codes


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_contract_premium_fails_closed(bad):
    result = measure_risk_telemetry(_plan(contract_plan=_contract(premium=bad)))
    assert result.status == RiskTelemetryStatus.INVALID
    assert "premium_not_finite" in result.reason_codes


@pytest.mark.parametrize("field,bad", [("max_contracts", float("inf")), ("dte", float("nan")), ("volume", "bad"), ("open_interest", 3.5)])
def test_malformed_integer_contract_facts_fail_closed(field, bad):
    result = measure_risk_telemetry(_plan(contract_plan=_contract(**{field: bad})))
    assert result.status == RiskTelemetryStatus.INVALID
    assert any(reason.startswith(field) for reason in result.reason_codes)


def test_huge_integer_does_not_raise_overflow():
    result = measure_risk_telemetry(_plan(contract_plan=_contract(max_contracts=10**10000)))
    assert result.status == RiskTelemetryStatus.INVALID


def test_malformed_correlation_payload_does_not_raise():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(correlation_risk=(42,))))
    assert result.status == RiskTelemetryStatus.INVALID
    assert "correlation_risk_0_malformed" in result.reason_codes


def test_telemetry_does_not_recompute_canonical_premium_stop_risk():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(planned_dollar_risk=49.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.planned_total_trade_risk == pytest.approx(49.0)
    assert result.snapshot.planned_stop_risk_per_contract == pytest.approx(49.0)


def test_telemetry_does_not_recompute_full_debit_from_contract_premium():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(capital_deployed=249.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.full_debit_total == pytest.approx(249.0)
    assert result.snapshot.full_debit_per_contract == pytest.approx(249.0)


def test_telemetry_does_not_reapply_projected_risk_formula():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(projected_open_risk=351.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.projected_aggregate_planned_open_risk == pytest.approx(351.0)


def test_telemetry_does_not_reapply_projected_debit_formula():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(projected_capital_deployed=1251.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.projected_aggregate_full_debit == pytest.approx(1251.0)


def test_telemetry_does_not_reapply_trade_cap():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(max_trade_risk_dollars=40.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.max_trade_risk_dollars == pytest.approx(40.0)
    assert result.snapshot.planned_total_trade_risk == pytest.approx(50.0)


def test_telemetry_does_not_reapply_aggregate_cap():
    result = measure_risk_telemetry(_plan(risk_plan=_risk(max_aggregate_open_risk_dollars=349.0)))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.max_aggregate_open_risk_dollars == pytest.approx(349.0)
    assert result.snapshot.projected_aggregate_planned_open_risk == pytest.approx(350.0)


def test_optional_underlying_levels_can_be_absent_without_inventing_them():
    result = measure_risk_telemetry(_plan(entry_trigger=None, underlying_invalidation=None, target_1=None, target_2=None, rr_1=None, rr_2=None, actionable=False, status=PlanStatus.WATCHING))
    assert result.status == RiskTelemetryStatus.COMPLETE
    assert result.snapshot.distance_to_invalidation is None
