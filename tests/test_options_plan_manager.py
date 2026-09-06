"""Phase-1 advisory thesis/plan manager tests.

These tests pin the rule the repeated-alert lane previously lacked: one setup is
one evolving thesis, and repeated Signa observations are telemetry rather than
additional conviction or permission.
"""

from __future__ import annotations

import ast
from pathlib import Path

import options_manager.plans.base as plan_base_module
import options_manager.plans.manager as plan_manager_module
from options_manager.plans import (
    ConvictionBand,
    ConvictionProofs,
    PlanObservation,
    PlanPolicy,
    PlanStatus,
    SignaObservation,
    StructuralLevel,
    update_trade_thesis,
)


def _call_levels() -> tuple[StructuralLevel, ...]:
    return (
        StructuralLevel(price=101.0, side="RESISTANCE", source="PDH"),
        StructuralLevel(price=103.0, side="RESISTANCE", source="PWH"),
        StructuralLevel(price=97.0, side="SUPPORT", source="PDL"),
    )


def _put_levels() -> tuple[StructuralLevel, ...]:
    return (
        StructuralLevel(price=99.0, side="SUPPORT", source="PDL"),
        StructuralLevel(price=97.0, side="SUPPORT", source="PWL"),
        StructuralLevel(price=103.0, side="RESISTANCE", source="PDH"),
    )


def _call_observation(**overrides) -> PlanObservation:
    fields = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        observed_at="2026-09-01T10:30:00-04:00",
        mechanical_triggered=True,
        entry_trigger=100.0,
        underlying_invalidation=98.0,
        levels=_call_levels(),
        contract_valid=True,
        portfolio_risk_valid=True,
        spy_qqq_aligned=True,
        htf_aligned=True,
        event_risk_clear=True,
        source_reference="scan:1",
    )
    fields.update(overrides)
    return PlanObservation(**fields)


def _put_observation(**overrides) -> PlanObservation:
    fields = dict(
        ticker="NVDA",
        direction="PUT",
        setup_type="break + retest",
        timeframe="30m",
        observed_at="2026-09-01T11:00:00-04:00",
        mechanical_triggered=True,
        entry_trigger=100.0,
        underlying_invalidation=102.0,
        levels=_put_levels(),
        contract_valid=True,
        portfolio_risk_valid=True,
        spy_qqq_aligned=True,
        htf_aligned=True,
        event_risk_clear=True,
    )
    fields.update(overrides)
    return PlanObservation(**fields)


def _signa(**overrides) -> SignaObservation:
    fields = dict(
        direction="UP",
        grade="A",
        score=80.0,
        requested_tf="30m",
        signal_timestamp="2026-09-01T10:00:00-04:00",
        technicals_as_of="2026-09-01T10:00:00-04:00",
        stale_minutes=1.0,
        retrieved_at="2026-09-01T10:30:00-04:00",
        parser_version="v1",
    )
    fields.update(overrides)
    return SignaObservation(**fields)


# --- targets: reuse the existing target authority, keep provenance -----------------------


def test_call_targets_are_nearest_directional_resistance_levels_with_sources():
    result = update_trade_thesis(None, _call_observation())
    plan = result.snapshot
    assert plan.target_status == "VALID"
    assert plan.target_1 == 101.0
    assert plan.target_2 == 103.0
    assert plan.target_1_source == "PDH"
    assert plan.target_2_source == "PWH"
    assert plan.rr_1 == 0.5
    assert plan.rr_2 == 1.5
    assert plan.actionable is True


def test_put_targets_are_nearest_directional_support_levels_with_sources():
    plan = update_trade_thesis(None, _put_observation()).snapshot
    assert plan.target_1 == 99.0
    assert plan.target_2 == 97.0
    assert plan.target_1_source == "PDL"
    assert plan.target_2_source == "PWL"


def test_wrong_kind_level_on_profit_side_is_not_used_as_target():
    levels = (
        StructuralLevel(price=101.0, side="SUPPORT", source="STALE_SUPPORT_ABOVE"),
        StructuralLevel(price=102.0, side="RESISTANCE", source="PDH"),
        StructuralLevel(price=104.0, side="RESISTANCE", source="PWH"),
    )
    plan = update_trade_thesis(None, _call_observation(levels=levels)).snapshot
    assert plan.target_1 == 102.0
    assert plan.target_2 == 104.0


def test_unverified_gamma_is_ignored_and_never_becomes_a_target():
    levels = (
        StructuralLevel(
            price=100.5,
            side="RESISTANCE",
            source="SIGNA_R1_NOT_GEX",
            is_gamma=True,
            verified_gamma=False,
        ),
        StructuralLevel(price=101.0, side="RESISTANCE", source="PDH"),
        StructuralLevel(price=103.0, side="RESISTANCE", source="PWH"),
    )
    plan = update_trade_thesis(None, _call_observation(levels=levels)).snapshot
    assert plan.target_1 == 101.0
    assert any("unverified_gamma_level_ignored" in warning for warning in plan.warnings)


