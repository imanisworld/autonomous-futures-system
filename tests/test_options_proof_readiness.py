from __future__ import annotations

from datetime import datetime, timezone

import pytest

from options_evidence import proof_readiness as pr
from options_evidence.strategy_epochs import load_registry
from tests.test_options_strategy_epochs import _epoch, _registry

AT = datetime(2026, 10, 6, 20, tzinfo=timezone.utc)


def all_runtime(passed=True):
    return {name: pr.EvidenceRef(passed, f"status-run-{name}", AT) for name in pr.RUNTIME}


def test_committed_122_epoch_is_not_ready_and_names_why():
    readiness = pr.evaluate_readiness(
        load_registry(), strategy="options_122", epoch="122-IEX-E1", runtime_evidence=all_runtime()
    )
    assert readiness.ready is False
    blockers = "\n".join(readiness.blockers)
    assert "outcome_definitions_frozen" in blockers
    assert "oos_reference_registered" in blockers
    assert "costs_frozen" in blockers


def test_fully_specified_epoch_with_runtime_proof_is_ready(tmp_path):
    registry = _registry(tmp_path, _epoch())
    readiness = pr.evaluate_readiness(registry, strategy="322", epoch="2026Q4_v1", runtime_evidence=all_runtime())
    assert readiness.ready is True
    assert readiness.blockers == ()


@pytest.mark.parametrize("missing", pr.RUNTIME)
def test_each_runtime_prerequisite_blocks_when_absent_or_failed(tmp_path, missing):
    registry = _registry(tmp_path, _epoch())
    evidence = all_runtime()
    del evidence[missing]
    assert not pr.evaluate_readiness(registry, strategy="322", epoch="2026Q4_v1", runtime_evidence=evidence).ready
    evidence = all_runtime()
    evidence[missing] = pr.EvidenceRef(False, "status-run", AT, "capture lag 340s")
    out = pr.evaluate_readiness(registry, strategy="322", epoch="2026Q4_v1", runtime_evidence=evidence)
    assert not out.ready and any(missing in b for b in out.blockers)


def test_derived_checks_cannot_be_asserted_by_hand(tmp_path):
    registry = _registry(tmp_path, _epoch(oos_reference=None))
    with pytest.raises(ValueError, match="derived checks cannot be asserted"):
        pr.evaluate_readiness(
            registry,
            strategy="322",
            epoch="2026Q4_v1",
            runtime_evidence={**all_runtime(), "oos_reference_registered": pr.EvidenceRef(True, "me", AT)},
        )


def test_draft_and_unknown_epochs_are_not_ready(tmp_path):
    registry = _registry(tmp_path, _epoch(status="DRAFT", effective_from=None, source_commit=None))
    assert not pr.evaluate_readiness(registry, strategy="322", epoch="2026Q4_v1", runtime_evidence=all_runtime()).ready
    assert not pr.evaluate_readiness(registry, strategy="322", epoch="nope", runtime_evidence=all_runtime()).ready


def test_evidence_needs_source_and_timezone():
    with pytest.raises(ValueError):
        pr.EvidenceRef(True, "", AT)
    with pytest.raises(ValueError):
        pr.EvidenceRef(True, "x", datetime(2026, 10, 6))


# --- capture_integrity derived from the real #1145 setup-capture engine ---------------

def _capture_state(tmp_path, runs, **kw):
    from alert_ranker.setup_capture_engine import BarAvailabilityOracle
    from alert_ranker.setup_capture_store import SetupCaptureJournal
    from tests.test_options_setup_capture import make_engine, oct2_30m, oct2_session_closes

    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    engine = make_engine(tmp_path, oracle, **kw)
    for now in runs:
        engine.run(now=now)
    return list(SetupCaptureJournal(engine.journal.path, create=False).peek_state()["current"].values())


def test_capture_integrity_passes_only_on_1145_prospective_catches(tmp_path):
    from alert_ranker.setup_capture import catch_count
    from tests.test_options_setup_capture import _print, et

    rows = _capture_state(
        tmp_path,
        [et(2026, 10, 2, 16, 16), et(2026, 10, 5, 9, 31, 0), et(2026, 10, 5, 10, 46, 0)],
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    ref = pr.capture_integrity_evidence(rows, source="options_setup_capture status", observed_at=AT)
    assert ref.passed is True
    assert f"catch_count={catch_count(rows)}" in ref.note


def test_capture_integrity_fails_on_cold_start_missed_late(tmp_path):
    from tests.test_options_setup_capture import _print, et

    rows = _capture_state(
        tmp_path,
        [et(2026, 10, 5, 10, 16, 45)],  # first run after the trigger -> MISSED_LATE
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    ref = pr.capture_integrity_evidence(rows, source="status", observed_at=AT)
    assert ref.passed is False and "missed_late=" in ref.note and "missed_late=0" not in ref.note


def test_capture_integrity_fails_closed_on_empty_or_unproven_windows():
    assert pr.capture_integrity_evidence([], source="s", observed_at=AT).passed is False
    pending = {"status": "TRIGGERED", "prospective_catch": True, "capture_late": False, "gap_through": False}
    assert pr.capture_integrity_evidence([pending], source="s", observed_at=AT).passed is False
    for bad in ("DATA_BLOCKED", "AMBIGUOUS"):
        assert pr.capture_integrity_evidence([{"status": bad}], source="s", observed_at=AT).passed is False


def test_122_stays_not_ready_even_with_passing_capture_evidence(tmp_path):
    from tests.test_options_setup_capture import _print, et

    rows = _capture_state(
        tmp_path,
        [et(2026, 10, 2, 16, 16), et(2026, 10, 5, 9, 31, 0), et(2026, 10, 5, 10, 46, 0)],
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    evidence = {**all_runtime(), "capture_integrity": pr.capture_integrity_evidence(rows, source="s", observed_at=AT)}
    out = pr.evaluate_readiness(load_registry(), strategy="options_122", epoch="122-IEX-E1", runtime_evidence=evidence)
    assert out.ready is False
    assert {b.split(":")[0] for b in out.blockers} == {
        "outcome_definitions_frozen", "oos_reference_registered", "costs_frozen",
    }


def test_readiness_module_never_declares_day_one_or_touches_execution():
    import ast
    from pathlib import Path

    src = Path(pr.__file__).read_text()
    mods = {n.module for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("options_manager", "execution", "risk")) for m in mods)
    assert "day_1" not in src.lower().replace("day 1", "")
