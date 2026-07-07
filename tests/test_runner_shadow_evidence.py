from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ops.runner_shadow_evidence import (
    EVIDENCE_FILENAME,
    append_runner_shadow_evidence,
    runner_shadow_status,
)


def _result() -> dict:
    return {
        "direction": "LONG",
        "favorable_r": 1.25,
        "trailing": True,
        "moved": True,
        "would_stop": 19505.0,
        "original_stop": 19490.0,
    }


def test_runner_shadow_status_reports_recent_live_path_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    append_runner_shadow_evidence(
        tmp_path,
        instrument="MNQ",
        setup="orb_reclaim",
        bar_ts="2026-06-30T14:30:00+00:00",
        result=_result(),
        fill_confirmed=True,
    )

    status = runner_shadow_status(tmp_path)

    assert status["state"] == "proof_sufficient"
    assert status["recent"] is True
    assert status["path_observed_recently"] is True
    assert status["proof_sufficient"] is True
    assert status["live_trailing_blocked"] is False
    assert status["latest"]["source"] == "process_alert"
    assert status["latest"]["instrument"] == "MNQ"
    assert status["latest"]["setup"] == "orb_reclaim"
    assert status["latest"]["armed"] is True
    assert status["latest"]["fill_confirmed"] is True


def test_armed_evidence_without_confirmed_fill_is_not_proof(monkeypatch, tmp_path):
    """Unreadable fill status → row kept, tagged null, but NEVER promotion proof."""
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    append_runner_shadow_evidence(
        tmp_path,
        instrument="MES",
        setup="orb_reclaim",
        bar_ts="2026-07-02T14:30:00+00:00",
        result=_result(),
        fill_confirmed=None,
    )

    status = runner_shadow_status(tmp_path)

    assert status["state"] == "recent_path_evidence"
    assert status["proof_sufficient"] is False
    assert status["live_trailing_blocked"] is True
    assert status["latest"]["fill_confirmed"] is None
    assert "entry fill is unconfirmed" in status["summary"]
    assert "fill-fiction guard" in status["next_step"]


def test_legacy_rows_without_fill_key_are_excluded_from_proof(tmp_path, monkeypatch):
    """Rows written before the fill gate (e.g. the contaminated 2026-07-02 MES
    orb_reclaim row, whose IOC entry never filled) lack fill_confirmed and must
    never count as proof, even when armed+moved and fresh."""
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    path = tmp_path / EVIDENCE_FILENAME
    path.write_text(
        json.dumps({
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "process_alert",
            "instrument": "MES",
            "setup": "orb_reclaim",
            "armed": True,
            "moved": True,
            "favorable_r": 1.0,
        })
        + "\n"
    )

    status = runner_shadow_status(tmp_path)

    assert status["state"] == "recent_path_evidence"
    assert status["proof_sufficient"] is False
    assert status["live_trailing_blocked"] is True


def test_recent_unarmed_path_evidence_does_not_unblock_live_trailing(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    result = _result()
    result.update({"trailing": False, "moved": False})
    append_runner_shadow_evidence(
        tmp_path,
        instrument="MES",
        setup="vwap_hold",
        bar_ts="2026-06-30T14:30:00+00:00",
        result=result,
    )

    status = runner_shadow_status(tmp_path)

    assert status["state"] == "recent_path_evidence"
    assert status["path_observed_recently"] is True
    assert status["proof_sufficient"] is False
    assert status["live_trailing_blocked"] is True


def test_runner_shadow_status_is_fail_soft_and_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    path = tmp_path / EVIDENCE_FILENAME
    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    path.write_text(
        "not json\n"
        + json.dumps({
            "observed_at": stale.isoformat(),
            "source": "process_alert",
            "instrument": "MES",
        })
        + "\n"
    )

    status = runner_shadow_status(tmp_path, fresh_seconds=1800)

    assert status["state"] == "stale_evidence"
    assert status["recent"] is False
    assert status["live_trailing_blocked"] is True
    assert "Keep live trailing blocked" in status["next_step"]


def test_runner_shadow_status_distinguishes_disabled_from_awaiting(monkeypatch, tmp_path):
    monkeypatch.delenv("EXIT_MODE", raising=False)
    monkeypatch.delenv("RUNNER_SHADOW_ENABLED", raising=False)
    assert runner_shadow_status(tmp_path)["state"] == "disabled"

    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "1")
    status = runner_shadow_status(tmp_path)
    assert status["state"] == "awaiting_evidence"


def test_explicit_static_mode_overrides_legacy_runner_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("EXIT_MODE", "static")
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    monkeypatch.setenv("RUNNER_LIVE_ENABLED", "true")
    status = runner_shadow_status(tmp_path)
    assert status["enabled"] is False
    assert status["live_enabled"] is False
    assert status["evidence_observed"] is False
