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
from options_evidence.strategy_epochs import EpochStatus, OOSReference, StrategyEpoch

AT = datetime(2026, 10, 6, 20, tzinfo=timezone.utc)
# Untouched OOS: 50% win at +2R, 50% loss at -1R  -> mean +0.5R.
OOS = tuple([2.0, -1.0] * 30)


def epoch(oos=OOS) -> StrategyEpoch:
    return StrategyEpoch(
        strategy="322",
        epoch="2026Q4_v1",
        status=EpochStatus.FROZEN,
        definition={},
        thresholds={},
        definition_sha256="f" * 64,
        effective_from=AT,
        effective_until=None,
        source_commit="a" * 40,
        preregistration_doc="docs/x.md",
        oos_reference=None
        if oos is None
        else OOSReference("oos", "docs/r.json", "b" * 64, tuple(oos), "ask/bid + fees"),
        supersedes=None,
        observation_only=False,
    )


def obs(r, i=0, **kw) -> fx.Observation:
    data = dict(
        signal_id=f"sg_{i}",
        strategy="322",
        strategy_epoch="2026Q4_v1",
        result_r=r,
        executed=False,
        data_integrity=IS.VALID,
        signal_integrity=IS.VALID,
        execution_integrity=IS.NOT_APPLICABLE,
    )
    data.update(kw)
    return fx.Observation(**data)


def series(values):
    return [obs(r, i) for i, r in enumerate(values)]


HEALTHY = [2.0, -1.0, 2.0, -1.0, 2.0, 2.0, -1.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0, 2.0, -1.0]
FAILING = [-1.0] * 12


def granted() -> fx.AuthorityState:
    state = fx.AuthorityState(strategy="322", epoch="2026Q4_v1")
    return fx.human_grant(state, approved_by="operator", approval_ref="GO-2026-10-06-1", at=AT)


# ── 1. failure revokes ──────────────────────────────────────────────────────


def test_failing_prospective_series_is_fail_candidate_at_checkpoint():
    verdict = fx.evaluate_fitness(epoch(), series(FAILING))
    assert verdict.state is fx.FitnessState.FAIL_CANDIDATE
    assert verdict.at_checkpoint == 10
    assert verdict.p_cumulative_r < 0.02


def test_failure_revokes_execution_authority():
    state = granted()
    assert state.execution_authority is True
    after = fx.apply_verdict(state, fx.evaluate_fitness(epoch(), series(FAILING)), at=AT)
    assert after.status is fx.FitnessState.SUSPENDED
    assert after.execution_authority is False
    assert after.history[-1].actor == fx.EVALUATOR_ACTOR


def test_holding_authority_without_oos_reference_is_revoked_but_research_epoch_is_not_suspended():
    no_ref = epoch(oos=None)
    verdict = fx.evaluate_fitness(no_ref, series(HEALTHY))
    assert "no_oos_reference" in verdict.reasons
    revoked = fx.apply_verdict(granted(), verdict, at=AT)
    assert revoked.execution_authority is False and revoked.status is fx.FitnessState.SUSPENDED
    research = fx.apply_verdict(fx.AuthorityState("322", "2026Q4_v1"), verdict, at=AT)
    assert research.status is fx.FitnessState.COLLECTING and research.execution_authority is False


def test_fail_level_tail_before_first_checkpoint_only_warns():
    verdict = fx.evaluate_fitness(epoch(), series([-1.0] * 8))
    assert verdict.state is fx.FitnessState.WARNING
    assert verdict.at_checkpoint is None


# ── 2. healthy cannot turn trading on ───────────────────────────────────────


def test_healthy_verdict_never_grants_authority():
    verdict = fx.evaluate_fitness(epoch(), series(HEALTHY))
    assert verdict.state is fx.FitnessState.COLLECTING
    state = fx.AuthorityState(strategy="322", epoch="2026Q4_v1")
    for _ in range(5):
        state = fx.apply_verdict(state, verdict, at=AT)
    assert state.execution_authority is False


def test_healthy_verdict_never_restores_a_suspended_strategy():
    suspended = fx.apply_verdict(granted(), fx.evaluate_fitness(epoch(), series(FAILING)), at=AT)
    healthy = fx.evaluate_fitness(epoch(), series(HEALTHY))
    after = fx.apply_verdict(suspended, healthy, at=AT)
    assert after.status is fx.FitnessState.SUSPENDED
    assert after.execution_authority is False


def test_apply_verdict_never_sets_authority_true_for_any_verdict_state():
    base = fx.evaluate_fitness(epoch(), series(HEALTHY))
    for st in fx.FitnessState:
        verdict = fx.FitnessVerdict(**{**base.__dict__, "state": st})
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
    suspended = fx.apply_verdict(granted(), fx.evaluate_fitness(epoch(), series(FAILING)), at=AT)
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
    suspended = fx.apply_verdict(granted(), fx.evaluate_fitness(epoch(), series(FAILING)), at=AT)
    assert suspended.observer_enabled is True
    retired = fx.human_retire(suspended, approved_by="operator", reason="x", at=AT)
    assert retired.observer_enabled is True


def test_suspended_strategy_still_accumulates_and_evaluates_observations():
    suspended = fx.apply_verdict(granted(), fx.evaluate_fitness(epoch(), series(FAILING)), at=AT)
    more = fx.evaluate_fitness(epoch(), series(FAILING + HEALTHY))
    assert more.valid_n == len(FAILING) + len(HEALTHY)
    assert fx.apply_verdict(suspended, more, at=AT).observer_enabled is True


# ── evidence handling ───────────────────────────────────────────────────────


