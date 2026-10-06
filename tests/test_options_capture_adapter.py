"""#1145 setup-capture journal → canonical prospective signal (real engine output).

Scenarios drive the merged #1145 ``SetupCaptureEngine`` with #1145's own Oct 5
fixtures, then fold the journal it wrote. Nothing here re-implements capture.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta, timezone

import pytest

from alert_ranker.setup_capture import STATUS_TRIGGERED, catch_count
from alert_ranker.setup_capture_engine import BarAvailabilityOracle
from alert_ranker.setup_capture_store import SetupCaptureJournal
from options_evidence import capture_adapter as ca
from options_evidence import signal as sg
from tests.test_options_setup_capture import (
    OCT5_HIGH,
    OCT5_LOW,
    _print,
    et,
    make_engine,
    oct2_30m,
    oct2_session_closes,
)

SPY_1H = "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U"


def _engine(tmp_path, **kw):
    oracle = BarAvailabilityOracle(oct2_30m(), session_closes=oct2_session_closes())
    return make_engine(tmp_path, oracle, **kw)


def _rows(engine):
    return list(ca.read_capture_journal(engine.journal.path))


def _spy_1h(fold):
    signal = fold.signal_for(SPY_1H)
    assert signal is not None, sorted(fold.signal_by_key)
    return signal


def test_watching_maps_to_one_two_sided_canonical_signal(tmp_path):
    engine = _engine(tmp_path)
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 2, 16, 30))  # re-run: #1145 dedupes; so must we
    fold = ca.fold_capture_rows(_rows(engine))
    s = _spy_1h(fold)
    assert s.structure_id == SPY_1H == s.links.capture_structure_key
    assert s.state is sg.LifecycleState.WATCHING
    assert s.direction is None and s.trigger is None
    assert (s.levels.boundary_high, s.levels.boundary_low) == (OCT5_HIGH, OCT5_LOW)
    assert s.setup_ready_time == et(2026, 10, 2, 16, 0).astimezone(timezone.utc)
    assert len(fold.journal.by_structure(SPY_1H)) == 1
    # every #1145 structure key maps to exactly one canonical signal
    official = SetupCaptureJournal(engine.journal.path, create=False).peek_state()["current"]
    assert set(fold.signal_by_key) == set(official)


def test_triggered_then_reconciled_prospective_catch(tmp_path):
    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, trade_id="iex-20")],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip", trade_id="sip-20")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    provisional = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert provisional.state is sg.LifecycleState.TRIGGERED
    assert (provisional.direction, provisional.trigger, provisional.invalidation) == ("LONG", OCT5_HIGH, OCT5_LOW)
    assert provisional.signal_integrity is sg.IntegrityStatus.UNKNOWN  # pending SIP
    assert provisional.capture["trigger_feed"] == "iex"
    assert "sip_crossed_at" not in provisional.capture  # missing stays missing

    engine.run(now=et(2026, 10, 5, 10, 46, 0))
    final = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert final.state is sg.LifecycleState.TRIGGERED
    assert final.capture["prospective_catch"] is True
    assert final.capture["sip_crossed_at"]
    assert final.signal_integrity is sg.IntegrityStatus.VALID
    assert final.data_integrity is sg.IntegrityStatus.VALID
    assert final.prearmed is True


def test_cold_start_missed_late_survives_mapping(tmp_path):
    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.09, feed="sip", trade_id="s1")],
    )
    engine.run(now=et(2026, 10, 5, 10, 16, 45))  # first sight after the trigger
    s = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert s.state is sg.LifecycleState.MISSED_LATE
    assert s.prearmed is False
    assert s.signal_integrity is sg.IntegrityStatus.DEGRADED


def test_iex_none_sip_cross_is_missed_late_with_reason(tmp_path):
    engine = _engine(tmp_path, iex=[], sip=[_print(et(2026, 10, 5, 9, 30, 40), 770.09, feed="sip")])
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 10, 47, 0))
    s = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert s.state is sg.LifecycleState.MISSED_LATE
    assert s.capture["status_reason"] == "iex_no_cross_sip_cross"


def test_gap_through_open_maps_to_missed_gap(tmp_path):
    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 0), 770.40, trade_id="gap")],
        sip=[_print(et(2026, 10, 5, 9, 30, 0), 770.40, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    s = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert s.state is sg.LifecycleState.MISSED_GAP
    assert s.capture["gap_through"] is True
    assert s.capture["first_print_price"] == 770.40
    assert s.signal_integrity is sg.IntegrityStatus.DEGRADED


def test_expiry_maps_to_expired(tmp_path):
    engine = _engine(tmp_path, iex=[], sip=[])
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 10, 47, 0))
    s = _spy_1h(ca.fold_capture_rows(_rows(engine)))
    assert s.state is sg.LifecycleState.EXPIRED and s.terminal


def test_catch_classification_is_unchanged_by_adaptation(tmp_path):
    """Canonical VALID signal integrity ⇔ #1145 is_prospective_catch."""
    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    engine.run(now=et(2026, 10, 5, 10, 46, 0))
    official = SetupCaptureJournal(engine.journal.path, create=False).peek_state()["current"]
    fold = ca.fold_capture_rows(_rows(engine))
    valid = {
        key for key, sid in fold.signal_by_key.items()
        if fold.journal.get(sid).signal_integrity is sg.IntegrityStatus.VALID
        and fold.journal.get(sid).state is sg.LifecycleState.TRIGGERED
    }
    assert len(valid) == catch_count(official.values()) >= 1


