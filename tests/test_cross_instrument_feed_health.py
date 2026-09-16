"""Regression coverage for authoritative cross-instrument 15m feed health."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from context.bar_history import BarHistory
from execution import cross_instrument_observation as cio
from ops.cross_instrument_feed_health import build_feed_health
from scripts import feed_watchdog as fw

EPOCH = "feed-health-epoch"
NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)  # Monday 10:00 ET


def _arm(monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)


def _write_state(tmp_path: Path, rows: list[tuple[str, str]]) -> None:
    state = {
        "campaign_id": cio.CAMPAIGN_ID,
        "pending": {},
        "seen_candidate_ids": [],
        "seen_bars": [f"{EPOCH}|{root}|15|{ts}" for root, ts in rows],
        "strat_212_122": {},
    }
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")


def _record_15m(tmp_path: Path, root: str, ts: str) -> None:
    BarHistory(log_dir=str(tmp_path)).record(
        root,
        ts=ts,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
        timeframe="15",
    )


def _write_latest(tmp_path: Path, root: str, received_at: datetime, tf: str) -> None:
    (tmp_path / f"latest_webhook_{root}.json").write_text(
        json.dumps({
            "received_at": received_at.isoformat(),
            "payload": {"ticker": f"{root}1!", "timeframe": tf},
            "result": {"decision": "OBSERVATION_ONLY"},
        }),
        encoding="utf-8",
    )


def _write_transport(tmp_path: Path, root: str, *, bar_ts: str, ok: bool, recorded: bool, error=None) -> None:
    (tmp_path / f"{cio.CAMPAIGN_ID}_transport_{root}.json").write_text(
        json.dumps({
            "campaign_id": cio.CAMPAIGN_ID,
            "evidence_epoch": EPOCH,
            "attempted_at": NOW.isoformat(),
            "bar_ts": bar_ts,
            "timeframe_minutes": 15,
            "transport_ok": ok,
            "bar_recorded": recorded,
            "last_error": error,
        }),
        encoding="utf-8",
    )


def _cfg(tmp_path: Path):
    return SimpleNamespace(log_dir=str(tmp_path), expected_timeframe_minutes=15, discord_webhook_url="x")


def test_success_requires_seen_bar_and_matching_15m_bar_history(tmp_path, monkeypatch):
    _arm(monkeypatch)
    ts = (NOW - timedelta(minutes=15)).isoformat()
    _write_state(tmp_path, [("M2K", ts)])

    health = build_feed_health(tmp_path, now=NOW)
    assert health["instruments"]["M2K"]["transport_ok"] is False
    assert health["instruments"]["M2K"]["bar_recorded"] is False
    assert health["instruments"]["M2K"]["proven_15m"] is False
    assert health["instruments"]["M2K"]["status"] == "BAR_HISTORY_MISSING"

    _record_15m(tmp_path, "M2K", ts)
    health = build_feed_health(tmp_path, now=NOW)
    assert health["instruments"]["M2K"]["proven_15m"] is True
    assert health["instruments"]["M2K"]["transport_ok"] is True
    assert health["instruments"]["M2K"]["status"] == "HEALTHY"


def test_fresh_5m_receipt_cannot_mask_stale_15m_campaign_feed(tmp_path, monkeypatch):
    _arm(monkeypatch)
    stale_ts = (NOW - timedelta(minutes=90)).isoformat()
    _write_state(tmp_path, [("M2K", stale_ts)])
    _record_15m(tmp_path, "M2K", stale_ts)
    _write_latest(tmp_path, "M2K", NOW - timedelta(minutes=1), "5")

    health = build_feed_health(tmp_path, now=NOW)
    row = health["instruments"]["M2K"]
    assert row["proven_15m"] is True
    assert row["status"] == "STALE"
    assert row["age_seconds"] == 90 * 60

    messages: list[str] = []
    out = fw.check_instruments(
        NOW,
        tmp_path,
        {},
        lambda cfg, msg: messages.append(msg) or SimpleNamespace(sent=True),
        _cfg(tmp_path),
        15,
    )
    assert out["authority"] == "campaign_seen_bar_plus_matching_15m_bar_history_plus_latest_transport_attempt"
    assert out["stale"] and out["stale"][0].startswith("M2K ")
    assert any("OBSERVATION 15M FEED STALE" in msg and "M2K" in msg for msg in messages)


def test_newer_failed_15m_transport_fails_health_immediately(tmp_path, monkeypatch):
    _arm(monkeypatch)
    good_ts = (NOW - timedelta(minutes=15)).isoformat()
    failed_ts = NOW.isoformat()
    _write_state(tmp_path, [("M2K", good_ts)])
    _record_15m(tmp_path, "M2K", good_ts)
    _write_transport(
        tmp_path,
        "M2K",
        bar_ts=failed_ts,
        ok=False,
        recorded=True,
        error="synthetic detector failure",
    )

    health = build_feed_health(tmp_path, now=NOW)
    row = health["instruments"]["M2K"]
    assert row["proven_15m"] is True  # a prior good bar exists
    assert row["status"] == "TRANSPORT_ERROR"
    assert row["transport_ok"] is False
    assert row["last_error"] == "synthetic detector failure"
    assert health["transport_error_instruments"] == ["M2K"]
    assert health["ready_to_trust_collection_feed"] is False


def test_5m_transport_marker_is_never_authoritative(tmp_path, monkeypatch):
    _arm(monkeypatch)
    good_ts = (NOW - timedelta(minutes=15)).isoformat()
    _write_state(tmp_path, [("M2K", good_ts)])
    _record_15m(tmp_path, "M2K", good_ts)
    path = tmp_path / f"{cio.CAMPAIGN_ID}_transport_M2K.json"
    path.write_text(json.dumps({
        "campaign_id": cio.CAMPAIGN_ID,
        "evidence_epoch": EPOCH,
        "bar_ts": NOW.isoformat(),
        "timeframe_minutes": 5,
        "transport_ok": False,
        "bar_recorded": False,
        "last_error": "5m failure",
    }), encoding="utf-8")
    row = build_feed_health(tmp_path, now=NOW)["instruments"]["M2K"]
    assert row["status"] == "HEALTHY"
    assert row["last_error"] is None


def test_wrong_epoch_seen_bar_does_not_prove_current_feed(tmp_path, monkeypatch):
    _arm(monkeypatch)
    ts = (NOW - timedelta(minutes=15)).isoformat()
    state = {
        "campaign_id": cio.CAMPAIGN_ID,
        "pending": {},
        "seen_candidate_ids": [],
        "seen_bars": [f"old-epoch|M2K|15|{ts}"],
        "strat_212_122": {},
    }
    (tmp_path / cio.STATE_FILENAME).write_text(json.dumps(state), encoding="utf-8")
    _record_15m(tmp_path, "M2K", ts)
    health = build_feed_health(tmp_path, now=NOW)
    assert health["instruments"]["M2K"]["status"] == "UNPROVEN"
    assert "M2K" in health["unproven_instruments"]


def test_all_six_require_independent_15m_proof(tmp_path, monkeypatch):
    _arm(monkeypatch)
    ts = (NOW - timedelta(minutes=15)).isoformat()
    rows = []
    for root in cio.OBSERVATION_UNIVERSE:
        rows.append((root, ts))
        _record_15m(tmp_path, root, ts)
    _write_state(tmp_path, rows)
    health = build_feed_health(tmp_path, now=NOW)
    assert health["unproven_instruments"] == []
    assert health["all_instruments_proven_once"] is True
    assert health["active_instruments_healthy"] is True
    assert health["ready_to_trust_collection_feed"] is True
