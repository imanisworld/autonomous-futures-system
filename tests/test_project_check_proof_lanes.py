from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from execution.mnq_strat_evidence import evidence_path, state_path
from ops.project_check.proof_lanes import (
    build_proof_lane_status,
    format_proof_lane_status,
)


OPEN_NOW = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
CLOSED_NOW = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)


def _repo(tmp_path: Path, runner_source: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "webhook").mkdir(parents=True)
    source = runner_source or (
        "evaluate_mnq_orb_breakout_inverse\n"
        "mnq_orb_breakout_inverse_audit\n"
        "evaluate_mnq_orb_reclaim_proof\n"
        "mnq_orb_reclaim_proof_audit\n"
        "process_mnq_strat_evidence\n"
    )
    (root / "webhook" / "runner.py").write_text(source)
    return root


def _lane(report: dict, name: str) -> dict:
    return next(row for row in report["lanes"] if row["lane"] == name)


def test_reports_effective_modes_and_latest_existing_activity(tmp_path):
    root = _repo(tmp_path)
    (root / ".env").write_text(
        "MNQ_ORB_BREAKOUT_INVERSE_MODE=paper_sim\n"
        "MNQ_ORB_RECLAIM_PROOF_MODE=observe_only\n"
        "MNQ_STRAT_22_REVERSAL_MODE=paper_sim\n"
    )
    logs = root / "logs"
    logs.mkdir()
    journal = logs / "journal_2026-09-21.jsonl"
    journal.write_text(
        json.dumps({
            "timestamp": "2026-09-21T13:30:00+00:00",
            "mnq_orb_breakout_inverse_audit": {"paper_mode": "paper_sim"},
        }) + "\n"
        + json.dumps({
            "timestamp": "2026-09-21T13:45:00+00:00",
            "mnq_orb_reclaim_proof_audit": {"proof_mode": "observe_only"},
        }) + "\n"
    )
    evidence_path(logs, "strat_22_reversal").write_text(
        json.dumps({
            "event": "CANDIDATE",
            "observed_at": "2026-09-21T13:50:00+00:00",
        }) + "\n"
    )
    state_path(logs, "strat_22_reversal").write_text(
        json.dumps({"seen": [], "position": None})
    )

    report = build_proof_lane_status(repo_root=root, log_dir=logs, now=OPEN_NOW)

    inverse = _lane(report, "mnq_inverse_orb")
    reclaim = _lane(report, "mnq_orb_reclaim")
    reversal = _lane(report, "mnq_strat_22_reversal")
    assert inverse["effective_mode"] == "paper_sim"
    assert inverse["mode_source"] == ".env"
    assert inverse["collection_status"] == "PAPER_SIM"
    assert inverse["latest_journal_timestamp"] == "2026-09-21T13:30:00+00:00"
    assert reclaim["effective_mode"] == "observe_only"
    assert reclaim["collection_status"] == "OBSERVE_ONLY"
    assert reclaim["latest_journal_timestamp"] == "2026-09-21T13:45:00+00:00"
    assert reversal["effective_mode"] == "paper_sim"
    assert reversal["collection_status"] == "PAPER_SIM"
    assert reversal["latest_state_or_evidence_timestamp"] is not None
    assert all(row["wiring_status"] == "WIRED" for row in report["lanes"])
    assert all(row["off_session_silence_expected"] is False for row in report["lanes"])


def test_market_closed_marks_silence_expected_without_calling_it_failure(tmp_path):
    root = _repo(tmp_path)
    report = build_proof_lane_status(
        repo_root=root,
        log_dir=root / "missing-logs",
        now=CLOSED_NOW,
    )

    assert report["product_session_active"] is False
    for row in report["lanes"]:
        assert row["off_session_silence_expected"] is True
        assert "silence is expected" in row["reason"]
        assert row["wiring_status"] == "WIRED"


def test_missing_runner_integration_is_reported_not_wired(tmp_path):
    root = _repo(
        tmp_path,
        runner_source=(
            "evaluate_mnq_orb_reclaim_proof\n"
            "mnq_orb_reclaim_proof_audit\n"
            "process_mnq_strat_evidence\n"
        ),
    )
    report = build_proof_lane_status(
        repo_root=root,
        log_dir=root / "logs",
        now=OPEN_NOW,
    )
    inverse = _lane(report, "mnq_inverse_orb")

    assert inverse["wiring_status"] == "NOT_WIRED"
    assert inverse["collection_status"] == "NOT_WIRED"
    assert "missing runner integration token" in inverse["reason"]


def test_invalid_mode_is_fail_closed_and_visible(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("MNQ_STRAT_22_REVERSAL_MODE", "live")

    report = build_proof_lane_status(
        repo_root=root,
        log_dir=root / "logs",
        now=OPEN_NOW,
    )
    reversal = _lane(report, "mnq_strat_22_reversal")

    assert reversal["effective_mode"] == "observe_only"
    assert reversal["mode_source"] == "process_env"
    assert reversal["config_valid"] is False
    assert reversal["collection_status"] == "CONFIG_INVALID"
    assert "must fail closed" in reversal["reason"]


def test_status_is_read_only_and_does_not_create_missing_log_directory(tmp_path):
    root = _repo(tmp_path)
    logs = root / "does-not-exist"

    report = build_proof_lane_status(repo_root=root, log_dir=logs, now=OPEN_NOW)

    assert report["read_only"] is True
    assert logs.exists() is False


def test_text_view_shows_mode_activity_silence_and_reason(tmp_path):
    root = _repo(tmp_path)
    report = build_proof_lane_status(
        repo_root=root,
        log_dir=root / "logs",
        now=CLOSED_NOW,
    )

    text = format_proof_lane_status(report)
    assert "PROJECT_CHECK PROOF LANES — READ ONLY" in text
    assert "MNQ inverse ORB" in text
    assert "MNQ ORB reclaim" in text
    assert "MNQ 2-2 reversal" in text
    assert "deployed effective mode:" in text
    assert "latest journal/state:" in text
    assert "silence expected off-session: YES" in text
    assert "reason:" in text