def test_repeated_scanner_sightings_link_without_duplicating(tmp_path):
    engine = _engine(tmp_path)
    engine.run(now=et(2026, 10, 2, 16, 16))
    fold = ca.fold_capture_rows(_rows(engine))
    sightings = [
        {"id": f"shadow:{n}", "ticker": "SPY", "timeframe": "1H",
         "structure_close": "2026-10-02T20:00:00Z", "pattern": "222:2U:2U",
         "seen_at": et(2026, 10, 5, 10, 16, 45)}
        for n in (9925, 9933, 9925)
    ]
    stray = {"id": "shadow:1", "ticker": "QQQ", "timeframe": "1H",
             "structure_close": "2026-10-02T20:00:00Z", "pattern": "222:2U:2U", "seen_at": et(2026, 10, 5, 10, 0)}
    unmatched = ca.link_scanner_sightings(fold, [*sightings, stray])
    assert unmatched == [stray]  # scanner never creates a canonical signal
    s = _spy_1h(fold)
    assert s.links.scanner_sighting_ids == ("shadow:9925", "shadow:9933")
    assert len(fold.journal.by_structure(SPY_1H)) == 1


def test_source_drift_revises_levels_on_the_same_signal():
    base = {
        "record_type": "WATCHING", "structure_key": SPY_1H, "ticker": "SPY", "timeframe": "1H",
        "pattern": "222:2U:2U", "status": "WATCHING", "boundary_high": OCT5_HIGH, "boundary_low": OCT5_LOW,
        "structure_close": "2026-10-02T20:00:00+00:00", "knowable_at": "2026-10-02T20:00:00+00:00",
        "first_seen_at": "2026-10-02T20:16:00+00:00", "persisted_at": "2026-10-02T20:16:00+00:00",
        "observed_at": "2026-10-02T20:16:00+00:00", "revision": 0, "observation_only": True,
        "execution_authority": False, "trade_authority": False,
    }
    drift = {"record_type": "SOURCE_DRIFT", "structure_key": SPY_1H, "status": "WATCHING", "revision": 1,
             "boundary_high": 770.08, "boundary_low": OCT5_LOW, "observed_at": "2026-10-02T20:20:00+00:00"}
    rewatch = {**base, "boundary_high": 770.08, "revision": 1, "observed_at": "2026-10-02T20:20:00+00:00"}
    fold = ca.fold_capture_rows([base, drift, rewatch])
    s = _spy_1h(fold)
    assert s.levels.boundary_high == 770.08 and s.levels.revision == 1
    assert len(fold.signal_by_key) == 1


