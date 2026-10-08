"""Adversarial source-only tests for the dedicated, non-#1145 E1 adapter."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from options_evidence.options_122_adapter import AdapterError, fold_122_rows, read_122_journal
from options_evidence.signal import LifecycleState, to_record, verify_record
from scripts.options_122_prospective_collect import (
    COLLECTOR_ID, COLLECTOR_VERSION, POLICY_EPOCH, _canonical_binding,
)
from scripts.options_122_iex_provisional_audit import Arm, reconcile_pair

CLOSE = "2026-10-07T14:00:00+00:00"
ARMED = "2026-10-07T14:01:00+00:00"
IEX = "2026-10-07T14:02:00+00:00"
SIP = "2026-10-07T14:01:59+00:00"
RESOLVED = "2026-10-07T14:03:00+00:00"
RECONCILED = "2026-10-07T14:46:00+00:00"


def _time_ns(value):
    return int(datetime.fromisoformat(value).timestamp() * 1_000_000_000)


def _feed(tmp_path: Path, feed: str, at: str, direction: str, family: str):
    path = tmp_path / f"s1.{feed}.jsonl"
    payload = (feed + ":strict-trade-window\n").encode()
    path.write_bytes(payload)
    return {
        "status": "PROVEN", "source": f"alpaca_{feed}",
        "break_side": "LOW" if direction == "SHORT" else "HIGH",
        "direction": direction, "family_side": family,
        "timestamp": at, "timestamp_ns": _time_ns(at),
        "raw_trade_file": str(path),
        "raw_trade_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _rows(tmp_path: Path):
    armobs = {
        "setup_id": "s1", "setup_fingerprint": "fingerprint-1",
        "ticker": "SPY", "session_date": "2026-10-07",
        "source_timeframe": "30Min", "structure_close_time": CLOSE,
        "watch_start": CLOSE, "watch_until": "2026-10-07T14:30:00+00:00",
        "reference_direction": "two_up", "boundary_high": 11.0,
        "boundary_low": 6.5, "status": "WATCHING",
    }
    stamp = _canonical_binding(SimpleNamespace(**armobs))
    common = dict(
        collector_id=COLLECTOR_ID, collector_version=COLLECTOR_VERSION,
        policy_epoch=POLICY_EPOCH, setup_id="s1", canonical_binding=stamp,
    )
    iex = _feed(tmp_path, "iex", IEX, "SHORT", "REVERSAL")
    sip = _feed(tmp_path, "sip", SIP, "SHORT", "REVERSAL")
    resolved = {**armobs, "status": "TRIGGERED"}
    arm = {**common, "record_type": "ARMED", "observed_at": ARMED, "observation": armobs}
    resolution = {
        **common, "record_type": "RESOLUTION", "observed_at": RESOLVED,
        "observation": resolved, "prearmed_at": ARMED,
        "source_outcome": "REVERSAL", "trigger_source": iex,
        "capture_gate_eligible": True, "option_evidence_usable": True,
        "option_evidence": {
            "status": "CAPTURED",
            "captured_at": "2026-10-07T14:02:30+00:00",
            "selector_evidence": {"status": "CAPTURED", "production_replay_parity": True},
            "selected_contract": "SPY261106P00600000",
            "option_quote_age_seconds": 4.0, "underlying_quote_age_seconds": 3.0,
        },
    }
    armshape = Arm(
        session_date="2026-10-07", symbol="SPY",
        watch_start=datetime.fromisoformat(CLOSE),
        watch_end=datetime.fromisoformat(armobs["watch_until"]),
        boundary_high=11.0, boundary_low=6.5, reference_direction="two_up",
    )
    reconciliation = {
        **common, "record_type": "RECONCILIATION",
        "observed_at": RECONCILED, "observation": resolved,
        "iex": iex, "sip": sip,
        "policy": reconcile_pair(arm=armshape, iex=iex, sip=sip),
    }
    return [arm, resolution, reconciliation]


def _repolicy(rows):
    obs = rows[0]["observation"]
    arm = Arm(
        session_date=obs["session_date"], symbol=obs["ticker"],
        watch_start=datetime.fromisoformat(obs["watch_start"]),
        watch_end=datetime.fromisoformat(obs["watch_until"]),
        boundary_high=obs["boundary_high"], boundary_low=obs["boundary_low"],
        reference_direction=obs["reference_direction"],
    )
    rows[2]["iex"] = ({"status": "NO_BREAK"}
                      if rows[1]["source_outcome"] == "NO_BREAK"
                      else copy.deepcopy(rows[1]["trigger_source"]))
    rows[2]["policy"] = reconcile_pair(
        arm=arm, iex=rows[2]["iex"], sip=rows[2]["sip"],
    )


def test_confirmed_catch_is_one_canonical_verified_observation(tmp_path):
    rows = _rows(tmp_path)
    fold = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert len(fold.verified_catches) == 1
    signal = fold.signal_for("s1")
    assert signal is not None
    record = to_record(signal)
    assert not verify_record(record, registry=fold.journal.registry)
    assert signal.is_prospective_catch
    assert record["strategy_epoch"] == "122-IEX-E1"
    assert record["resolution_state"] == "TRIGGERED"
    assert record["execution_authority"] is False


def test_unverified_raw_bytes_never_count_as_catch(tmp_path):
    rows = _rows(tmp_path)
    no_root = fold_122_rows(rows)
    assert not no_root.verified_catches
    assert no_root.signal_for("s1").state is LifecycleState.DATA_BLOCKED
    (tmp_path / "s1.iex.jsonl").write_bytes(b"mutated\n")
    with pytest.raises(AdapterError, match="iex_raw_hash_mismatch"):
        fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)


@pytest.mark.parametrize("flaw", [
    "wrong_provisional_source", "wrong_timeframe", "wrong_pattern",
    "wrong_collector", "wrong_universe", "wrong_ticker", "wrong_structure_close",
    "missing_binding", "wrong_policy", "changed_fingerprint", "duplicate_armed",
    "out_of_order", "source_drift", "legacy_upgrade",
])
def test_forged_or_ambiguous_binding_fails_closed(tmp_path, flaw):
    rows = _rows(tmp_path)
    if flaw == "wrong_provisional_source":
        rows[0]["canonical_binding"]["provisional_trigger_source"] = "alpaca_iex"
    elif flaw == "wrong_timeframe":
        rows[0]["canonical_binding"]["timeframe"] = "1h"
    elif flaw == "wrong_pattern":
        rows[0]["canonical_binding"]["pattern"] = "122:2D"
    elif flaw == "wrong_collector":
        rows[0]["collector_id"] = "OPTIONS_SETUP_CAPTURE"
    elif flaw == "wrong_universe":
        rows[0]["canonical_binding"]["universe"] = "SPY_ONLY"
    elif flaw == "wrong_ticker":
        rows[0]["observation"]["ticker"] = "FAKE"
    elif flaw == "wrong_structure_close":
        rows[0]["observation"]["structure_close_time"] = "2026-10-07T13:30:00Z"
    elif flaw == "missing_binding":
        rows[0].pop("canonical_binding")
    elif flaw == "wrong_policy":
        rows[2]["policy"]["reconciliation"] = "CONFIRMED_FORGED"
    elif flaw == "changed_fingerprint":
        rows[1]["observation"]["setup_fingerprint"] = "revised"
    elif flaw == "duplicate_armed":
        rows.insert(1, copy.deepcopy(rows[0]))
    elif flaw == "out_of_order":
        rows[1]["observed_at"] = "2026-10-07T13:59:00Z"
    elif flaw == "source_drift":
        drift = copy.deepcopy(rows[1])
        drift["record_type"] = "SOURCE_DRIFT"
        rows[1] = drift
    elif flaw == "legacy_upgrade":
        rows[0].pop("canonical_binding")
    if flaw == "missing_binding":
        with pytest.raises(AdapterError, match="bound_row_without_arm|legacy_setup_upgrade"):
            fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    else:
        with pytest.raises(AdapterError):
            fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)


def test_provisional_iex_reversal_is_pending_not_scoreable(tmp_path):
    rows = _rows(tmp_path)
    fold = fold_122_rows(rows[:2], raw_root=tmp_path)
    assert not fold.verified_catches
    assert fold.excluded["PENDING_SIP"] == 1
    assert not fold.signal_for("s1").is_prospective_catch


def test_sip_disagrees_with_iex_provisional(tmp_path):
    rows = _rows(tmp_path)
    rows[2]["sip"]["family_side"] = "CONTINUATION"
    _repolicy(rows)
    fold = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert not fold.verified_catches
    assert fold.signal_for("s1").state is LifecycleState.DATA_BLOCKED


def test_iex_missed_sip_reversal_remains_missed_late(tmp_path):
    rows = _rows(tmp_path)
    rows[1]["source_outcome"] = "NO_BREAK"
    rows[1]["trigger_source"] = {"status": "NO_BREAK"}
    rows[1]["observation"]["status"] = "WATCHING"
    rows[2]["observation"]["status"] = "WATCHING"
    _repolicy(rows)
    fold = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert not fold.verified_catches
    assert fold.excluded["MISSED_BY_IEX"] == 1
    assert fold.signal_for("s1").state is LifecycleState.MISSED_LATE


def test_same_direction_iex_first_break_cancels(tmp_path):
    rows = _rows(tmp_path)
    rows[1]["source_outcome"] = "CONTINUATION"
    rows[1]["trigger_source"]["direction"] = "LONG"
    rows[1]["trigger_source"]["break_side"] = "HIGH"
    rows[1]["trigger_source"]["family_side"] = "CONTINUATION"
    rows[1]["observation"]["status"] = "CANCELLED"
    rows[2]["observation"]["status"] = "CANCELLED"
    _repolicy(rows)
    fold = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert not fold.verified_catches
    assert fold.signal_for("s1").state is LifecycleState.INVALIDATED


@pytest.mark.parametrize("failure", ["sip_blocked", "option_missing", "late_capture", "sip_earlier_than_arm"])
def test_blocked_or_late_capture_never_admitted(tmp_path, failure):
    rows = _rows(tmp_path)
    if failure == "sip_blocked":
        rows[2]["sip"] = {"status": "DATA_BLOCKED"}
        _repolicy(rows)
    elif failure == "option_missing":
        rows[1]["option_evidence"] = None
    elif failure == "late_capture":
        rows[1]["option_evidence"]["captured_at"] = "2026-10-07T14:08:00Z"
    elif failure == "sip_earlier_than_arm":
        # Keep the timestamp and nanoseconds together, while the pre-arm proof fails.
        rows[2]["sip"]["timestamp"] = "2026-10-07T14:00:30+00:00"
        rows[2]["sip"]["timestamp_ns"] = _time_ns(rows[2]["sip"]["timestamp"])
        _repolicy(rows)
    fold = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert not fold.verified_catches
    assert not fold.signal_for("s1").is_prospective_catch


def test_legacy_and_1145_never_gain_registered_epoch(tmp_path):
    rows = _rows(tmp_path)
    legacy = [{k: v for k, v in row.items() if k != "canonical_binding"} for row in rows]
    fold = fold_122_rows(legacy)
    assert not fold.verified_catches
    assert fold.excluded["LEGACY_UNVERSIONED"] == 3
    broad = [{
        "record_type": "WATCHING", "setup_id": "SPY|30m|legacy",
        "capture_id": "OPTIONS_SETUP_CAPTURE", "level_source": "public_regular_30m",
    }]
    fold2 = fold_122_rows(broad)
    assert not fold2.verified_catches and not fold2.journal.signals()


def test_readonly_journal_never_changes_bytes_and_refuses_torn_tail(tmp_path):
    rows = _rows(tmp_path)
    path = tmp_path / "journal.jsonl"
    data = b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in rows)
    path.write_bytes(data)
    fold = fold_122_rows(read_122_journal(path), raw_root=tmp_path)
    assert fold.verified_catches
    assert path.read_bytes() == data
    path.write_bytes(data + b'{"unfinished":')
    with pytest.raises(AdapterError, match="torn_journal_tail"):
        list(read_122_journal(path))


def test_without_explicit_trusted_freshness_limit_no_catch(tmp_path):
    rows = _rows(tmp_path)
    folded = fold_122_rows(rows, raw_root=tmp_path)
    assert not folded.verified_catches
    assert folded.signal_for("s1").state is LifecycleState.DATA_BLOCKED


def test_stale_option_quote_refused_even_if_captured_status_claimed(tmp_path):
    rows = _rows(tmp_path)
    rows[1]["option_evidence"]["option_quote_age_seconds"] = 90.0
    folded = fold_122_rows(rows, raw_root=tmp_path, max_quote_age_seconds=15)
    assert not folded.verified_catches
    assert folded.excluded["CAPTURE_UNUSABLE"] == 1


def test_duplicate_json_keys_rejected_even_if_last_value_looks_valid(tmp_path):
    path = tmp_path / "duplicate-keys.jsonl"
    path.write_bytes(b'{"record_type":"ARMED","record_type":"RESOLUTION"}\n')
    with pytest.raises(AdapterError, match="duplicate_json_key"):
        list(read_122_journal(path))
