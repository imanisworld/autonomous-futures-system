"""Governance checks for the unapproved 212 continuation outcome draft.

These tests do not score episodes, open outcome files, or execute an adapter.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ops.research_experiment_runner import (
    classify_experiment_result,
    discover_specs,
    execute_experiment,
    run_validation,
)

ROOT = Path(__file__).resolve().parents[1]
DRAFT_REL = "docs/research-experiment-specs/E-2026-10-02-options-212c-floor-outcome-01.json"
CLOSED_REL = "docs/research-experiment-specs/E-2026-09-25-options-212c-target-geometry-01.json"
CLOSED_REPORT = (
    "docs/research-evidence/"
    "T-2026-09-25-prereg-options-212c-target-geometry-2026-09-25-01/runner_report.json"
)
CLOSED_REPORT_SHA256 = (
    "0d47bf46fd62748e9e6b67a248d2ef6ef76aad072e6ad1d2fd43192f7f3343e8"
)


def _draft() -> dict:
    return json.loads((ROOT / DRAFT_REL).read_text(encoding="utf-8"))


def test_draft_is_registered_and_not_executable(tmp_path: Path) -> None:
    spec = _draft()
    assert spec["status"] == "DRAFT"
    assert spec["approved_by"] is None
    assert spec["approved_at"] is None
    assert spec["acceptance_criteria"] is None
    assert spec["rejection_criteria"] is None
    assert spec["data"]["window"]["start"] == "2026-10-05"
    assert spec["setup_type"] == "options_212c_floor_underlying_outcome"
    assert "2026-09-09" not in spec["population"]
    assert "2026-09-15" not in spec["population"]

    structural = run_validation(ROOT, ROOT / DRAFT_REL, for_execution=False)
    assert structural.status == "VALID"
    assert structural.result == "INCONCLUSIVE"

    blocked = execute_experiment(
        ROOT,
        ROOT / DRAFT_REL,
        write_evidence=True,
        evidence_dir=tmp_path / "evidence",
    )
    assert blocked.status == "BLOCKED"
    assert blocked.result == "BLOCKED"
    assert not (tmp_path / "evidence").exists()

    approved = discover_specs(ROOT)
    assert [path.name for path in approved] == [
        "E-2026-09-25-options-212c-target-geometry-01.json"
    ]


def test_entry_gap_and_blind_readout_are_frozen() -> None:
    spec = _draft()
    prereg = (ROOT / spec["prereg_path"]).read_text(encoding="utf-8")
    execution = json.dumps(spec["execution"])
    assert "measured_entry_price = first_sight_price" in prereg
    assert "measured_entry_price = first_sight_price" in execution
    assert "entry_trigger" in spec["execution"]["entry_logic"]
    assert "WOULD_OTHERWISE_QUALIFY" in spec["execution"]["entry_logic"]
    for phrase in (
        "opens beyond the stop",
        "opens beyond Target 1",
        "beyond Target 2",
        "gap_or_range_spans_stop_and_target",
        "NYSE sessions elapsed",
        "cumulative `floor_ge1r` activation count",
        "INSUFFICIENT SAMPLE",
        "DESCRIPTIVE MEASUREMENT",
        "NOT EVALUATED",
    ):
        assert phrase in prereg
    assert "sessions elapsed" in spec["notes"]
    assert "cumulative floor_ge1r activation count" in spec["notes"]
    assert "confirmatory edge test" in spec["notes"]
    assert spec["acceptance_criteria"] is None


def test_null_criteria_cannot_classify_as_supported() -> None:
    label, details = classify_experiment_result(
        _draft(),
        {"population_size": {"count": 25}, "expectancy": {"value": 2.0}},
        {"population_size": {"count": 25}, "expectancy": {"value": 2.0}},
    )
    assert label == "INCONCLUSIVE"
    assert details["reason"] == "no preregistered acceptance/rejection criteria"


def test_closed_59_episode_artifacts_stay_untouched() -> None:
    closed = json.loads((ROOT / CLOSED_REL).read_text(encoding="utf-8"))
    assert closed["status"] == "APPROVED"
    assert closed["data"]["population_count"] == 59
    assert closed["data"]["dataset_hash"] == (
        "1963db73bccf0fd366eaaa077bb4e9582ed453ff220f1c5e789961096f3f113c"
    )
    report = ROOT / CLOSED_REPORT
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert digest == CLOSED_REPORT_SHA256
