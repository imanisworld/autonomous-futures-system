"""Causal, append-only forward outcome events on the existing options DB."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_manager.config import OptionsManagerConfig
from options_manager.outcomes import (
    EVENT_TYPES,
    SETUP_STATES,
    ForwardOutcomeEvent,
    event_content_hash,
    validate_forward_outcome_event,
)
from options_manager.storage import (
    append_forward_outcome_event,
    init_options_storage,
    load_forward_outcome_events,
)

NOW = datetime(2026, 9, 2, 14, 3, 20, tzinfo=timezone.utc)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


@pytest.fixture
def db_path(tmp_path) -> str:
    path = str(tmp_path / "options.sqlite")
    assert init_options_storage(path, _config()).status == "WRITTEN"
    return path


def _event(**overrides) -> ForwardOutcomeEvent:
    fields = dict(
        session_id="2026-09-02",
        thesis_id="2026-09-02:XYZ:CALL:strat_212:30m",
        ticker="XYZ",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        event_type="SESSION_STAGE",
        event_at=NOW - timedelta(minutes=3),
        provider="robinhood-readonly",
        system_commit_sha="a2d97cf5ce53",
        setup_state="NO_SETUP",
        contract_facts={"strike": 100.0, "bid": 1.95, "ask": 2.05},
        market_context={"spy": 761.2, "qqq": 704.1},
        observations={"stage": "10:03", "strat_type_0930": "2U"},
        reason_codes=("NO_ACTIONABLE_212",),
        provider_updated_at=NOW - timedelta(minutes=4),
    )
    fields.update(overrides)
    return ForwardOutcomeEvent(**fields)


def test_init_adds_table_to_existing_options_db(db_path):
    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"forward_outcome_events", "thesis_snapshot_events"} <= tables
    assert init_options_storage(db_path, _config()).status == "WRITTEN"  # idempotent


def test_append_and_causal_read_round_trip(db_path):
    first = _event()
    later = _event(event_type="TRIGGER", setup_state="TRIGGERED_ACTIONABLE", event_at=NOW - timedelta(minutes=1), observations={"bar_close": 123.9})
    assert append_forward_outcome_event(db_path, later, _config(), recorded_at=NOW).status == "WRITTEN"
    assert append_forward_outcome_event(db_path, first, _config(), recorded_at=NOW).status == "WRITTEN"
    read = load_forward_outcome_events(db_path, _config(), session_id="2026-09-02")
    assert read.status == "FOUND"
    events = read.record["events"]
    assert [e["event_type"] for e in events] == ["SESSION_STAGE", "TRIGGER"]  # ordered by event_at, not insertion
    assert events[0]["recorded_at"] == NOW.isoformat()
    assert events[0]["provider"] == "robinhood-readonly"
    assert events[0]["system_commit_sha"] == "a2d97cf5ce53"
    assert events[0]["contract_facts"] == {"strike": 100.0, "bid": 1.95, "ask": 2.05}
    assert events[0]["reason_codes"] == ["NO_ACTIONABLE_212"]
    assert events[0]["content_hash"] == event_content_hash(first)


def test_exact_retry_is_idempotent(db_path):
    event = _event()
    assert append_forward_outcome_event(db_path, event, _config(), recorded_at=NOW).status == "WRITTEN"
    retry = append_forward_outcome_event(db_path, event, _config(), recorded_at=NOW + timedelta(seconds=30))
    assert retry.status == "DUPLICATE"
    assert load_forward_outcome_events(db_path, _config()).record["count"] == 1
    changed = _event(observations={"stage": "10:03", "strat_type_0930": "2D"})
    assert append_forward_outcome_event(db_path, changed, _config(), recorded_at=NOW).status == "WRITTEN"
    assert load_forward_outcome_events(db_path, _config()).record["count"] == 2


def test_source_timestamp_after_recording_is_future_leakage(db_path):
    result = append_forward_outcome_event(db_path, _event(event_at=NOW + timedelta(seconds=1)), _config(), recorded_at=NOW)
    assert result.status == "DATA_BLOCKED" and "causality" in result.failed_stage
    result = append_forward_outcome_event(db_path, _event(provider_updated_at=NOW + timedelta(seconds=1)), _config(), recorded_at=NOW)
    assert result.status == "DATA_BLOCKED"
    assert load_forward_outcome_events(db_path, _config()).record["count"] == 0


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"session_id": ""}, "missing session_id"),
        ({"thesis_id": " "}, "missing thesis_id"),
        ({"direction": "LONG"}, "must be CALL, PUT, or NONE"),
        ({"event_type": "OUTCOME"}, "not in vocabulary"),
        ({"setup_state": "WIN"}, "not in vocabulary"),
        ({"event_at": datetime(2026, 9, 2, 14, 0)}, "timezone-aware"),
        ({"provider_updated_at": datetime(2026, 9, 2, 14, 0)}, "timezone-aware"),
        ({"system_commit_sha": "abc"}, "too short"),
        ({"contract_facts": {"iv": float("nan")}}, "non-finite"),
        ({"market_context": {"spy": float("inf")}}, "non-finite"),
        ({"observations": {"obj": object()}}, "not JSON-serializable"),
        ({"reason_codes": ["x"]}, "tuple of strings"),
        ({"provider": ""}, "missing provider"),
    ],
)
def test_unstorable_events_fail_closed(db_path, overrides, fragment):
    event = _event(**overrides)
    assert any(fragment in r for r in validate_forward_outcome_event(event)), validate_forward_outcome_event(event)
    result = append_forward_outcome_event(db_path, event, _config(), recorded_at=NOW)
    assert result.status == "DATA_BLOCKED"
    assert load_forward_outcome_events(db_path, _config()).record["count"] == 0


def test_naive_recorded_at_and_storage_disabled_fail_closed(db_path):
    assert append_forward_outcome_event(db_path, _event(), _config(), recorded_at=datetime(2026, 9, 2, 14, 3)).status == "DATA_BLOCKED"
    assert append_forward_outcome_event(db_path, _event(), _config(storage_enabled=False), recorded_at=NOW).status == "REJECTED"
    assert load_forward_outcome_events(db_path, _config(storage_enabled=False)).status == "DATA_BLOCKED"


def test_corrupt_row_is_reported_not_skipped(db_path):
    assert append_forward_outcome_event(db_path, _event(), _config(), recorded_at=NOW).status == "WRITTEN"
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE forward_outcome_events SET payload_json = '{not json'")
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD"  # existing storage vocabulary


def test_filters_by_thesis(db_path):
    assert append_forward_outcome_event(db_path, _event(), _config(), recorded_at=NOW).status == "WRITTEN"
    other = _event(thesis_id="2026-09-02:ABC:PUT:strat_212:30m", ticker="ABC", direction="PUT")
    assert append_forward_outcome_event(db_path, other, _config(), recorded_at=NOW).status == "WRITTEN"
    assert load_forward_outcome_events(db_path, _config(), thesis_id="2026-09-02:ABC:PUT:strat_212:30m").record["count"] == 1
    assert load_forward_outcome_events(db_path, _config(), session_id="2026-09-01").record["count"] == 0


def test_vocabularies_cover_the_reducer_states():
    assert set(SETUP_STATES) == {"NO_SETUP", "SETUP_NOT_TRIGGERED", "TRIGGERED_CONTRACT_BLOCKED", "TRIGGERED_ACTIONABLE", "INVALIDATED", "T1_HIT", "T2_HIT"}
    assert {"TRIGGER", "INVALIDATION", "TARGET_1", "TARGET_2", "CONTRACT_OBSERVATION", "MARKET_CONTEXT"} <= set(EVENT_TYPES)


def test_outcomes_package_has_no_execution_network_or_clock_access():
    import options_manager.outcomes.events as mod

    source = Path(mod.__file__).read_text()
    for forbidden in ("datetime.now", "utcnow", "subprocess", "requests", "httpx", "socket", "execution", "options_companion", "sqlite3", "open("):
        assert forbidden not in source, forbidden


# --- read-integrity regressions: a stored row must be re-verified on read, mirroring
# thesis_snapshot_events (hash + canonical text + indexed columns + causality). ---


def _stored_row(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM forward_outcome_events").fetchone()


def _write_one(db_path):
    assert append_forward_outcome_event(db_path, _event(), _config(), recorded_at=NOW).status == "WRITTEN"


def test_valid_json_payload_tampering_is_corrupt_not_trusted(db_path):
    """(a) payload rewritten to different but syntactically valid JSON."""
    _write_one(db_path)
    payload = json.loads(_stored_row(db_path)["payload_json"])
    payload["setup_state"] = "T2_HIT"
    payload["observations"] = {"stage": "10:03", "strat_type_0930": "2U", "bar_close": 999.0}
    tampered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE forward_outcome_events SET payload_json = ?", (tampered,))
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read
    assert read.record is None


def test_content_hash_mismatch_is_corrupt(db_path):
    """(b) stored hash no longer matches the sha256 of the canonical payload."""
    _write_one(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE forward_outcome_events SET content_hash = ?", ("0" * 64,))
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read


def test_non_canonical_but_equivalent_payload_text_is_corrupt(db_path):
    """(a/b) same data, non-canonical serialization: the row was not written by this writer."""
    _write_one(db_path)
    payload = json.loads(_stored_row(db_path)["payload_json"])
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE forward_outcome_events SET payload_json = ?", (json.dumps(payload, indent=2),))
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read


@pytest.mark.parametrize(
    "column, value",
    [
        ("thesis_id", "2026-09-02:ABC:PUT:strat_212:30m"),
        ("event_type", "TRIGGER"),
        ("setup_state", "T1_HIT"),
        ("event_at", (NOW - timedelta(minutes=30)).isoformat()),
        ("recorded_at", (NOW - timedelta(minutes=1)).isoformat()),
        ("ticker", "ABC"),
        ("provider", "someone-else"),
        ("system_commit_sha", "deadbeefcafe"),
    ],
)
def test_indexed_column_payload_mismatch_is_corrupt(db_path, column, value):
    """(c) indexed column edited while the payload still says otherwise."""
    _write_one(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE forward_outcome_events SET {column} = ?", (value,))
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read


def test_causal_order_is_revalidated_on_read(db_path):
    """(d) recorded_at moved before event_at consistently in column AND payload.
    content_hash excludes recorded_at, so only a causal re-check catches this."""
    _write_one(db_path)
    row = _stored_row(db_path)
    payload = json.loads(row["payload_json"])
    early = (NOW - timedelta(hours=1)).isoformat()  # event_at is NOW-3m, provider_updated_at NOW-4m
    payload["recorded_at"] = early
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE forward_outcome_events SET recorded_at = ?, payload_json = ?",
            (early, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
        )
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read


def test_provider_timestamp_after_recorded_at_is_corrupt_on_read(db_path):
    """(d) provider_updated_at pushed after recorded_at in column AND payload with a
    freshly consistent content_hash: still a causal violation the reader must reject."""
    _write_one(db_path)
    row = _stored_row(db_path)
    payload = json.loads(row["payload_json"])
    late = (NOW + timedelta(seconds=5)).isoformat()
    payload["provider_updated_at"] = late
    forged_hash = event_content_hash(_event(provider_updated_at=NOW + timedelta(seconds=5)))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE forward_outcome_events SET provider_updated_at = ?, payload_json = ?, content_hash = ?",
            (late, json.dumps(payload, sort_keys=True, separators=(",", ":")), forged_hash),
        )
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "CORRUPT_RECORD", read


def test_untampered_rows_still_load_after_integrity_checks(db_path):
    _write_one(db_path)
    other = _event(thesis_id="2026-09-02:ABC:PUT:strat_212:30m", ticker="abc ", direction="PUT", provider_updated_at=None, reason_codes=())
    assert append_forward_outcome_event(db_path, other, _config(), recorded_at=NOW).status == "WRITTEN"
    read = load_forward_outcome_events(db_path, _config())
    assert read.status == "FOUND", read
    assert read.record["count"] == 2