def test_observation_rows_cannot_gain_authority(tmp_path):
    engine = _engine(tmp_path)
    engine.run(now=et(2026, 10, 2, 16, 16))
    rows = _rows(engine)
    forged = [dict(r) for r in rows]
    forged[-1]["execution_authority"] = True
    with pytest.raises(ca.AdapterError, match="claims authority"):
        ca.fold_capture_rows(forged)
    forged = [dict(r) for r in rows]
    forged[-1]["observation_only"] = False
    with pytest.raises(ca.AdapterError, match="not observation-only"):
        ca.fold_capture_rows(forged)
    fold = ca.fold_capture_rows(rows)
    assert all(not s.execution_authority and s.observation_only for s in fold.journal.signals())
    # The adapter's default epoch is the collector version, not a registered
    # strategy epoch, so research/fitness (registry lookups) exclude it.
    from options_evidence.strategy_epochs import UNREGISTERED_EPOCH, epoch_label_for_record, load_registry

    registry = load_registry()
    for s in fold.journal.signals():
        assert s.strategy_epoch == ca.DEFAULT_EPOCH
        assert epoch_label_for_record(registry, sg.to_record(s)) == UNREGISTERED_EPOCH


def test_missing_trigger_evidence_is_recorded_not_synthesized():
    watching = {
        "record_type": "WATCHING", "structure_key": SPY_1H, "ticker": "SPY", "timeframe": "1H",
        "pattern": "222:2U:2U", "status": "WATCHING", "boundary_high": OCT5_HIGH, "boundary_low": OCT5_LOW,
        "structure_close": "2026-10-02T20:00:00+00:00", "knowable_at": "2026-10-02T20:00:00+00:00",
        "first_seen_at": "2026-10-02T20:16:00+00:00", "observed_at": "2026-10-02T20:16:00+00:00",
    }
    resolution = {**watching, "record_type": "RESOLUTION", "status": STATUS_TRIGGERED, "direction": "LONG",
                  "observed_at": "2026-10-05T13:31:00+00:00"}  # no trigger_crossed_at / sip_crossed_at
    s = _spy_1h(ca.fold_capture_rows([watching, resolution]))
    assert s.state is sg.LifecycleState.DATA_BLOCKED
    assert s.trigger_market_time is None
    assert s.data_integrity is sg.IntegrityStatus.INVALID


def test_reconciliation_demotion_to_data_blocked_is_mirrored():
    watching = {
        "record_type": "WATCHING", "structure_key": SPY_1H, "ticker": "SPY", "timeframe": "1H",
        "pattern": "222:2U:2U", "status": "WATCHING", "boundary_high": OCT5_HIGH, "boundary_low": OCT5_LOW,
        "structure_close": "2026-10-02T20:00:00+00:00", "knowable_at": "2026-10-02T20:00:00+00:00",
        "first_seen_at": "2026-10-02T20:16:00+00:00", "observed_at": "2026-10-02T20:16:00+00:00",
    }
    trig = {**watching, "record_type": "RESOLUTION", "status": "TRIGGERED", "direction": "LONG",
            "trigger_crossed_at": "2026-10-05T13:30:20+00:00", "detected_at": "2026-10-05T13:31:00+00:00",
            "trigger_feed": "iex", "observed_at": "2026-10-05T13:31:00+00:00"}
    blocked = {**trig, "record_type": "RECONCILIATION", "status": "DATA_BLOCKED",
               "status_reason": "sip_reconcile_unavailable", "observed_at": "2026-10-05T15:20:00+00:00"}
    s = _spy_1h(ca.fold_capture_rows([watching, trig, blocked]))
    assert s.state is sg.LifecycleState.DATA_BLOCKED
    assert s.direction == "LONG"  # resolution evidence kept
    assert s.signal_integrity is sg.IntegrityStatus.INVALID


def test_diagnostic_rows_are_ignored_and_journal_is_never_written(tmp_path):
    engine = _engine(tmp_path, clock_offset_s=0.0)
    engine.run(now=et(2026, 10, 2, 16, 16))
    path = engine.journal.path
    with path.open("a") as handle:
        handle.write('{"record_type":"WATCHING","structure_key":"Z|')  # torn tail
    before = path.read_bytes()
    fold = ca.fold_capture_rows(ca.read_capture_journal(path))
    assert fold.ignored["COLLECTOR_STATUS"] >= 1
    assert path.read_bytes() == before


def test_adapter_has_no_execution_or_risk_imports():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(ca.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("execution", "risk", "webhook", "options_manager", "alert_ranker.paper"))
                   for m in mods)
    assert "alert_ranker.setup_capture" in mods
