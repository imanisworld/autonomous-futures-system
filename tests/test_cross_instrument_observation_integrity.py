"""Regression tests for the independent #585 audit blockers.

These tests prove the observation campaign cannot be contaminated by off-timeframe
alerts, UTC midnight, a prior evidence epoch, or duplicate terminal rows after a
crash/restart window.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from context.bar_history import BarHistory
from execution import cross_instrument_observation as cio
from webhook.observation_transport import observe_collection_only_alert
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state

EPOCH_A = "epoch-A"
EPOCH_B = "epoch-B"


def _arm(monkeypatch, epoch=EPOCH_A):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, epoch)
    assert cio.campaign_enabled()


def _payload(
    *,
    ticker="M2K1!",
    ts="2026-09-15T23:45:00+00:00",
    tf="15",
    o=100.0,
    h=101.0,
    l=99.0,
    c=100.5,
    current_bar_type=None,
    previous_bar_type=None,
):
    return AlertPayload(
        ticker=ticker,
        timestamp=ts,
        timeframe=tf,
        open=o,
        high=h,
        low=l,
        close=c,
        volume=1000,
        avg_volume=900,
        vwap=100.0,
        market_condition="TRENDING",
        trend_direction="UP",
        trend_strength="MODERATE",
        previous_day_high=105.0,
        previous_day_low=95.0,
        previous_day_close=100.0,
        current_bar_type=current_bar_type,
        previous_bar_type=previous_bar_type,
    )


def test_collection_campaign_rejects_non_15m_without_recording_bar(tmp_path, monkeypatch):
    _arm(monkeypatch)
    out = observe_collection_only_alert(
        _payload(tf="60"), log_dir=str(tmp_path), for_date=date(2026, 9, 15)
    )
    assert out["decision"] == cio.DECISION_OBSERVATION_ONLY
    assert out["observation"]["transport_ok"] is False
    assert out["observation"]["bar_recorded"] is False
    assert out["observation"]["timeframe_minutes"] == 60
    assert "requires 15m" in out["observation"]["error"]
    assert not list(tmp_path.glob("bars_M2K_*.jsonl"))
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()
    assert not (tmp_path / cio.STATE_FILENAME).exists()


def test_direct_observe_bar_is_also_pinned_to_15m(tmp_path, monkeypatch):
    _arm(monkeypatch)
    state = build_market_state(_payload(tf="1h"))
    out = cio.observe_bar(
        tmp_path,
        state,
        [{"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 101.0, "stop": 99.0, "target": 105.0}],
        timeframe="1h",
        source="test",
        include_strat_212_122=False,
    )
    assert out["written"] == 0
    assert out["skipped"] == "unsupported observation timeframe"
    assert not (tmp_path / cio.EVIDENCE_FILENAME).exists()
    assert not (tmp_path / cio.STATE_FILENAME).exists()


def test_utc_midnight_does_not_expire_same_globex_observation_day(tmp_path, monkeypatch):
    _arm(monkeypatch)
    state = build_market_state(_payload(ts="2026-09-15T23:45:00+00:00"))
    summary = cio.observe_bar(
        tmp_path,
        state,
        [{"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 101.0, "stop": 99.0, "target": 103.0}],
        timeframe="15",
        source="test",
        include_strat_212_122=False,
    )
    assert summary["written"] == 1
    candidate = next(r for r in cio.read_evidence(tmp_path) if r["record_type"] == "CANDIDATE")
    assert candidate["observation_date"] == "2026-09-16"

    hist = BarHistory(log_dir=str(tmp_path))
    hist.record("M2K", ts="2026-09-15T23:45:00+00:00", open=100, high=101, low=99, close=100.5, timeframe="15")
    hist.record("M2K", ts="2026-09-16T00:00:00+00:00", open=100.5, high=101.5, low=100.0, close=101.2, timeframe="15")
    hist.record("M2K", ts="2026-09-16T00:15:00+00:00", open=101.2, high=103.5, low=100.8, close=103.2, timeframe="15")

    resolved = cio.resolve_pending(
        tmp_path,
        instrument="M2K",
        bars=[],  # resolver must recover both UTC files itself
        current_bar_ts="2026-09-16T00:15:00+00:00",
    )
    assert len(resolved) == 1
    assert resolved[0]["result"] == "WIN"
    assert resolved[0]["exit_reason"] == "TARGET_HIT"
    assert resolved[0]["observation_date"] == "2026-09-16"


def test_prior_epoch_strat_arm_cannot_fire_in_new_epoch(tmp_path, monkeypatch):
    _arm(monkeypatch, EPOCH_A)
    # Epoch A: inside bar after 2U arms a 2-1-2 LONG but emits no candidate yet.
    arm_state = build_market_state(
        _payload(
            ts="2026-09-15T14:30:00+00:00",
            o=100.0,
            h=101.0,
            l=99.0,
            c=100.5,
            current_bar_type="1",
            previous_bar_type="2U",
        )
    )
    first = cio.observe_bar(tmp_path, arm_state, [], timeframe="15", source="test")
    assert first["written"] == 0

    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH_B)
    # This bar would have resolved epoch A's arm if canonical detector state were
    # keyed only by instrument. Under epoch B it may arm its own 1-2-2 state, but
    # it must not emit A's 2-1-2 candidate.
    watch_state = build_market_state(
        _payload(
            ts="2026-09-15T14:45:00+00:00",
            o=100.5,
            h=102.0,
            l=100.0,
            c=101.5,
            current_bar_type="2U",
            previous_bar_type="1",
        )
    )
    second = cio.observe_bar(tmp_path, watch_state, [], timeframe="15", source="test")
    assert second["written"] == 0
    assert not cio.read_evidence(tmp_path)

    state = json.loads((tmp_path / cio.STATE_FILENAME).read_text())
    assert f"{EPOCH_A}|M2K" in state["strat_212_122"]
    assert f"{EPOCH_B}|M2K" in state["strat_212_122"]
    assert all(key.startswith(EPOCH_A + "|") or key.startswith(EPOCH_B + "|") for key in state["seen_bars"])


def _terminal_row(candidate_id: str) -> dict:
    return {
        "evidence_schema_version": cio.SCHEMA_VERSION,
        "campaign_id": cio.CAMPAIGN_ID,
        "record_type": "OUTCOME",
        "candidate_id": candidate_id,
        "strategy": "strat_212",
        "instrument": "M2K",
        "variant": "observer",
        "evidence_epoch": EPOCH_A,
        "collection_mode": cio.STRUCTURAL_OUTCOME,
        "collection_only": True,
        "direction": "LONG",
        "signal_timestamp": "2026-09-15T14:30:00+00:00",
        "trading_date": "2026-09-15",
        "observation_date": "2026-09-15",
        "result": "WIN",
        "pnl_r": 2.0,
    }


def test_evidence_append_is_idempotent_across_lost_state_restart(tmp_path, monkeypatch):
    _arm(monkeypatch)
    row = _terminal_row("same-candidate")
    assert cio._append_evidence(tmp_path, row) is True
    assert cio._append_evidence(tmp_path, row) is False
    assert len(cio.read_evidence(tmp_path)) == 1
    report = cio.build_report(tmp_path)
    pop = next(p for p in report["populations"] if p["strategy"] == "strat_212" and p["instrument"] == "M2K")
    assert pop["terminal_outcomes"] == 1
    assert pop["status"] == "INSUFFICIENT SAMPLE"


def test_report_defensively_dedupes_preexisting_duplicate_terminal_rows(tmp_path, monkeypatch):
    _arm(monkeypatch)
    row = _terminal_row("legacy-duplicate")
    path = tmp_path / cio.EVIDENCE_FILENAME
    with path.open("w", encoding="utf-8") as handle:
        for i in range(30):
            handle.write(json.dumps({"observed_at": f"2026-09-15T14:{i:02d}:00+00:00", **row}) + "\n")
    report = cio.build_report(tmp_path)
    pop = next(p for p in report["populations"] if p["strategy"] == "strat_212" and p["instrument"] == "M2K")
    assert report["evidence_rows_raw"] == 30
    assert report["evidence_rows"] == 1
    assert report["duplicate_rows_ignored"] == 29
    assert pop["terminal_outcomes"] == 1
    assert pop["status"] == "INSUFFICIENT SAMPLE"