def test_only_valid_same_epoch_observations_judge_the_strategy():
    rows = series(HEALTHY) + [
        obs(-5.0, 100, data_integrity=IS.DEGRADED),
        obs(-5.0, 101, signal_integrity=IS.INVALID),
        obs(-5.0, 102, executed=True, execution_integrity=IS.INVALID),
        obs(None, 103),
        obs(-5.0, 104, strategy_epoch="2026Q3_v9"),
    ]
    verdict = fx.evaluate_fitness(epoch(), rows)
    assert verdict.valid_n == len(HEALTHY)
    assert verdict.excluded == {
        "data_integrity": 1,
        "execution_integrity": 1,
        "result_unavailable": 1,
        "signal_integrity": 1,
    }


def test_high_invalid_share_warns_on_evidence_quality():
    rows = series(HEALTHY[:10]) + [obs(None, 200 + i) for i in range(6)]
    verdict = fx.evaluate_fitness(epoch(), rows)
    assert verdict.state is fx.FitnessState.WARNING
    assert any("evidence_quality" in r for r in verdict.reasons)


def test_not_profit_factor_drawdown_alone_can_fail():
    # Positive total but a drawdown the OOS distribution essentially never produces.
    rows = [10.0] + [-1.0] * 14
    verdict = fx.evaluate_fitness(epoch(), series(rows))
    assert verdict.p_drawdown < 0.02
    assert verdict.state is fx.FitnessState.FAIL_CANDIDATE


def test_verdict_is_deterministic():
    a = fx.evaluate_fitness(epoch(), series(HEALTHY))
    b = fx.evaluate_fitness(epoch(), series(HEALTHY))
    assert (a.p_cumulative_r, a.p_drawdown) == (b.p_cumulative_r, b.p_drawdown)


def test_policy_rejects_incoherent_thresholds():
    with pytest.raises(ValueError):
        fx.FitnessPolicy(review_checkpoints=(15, 10))
    with pytest.raises(ValueError):
        fx.FitnessPolicy(fail_tail_probability=0.2, warn_tail_probability=0.1)


def test_checkpoints_are_policy_not_hard_coded():
    late = fx.FitnessPolicy(review_checkpoints=(30,))
    assert fx.evaluate_fitness(epoch(), series(FAILING), late).state is fx.FitnessState.WARNING


def test_observation_from_records_never_turns_unknown_into_zero():
    signal = {"signal_id": "sg_1", "strategy": "322", "strategy_epoch": "2026Q4_v1",
              "data_integrity": "VALID", "signal_integrity": "VALID"}
    outcome = {
        "signal_id": "sg_1",
        "executed": False,
        "result_r": {"value": None, "status": "UNAVAILABLE", "reason": "gap"},
        "data_integrity": "VALID",
        "signal_integrity": "VALID",
    }
    o = fx.Observation.from_records(signal, outcome)
    assert o.result_r is None
    assert fx.classify(o, epoch()) == "result_unavailable"


def test_fitness_module_is_isolated_from_risk_and_execution():
    tree = ast.parse(Path(fx.__file__).read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for forbidden in ("risk", "execution", "options_manager", "alert_ranker", "webhook", "broker"):
        assert not any(m == forbidden or m.startswith(forbidden + ".") for m in modules), forbidden


# ── integration with the canonical signal / #1145 capture ───────────────────


def _outcome_record(signal_id, r, **kw):
    return {
        "signal_id": signal_id,
        "executed": False,
        "result_r": {"value": r, "status": "DERIVED", "reason": "", "source": ""},
        "data_integrity": "VALID",
        "signal_integrity": "VALID",  # an outcome row cannot launder signal integrity
        **kw,
    }


def test_late_or_gap_capture_never_judges_fitness_even_with_a_valid_outcome_row(tmp_path):
    from dataclasses import replace as dc_replace

    from options_evidence import capture_adapter as ca
    from options_evidence import signal as sg
    from tests.test_options_capture_adapter import _engine, _print, et

    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.09, feed="sip", trade_id="s1")],
    )
    engine.run(now=et(2026, 10, 5, 10, 16, 45))  # cold start → MISSED_LATE
    fold = ca.fold_capture_rows(
        ca.read_capture_journal(engine.journal.path), strategy="322", strategy_epoch="2026Q4_v1"
    )
    records = [sg.to_record(s) for s in fold.journal.signals()]
    assert records and all(r["signal_integrity"] != "VALID" for r in records if r["lifecycle_state"] == "MISSED_LATE")
    obs = [fx.Observation.from_records(r, _outcome_record(r["signal_id"], -1.0)) for r in records]
    verdict = fx.evaluate_fitness(epoch(), obs)
    assert verdict.valid_n == 0
    assert sum(verdict.excluded.values()) == len(records)


def test_only_prospective_catches_judge_fitness(tmp_path):
    from options_evidence import capture_adapter as ca
    from options_evidence import signal as sg
    from tests.test_options_capture_adapter import _engine, _print, et

    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    engine.run(now=et(2026, 10, 5, 10, 46, 0))
    fold = ca.fold_capture_rows(
        ca.read_capture_journal(engine.journal.path), strategy="322", strategy_epoch="2026Q4_v1"
    )
    records = [sg.to_record(s) for s in fold.journal.signals()]
    catches = [r for r in records if r["lifecycle_state"] == "TRIGGERED" and r["signal_integrity"] == "VALID"]
    obs = [fx.Observation.from_records(r, _outcome_record(r["signal_id"], 1.0)) for r in records]
    verdict = fx.evaluate_fitness(epoch(), obs)
    assert verdict.valid_n == len(catches) >= 1
    # Fitness never touches authority on its own; revocation still requires apply_verdict.
    assert all(not r["execution_authority"] for r in records)