def test_verified_gamma_can_be_used_when_caller_explicitly_marks_it_verified():
    levels = (
        StructuralLevel(
            price=100.5,
            side="RESISTANCE",
            source="REAL_GEX_CALL_WALL",
            is_gamma=True,
            verified_gamma=True,
        ),
        StructuralLevel(price=101.0, side="RESISTANCE", source="PDH"),
    )
    plan = update_trade_thesis(None, _call_observation(levels=levels)).snapshot
    assert plan.target_1 == 100.5
    assert plan.target_1_source == "REAL_GEX_CALL_WALL"


def test_missing_second_target_fails_closed_and_plan_is_not_actionable():
    plan = update_trade_thesis(
        None,
        _call_observation(
            levels=(StructuralLevel(price=101.0, side="RESISTANCE", source="PDH"),)
        ),
    ).snapshot
    assert plan.target_status == "INVALID"
    assert plan.target_reason_code == "no_target_2"
    assert plan.actionable is False
    assert any(reason.startswith("targets_not_valid") for reason in plan.blocking_reasons)


# --- Signa: one state is one event, repeats do not manufacture proof ----------------------


def test_repeated_signa_state_is_telemetry_only_not_a_new_event_or_alert():
    first = update_trade_thesis(None, _call_observation(signa=_signa())).snapshot
    second_update = update_trade_thesis(
        first,
        _call_observation(
            observed_at="2026-09-01T10:35:00-04:00",
            signa=_signa(retrieved_at="2026-09-01T10:35:00-04:00"),
            source_reference="scan:2",
        ),
    )
    second = second_update.snapshot
    assert first.signa_event_count == 1
    assert second.signa_event_count == 1
    assert second.signa_repeat_count == 1
    assert second_update.signa_repeated is True
    assert second_update.should_emit_update is False
    assert second_update.telemetry_only is True
    assert second.conviction == first.conviction
    assert second.actionable == first.actionable


def test_new_signa_state_is_recorded_but_cannot_change_actionability_or_conviction():
    first = update_trade_thesis(None, _call_observation(signa=_signa())).snapshot
    changed = update_trade_thesis(
        first,
        _call_observation(
            observed_at="2026-09-01T10:35:00-04:00",
            signa=_signa(direction="DOWN", grade="F", score=10.0),
        ),
    )
    assert changed.signa_changed is True
    assert changed.snapshot.signa_event_count == 2
    assert changed.snapshot.actionable is True
    assert changed.snapshot.conviction == ConvictionBand.STANDARD
    assert changed.should_emit_update is False
    assert changed.telemetry_only is True


def test_missing_signa_does_not_block_an_otherwise_complete_plan():
    plan = update_trade_thesis(None, _call_observation(signa=None)).snapshot
    assert plan.actionable is True
    assert plan.conviction == ConvictionBand.STANDARD
    assert not any("signa" in reason.lower() for reason in plan.blocking_reasons)


# --- conviction: explicit policy only, independent proof only -----------------------------


def test_high_conviction_has_no_default_threshold():
    proofs = ConvictionProofs(
        full_timeframe_continuity=True,
        clean_continuation_or_retest=True,
        strong_level_confluence=True,
        exceptional_liquidity=True,
        strong_target_room=True,
    )
    plan = update_trade_thesis(
        None, _call_observation(conviction_proofs=proofs, signa=_signa(score=99.0))
    ).snapshot
    assert plan.conviction_confirmation_count == 5
    assert plan.conviction == ConvictionBand.STANDARD


def test_explicit_high_conviction_policy_can_label_candidate_without_changing_actionability():
    proofs = ConvictionProofs(
        full_timeframe_continuity=True,
        clean_continuation_or_retest=True,
        strong_level_confluence=True,
        exceptional_liquidity=True,
    )
    plan = update_trade_thesis(
        None,
        _call_observation(conviction_proofs=proofs),
        policy=PlanPolicy(high_conviction_min_confirmations=4),
    ).snapshot
    assert plan.actionable is True
    assert plan.conviction == ConvictionBand.HIGH_CONVICTION_CANDIDATE


def test_signa_is_not_a_conviction_confirmation():
    plan = update_trade_thesis(
        None,
        _call_observation(signa=_signa(score=100.0)),
        policy=PlanPolicy(high_conviction_min_confirmations=1),
    ).snapshot
    assert plan.conviction_confirmation_count == 0
    assert plan.conviction == ConvictionBand.STANDARD


