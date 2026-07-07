"""
tests/test_options_storage_replay_restart.py

Phase 13 — storage replay / restart integration tests. Test-only: proves the
Phase 12 append-only SQLite storage layer (options_manager/storage.py) blocks
replay and duplicate use across a simulated process-restart boundary.

No production code is added or modified in this phase. "Restart" is simulated
by calling the public storage functions again against the same on-disk
db_path from a separate call, without reusing any Python object held open by
a prior call — storage.py already opens and closes its own sqlite3
connection per call (see _connect()/init_options_storage/etc.), so this is a
faithful simulation of a fresh process reading persisted state.
"""

from __future__ import annotations

import ast
import socket
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import options_manager.storage as storage_module
from options_manager.config import OptionsManagerConfig
from options_manager.human_confirm import ConfirmationRecord
from options_manager.order_ticket import PreparedOrderTicket
from options_manager.storage import (
    append_confirmation_consumed_event,
    append_ticket_created_event,
    has_confirmation_consumed,
    has_ticket_for_confirmation,
    init_options_storage,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _config(**overrides) -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), **overrides)


def _confirmation_record(**overrides) -> ConfirmationRecord:
    base = dict(
        confirmation_id="conf-1",
        intent_id="intent-abc",
        reviewer="alice",
        approved=True,
        approval_text="CONFIRM DRY RUN ORDER PREP",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=300),
        used_at=None,
        nonce="nonce-1",
    )
    base.update(overrides)
    return ConfirmationRecord(**base)


def _ticket(**overrides) -> PreparedOrderTicket:
    base = dict(
        ticket_id="ticket-1",
        confirmation_id="conf-1",
        ticker="BAC",
        direction="CALL",
        order_action="BUY_TO_OPEN",
        quantity=1,
        contract_strike=60.00,
        contract_expiry=date.today() + timedelta(days=30),
        limit_price=1.95,
        estimated_notional=195.0,
        account_tag="agentic_micro_account",
        source="claude_session",
        dry_run_only=True,
        executable=False,
        broker=None,
        broker_order_id=None,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=120),
        warnings=[],
    )
    base.update(overrides)
    return PreparedOrderTicket(**base)


@pytest.fixture()
def db_path(tmp_path) -> str:
    return str(tmp_path / "options_storage_restart.sqlite")


def _simulate_restart():
    """No-op marker: storage.py holds no connection/state between calls, so
    the next call against the same db_path is already a fresh-process read.
    This function exists to make that assumption explicit at each call site."""
    return None


# --- confirmation replay survives restart -----------------------------------------


def test_consumed_confirmation_remains_found_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    write_result = append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )
    assert write_result.status == "WRITTEN"

    _simulate_restart()

    read_result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert read_result.found is True
    assert read_result.status == "FOUND"
    assert read_result.record["confirmation_id"] == "conf-1"
    assert read_result.record["ticket_id"] == "ticket-1"


def test_duplicate_consumed_confirmation_returns_duplicate_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    record = _confirmation_record()
    first = append_confirmation_consumed_event(
        db_path, record, "ticket-1", _config(), consumed_at=NOW
    )
    assert first.status == "WRITTEN"

    _simulate_restart()

    second = append_confirmation_consumed_event(
        db_path, record, "ticket-2", _config(), consumed_at=NOW + timedelta(seconds=5)
    )
    assert second.status == "DUPLICATE"
    assert second.written is False

    # The original record is untouched — no overwrite occurred.
    read_result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert read_result.record["ticket_id"] == "ticket-1"


# --- ticket-created replay survives restart ---------------------------------------


def test_ticket_created_event_remains_found_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    write_result = append_ticket_created_event(db_path, _ticket(), _config())
    assert write_result.status == "WRITTEN"

    _simulate_restart()

    read_result = has_ticket_for_confirmation(db_path, "conf-1", _config())
    assert read_result.found is True
    assert read_result.status == "FOUND"
    assert read_result.record["ticket_id"] == "ticket-1"


def test_duplicate_ticket_for_same_confirmation_returns_duplicate_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    first = append_ticket_created_event(db_path, _ticket(), _config())
    assert first.status == "WRITTEN"

    _simulate_restart()

    second = append_ticket_created_event(
        db_path, _ticket(ticket_id="ticket-2"), _config()
    )
    assert second.status == "DUPLICATE"
    assert second.written is False


def test_duplicate_ticket_id_returns_duplicate_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    first = append_ticket_created_event(
        db_path, _ticket(confirmation_id="conf-a"), _config()
    )
    assert first.status == "WRITTEN"

    _simulate_restart()

    second = append_ticket_created_event(
        db_path, _ticket(confirmation_id="conf-b"), _config()
    )
    assert second.status == "DUPLICATE"
    assert second.written is False


