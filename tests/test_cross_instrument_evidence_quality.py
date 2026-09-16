"""Evidence-quality regressions for cross_instrument_observation_v1."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from context.bar_history import BarHistory
from execution import cross_instrument_observation as cio
from execution.cross_instrument_evidence_quality import (
    CODE_PROVENANCE_UNKNOWN,
    DATA_GAP_CONTAMINATED,
    DETECTOR_PROVENANCE_UNKNOWN,
    MBT_OUTCOME_HORIZON_UNPROVEN,
    ROLL_CONTAMINATED,
    ROLL_PROVENANCE_UNKNOWN,
    VALID,
    assess_evidence_row,
    build_quality_report,
    population_quality_blockers,
)

EPOCH = "quality-epoch"
SHA = "a" * 40


def _ts(hour: int, minute: int, day: date = date(2026, 8, 10)) -> str:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).isoformat()


def _record(tmp_path, root: str, ts: str, ticker: str) -> None:
    BarHistory(log_dir=str(tmp_path)).record(
        root,
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        timeframe="15",
        source_ticker=ticker,
    )


def _row(root="M2K", signal=None, exit_ts=None, *, sha=SHA):
    return {
        "campaign_id": cio.CAMPAIGN_ID,
        "evidence_schema_version": cio.SCHEMA_VERSION,
        "record_type": "OUTCOME",
        "candidate_id": "q-1",
        "strategy": "strat_212",
        "instrument": root,
        "variant": "observer",
        "evidence_epoch": EPOCH,
        "collection_mode": cio.STRUCTURAL_OUTCOME,
        "source_timeframe": "15",
        "signal_timestamp": signal or _ts(15, 0),
        "exit_timestamp": exit_ts or _ts(15, 15),
        "result": "WIN",
        "pnl_r": 2.0,
        "generating_git_sha": sha,
        "provenance_status": "environment:AFS_RELEASE_SHA" if sha else "unknown",
    }


def test_complete_m2k_continuous_window_is_quality_eligible_off_roll(tmp_path):
    # Eight complete 15m dependencies exist at/before the 15:00 signal, plus
    # the 15:15 terminal bar. Startup samples with a shorter reconstructed
    # detector window are deliberately blocked by a separate regression below.
    for hour, minute in (
        (13, 15), (13, 30), (13, 45), (14, 0), (14, 15),
        (14, 30), (14, 45), (15, 0), (15, 15),
    ):
        _record(tmp_path, "M2K", _ts(hour, minute), "M2K1!")
    quality = assess_evidence_row(_row(), tmp_path)
    assert quality["eligible"] is True
    assert quality["status"] == VALID
    assert quality["continuity"]["missing_expected_bars"] == []
    assert quality["roll"]["status"] == VALID
    assert quality["code_provenance"]["detector_dependency_count"] == 8


def test_startup_sample_with_incomplete_reconstructed_detector_window_is_blocked(tmp_path):
    for hour, minute in ((14, 30), (14, 45), (15, 0), (15, 15)):
        _record(tmp_path, "M2K", _ts(hour, minute), "M2K1!")
    quality = assess_evidence_row(_row(), tmp_path)
    assert quality["eligible"] is False
    assert DETECTOR_PROVENANCE_UNKNOWN in quality["issues"]
    assert quality["code_provenance"]["detector_dependency_count"] < 8


def test_missing_expected_15m_bar_contaminates_sample(tmp_path):
    for hour, minute in ((14, 0), (14, 15), (14, 45), (15, 0), (15, 15)):
        _record(tmp_path, "M2K", _ts(hour, minute), "M2K1!")
    quality = assess_evidence_row(_row(), tmp_path)
    assert quality["eligible"] is False
    assert DATA_GAP_CONTAMINATED in quality["issues"]
    assert any("14:30:00" in ts for ts in quality["continuity"]["missing_expected_bars"])


def test_known_equity_halt_is_not_misclassified_as_gap(tmp_path):
    # 2026-08-10 is EDT: 20:00Z=16:00 ET and 20:30Z=16:30 ET.
    # The 16:15 ET bar open is inside the known equity-index halt.
    _record(tmp_path, "M2K", _ts(20, 0), "M2K1!")
    _record(tmp_path, "M2K", _ts(20, 30), "M2K1!")
    row = _row(signal=_ts(20, 30), exit_ts=_ts(20, 30))
    quality = assess_evidence_row(row, tmp_path)
    assert DATA_GAP_CONTAMINATED not in quality["issues"]
    assert quality["continuity"]["missing_expected_bars"] == []


def test_unproven_mgc_continuous_roll_schedule_blocks_quality(tmp_path):
    for hour, minute in ((14, 30), (14, 45), (15, 0), (15, 15)):
        _record(tmp_path, "MGC", _ts(hour, minute), "MGC1!")
    row = _row(root="MGC", signal=_ts(15, 0), exit_ts=_ts(15, 15))
    quality = assess_evidence_row(row, tmp_path)
    assert quality["eligible"] is False
    assert quality["roll"]["status"] == ROLL_PROVENANCE_UNKNOWN
    assert ROLL_PROVENANCE_UNKNOWN in quality["issues"]


def test_contract_change_inside_window_is_roll_contaminated(tmp_path):
    _record(tmp_path, "M2K", _ts(14, 30), "M2KU6")
    _record(tmp_path, "M2K", _ts(14, 45), "M2KU6")
    _record(tmp_path, "M2K", _ts(15, 0), "M2KZ6")
    _record(tmp_path, "M2K", _ts(15, 15), "M2KZ6")
    quality = assess_evidence_row(_row(), tmp_path)
    assert quality["eligible"] is False
    assert quality["roll"]["status"] == ROLL_CONTAMINATED
    assert ROLL_CONTAMINATED in quality["issues"]


def test_unknown_generating_sha_cannot_count(tmp_path):
    for hour, minute in ((14, 30), (14, 45), (15, 0), (15, 15)):
        _record(tmp_path, "M2K", _ts(hour, minute), "M2K1!")
    quality = assess_evidence_row(_row(sha=None), tmp_path)
    assert quality["eligible"] is False
    assert CODE_PROVENANCE_UNKNOWN in quality["issues"]


def test_mbt_structural_population_has_explicit_horizon_blocker():
    blockers = population_quality_blockers({"instrument": "MBT", "collection_mode": cio.STRUCTURAL_OUTCOME})
    assert blockers == [MBT_OUTCOME_HORIZON_UNPROVEN]
    assert population_quality_blockers({"instrument": "MBT", "collection_mode": cio.SIGNAL_METRICS}) == []


def test_raw_sample_gate_cannot_override_quality_block(tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    # Thirty raw terminal outcomes across ten days would satisfy the old raw gate.
    # They deliberately have no code/bar/roll provenance, so none may count now.
    for i in range(30):
        day = date(2026, 8, 1) + timedelta(days=i % 10)
        cio._append_evidence(tmp_path, {
            "evidence_schema_version": cio.SCHEMA_VERSION,
            "campaign_id": cio.CAMPAIGN_ID,
            "record_type": "OUTCOME",
            "candidate_id": f"raw-{i}",
            "strategy": "strat_212",
            "instrument": "M2K",
            "variant": "observer",
            "evidence_epoch": EPOCH,
            "collection_mode": cio.STRUCTURAL_OUTCOME,
            "source_timeframe": "15",
            "signal_timestamp": datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc).isoformat(),
            "exit_timestamp": datetime(day.year, day.month, day.day, 14, 45, tzinfo=timezone.utc).isoformat(),
            "result": "WIN",
            "pnl_r": 2.0,
            "generating_git_sha": None,
            "provenance_status": "unknown",
        })
    report = build_quality_report(tmp_path)
    pop = next(p for p in report["populations"] if p["instrument"] == "M2K" and p["strategy"] == "strat_212")
    assert pop["terminal_outcomes"] == 30
    assert pop["quality_eligible_terminal_outcomes"] == 0
    assert pop["quality_blocked_terminal_outcomes"] == 30
    assert pop["status"] == "QUALITY BLOCKED"
    assert pop["raw_status"] == "READY FOR REVIEW"
    assert report["quality_gate_authoritative"] is True


def test_bar_history_preserves_source_ticker_without_changing_legacy_shape(tmp_path):
    history = BarHistory(log_dir=str(tmp_path))
    with_ticker = history.record(
        "M2K", ts=_ts(14, 30), open=1, high=2, low=0, close=1.5,
        timeframe="15", source_ticker="CME_MINI:M2K1!",
    )
    without_ticker = history.record(
        "M2K", ts=_ts(14, 45), open=1, high=2, low=0, close=1.5,
        timeframe="15",
    )
    assert with_ticker["source_ticker"] == "CME_MINI:M2K1!"
    assert "source_ticker" not in without_ticker


def test_resolver_ignores_non_15m_barhistory_rows(tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    signal_ts = _ts(15, 0)
    record = {
        "evidence_schema_version": cio.SCHEMA_VERSION,
        "campaign_id": cio.CAMPAIGN_ID,
        "record_type": "CANDIDATE",
        "candidate_id": "resolver-15m-only",
        "strategy": "strat_212",
        "instrument": "M2K",
        "variant": "observer",
        "evidence_epoch": EPOCH,
        "collection_mode": cio.STRUCTURAL_OUTCOME,
        "direction": "LONG",
        "signal_timestamp": signal_ts,
        "trading_date": cio.observation_day("M2K", signal_ts).isoformat(),
        "observation_date": cio.observation_day("M2K", signal_ts).isoformat(),
        "source_timeframe": "15",
        "entry": 100.0,
        "stop": 99.0,
        "target": 105.0,
    }
    state = {
        "campaign_id": cio.CAMPAIGN_ID,
        "pending": {
            record["candidate_id"]: {
                "record": record,
                "filled": True,
                "fill_ts": signal_ts,
                "mae_points": 0.0,
                "mfe_points": 0.0,
                "bars_seen": 0,
            }
        },
        "seen_candidate_ids": [record["candidate_id"]],
        "seen_bars": [],
        "strat_212_122": {},
    }
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")

    history = BarHistory(log_dir=str(tmp_path))
    history.record(
        "M2K", ts=_ts(15, 15), open=100, high=104, low=100, close=103,
        timeframe="15", source_ticker="M2K1!",
    )
    # This row would falsely resolve WIN if canonical history were not filtered.
    history.record(
        "M2K", ts=_ts(15, 30), open=103, high=106, low=100, close=105,
        timeframe="60", source_ticker="M2K1!",
    )
    out = cio.resolve_pending(
        tmp_path,
        instrument="M2K",
        bars=[],
        current_bar_ts=_ts(15, 30),
    )
    assert out == []
    assert record["candidate_id"] in json.loads((tmp_path / cio.STATE_FILENAME).read_text())["pending"]

    history.record(
        "M2K", ts=_ts(15, 45), open=103, high=106, low=101, close=105,
        timeframe="15", source_ticker="M2K1!",
    )
    out = cio.resolve_pending(
        tmp_path,
        instrument="M2K",
        bars=[],
        current_bar_ts=_ts(15, 45),
    )
    assert len(out) == 1
    assert out[0]["result"] == "WIN"
    assert out[0]["exit_timestamp"] == _ts(15, 45)
