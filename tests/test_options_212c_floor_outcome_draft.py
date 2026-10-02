"""Governance checks for the unapproved 212 continuation outcome draft.

These tests do not score episodes, open outcome files, or execute an adapter.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from alert_ranker.session_calendar import nyse_session_for
from ops.options_212c_floor_outcome_monitor import (
    ACTIVATION_CAP,
    EPISODE_IDENTITY_KEYS,
    EPISODE_SNAPSHOT_FIELDS,
    FIVE_MINUTE_GRID,
    PATH_RECORD_INTEGRITY,
    PATH_RECORD_ROOT,
    PATH_RECORD_VERSION,
    READOUT_FIELDS,
    SESSION_CAP,
    SESSION_SEAL_FIELDS,
    STUDY_ONLY_METRICS,
    STUDY_SCORER_VERSION,
    THRESHOLD_CROSSING_RULE,
    sealed_path_record_relpath,
    study_readout,
)
from ops.research_experiment_runner import (
    classify_experiment_result,
    compute_metrics,
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
    held = "\n".join(spec["held_constant"])
    assert "measured_entry_price = first_sight_price" in prereg
    assert "measured_entry_price = first_sight_price" in execution
    assert "entry_trigger" in spec["execution"]["entry_logic"]
    assert "WOULD_OTHERWISE_QUALIFY" in spec["execution"]["entry_logic"]
    for phrase in (
        "opens beyond the stop",
        "opens beyond Target 1",
        "beyond Target 2",
        "gap_or_range_spans_stop_and_target",
        "sessions_elapsed",
        "stop_condition_met",
        "activation_cap",
        "session_cap",
        STUDY_SCORER_VERSION,
        PATH_RECORD_VERSION,
        PATH_RECORD_ROOT,
        "DATA_INVALID",
        "threshold-crossing session",
        "start` is greater than or equal to `first_sight_at`",
        "structural_risk",
        "floor_target_1",
        "INSUFFICIENT SAMPLE",
        "DESCRIPTIVE MEASUREMENT",
        "NOT EVALUATED",
        "blocked until",
    ):
        assert phrase in prereg
    assert "cumulative `floor_ge1r` activation count" not in prereg
    assert "out-v0.1 outcome semantics are held constant" not in prereg
    assert STUDY_SCORER_VERSION in spec["data"]["dataset_id"]
    assert "out-v0.1" not in spec["data"]["dataset_id"]
    assert "not an input" in spec["data"]["source"]
    assert "cov-v0.1 / ep-v0.1 / out-v0.1" not in held
    assert STUDY_SCORER_VERSION in held
    assert "stop_condition_met" in spec["notes"]
    assert "cumulative floor_ge1r activation count" not in spec["notes"]
    assert "confirmatory edge test" in spec["notes"]
    assert "Approval and scoring are blocked" in spec["notes"]
    assert spec["acceptance_criteria"] is None
    assert set(STUDY_ONLY_METRICS).issubset(spec["required_metrics"])


def _episode(
    session_date: str,
    *,
    symbol: str = "AAPL",
    gate: str = "WOULD_OTHERWISE_QUALIFY",
    bar: str = "14:00:00+00:00",
    direction: str = "LONG",
) -> dict:
    return {
        "family": "STRAT_212_CONTINUATION",
        "symbol": symbol,
        "session_date": session_date,
        "direction": direction,
        "first_bar_start": f"{session_date}T{bar}",
        "gate_bucket_floor": gate,
        "views": [{"outcome": "TARGET_FIRST", "close_r": 3.0}],
        "realized_r": 3.0,
    }


def test_readout_hides_activation_path_and_outcome_fields() -> None:
    sessions = ["2026-10-03", "2026-10-04", "2026-10-05", "2026-09-15"]
    quiet = study_readout(sessions, [])
    loud = study_readout(
        sessions,
        [
            _episode("2026-10-05"),
            _episode("2026-10-05"),
            _episode("2026-10-05", symbol="MSFT", bar="15:00:00+00:00", gate="LATE_AT_FIRST_SIGHT"),
            _episode("2026-09-15", symbol="NVDA"),
        ],
    )
    assert quiet == loud
    assert quiet == {
        "sessions_elapsed": 1,
        "stop_condition_met": False,
        "stop_condition": None,
    }
    assert set(quiet) == set(READOUT_FIELDS)
    rendered = json.dumps(quiet)
    for hidden in ("AAPL", "TARGET_FIRST", "LONG", "realized_r", "activation"):
        assert hidden not in rendered


def _eligible_dates(count: int) -> list[str]:
    found: list[str] = []
    day = date(2026, 10, 5)
    while len(found) < count:
        if nyse_session_for(day) is not None:
            found.append(day.isoformat())
        day += timedelta(days=1)
    return found


def test_readout_names_the_stopping_condition_without_a_count() -> None:
    one_session = ["2026-10-05"]
    below = [
        _episode("2026-10-05", bar=f"14:{index:02d}:00+00:00")
        for index in range(ACTIVATION_CAP - 1)
    ]
    at_cap = below + [_episode("2026-10-05", bar="15:30:00+00:00")]
    assert study_readout(one_session, below)["stop_condition_met"] is False
    fired = study_readout(one_session, at_cap)
    assert fired["sessions_elapsed"] == 1
    assert fired["stop_condition_met"] is True
    assert fired["stop_condition"] == "activation_cap"
    assert str(ACTIVATION_CAP) not in json.dumps(fired)

    almost = _eligible_dates(SESSION_CAP - 1)
    capped_days = _eligible_dates(SESSION_CAP)
    assert study_readout(almost, [])["stop_condition_met"] is False
    capped = study_readout(capped_days, [])
    assert capped["sessions_elapsed"] == SESSION_CAP
    assert capped["stop_condition"] == "session_cap"
    assert date(2026, 11, 26).isoformat() not in capped_days
    assert all(date.fromisoformat(day).weekday() < 5 for day in capped_days)

    both_days = _eligible_dates(SESSION_CAP)
    both_rows = [
        _episode(both_days[-1], bar=f"14:{index:02d}:00+00:00")
        for index in range(ACTIVATION_CAP)
    ]
    both = study_readout(both_days, both_rows)
    assert both["sessions_elapsed"] == SESSION_CAP
    assert both["stop_condition"] == "activation_cap_and_session_cap"


def test_direction_is_inside_the_identity_and_outside_the_readout() -> None:
    session = ["2026-10-05"]
    longs = [
        _episode("2026-10-05", direction="LONG", bar=f"14:{index:02d}:00+00:00")
        for index in range(ACTIVATION_CAP - 1)
    ]
    assert study_readout(session, longs)["stop_condition_met"] is False
    missing_direction = dict(longs[0])
    missing_direction["direction"] = None
    assert study_readout(session, longs[: ACTIVATION_CAP - 2] + [missing_direction])["stop_condition_met"] is False
    fired = study_readout(
        session,
        longs + [_episode("2026-10-05", direction="SHORT", bar="14:00:00+00:00")],
    )
    assert fired["stop_condition"] == "activation_cap"
    rendered = json.dumps(fired)
    assert "LONG" not in rendered
    assert "SHORT" not in rendered
    assert EPISODE_IDENTITY_KEYS == (
        "symbol",
        "session_date",
        "direction",
        "first_bar_start",
        "family",
    )


def test_threshold_crossing_session_is_included_in_full() -> None:
    day1, day2, day3 = "2026-10-05", "2026-10-06", "2026-10-07"
    prior = [
        _episode(day1, bar=f"14:{index:02d}:00+00:00")
        for index in range(ACTIVATION_CAP - 1)
    ]
    crossing = [_episode(day2, bar=f"15:{index:02d}:00+00:00") for index in range(3)]
    later = [_episode(day3, bar="14:00:00+00:00")]
    readout = study_readout([day1, day2, day3], prior + crossing + later)
    assert readout == {
        "sessions_elapsed": 2,
        "stop_condition_met": True,
        "stop_condition": "activation_cap",
    }
    rendered = json.dumps(readout)
    assert "27" not in rendered
    assert str(ACTIVATION_CAP) not in rendered
    assert "full threshold-crossing session" in THRESHOLD_CROSSING_RULE


def test_sealed_snapshot_and_bar_grid_are_frozen() -> None:
    spec = _draft()
    prereg = (ROOT / spec["prereg_path"]).read_text(encoding="utf-8")
    for field in SESSION_SEAL_FIELDS + EPISODE_SNAPSHOT_FIELDS:
        assert field in prereg
    assert "start >= first_sight_at" in FIVE_MINUTE_GRID or "greater than or equal to first_sight_at" in FIVE_MINUTE_GRID
    assert "session_close" in FIVE_MINUTE_GRID
    assert "sealed episode snapshot" in spec["notes"]
    assert "not an exact final-N" in spec["notes"]
    assert "open(" not in (ROOT / "ops/options_212c_floor_outcome_monitor.py").read_text(encoding="utf-8")


def test_sealed_path_contract_is_frozen_and_has_no_reader() -> None:
    assert sealed_path_record_relpath("2026-10-05") == (
        f"{PATH_RECORD_ROOT}/2026-10-05.json"
    )
    assert "sha256" in PATH_RECORD_INTEGRITY.lower() or "SHA-256" in PATH_RECORD_INTEGRITY
    assert "DATA_INVALID" in PATH_RECORD_INTEGRITY
    source = (ROOT / "ops/options_212c_floor_outcome_monitor.py").read_text(encoding="utf-8")
    assert "open(" not in source
    assert "read_text" not in source
    assert "coverage_outcomes" not in source


def test_generic_runner_cannot_complete_when_study_metrics_are_absent() -> None:
    spec = _draft()
    metrics = compute_metrics(
        [
            {
                "setup_detected": True,
                "evaluated": True,
                "activated": True,
                "entered": True,
                "completed": True,
                "result": 1.0,
                "mae": 0.2,
                "mfe": 1.0,
                "exit_reason": "target",
            }
        ],
        spec["required_metrics"],
    )
    missing = set(metrics["_missing_required"])
    assert set(STUDY_ONLY_METRICS).issubset(missing)
    label, details = classify_experiment_result(
        spec,
        {"_missing_required": sorted(missing)},
        {"_missing_required": sorted(missing)},
    )
    assert label == "INVALID EXPERIMENT"
    assert details["reason"] == "required metrics missing"


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