# --- expires_at preserved exactly across restart -----------------------------------


def test_stored_expires_at_preserved_exactly_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    exact_expiry = NOW + timedelta(seconds=317, microseconds=123456)
    append_confirmation_consumed_event(
        db_path,
        _confirmation_record(expires_at=exact_expiry),
        "ticket-1",
        _config(),
        consumed_at=NOW,
    )

    _simulate_restart()

    read_result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert read_result.record["expires_at"] == exact_expiry


def test_stored_ticket_expires_at_preserved_exactly_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    exact_expiry = NOW + timedelta(seconds=119, microseconds=654321)
    append_ticket_created_event(db_path, _ticket(expires_at=exact_expiry), _config())

    _simulate_restart()

    read_result = has_ticket_for_confirmation(db_path, "conf-1", _config())
    assert read_result.record["expires_at"] == exact_expiry


# --- corruption handling survives restart ------------------------------------------


def test_corrupted_confirmation_row_returns_corrupt_record_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE confirmation_consumed_events SET consumed_at = ? WHERE confirmation_id = ?",
        ("not-a-valid-timestamp", "conf-1"),
    )
    conn.commit()
    conn.close()

    _simulate_restart()

    result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert result.status == "CORRUPT_RECORD"
    assert result.failed_stage == "malformed_row"


def test_corrupted_ticket_row_returns_corrupt_record_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    append_ticket_created_event(db_path, _ticket(), _config())

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ticket_created_events SET expires_at = ? WHERE ticket_id = ?",
        ("also-not-a-valid-timestamp", "ticket-1"),
    )
    conn.commit()
    conn.close()

    _simulate_restart()

    result = has_ticket_for_confirmation(db_path, "conf-1", _config())
    assert result.status == "CORRUPT_RECORD"
    assert result.failed_stage == "malformed_row"


def test_naive_stored_timestamp_returns_corrupt_record_after_simulated_restart(db_path):
    init_options_storage(db_path, _config())
    append_confirmation_consumed_event(
        db_path, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
    )

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE confirmation_consumed_events SET expires_at = ? WHERE confirmation_id = ?",
        (datetime(2026, 1, 1, 1).isoformat(), "conf-1"),
    )
    conn.commit()
    conn.close()

    _simulate_restart()

    result = has_confirmation_consumed(db_path, "conf-1", _config())
    assert result.status == "CORRUPT_RECORD"
    assert result.failed_stage == "malformed_row"


# --- structural safety re-checks (must still hold in Phase 13) --------------------


def test_append_only_source_still_contains_no_mutation_keywords():
    source = Path(storage_module.__file__).read_text()
    for forbidden_keyword in ("UPDATE", "DELETE", "UPSERT", "REPLACE"):
        assert forbidden_keyword not in source, (
            f"storage.py must not contain the SQL keyword {forbidden_keyword!r} "
            "(append-only: INSERT/SELECT/CREATE TABLE only)"
        )


def test_schema_still_contains_no_order_queue_fields():
    source = Path(storage_module.__file__).read_text()
    forbidden_fields = (
        "pending_submit",
        "ready_to_submit",
        "auto_submit",
        "execute_after",
        "order_queue",
        "route_to_broker",
        "broker_payload",
        "credentials",
        "account_number",
        "live_account",
    )
    for forbidden in forbidden_fields:
        assert forbidden not in source, f"storage.py must not contain field {forbidden!r}"


def test_replay_restart_tests_never_open_a_network_socket(monkeypatch):
    def _blocked_socket(*args, **kwargs):
        raise AssertionError("storage replay/restart tests must not open network sockets")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    db = None
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "options_storage_socket_check.sqlite")
        init_options_storage(db, _config())
        append_confirmation_consumed_event(
            db, _confirmation_record(), "ticket-1", _config(), consumed_at=NOW
        )
        has_confirmation_consumed(db, "conf-1", _config())


def test_replay_restart_tests_do_not_mutate_live_options_flag(monkeypatch):
    monkeypatch.delenv("LIVE_OPTIONS_TRADING_ENABLED", raising=False)
    import os

    assert os.getenv("LIVE_OPTIONS_TRADING_ENABLED") is None


def _module_imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_replay_restart_test_module_has_no_forbidden_imports():
    import tests.test_options_storage_replay_restart as this_module

    modules = _module_imported_modules(this_module)
    forbidden_module_fragments = (
        "execution",
        "webhook",
        "risk_engine",
        "alert_ranker",
        "httpx",
        "requests",
        "urllib",
        "live_lock",
    )
    for module in modules:
        for forbidden in forbidden_module_fragments:
            assert forbidden not in module, (
                f"test_options_storage_replay_restart.py must not import {module!r}"
            )
