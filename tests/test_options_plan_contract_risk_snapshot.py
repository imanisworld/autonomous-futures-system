"""Contract/risk fact tests for persistent advisory options theses."""

from __future__ import annotations

from datetime import datetime, timezone

from options_manager.config import OptionsManagerConfig
from options_manager.plans import (
    ContractPlanSnapshot,
    ConvictionBand,
    PlanObservation,
    PlanStatus,
    PlanUpdate,
    RiskPlanSnapshot,
    StructuralLevel,
    TradePlanSnapshot,
    render_plan_update,
    update_trade_thesis,
)
from options_manager.storage import (
    append_thesis_snapshot_event,
    init_options_storage,
    load_latest_thesis_for_identity,
)

NOW = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)


def _contract() -> ContractPlanSnapshot:
    return ContractPlanSnapshot(
        expiration="2026-10-16",
        strike=100.0,
        premium=2.0,
        bid=1.95,
        ask=2.05,
        spread_percent=5.0,
        volume=1000,
        open_interest=2000,
        dte=45,
        max_contracts=1,
        premium_stop=1.60,
        distance_to_target=4.0,
        iv_event_risk="none",
        theta_risk="none",
        trade_style="swing",
    )


def _risk() -> RiskPlanSnapshot:
    return RiskPlanSnapshot(
        planned_dollar_risk=40.0,
        capital_deployed=200.0,
        stated_max_dollar_risk=40.0,
        max_trade_risk_dollars=300.0,
        aggregate_open_risk=120.0,
        projected_open_risk=160.0,
        max_aggregate_open_risk_dollars=500.0,
        aggregate_capital_deployed=600.0,
        projected_capital_deployed=800.0,
        open_position_count=3,
        correlation_risk=(("mega_cap_tech", 90.0),),
    )


def _levels():
    return (
        StructuralLevel(103.0, "RESISTANCE", "PDH"),
        StructuralLevel(106.0, "RESISTANCE", "WEEKLY_HIGH"),
    )


def _observation(**overrides) -> PlanObservation:
    fields = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        observed_at=NOW.isoformat(),
        mechanical_triggered=True,
        entry_trigger=99.0,
        underlying_invalidation=96.0,
        levels=_levels(),
        contract_valid=True,
        portfolio_risk_valid=True,
        spy_qqq_aligned=True,
        htf_aligned=True,
        event_risk_clear=True,
        contract_plan=_contract(),
        risk_plan=_risk(),
        source_references=("scan:1", "proof:1"),
    )
    fields.update(overrides)
    return PlanObservation(**fields)


def _snapshot() -> TradePlanSnapshot:
    return update_trade_thesis(None, _observation()).snapshot


def test_manager_carries_validated_contract_and_risk_facts_forward_when_management_poll_omits_them():
    first = _snapshot()
    second = update_trade_thesis(
        first,
        _observation(
            observed_at="2026-09-02T14:05:00+00:00",
            contract_plan=None,
            risk_plan=None,
            source_references=("scan:2",),
        ),
    )
    assert second.snapshot.contract_plan == first.contract_plan == _contract()
    assert second.snapshot.risk_plan == first.risk_plan == _risk()
    assert "contract_plan_changed" not in second.material_reasons
    assert "risk_plan_changed" not in second.material_reasons


def test_renderer_shows_contract_liquidity_planned_risk_full_debit_and_position_count_as_metric_only():
    snapshot = _snapshot()
    update = PlanUpdate(
        snapshot=snapshot,
        should_emit_update=True,
        material_reasons=("new_plan",),
        telemetry_only=False,
        signa_changed=False,
        signa_repeated=False,
    )
    rendered = render_plan_update(update)
    assert "2026-10-16 100 CALL" in rendered.body
    assert "DTE=45" in rendered.body
    assert "spread=5.00%" in rendered.body
    assert "volume=1000 OI=2000" in rendered.body
    assert "premium stop=$1.60" in rendered.body
    assert "Planned risk: $40.00" in rendered.body
    assert "full debit/capital deployed=$200.00" in rendered.body
    assert "projected=$160.00 / cap=$500.00" in rendered.body
    assert "open positions=3 (metric only; no position-count cap)" in rendered.body
    assert "mega_cap_tech=$90.00" in rendered.body


