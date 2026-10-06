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
