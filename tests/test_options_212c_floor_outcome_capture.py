"""Synthetic QA for the real path-v0.2 capture integration.

No provider call and no real trial data. These tests exercise immutable file /
manifest mechanics, blind-monitor sequencing, and the outcomes capture hook.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_episodes import reduce_events
from alert_ranker.coverage_observer import OBSERVER_VERSION
from alert_ranker.session_calendar import nyse_session_for
import ops.options_212c_floor_outcome_capture as capture_module
from ops.options_212c_floor_outcome_capture import (
    CaptureIntegrationError,
    capture_decision,
    capture_progress,
    inspect_seal,
    load_verified_seals,
    manifest_path,
    seal_path,
    write_artifact_once,
)
from ops.options_212c_floor_outcome_monitor import (
    PATH_RECORD_VERSION,
    TRIAL_ID,
    canonical_seal_bytes,
    seal_sha256,
)
from ops.options_212c_floor_outcome_study import SealedSessionArtifact
from scripts import options_coverage_outcomes as outcome_script

DAY = "2026-10-06"
DAY2 = "2026-10-07"
UTC = timezone.utc


def _session(day: str = DAY):
    session = nyse_session_for(date.fromisoformat(day))
    assert session is not None
    return session


def _empty_artifact(day: str = DAY) -> SealedSessionArtifact:
    session = _session(day)
    record = {
        "path_record_version": PATH_RECORD_VERSION,
        "trial_id": TRIAL_ID,
        "session_date": day,
        "session_open": session.open.astimezone(UTC).isoformat(),
        "session_close": session.close.astimezone(UTC).isoformat(),
        "source": {
            "provider": "synthetic",
            "request_start": session.open.astimezone(UTC).isoformat(),
            "request_end": session.close.astimezone(UTC).isoformat(),
            "observer_run_id": 1,
            "observer_ran_at": (session.close + timedelta(minutes=30)).astimezone(UTC).isoformat(),
            "source_sha": "a" * 40,
        },
        "captured_at": (session.close + timedelta(minutes=31)).astimezone(UTC).isoformat(),
        "episodes": [],
    }
    body = canonical_seal_bytes(record)
    digest = seal_sha256(record)
    return SealedSessionArtifact(
        record=record,
        body=body,
        sha256=digest,
        manifest={
            "session_date": day,
            "byte_length": len(body),
            "sha256": digest,
        },
    )


def _event(day: str = DAY) -> dict:
    session = _session(day)
    first_bar_start = session.open.astimezone(UTC)
    first_bar_close = first_bar_start + timedelta(minutes=30)
    first_sight = first_bar_close + timedelta(minutes=17, seconds=57)
    return {
        "observer_id": "OPTIONS_COVERAGE_OBSERVER",
        "observer_version": OBSERVER_VERSION,
        "symbol": "AAPL",
        "timeframe": "30m",
        "session_date": day,
        "bar_start": first_bar_start.isoformat(),
        "bar_close": first_bar_close.isoformat(),
        "family": "STRAT_212_CONTINUATION",
        "sequence": "2U-1-2U",
        "requested_family": True,
        "v1_supported": True,
        "direction": "LONG",
        "two_back_type": "two_up",
        "previous_type": "inside",
        "current_type": "two_up",
        "entry_trigger": 100.0,
        "invalidation": 99.0,
        "risk": 1.0,
        "nearest_target_1": 101.0,
        "nearest_target_2": None,
        "nearest_rr_1": 1.0,
        "nearest_reason": "",
        "nearest_geometry_ok": True,
        "floor_target_1": 102.0,
        "floor_target_2": 103.0,
        "floor_rr_1": 2.0,
        "floor_reason": "",
        "floor_geometry_ok": True,
        "floor_rescued": False,
        "spy_trend": "bullish",
        "qqq_trend": "bullish",
        "hourly_candle_type": "two_up",
        "daily_candle_type": "two_up",
        "alignment_ok": True,
        "alignment_failures": "",
        "first_sight_at": first_sight.isoformat(),
        "first_sight_after_close": False,
        "first_sight_price": 100.25,
        "first_sight_price_source": "5Min",
        "nearest_remaining_rr": 0.75,
        "floor_remaining_rr": 1.75,
        "late_nearest": True,
        "late_floor": False,
        "would_qualify_v1_rule": False,
        "would_qualify_floor_rule": True,
        "resistance_levels": [],
        "support_levels": [],
    }


def _bars(day: str = DAY) -> list[Bar]:
    session = _session(day)
    cursor = session.open.astimezone(UTC)
    close = session.close.astimezone(UTC)
    bars: list[Bar] = []
    while cursor + timedelta(minutes=5) <= close:
        bars.append(
            Bar(
                start=cursor,
                open=100.25,
                high=100.6,
                low=100.0,
                close=100.4,
                volume=1000,
            )
        )
        cursor += timedelta(minutes=5)
    return bars


def test_unset_start_is_dormant_and_writes_nothing(tmp_path: Path) -> None:
    decision = capture_decision(tmp_path, DAY)
    assert decision.required is False
    assert decision.reason == "eligible_start_unset"
    assert decision.progress.enabled is False
    with pytest.raises(CaptureIntegrationError, match="capture_not_allowed"):
        write_artifact_once(tmp_path, _empty_artifact())
    assert not seal_path(tmp_path, DAY).exists()
    assert not manifest_path(tmp_path).exists()


def test_prestart_seal_artifact_is_a_hard_refusal(tmp_path: Path) -> None:
    path = seal_path(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(_empty_artifact().body)
    with pytest.raises(CaptureIntegrationError, match="seal_exists_before_start"):
        capture_progress(tmp_path)


def test_october_fifth_or_earlier_cannot_be_registered_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", "2026-10-05")
    with pytest.raises(CaptureIntegrationError, match="eligible_start_invalid"):
        capture_progress(tmp_path)


def test_write_once_manifest_once_and_advance_blind_monitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    artifact = _empty_artifact()
    first = write_artifact_once(tmp_path, artifact)
    assert first.ok
    path = seal_path(tmp_path, DAY)
    assert path.read_bytes() == artifact.body
    assert not (path.stat().st_mode & 0o200)

    lines = manifest_path(tmp_path).read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == artifact.manifest

    progress = capture_progress(tmp_path)
    assert progress.sessions_elapsed == 1
    assert progress.next_session == DAY2
    public = progress.to_public_dict()
    assert "activation_count" not in json.dumps(public)
    assert "episodes" not in json.dumps(public)

    # Idempotence verifies the existing bytes; it never rewrites or appends.
    second = write_artifact_once(tmp_path, artifact)
    assert second == first
    assert len(manifest_path(tmp_path).read_text().splitlines()) == 1

    mismatched = _empty_artifact()
    mismatched_record = dict(mismatched.record)
    mismatched_record["captured_at"] = (
        _session().close + timedelta(minutes=32)
    ).astimezone(UTC).isoformat()
    mismatched_body = canonical_seal_bytes(mismatched_record)
    mismatched_digest = seal_sha256(mismatched_record)
    mismatched = SealedSessionArtifact(
        record=mismatched_record,
        body=mismatched_body,
        sha256=mismatched_digest,
        manifest={
            "session_date": DAY,
            "byte_length": len(mismatched_body),
            "sha256": mismatched_digest,
        },
    )
    with pytest.raises(
        CaptureIntegrationError, match="seal_already_exists_mismatch"
    ):
        write_artifact_once(tmp_path, mismatched)


def test_writer_refuses_outcome_field_schema_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    artifact = _empty_artifact()
    record = dict(artifact.record)
    record["outcome"] = "TARGET_FIRST"
    body = canonical_seal_bytes(record)
    digest = seal_sha256(record)
    drifted = SealedSessionArtifact(
        record=record,
        body=body,
        sha256=digest,
        manifest={
            "session_date": DAY,
            "byte_length": len(body),
            "sha256": digest,
        },
    )
    with pytest.raises(
        CaptureIntegrationError, match="artifact_session_fields_invalid"
    ):
        write_artifact_once(tmp_path, drifted)
    assert not seal_path(tmp_path, DAY).exists()


def test_partial_or_tampered_seal_refuses_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    path = seal_path(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(_empty_artifact().body)
    with pytest.raises(CaptureIntegrationError, match="seal_manifest_partial"):
        inspect_seal(tmp_path, DAY)

    path.unlink()
    manifest_path(tmp_path).write_text(
        json.dumps(_empty_artifact().manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(CaptureIntegrationError, match="seal_manifest_partial"):
        inspect_seal(tmp_path, DAY)

    manifest_path(tmp_path).unlink()
    write_artifact_once(tmp_path, _empty_artifact())
    os.chmod(path, 0o644)
    body = bytearray(path.read_bytes())
    body[-2] ^= 1
    path.write_bytes(bytes(body))
    with pytest.raises(CaptureIntegrationError, match="seal_sha256_mismatch"):
        load_verified_seals(tmp_path)


def test_out_of_sequence_session_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    with pytest.raises(CaptureIntegrationError, match="capture_out_of_sequence"):
        capture_decision(tmp_path, DAY2)


def test_outcome_fetch_hook_seals_before_any_trial_scoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _event()
    episode = reduce_events([event])[0]
    session = _session()
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)

    request = {
        "session_date": DAY,
        "root": tmp_path,
        "source_sha": "b" * 40,
        "observer_run_id": 7,
        "observer_ran_at": (session.close + timedelta(minutes=30)).astimezone(UTC).isoformat(),
        "already_sealed": False,
    }
    result = outcome_script._seal_from_fetch(
        request,
        session,
        [episode],
        [event],
        {"AAPL": _bars()},
        {},
        captured_at=session.close + timedelta(minutes=31),
    )
    assert result["state"] == "valid"
    record = json.loads(seal_path(tmp_path, DAY).read_bytes())
    assert record["source"]["provider"] == "alpaca_sip"
    assert record["source"]["source_sha"] == "b" * 40
    assert len(record["episodes"]) == 1
    snapshot = record["episodes"][0]
    assert snapshot["gate_bucket_floor"] == "WOULD_OTHERWISE_QUALIFY"
    assert snapshot["floor_target_2"] == 103.0
    keys = set(record)
    for episode_row in record["episodes"]:
        keys.update(episode_row)
        for bar_row in episode_row["bars"]:
            keys.update(bar_row)
    for forbidden in ("outcome", "realized_r", "mae_r", "mfe_r"):
        assert forbidden not in keys


def test_capture_provider_error_freezes_missing_grid_instead_of_refetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = _event()
    episode = reduce_events([event])[0]
    session = _session()
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    request = {
        "session_date": DAY,
        "root": tmp_path,
        "source_sha": "b" * 40,
        "observer_run_id": 7,
        "observer_ran_at": (session.close + timedelta(minutes=30)).astimezone(UTC).isoformat(),
        "already_sealed": False,
    }
    result = outcome_script._seal_from_fetch(
        request,
        session,
        [episode],
        [event],
        {},
        {"AAPL": "provider_error:synthetic"},
        captured_at=session.close + timedelta(minutes=31),
    )
    assert result["state"] == "valid"
    record = json.loads(seal_path(tmp_path, DAY).read_bytes())
    assert record["episodes"][0]["bars"] == []


def test_capture_request_refuses_existing_ordinary_outcome_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    daily = tmp_path / "daily"
    daily.mkdir()
    (daily / f"outcomes_{DAY}_{DAY}.json").write_text("{}")
    args = SimpleNamespace(
        capture_root=str(tmp_path),
        capture_source_sha="b" * 40,
        capture_observer_run_id=7,
        capture_observer_ran_at="2026-10-06T20:30:00+00:00",
        date_from=DAY,
        date_to=DAY,
        out=str(daily),
    )
    with pytest.raises(
        CaptureIntegrationError, match="capture_prior_outcome_artifact"
    ):
        outcome_script._capture_request(args, tmp_path / "unused.sqlite", [])


def test_capture_request_refuses_noncanonical_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(capture_module, "ELIGIBLE_START", DAY)
    args = SimpleNamespace(
        capture_root=str(tmp_path),
        capture_source_sha="b" * 40,
        capture_observer_run_id=7,
        capture_observer_ran_at="2026-10-06T20:30:00+00:00",
        date_from=DAY,
        date_to=DAY,
        out=str(tmp_path / "elsewhere"),
    )
    with pytest.raises(CaptureIntegrationError, match="capture_out_dir_mismatch"):
        outcome_script._capture_request(args, tmp_path / "unused.sqlite", [])