def test_contract_or_risk_policy_change_is_material_and_classified_without_creating_new_thesis():
    first = _snapshot()
    contract_changed = update_trade_thesis(
        first,
        _observation(
            contract_plan=ContractPlanSnapshot(
                **{**_contract().__dict__, "premium_stop": 1.55}
            )
        ),
    )
    assert "contract_plan_changed" in contract_changed.material_reasons
    assert render_plan_update(contract_changed).kind.value == "CONTRACT_UPDATED"

    risk_changed = update_trade_thesis(
        first,
        _observation(
            risk_plan=RiskPlanSnapshot(
                **{**_risk().__dict__, "max_aggregate_open_risk_dollars": 600.0}
            )
        ),
    )
    assert "risk_plan_changed" in risk_changed.material_reasons
    assert render_plan_update(risk_changed).kind.value == "RISK_UPDATED"


def test_quote_and_exposure_telemetry_refresh_without_duplicate_user_facing_update():
    first = _snapshot()
    refreshed_contract = ContractPlanSnapshot(
        **{
            **_contract().__dict__,
            "premium": 2.03,
            "bid": 1.98,
            "ask": 2.08,
            "spread_percent": 4.9,
            "volume": 1110,
            "open_interest": 2015,
            "dte": 44,
            "distance_to_target": 3.8,
        }
    )
    refreshed_risk = RiskPlanSnapshot(
        **{
            **_risk().__dict__,
            "planned_dollar_risk": 43.0,
            "capital_deployed": 203.0,
            "aggregate_open_risk": 150.0,
            "projected_open_risk": 193.0,
            "aggregate_capital_deployed": 700.0,
            "projected_capital_deployed": 903.0,
            "open_position_count": 4,
            "correlation_risk": (("mega_cap_tech", 123.0),),
        }
    )
    update = update_trade_thesis(
        first,
        _observation(
            observed_at="2026-09-02T14:05:00+00:00",
            contract_plan=refreshed_contract,
            risk_plan=refreshed_risk,
        ),
    )

    assert update.snapshot.contract_plan == refreshed_contract
    assert update.snapshot.risk_plan == refreshed_risk
    assert update.material_reasons == ()
    assert update.should_emit_update is False
    assert render_plan_update(update).kind.value == "UNCHANGED"


def test_contract_and_risk_facts_round_trip_in_existing_options_sqlite_snapshot_json(tmp_path):
    db_path = str(tmp_path / "options.sqlite")
    config = OptionsManagerConfig()
    assert init_options_storage(db_path, config).written is True
    snapshot = _snapshot()

    write = append_thesis_snapshot_event(
        db_path,
        "thesis-contract-risk-1",
        snapshot,
        config,
        recorded_at=NOW,
    )
    assert write.written is True

    loaded = load_latest_thesis_for_identity(
        db_path,
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        config=config,
    )
    assert loaded.found is True
    restored = loaded.record["snapshot"]
    assert restored == snapshot
    assert restored.contract_plan == _contract()
    assert restored.risk_plan == _risk()


def test_legacy_snapshot_shape_with_no_contract_or_risk_still_round_trips(tmp_path):
    db_path = str(tmp_path / "options.sqlite")
    config = OptionsManagerConfig()
    assert init_options_storage(db_path, config).written is True
    snapshot = TradePlanSnapshot(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        observed_at=NOW.isoformat(),
        status=PlanStatus.TRIGGERED,
        actionable=True,
        conviction=ConvictionBand.STANDARD,
        conviction_confirmation_count=0,
        entry_trigger=99.0,
        underlying_invalidation=96.0,
        target_1=103.0,
        target_2=106.0,
        target_1_source="PDH",
        target_2_source="WEEKLY_HIGH",
        rr_1=4 / 3,
        rr_2=7 / 3,
        target_status="VALID",
        target_reason_code="targets_confirmed",
        blocking_reasons=(),
        warnings=(),
    )
    assert snapshot.contract_plan is None
    assert snapshot.risk_plan is None
    assert append_thesis_snapshot_event(
        db_path, "legacy-thesis", snapshot, config, recorded_at=NOW
    ).written is True
    loaded = load_latest_thesis_for_identity(
        db_path,
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        config=config,
    )
    assert loaded.found is True
    assert loaded.record["snapshot"] == snapshot