def test_invalid_high_conviction_policy_fails_instead_of_guessing():
    try:
        update_trade_thesis(
            None,
            _call_observation(),
            policy=PlanPolicy(high_conviction_min_confirmations=0),
        )
    except ValueError as exc:
        assert "high_conviction_min_confirmations" in str(exc)
    else:  # pragma: no cover - explicit failure message
        raise AssertionError("invalid conviction policy must fail")


# --- lifecycle and material updates -------------------------------------------------------


def test_new_plan_is_triggered_and_emits_one_material_update():
    update = update_trade_thesis(None, _call_observation())
    assert update.snapshot.status == PlanStatus.TRIGGERED
    assert update.should_emit_update is True
    assert update.material_reasons == ("new_plan",)


def test_incomplete_untriggered_plan_is_watching():
    plan = update_trade_thesis(
        None,
        _call_observation(
            mechanical_triggered=False,
            entry_trigger=None,
            underlying_invalidation=None,
            contract_valid=False,
            portfolio_risk_valid=False,
        ),
    ).snapshot
    assert plan.status == PlanStatus.WATCHING
    assert plan.actionable is False
    assert plan.conviction == ConvictionBand.OBSERVATIONAL


def test_mark_active_requires_actionable_proof():
    plan = update_trade_thesis(
        None, _call_observation(contract_valid=False, mark_active=True)
    ).snapshot
    assert plan.status == PlanStatus.TRIGGERED
    assert plan.actionable is False
    assert "active_requires_actionable_plan" in plan.blocking_reasons


def test_actionable_plan_can_be_marked_active_and_stays_active_on_later_observation():
    active = update_trade_thesis(None, _call_observation(mark_active=True)).snapshot
    assert active.status == PlanStatus.ACTIVE
    later = update_trade_thesis(
        active,
        _call_observation(observed_at="2026-09-01T11:00:00-04:00"),
    ).snapshot
    assert later.status == PlanStatus.ACTIVE


def test_active_plan_can_exit_but_nonactive_plan_cannot():
    active = update_trade_thesis(None, _call_observation(mark_active=True)).snapshot
    exited = update_trade_thesis(
        active,
        _call_observation(observed_at="2026-09-01T12:00:00-04:00", mark_exited=True),
    ).snapshot
    assert exited.status == PlanStatus.EXITED
    try:
        update_trade_thesis(None, _call_observation(mark_exited=True))
    except ValueError as exc:
        assert "ACTIVE" in str(exc)
    else:
        raise AssertionError("non-active thesis must not exit")


def test_invalidation_is_terminal_and_same_thesis_cannot_be_reopened():
    invalid = update_trade_thesis(None, _call_observation(invalidation_hit=True)).snapshot
    assert invalid.status == PlanStatus.INVALIDATED
    try:
        update_trade_thesis(invalid, _call_observation())
    except ValueError as exc:
        assert "terminal thesis" in str(exc)
    else:
        raise AssertionError("terminal thesis must not be reopened")


def test_source_references_accumulate_without_duplicates():
    first = update_trade_thesis(None, _call_observation(source_reference="scan:1")).snapshot
    second = update_trade_thesis(
        first,
        _call_observation(observed_at="2026-09-01T10:35:00-04:00", source_reference="scan:2"),
    ).snapshot
    third = update_trade_thesis(
        second,
        _call_observation(observed_at="2026-09-01T10:40:00-04:00", source_reference="scan:2"),
    ).snapshot
    assert third.source_references == ("scan:1", "scan:2")


def test_rr_policy_reuses_target_finder_and_fails_closed():
    plan = update_trade_thesis(
        None,
        _call_observation(),
        policy=PlanPolicy(min_rr_threshold=1.0),
    ).snapshot
    assert plan.target_status == "INVALID"
    assert plan.target_reason_code == "rr_below_threshold"
    assert plan.actionable is False


# --- no hidden execution/notification surface --------------------------------------------


def _absolute_imports(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.append(node.module)
    return imports


def test_plan_manager_imports_no_broker_execution_alert_or_network_modules():
    forbidden = (
        "execution",
        "broker",
        "alert_ranker",
        "discord",
        "webhook",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "ib_insync",
        "robin_stocks",
    )
    for module in (plan_base_module, plan_manager_module):
        for imported in _absolute_imports(module):
            assert not any(token in imported for token in forbidden), (module.__name__, imported)


def test_plan_manager_contains_no_order_or_alert_send_calls():
    forbidden = (
        "place_order",
        "submit_order",
        "cancel_order",
        "replace_order",
        "send_discord",
        "send_alert",
        "post_webhook",
    )
    for module in (plan_base_module, plan_manager_module):
        source = Path(module.__file__).read_text()
        for token in forbidden:
            assert token not in source
