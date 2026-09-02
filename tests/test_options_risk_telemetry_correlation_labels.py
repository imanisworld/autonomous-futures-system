from options_manager.plans import ContractPlanSnapshot, ConvictionBand, PlanStatus, RiskPlanSnapshot, TradePlanSnapshot
from options_manager.risk import RiskTelemetryStatus, measure_risk_telemetry


def test_non_string_correlation_group_is_rejected_not_coerced():
    contract = ContractPlanSnapshot(
        expiration="2026-10-16", strike=100.0, premium=2.5, bid=2.45, ask=2.55,
        spread_percent=4.0, volume=500, open_interest=1500, dte=44, max_contracts=1,
        premium_stop=2.0, distance_to_target=4.0, iv_event_risk="LOW", theta_risk="LOW", trade_style="SWING",
    )
    risk = RiskPlanSnapshot(
        planned_dollar_risk=50.0, capital_deployed=250.0, stated_max_dollar_risk=50.0,
        max_trade_risk_dollars=300.0, aggregate_open_risk=300.0, projected_open_risk=350.0,
        max_aggregate_open_risk_dollars=1000.0, aggregate_capital_deployed=1000.0,
        projected_capital_deployed=1250.0, open_position_count=1, correlation_risk=((123, 200.0),),
    )
    plan = TradePlanSnapshot(
        ticker="AAPL", direction="CALL", setup_type="strat_212", timeframe="30m",
        observed_at="2026-09-02T10:03:00-04:00", status=PlanStatus.TRIGGERED, actionable=True,
        conviction=ConvictionBand.STANDARD, conviction_confirmation_count=2, entry_trigger=100.0,
        underlying_invalidation=98.0, target_1=104.0, target_2=106.0, target_1_source="PDH",
        target_2_source="WEEKLY_HIGH", rr_1=2.0, rr_2=3.0, target_status="VALID",
        target_reason_code="targets_confirmed", blocking_reasons=(), warnings=(), contract_plan=contract, risk_plan=risk,
    )
    result = measure_risk_telemetry(plan)
    assert result.status == RiskTelemetryStatus.INVALID
    assert "correlation_risk_0_group_not_string" in result.reason_codes
