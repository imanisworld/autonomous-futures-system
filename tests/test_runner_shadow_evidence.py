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
    )

    status = runner_shadow_status(tmp_path)

    assert status["state"] == "recent_evidence"
    assert status["recent"] is True
    assert status["live_trailing_blocked"] is False
    assert status["latest"]["source"] == "process_alert"
    assert status["latest"]["instrument"] == "MNQ"
    assert status["latest"]["setup"] == "orb_reclaim"
    assert status["latest"]["armed"] is True


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
    monkeypatch.delenv("RUNNER_SHADOW_ENABLED", raising=False)
    assert runner_shadow_status(tmp_path)["state"] == "disabled"

    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "1")
    status = runner_shadow_status(tmp_path)
    assert status["state"] == "awaiting_evidence"
    assert status["evidence_observed"] is False
