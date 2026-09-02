from __future__ import annotations

from options_manager.plans.base import (
    ConvictionBand,
    PlanStatus,
    PlanUpdate,
    TradePlanSnapshot,
)
from options_manager.plans.renderer import PlanUpdateKind, render_plan_update


def _snapshot(**overrides) -> TradePlanSnapshot:
    values = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212_continuation",
        timeframe="30m",
        observed_at="2026-09-02T14:00:00+00:00",
        status=PlanStatus.TRIGGERED,
        actionable=True,
        conviction=ConvictionBand.STANDARD,
        conviction_confirmation_count=2,
        entry_trigger=230.5,
        underlying_invalidation=227.25,
        target_1=235.0,
        target_2=240.0,
        target_1_source="PDH",
        target_2_source="weekly_high",
        rr_1=1.384615,
        rr_2=2.923077,
        target_status="VALID",
        target_reason_code="targets_found",
        blocking_reasons=(),
        warnings=(),
        signa_event_count=1,
        signa_repeat_count=4,
        source_references=("scanner:abc", "proof:xyz"),
    )
    values.update(overrides)
    return TradePlanSnapshot(**values)


def _update(snapshot: TradePlanSnapshot, **overrides) -> PlanUpdate:
    values = dict(
        snapshot=snapshot,
        should_emit_update=True,
        material_reasons=("status_changed",),
        telemetry_only=False,
        signa_changed=False,
        signa_repeated=False,
    )
    values.update(overrides)
    return PlanUpdate(**values)


def test_new_thesis_is_one_material_user_facing_event():
    rendered = render_plan_update(
        _update(_snapshot(status=PlanStatus.WATCHING, actionable=False), material_reasons=("new_plan",))
    )

    assert rendered.kind == PlanUpdateKind.NEW_THESIS
    assert rendered.should_emit is True
    assert rendered.title == "[options plan] NEW_THESIS AAPL CALL"
    assert "Watching only" in rendered.summary
    assert "Status: WATCHING" in rendered.body


def test_triggered_actionable_plan_renders_mechanical_levels_and_provenance():
    rendered = render_plan_update(_update(_snapshot()))

    assert rendered.kind == PlanUpdateKind.TRIGGERED
    assert rendered.should_emit is True
    assert "canonically proven" in rendered.summary
    assert "Entry trigger: 230.5" in rendered.body
    assert "Stop / invalidation: 227.25" in rendered.body
    assert "Target 1: 235 (PDH)" in rendered.body
    assert "Target 2: 240 (weekly_high)" in rendered.body
    assert "Proof refs: scanner:abc | proof:xyz" in rendered.body


def test_triggered_but_non_actionable_never_reads_like_an_entry_call():
    rendered = render_plan_update(
        _update(
            _snapshot(
                actionable=False,
                blocking_reasons=("portfolio_risk_not_valid",),
            )
        )
    )

    assert rendered.kind == PlanUpdateKind.TRIGGERED
    assert "proof is incomplete; no entry call" in rendered.summary
    assert "Actionable: NO" in rendered.body
    assert "Blocks: portfolio_risk_not_valid" in rendered.body
    assert "TAKE" not in rendered.body
    assert "enter now" not in rendered.body.lower()


def test_repeated_signa_only_poll_is_not_user_facing_update():
    rendered = render_plan_update(
        _update(
            _snapshot(signa_event_count=1, signa_repeat_count=12),
            should_emit_update=False,
            material_reasons=(),
            telemetry_only=True,
            signa_repeated=True,
        )
    )

    assert rendered.kind == PlanUpdateKind.TELEMETRY_ONLY
    assert rendered.should_emit is False
    assert "observational telemetry changed only" in rendered.summary
    assert "Signa: OBSERVATIONAL ONLY (events=1, repeats=12)" in rendered.body
    assert "Changed: observational telemetry only" in rendered.body


def test_identical_poll_is_unchanged_and_suppressed():
    rendered = render_plan_update(
        _update(
            _snapshot(),
            should_emit_update=False,
            material_reasons=(),
            telemetry_only=False,
        )
    )

    assert rendered.kind == PlanUpdateKind.UNCHANGED
    assert rendered.should_emit is False
    assert "Same thesis, same proof, same levels" in rendered.summary
    assert "Changed: none" in rendered.body


def test_target_change_has_specific_event_priority():
    rendered = render_plan_update(
        _update(
            _snapshot(target_1=236.0, target_1_source="range_high"),
            material_reasons=("targets_changed", "blocking_reasons_changed"),
        )
    )

    assert rendered.kind == PlanUpdateKind.TARGETS_UPDATED
    assert "Target 1: 236 (range_high)" in rendered.body


def test_entry_or_invalidation_change_is_explicit_levels_update():
    rendered = render_plan_update(
        _update(
            _snapshot(entry_trigger=231.0),
            material_reasons=("entry_or_invalidation_changed",),
        )
    )

    assert rendered.kind == PlanUpdateKind.LEVELS_UPDATED
    assert "Entry trigger: 231" in rendered.body


def test_proof_change_is_separate_from_price_plan_change():
    rendered = render_plan_update(
        _update(
            _snapshot(actionable=False, blocking_reasons=("event_risk_not_clear",)),
            material_reasons=("actionability_changed", "blocking_reasons_changed"),
        )
    )

    assert rendered.kind == PlanUpdateKind.PROOF_UPDATED
    assert "Actionable: NO" in rendered.body


def test_terminal_statuses_are_explicit_and_not_reopen_language():
    cases = (
        (PlanStatus.INVALIDATED, PlanUpdateKind.INVALIDATED, "must not be reopened"),
        (PlanStatus.EXITED, PlanUpdateKind.EXITED, "generation is complete"),
        (PlanStatus.EXPIRED, PlanUpdateKind.EXPIRED, "generation is complete"),
    )
    for status, kind, phrase in cases:
        rendered = render_plan_update(
            _update(_snapshot(status=status, actionable=False), material_reasons=("status_changed",))
        )
        assert rendered.kind == kind
        assert phrase in rendered.summary


def test_active_with_broken_current_proof_is_management_only():
    rendered = render_plan_update(
        _update(
            _snapshot(
                status=PlanStatus.ACTIVE,
                actionable=False,
                blocking_reasons=("spy_qqq_not_aligned",),
            ),
            material_reasons=("blocking_reasons_changed",),
        )
    )

    assert rendered.kind == PlanUpdateKind.PROOF_UPDATED
    assert "management only, not a new-entry signal" in rendered.summary
    assert "Blocks: spy_qqq_not_aligned" in rendered.body


def test_high_conviction_candidate_is_never_rendered_as_size_instruction():
    rendered = render_plan_update(
        _update(
            _snapshot(
                conviction=ConvictionBand.HIGH_CONVICTION_CANDIDATE,
                conviction_confirmation_count=4,
            ),
            material_reasons=("conviction_changed",),
        )
    )

    assert rendered.kind == PlanUpdateKind.CONVICTION_UPDATED
    assert "HIGH_CONVICTION_CANDIDATE" in rendered.body
    assert "evidence label only, no sizing increase" in rendered.body


def test_renderer_refuses_to_invent_contract_or_risk_plan_fields():
    rendered = render_plan_update(_update(_snapshot()))

    assert "Contract plan: UNRESOLVED" in rendered.body
    assert "Risk plan: UNRESOLVED" in rendered.body
    assert "Contract:" not in rendered.body
    assert "Planned risk:" not in rendered.body
