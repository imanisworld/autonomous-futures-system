"""Restart-safe persistence tests for the advisory options thesis manager.

The thesis journal reuses ``options_manager.storage`` and the existing options
SQLite database. It is append-only, broker-free, and carries enough state to
prevent repeated Signa polling from becoming a new event after restart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from options_manager.config import OptionsManagerConfig
from options_manager.plans import (
    PlanObservation,
    PlanStatus,
    SignaObservation,
    StructuralLevel,
    update_trade_thesis,
)
from options_manager.storage import (
    append_thesis_snapshot_event,
    init_options_storage,
    load_latest_thesis_for_identity,
)

NOW = datetime(2026, 9, 2, 13, 0, tzinfo=timezone.utc)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def _observation(**overrides) -> PlanObservation:
    fields = dict(
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        observed_at=NOW.isoformat(),
        mechanical_triggered=True,
        entry_trigger=230.0,
        underlying_invalidation=226.0,
        levels=(
            StructuralLevel(234.0, "RESISTANCE", "PDH"),
            StructuralLevel(238.0, "RESISTANCE", "weekly_high"),
        ),
        contract_valid=True,
        portfolio_risk_valid=True,
        spy_qqq_aligned=True,
        htf_aligned=True,
        event_risk_clear=True,
        signa=SignaObservation(
            direction="bullish",
            grade="A",
            score=82.0,
            requested_tf="1d",
            signal_timestamp="2026-09-02T12:55:00+00:00",
            technicals_as_of="2026-09-02T12:50:00+00:00",
            stale_minutes=5.0,
            retrieved_at="2026-09-02T13:00:00+00:00",
            parser_version="v1",
        ),
        source_reference="scan:aapl:1300",
    )
    fields.update(overrides)
    return PlanObservation(**fields)


def _snapshot(**overrides):
    return update_trade_thesis(None, _observation(**overrides)).snapshot


@pytest.fixture()
def db_path(tmp_path) -> str:
    return str(tmp_path / "options_storage.sqlite")


def _load(db_path, config=None):
    return load_latest_thesis_for_identity(
        db_path,
        ticker="AAPL",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        config=config or _config(),
    )


def test_init_reuses_existing_options_db_and_adds_thesis_table(db_path):
    result = init_options_storage(db_path, _config())
    assert result.status == "WRITTEN"

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()

    assert "confirmation_consumed_events" in tables
    assert "ticket_created_events" in tables
    assert "thesis_snapshot_events" in tables


def test_snapshot_round_trip_survives_fresh_connection_restart(db_path):
    init_options_storage(db_path, _config())
    snapshot = _snapshot()

    write = append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", snapshot, _config(), recorded_at=NOW
    )
    assert write.status == "WRITTEN"

    # storage.py holds no persistent connection, so this read is equivalent to
    # a fresh process reading the same on-disk database.
    loaded = _load(db_path)
    assert loaded.status == "FOUND"
    assert loaded.record["thesis_id"] == "thesis-aapl-1"
    assert loaded.record["recorded_at"] == NOW
    assert loaded.record["snapshot"] == snapshot


def test_signa_event_repeat_state_survives_restart(db_path):
    init_options_storage(db_path, _config())
    first_update = update_trade_thesis(None, _observation())
    first = first_update.snapshot
    assert first.signa_event_count == 1
    assert first.signa_repeat_count == 0

    append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", first, _config(), recorded_at=NOW
    )

    repeat_observation = _observation(
        observed_at=(NOW + timedelta(minutes=5)).isoformat(),
        source_reference="scan:aapl:1305",
        signa=replace(
            _observation().signa,
            retrieved_at=(NOW + timedelta(minutes=5)).isoformat(),
            stale_minutes=10.0,
        ),
    )
    repeated = update_trade_thesis(first, repeat_observation).snapshot
    assert repeated.signa_event_count == 1
    assert repeated.signa_repeat_count == 1

    append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        repeated,
        _config(),
        recorded_at=NOW + timedelta(minutes=5),
    )

    loaded = _load(db_path)
    restored = loaded.record["snapshot"]
    assert restored.signa_event_count == 1
    assert restored.signa_repeat_count == 1
    assert restored.last_signa_fingerprint == repeated.last_signa_fingerprint
    assert restored.latest_signa == repeated.latest_signa
    assert restored.source_references == (
        "scan:aapl:1300",
        "scan:aapl:1305",
    )

    next_repeat = update_trade_thesis(
        restored,
        _observation(
            observed_at=(NOW + timedelta(minutes=10)).isoformat(),
            signa=replace(
                repeated.latest_signa,
                retrieved_at=(NOW + timedelta(minutes=10)).isoformat(),
                stale_minutes=15.0,
            ),
        ),
    ).snapshot
    assert next_repeat.signa_event_count == 1
    assert next_repeat.signa_repeat_count == 2


def test_exact_snapshot_retry_is_idempotent(db_path):
    init_options_storage(db_path, _config())
    snapshot = _snapshot()
    first = append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", snapshot, _config(), recorded_at=NOW
    )
    second = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        snapshot,
        _config(),
        recorded_at=NOW + timedelta(seconds=1),
    )
    assert first.status == "WRITTEN"
    assert second.status == "DUPLICATE"
    assert second.written is False


def test_second_generation_is_blocked_while_first_is_non_terminal(db_path):
    init_options_storage(db_path, _config())
    first = _snapshot()
    append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", first, _config(), recorded_at=NOW
    )

    second_generation = _snapshot(observed_at=(NOW + timedelta(hours=1)).isoformat())
    blocked = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-2",
        second_generation,
        _config(),
        recorded_at=NOW + timedelta(hours=1),
    )
    assert blocked.status == "REJECTED"
    assert blocked.failed_stage == "active_thesis_exists"


def test_terminal_generation_closes_and_allows_new_generation(db_path):
    init_options_storage(db_path, _config())
    first = _snapshot()
    append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", first, _config(), recorded_at=NOW
    )

    terminal = update_trade_thesis(
        first,
        _observation(
            observed_at=(NOW + timedelta(minutes=30)).isoformat(),
            invalidation_hit=True,
        ),
    ).snapshot
    assert terminal.status == PlanStatus.INVALIDATED
    terminal_write = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        terminal,
        _config(),
        recorded_at=NOW + timedelta(minutes=30),
    )
    assert terminal_write.status == "WRITTEN"

    late_same_generation = replace(
        terminal, observed_at=(NOW + timedelta(minutes=31)).isoformat()
    )
    closed = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        late_same_generation,
        _config(),
        recorded_at=NOW + timedelta(minutes=31),
    )
    assert closed.status == "REJECTED"
    assert closed.failed_stage == "terminal_thesis_closed"

    new_snapshot = _snapshot(observed_at=(NOW + timedelta(hours=2)).isoformat())
    new_write = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-2",
        new_snapshot,
        _config(),
        recorded_at=NOW + timedelta(hours=2),
    )
    assert new_write.status == "WRITTEN"

    loaded = _load(db_path)
    assert loaded.record["thesis_id"] == "thesis-aapl-2"
    assert loaded.record["snapshot"].status == PlanStatus.TRIGGERED


def test_corrupted_thesis_payload_fails_closed(db_path):
    init_options_storage(db_path, _config())
    append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", _snapshot(), _config(), recorded_at=NOW
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE thesis_snapshot_events SET snapshot_json = ? WHERE thesis_id = ?",
        ("{not-json", "thesis-aapl-1"),
    )
    conn.commit()
    conn.close()

    loaded = _load(db_path)
    assert loaded.status == "CORRUPT_RECORD"
    assert loaded.failed_stage == "malformed_row"


def test_storage_disabled_blocks_thesis_read_and_write(db_path):
    init_options_storage(db_path, _config())
    disabled = _config(storage_enabled=False)
    write = append_thesis_snapshot_event(
        db_path, "thesis-aapl-1", _snapshot(), disabled, recorded_at=NOW
    )
    read = _load(db_path, disabled)
    assert write.status == "REJECTED"
    assert write.failed_stage == "storage_disabled"
    assert read.status == "DATA_BLOCKED"
    assert read.failed_stage == "storage_disabled"


def test_naive_recorded_at_and_observed_at_fail_closed(db_path):
    init_options_storage(db_path, _config())
    naive_recorded = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        _snapshot(),
        _config(),
        recorded_at=datetime(2026, 9, 2, 13, 0),
    )
    assert naive_recorded.status == "DATA_BLOCKED"
    assert naive_recorded.failed_stage == "timestamp"

    naive_snapshot = _snapshot(observed_at="2026-09-02T13:00:00")
    naive_observed = append_thesis_snapshot_event(
        db_path,
        "thesis-aapl-1",
        naive_snapshot,
        _config(),
        recorded_at=NOW,
    )
    assert naive_observed.status == "DATA_BLOCKED"
    assert naive_observed.failed_stage == "snapshot"


def test_thesis_persistence_has_no_execution_or_network_imports():
    import ast
    import options_manager.storage as storage_module

    tree = ast.parse(Path(storage_module.__file__).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    forbidden = (
        "execution",
        "webhook",
        "alert_ranker",
        "options_companion",
        "requests",
        "httpx",
        "socket",
        "robin_stocks",
        "ib_insync",
    )
    for name in imports:
        assert not any(part in name for part in forbidden), name
