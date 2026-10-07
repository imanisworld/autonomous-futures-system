"""Strategy fitness kill-switch: revoke-only automation.

Required proofs:
  1. failure can revoke execution authority;
  2. a healthy research state cannot automatically turn trading on;
  3. observer collection continues while suspended.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from options_evidence import fitness as fx
from options_evidence.signal import IntegrityStatus as IS
from options_evidence.strategy_epochs import EpochRegistry, EpochStatus, OOSReference, StrategyEpoch, definition_hash

AT = datetime(2026, 10, 6, 20, tzinfo=timezone.utc)
# Untouched OOS: 50% win at +2R, 50% loss at -1R  -> mean +0.5R.
OOS = tuple([2.0, -1.0] * 30)


EPOCH_DEFINITION = {
    # Every material section, as the registry loader and the fitness boundary require.
    "setup": {"family": "STRAT_3_2_2", "timeframe": "1H"},
    "trigger": {"rule": "first strict break"},
    "target": {"t1": "1R"},
    "filters": {},
    "authority": {
        "observation_only": True,
        "execution_authority": False,
        "risk_reservation": False,
        "trade_alerts": False,
    },
}
EPOCH_SHA = definition_hash(EPOCH_DEFINITION, {})


def epoch(oos=OOS) -> StrategyEpoch:
    return StrategyEpoch(
        strategy="322",
        epoch="2026Q4_v1",
        status=EpochStatus.FROZEN,
        definition=EPOCH_DEFINITION,
        thresholds={},
        definition_sha256=EPOCH_SHA,
        effective_from=AT,
        effective_until=None,
        source_commit="a" * 40,
        preregistration_doc="docs/x.md",
        oos_reference=None
        if oos is None
        else OOSReference("oos", "docs/r.json", "b" * 64, tuple(oos), "ask/bid + fees"),
        supersedes=None,
        observation_only=True,
    )


def obs(r, i=0, **kw) -> fx.Observation:
    """Synthetic unit fixture.

    Production intake must use Observation.from_records. Unit tests explicitly
    mark these synthetic rows verified so evaluator math can be tested in
    isolation; direct construction alone never gains that flag.
    """
    data = dict(
        signal_id=f"sg_{i}",
        strategy="322",
        strategy_epoch="2026Q4_v1",
        result_r=r,
        executed=False,
        data_integrity=IS.VALID,
        signal_integrity=IS.VALID,
        execution_integrity=IS.NOT_APPLICABLE,
        prospective_catch=True,
        pnl_basis="paper_equivalent",
    )
    data.update(kw)
    row = fx.Observation(**data)
    object.__setattr__(row, "canonical_provenance", True)
    object.__setattr__(row, "epoch_definition_sha256", EPOCH_SHA)
    return row


def series(values):
    return [obs(r, i) for i, r in enumerate(values)]


def fit(ep, rows, policy=fx.FitnessPolicy()):
    return fx.evaluate_fitness(ep, rows, policy, registry=EpochRegistry((ep,)))


HEALTHY = [2.0, -1.0, 2.0, -1.0, 2.0, 2.0, -1.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0]
FAILING = [-1.0] * 12


def granted() -> fx.AuthorityState:
    state = fx.AuthorityState(strategy="322", epoch="2026Q4_v1")
    return fx.human_grant(state, approved_by="operator", approval_ref="GO-2026-10-06-1", at=AT)


# ── 1. failure revokes ──────────────────────────────────────────────────────


def test_failing_prospective_series_is_fail_candidate_at_checkpoint():
    verdict = fit(epoch(), series(FAILING))
    assert verdict.state is fx.FitnessState.FAIL_CANDIDATE
    assert verdict.at_checkpoint == 10
    assert verdict.p_cumulative_r < 0.02


def test_failure_revokes_execution_authority():
    state = granted()
    assert state.execution_authority is True
    after = fx.apply_verdict(state, fit(epoch(), series(FAILING)), at=AT)
    assert after.status is fx.FitnessState.SUSPENDED
    assert after.execution_authority is False
    assert after.history[-1].actor == fx.EVALUATOR_ACTOR


def test_holding_authority_without_oos_reference_is_revoked_but_research_epoch_is_not_suspended():
    no_ref = epoch(oos=None)
    verdict = fit(no_ref, series(HEALTHY))
    assert "no_oos_reference" in verdict.reasons
    revoked = fx.apply_verdict(granted(), verdict, at=AT)
    assert revoked.execution_authority is False and revoked.status is fx.FitnessState.SUSPENDED
    research = fx.apply_verdict(fx.AuthorityState("322", "2026Q4_v1"), verdict, at=AT)
    assert research.status is fx.FitnessState.COLLECTING and research.execution_authority is False


def test_fail_level_tail_before_first_checkpoint_only_warns():
    verdict = fit(epoch(), series([-1.0] * 8))
    assert verdict.state is fx.FitnessState.WARNING
    assert verdict.at_checkpoint is None


# ── 2. healthy cannot turn trading on ───────────────────────────────────────


def test_healthy_verdict_never_grants_authority():
    verdict = fit(epoch(), series(HEALTHY))
    assert verdict.state is fx.FitnessState.COLLECTING
    state = fx.AuthorityState(strategy="322", epoch="2026Q4_v1")
    for _ in range(5):
        state = fx.apply_verdict(state, verdict, at=AT)
    assert state.execution_authority is False


def test_healthy_verdict_never_restores_a_suspended_strategy():
    suspended = fx.apply_verdict(granted(), fit(epoch(), series(FAILING)), at=AT)
    healthy = fit(epoch(), series(HEALTHY))
    after = fx.apply_verdict(suspended, healthy, at=AT)
    assert after.status is fx.FitnessState.SUSPENDED
    assert after.execution_authority is False


def test_apply_verdict_never_sets_authority_true_for_any_verdict_state():
    base = fit(epoch(), series(HEALTHY))
    for st in fx.FitnessState:
        verdict = fx.FitnessVerdict(**{**base.__dict__, "state": st})
        if st.value not in fx.EVALUATOR_STATES:
            # SUSPENDED/RETIRED are authority states, never evaluator verdicts.
            with pytest.raises(ValueError, match="evaluator cannot emit"):
                fx.apply_verdict(granted(), verdict, at=AT)
            continue
        out = fx.apply_verdict(fx.AuthorityState("322", "2026Q4_v1"), verdict, at=AT)
        assert out.execution_authority is False


def test_evaluation_path_never_references_human_grant():
    """Static proof: no evaluator code path can call the grant function."""
    for fn in (fx.evaluate_fitness, fx.apply_verdict, fx.bootstrap_tail_probabilities, fx.classify):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        assert "human_grant" not in names, fn.__name__


def test_human_grant_requires_named_approver_and_reference():
    state = fx.AuthorityState("322", "2026Q4_v1")
    with pytest.raises(PermissionError):
        fx.human_grant(state, approved_by="", approval_ref="x", at=AT)
    with pytest.raises(PermissionError):
        fx.human_grant(state, approved_by=fx.EVALUATOR_ACTOR, approval_ref="x", at=AT)
    with pytest.raises(PermissionError):
        fx.human_grant(state, approved_by="operator", approval_ref=" ", at=AT)


def test_human_restore_from_suspension_is_explicit_and_retired_is_final():
    suspended = fx.apply_verdict(granted(), fit(epoch(), series(FAILING)), at=AT)
    with pytest.raises(PermissionError, match="restore_from_suspension"):
        fx.human_grant(suspended, approved_by="operator", approval_ref="GO-2", at=AT)
    restored = fx.human_grant(
        suspended, approved_by="operator", approval_ref="GO-2", at=AT, restore_from_suspension=True
    )
    assert restored.execution_authority is True
    retired = fx.human_retire(restored, approved_by="operator", reason="edge gone", at=AT)
    assert retired.execution_authority is False
    with pytest.raises(PermissionError, match="RETIRED"):
        fx.human_grant(retired, approved_by="operator", approval_ref="GO-3", at=AT, restore_from_suspension=True)


# ── 3. observer keeps collecting ────────────────────────────────────────────


def test_observer_stays_enabled_through_suspension_and_retirement():
    suspended = fx.apply_verdict(granted(), fit(epoch(), series(FAILING)), at=AT)
    assert suspended.observer_enabled is True
    retired = fx.human_retire(suspended, approved_by="operator", reason="x", at=AT)
    assert retired.observer_enabled is True


def test_suspended_strategy_still_accumulates_and_evaluates_observations():
    suspended = fx.apply_verdict(granted(), fit(epoch(), series(FAILING)), at=AT)
    more = fit(epoch(), series(FAILING + HEALTHY))
    assert more.valid_n == len(FAILING) + len(HEALTHY)
    assert fx.apply_verdict(suspended, more, at=AT).observer_enabled is True


# ── evidence handling ───────────────────────────────────────────────────────


def test_only_valid_same_epoch_observations_judge_the_strategy():
    rows = series(HEALTHY) + [
        obs(-5.0, 100, data_integrity=IS.DEGRADED),
        obs(-5.0, 101, signal_integrity=IS.INVALID),
        obs(-5.0, 102, executed=True, pnl_basis="executed", execution_integrity=IS.INVALID),
        obs(None, 103),
        obs(-5.0, 104, strategy_epoch="2026Q3_v9"),
    ]
    verdict = fit(epoch(), rows)
    assert verdict.valid_n == len(HEALTHY)
    assert verdict.excluded == {
        "data_integrity": 1,
        "execution_integrity": 1,
        "result_unavailable": 1,
        "signal_integrity": 1,
    }


def test_high_invalid_share_warns_on_evidence_quality():
    rows = series(HEALTHY[:10]) + [obs(None, 200 + i) for i in range(6)]
    verdict = fit(epoch(), rows)
    assert verdict.state is fx.FitnessState.WARNING
    assert any("evidence_quality" in r for r in verdict.reasons)


def test_not_profit_factor_drawdown_alone_can_fail():
    # Positive total but a drawdown the OOS distribution essentially never produces.
    rows = [10.0] + [-1.0] * 14
    verdict = fit(epoch(), series(rows))
    assert verdict.p_drawdown < 0.02
    assert verdict.state is fx.FitnessState.FAIL_CANDIDATE


def test_verdict_is_deterministic():
    a = fit(epoch(), series(HEALTHY))
    b = fit(epoch(), series(HEALTHY))
    assert (a.p_cumulative_r, a.p_drawdown) == (b.p_cumulative_r, b.p_drawdown)


def test_policy_rejects_incoherent_thresholds():
    with pytest.raises(ValueError):
        fx.FitnessPolicy(review_checkpoints=(15, 10))
    with pytest.raises(ValueError):
        fx.FitnessPolicy(fail_tail_probability=0.2, warn_tail_probability=0.1)


def test_checkpoints_are_policy_not_hard_coded():
    late = fx.FitnessPolicy(review_checkpoints=(30,))
    assert fit(epoch(), series(FAILING), late).state is fx.FitnessState.WARNING


def test_direct_observation_construction_cannot_judge_fitness():
    row = fx.Observation(
        signal_id="sg_direct",
        strategy="322",
        strategy_epoch="2026Q4_v1",
        result_r=2.0,
        executed=False,
        data_integrity=IS.VALID,
        signal_integrity=IS.VALID,
        execution_integrity=IS.NOT_APPLICABLE,
        prospective_catch=True,
        pnl_basis="paper_equivalent",
    )
    assert row.canonical_provenance is False
    assert fx.classify(row, epoch()) == "provenance_unverified"


def _canonical_outcome_record(signal_record, r, **overrides):
    from options_evidence import outcome as oc

    def measured(value=None, status="UNAVAILABLE"):
        if status == "UNAVAILABLE":
            return {"value": None, "status": status, "reason": "not measured", "source": ""}
        return {"value": value, "status": status, "reason": "", "source": "test" if status == "OBSERVED" else ""}

    record = {
        "schema": oc.SCHEMA,
        "signal_id": signal_record["signal_id"],
        "structure_id": signal_record["structure_id"],
        "strategy_epoch": signal_record["strategy_epoch"],
        "executed": False,
        "pnl_basis": "paper_equivalent",
        "resolution_state": signal_record["resolution_state"],
        "prospective_catch": signal_record["prospective_catch"],
        "result_r": measured(r, "DERIVED") if r is not None else measured(),
        "mae_r": measured(),
        "mfe_r": measured(),
        "gross_pnl": measured(),
        "net_pnl": measured(),
        "data_integrity": "VALID",
        "signal_integrity": signal_record["signal_integrity"],
        "execution_integrity": "NOT_APPLICABLE",
    }
    record.update(overrides)
    return record


def _canonical_catch_with_oos():
    from dataclasses import replace as dc_replace

    from options_evidence import signal as sg
    from tests.test_options_prospective_signal import caught, registered_epoch

    ep = dc_replace(
        registered_epoch(),
        oos_reference=OOSReference("oos", "docs/r.json", "b" * 64, OOS, "ask/bid + fees"),
    )
    registry = EpochRegistry((ep,))
    signal = caught(sg.SignalJournal(registry))
    return ep, registry, sg.to_record(signal)


def test_observation_from_records_requires_exact_bool_and_numeric_types():
    _, registry, signal_record = _canonical_catch_with_oos()
    good = _canonical_outcome_record(signal_record, 1.0)
    row = fx.Observation.from_records(signal_record, good, registry=registry)
    assert row.canonical_provenance is True
    assert row.result_r == 1.0

    bad_bool = dict(good)
    bad_bool["executed"] = "false"
    with pytest.raises(ValueError, match="exact bool"):
        fx.Observation.from_records(signal_record, bad_bool, registry=registry)

    bad_number = dict(good)
    bad_number["mae_r"] = {"value": True, "status": "DERIVED", "reason": "", "source": ""}
    with pytest.raises(ValueError, match="finite number"):
        fx.Observation.from_records(signal_record, bad_number, registry=registry)


def test_canonical_prospective_catch_judges_fitness():
    ep, registry, signal_record = _canonical_catch_with_oos()
    rows = [
        fx.Observation.from_records(signal_record, _canonical_outcome_record(signal_record, 1.0), registry=registry)
    ]
    verdict = fx.evaluate_fitness(ep, rows, registry=registry)
    assert verdict.valid_n == 1
    assert verdict.excluded == {}


def test_hand_built_epoch_cannot_judge_registered_production_evidence():
    ep, registry, signal_record = _canonical_catch_with_oos()
    row = fx.Observation.from_records(signal_record, _canonical_outcome_record(signal_record, 1.0), registry=registry)
    from dataclasses import replace as dc_replace

    forged = dc_replace(ep, notes="forged object")
    with pytest.raises(ValueError, match="exact registered epoch"):
        fx.evaluate_fitness(forged, [row], registry=registry)


def test_fitness_module_is_isolated_from_risk_and_execution():
    tree = ast.parse(Path(fx.__file__).read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for forbidden in ("risk", "execution", "options_manager", "alert_ranker", "webhook", "broker"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in modules), forbidden


# ── integration with canonical signal provenance ─────────────────────────────


def test_forged_signal_record_cannot_enter_fitness():
    _, registry, signal_record = _canonical_catch_with_oos()
    forged = dict(signal_record)
    forged["data_source"] = "forged:source"
    outcome = _canonical_outcome_record(signal_record, 1.0)
    with pytest.raises(ValueError, match="invalid canonical signal record"):
        fx.Observation.from_records(forged, outcome, registry=registry)


# ── independent-review blockers on 4676024 ──────────────────────────────────


def test_duplicate_or_replayed_signal_is_never_independent_evidence():
    ep, registry, signal_record = _canonical_catch_with_oos()
    row = fx.Observation.from_records(signal_record, _canonical_outcome_record(signal_record, -1.0), registry=registry)
    with pytest.raises(ValueError, match="duplicate observation"):
        fx.evaluate_fitness(ep, [row] * 10, registry=registry)
    other = fx.Observation.from_records(signal_record, _canonical_outcome_record(signal_record, 2.0), registry=registry)
    with pytest.raises(ValueError, match="duplicate observation"):
        fx.evaluate_fitness(ep, [row, other], registry=registry)
    assert fx.evaluate_fitness(ep, [row], registry=registry).valid_n == 1


def test_evidence_verified_under_another_definition_never_judges():
    from dataclasses import replace as dc_replace

    from tests.test_options_prospective_signal import registered_epoch

    ep_a, registry_a, signal_record = _canonical_catch_with_oos()
    row = fx.Observation.from_records(signal_record, _canonical_outcome_record(signal_record, -1.0), registry=registry_a)
    assert row.epoch_definition_sha256 == ep_a.definition_sha256
    # Same names, different definition (another universe), its own registry.
    base_b = registered_epoch(universe="PRIMARY_20", arm_source="public_regular_30m", provisional_source="alpaca_sip")
    ep_b = dc_replace(base_b, oos_reference=ep_a.oos_reference)
    assert ep_b.key == ep_a.key and ep_b.definition_sha256 != ep_a.definition_sha256
    verdict = fx.evaluate_fitness(ep_b, [row], registry=EpochRegistry((ep_b,)))
    assert verdict.valid_n == 0 and verdict.excluded == {"epoch_definition_mismatch": 1}
    assert verdict.epoch_definition_sha256 == ep_b.definition_sha256


def _bad_epoch(**kw):
    from dataclasses import replace as dc_replace

    return dc_replace(epoch(), **kw)


@pytest.mark.parametrize("kw, match", [
    ({"definition_sha256": "f" * 64}, "definition_sha256 does not match"),
    ({"thresholds": {"max_capture_lag_seconds": 999}}, "definition_sha256 does not match"),
    ({"effective_from": None}, "effective_from"),
    ({"source_commit": None}, "source_commit"),
    ({"preregistration_doc": None}, "preregistration_doc"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, (), "fees")}, "non-empty tuple"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, [2.0, -1.0], "fees")}, "non-empty tuple"),
    ({"oos_reference": OOSReference("oos", "r.json", "bad", (2.0, -1.0), "fees")}, "sha256"),
    ({"oos_reference": OOSReference("", "r.json", "b" * 64, (2.0, -1.0), "fees")}, "non-blank"),
    ({"oos_reference": OOSReference("oos", "", "b" * 64, (2.0, -1.0), "fees")}, "non-blank"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, (2.0, -1.0), "")}, "non-blank"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, (float("nan"),) * 4, "fees")}, "finite"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, (float("inf"), 1.0), "fees")}, "finite"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, (True, -1.0), "fees")}, "finite"),
    ({"oos_reference": OOSReference("oos", "r.json", "b" * 64, ("2.0", -1.0), "fees")}, "finite"),
])
def test_caller_built_registry_cannot_skip_epoch_integrity(kw, match):
    bad = _bad_epoch(**kw)
    with pytest.raises(ValueError, match=match):
        fx.evaluate_fitness(bad, series(FAILING), registry=EpochRegistry((bad,)))


def test_incoherent_authority_state_is_refused_and_sticky_states_never_hold_authority():
    for status in (fx.FitnessState.SUSPENDED, fx.FitnessState.RETIRED):
        with pytest.raises(ValueError, match="cannot hold execution authority"):
            fx.AuthorityState("322", "2026Q4_v1", status=status, execution_authority=True)
    for field_name, value in (("execution_authority", "false"), ("observer_enabled", 0), ("status", "COLLECTING")):
        with pytest.raises(ValueError):
            fx.AuthorityState("322", "2026Q4_v1", **{field_name: value})
    # Even a state smuggled past __post_init__ is revoked by the sticky branch.
    from dataclasses import replace as dc_replace

    smuggled = dc_replace(fx.AuthorityState("322", "2026Q4_v1"), status=fx.FitnessState.SUSPENDED)
    object.__setattr__(smuggled, "execution_authority", True)
    after = fx.apply_verdict(smuggled, fit(epoch(), series(FAILING)), at=AT)
    assert after.execution_authority is False and after.status is fx.FitnessState.SUSPENDED


@pytest.mark.parametrize("state", [fx.FitnessState.SUSPENDED, fx.FitnessState.RETIRED])
def test_hand_built_authority_state_verdict_is_refused(state):
    verdict = fx.FitnessVerdict(**{**fit(epoch(), series(HEALTHY)).__dict__, "state": state, "reasons": ()})
    holder = granted()
    with pytest.raises(ValueError, match="evaluator cannot emit"):
        fx.apply_verdict(holder, verdict, at=AT)
    # A later real failure still revokes.
    after = fx.apply_verdict(holder, fit(epoch(), series(FAILING)), at=AT)
    assert after.execution_authority is False and after.status is fx.FitnessState.SUSPENDED
