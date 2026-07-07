"""Phase 12 — inert append-only storage layer.

Durable, replay-protection-only persistence for two facts:
  - a given confirmation_id has been consumed (used) exactly once
  - a given confirmation_id has produced exactly one order ticket

This module performs no broker calls, no HTTP, no order placement, and
creates no order queue. It only ever inserts new rows or creates tables —
existing rows are never modified or removed once written. Every stored
fact is an already-happened, immutable event — never a forward-looking
instruction. There is no field representing a queued or self-triggering
action, no broker routing, no broker payload, no credential, and no
account-number column anywhere in this schema, by design.

Every write defensively re-validates the same invariants already proven in
Phases 7 and 9 before persisting anything: a ticket must be
non-executable (`executable=False`), dry-run-only (`dry_run_only=True`),
and broker-free (`broker=None`, `broker_order_id=None`). Storage never
weakens those guarantees — it only makes the "was this confirmation/ticket
already used" fact durable across a process restart.

No live-options lock bypass exists here because no order path exists in
this phase — there is nothing for a lock to gate. This module never reads
or mutates LIVE_OPTIONS_TRADING_ENABLED, and never imports live_lock.

Independent of alert_ranker/storage.py (which mutates rows in place via
update_shadow_outcome — the opposite of this module's append-only design)
and options_companion/store.py (a wholly separate subsystem) — neither is
imported here.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .human_confirm import ConfirmationRecord
from .order_ticket import PreparedOrderTicket


@dataclass
class StorageWriteResult:
    written: bool
    status: Literal["WRITTEN", "REJECTED", "DATA_BLOCKED", "DUPLICATE", "CORRUPT_RECORD"]
    failed_stage: Optional[str] = None
    reason: str = ""
    record_id: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class StorageReadResult:
    found: bool
    status: Literal["FOUND", "NOT_FOUND", "DATA_BLOCKED", "CORRUPT_RECORD"]
    failed_stage: Optional[str] = None
    reason: str = ""
    record: Optional[dict] = None
    warnings: list[str] = field(default_factory=list)


def _written(record_id: str) -> StorageWriteResult:
    return StorageWriteResult(written=True, status="WRITTEN", record_id=record_id)


def _write_rejected(failed_stage: str, reason: str) -> StorageWriteResult:
    return StorageWriteResult(
        written=False, status="REJECTED", failed_stage=failed_stage, reason=reason
    )


def _write_data_blocked(failed_stage: str, reason: str) -> StorageWriteResult:
    return StorageWriteResult(
        written=False, status="DATA_BLOCKED", failed_stage=failed_stage, reason=reason
    )


def _write_duplicate(failed_stage: str, reason: str) -> StorageWriteResult:
    return StorageWriteResult(
        written=False, status="DUPLICATE", failed_stage=failed_stage, reason=reason
    )


def _write_corrupt(failed_stage: str, reason: str) -> StorageWriteResult:
    return StorageWriteResult(
        written=False, status="CORRUPT_RECORD", failed_stage=failed_stage, reason=reason
    )


def _found(record: dict) -> StorageReadResult:
    return StorageReadResult(found=True, status="FOUND", record=record)


def _not_found() -> StorageReadResult:
    return StorageReadResult(found=False, status="NOT_FOUND")


def _read_data_blocked(failed_stage: str, reason: str) -> StorageReadResult:
    return StorageReadResult(
        found=False, status="DATA_BLOCKED", failed_stage=failed_stage, reason=reason
    )


def _read_corrupt(failed_stage: str, reason: str) -> StorageReadResult:
    return StorageReadResult(
        found=False, status="CORRUPT_RECORD", failed_stage=failed_stage, reason=reason
    )


def _is_tz_aware(value: datetime) -> bool:
    return value.tzinfo is not None


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_options_storage(db_path: str, config: OptionsManagerConfig) -> StorageWriteResult:
    """Create the two append-only tables if they don't already exist.

    config is required and must be passed explicitly by the caller. Safe to
    call more than once against the same db_path — CREATE TABLE IF NOT
    EXISTS is idempotent.
    """
    cfg = config
    if not cfg.storage_enabled:
        return _write_rejected(
            "storage_disabled", "storage_enabled is False; storage not initialized"
        )

    try:
        with _connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS confirmation_consumed_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    confirmation_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    ticket_id TEXT NOT NULL,
                    consumed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(confirmation_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_created_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL UNIQUE,
                    confirmation_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(confirmation_id)
                )
                """
            )
    except sqlite3.DatabaseError as exc:
        return _write_corrupt("db_error", f"failed to initialize storage: {exc}")

    return _written("schema")


def append_confirmation_consumed_event(
    db_path: str,
    confirmation_record: ConfirmationRecord,
    ticket_id: str,
    config: OptionsManagerConfig,
    *,
    consumed_at: datetime,
) -> StorageWriteResult:
    """Durably record that confirmation_record.confirmation_id has been
    consumed. Insert-only; a second call for the same confirmation_id
    returns DUPLICATE rather than overwriting anything.
    """
    cfg = config
    if not cfg.storage_enabled:
        return _write_rejected(
            "storage_disabled", "storage_enabled is False; event not written"
        )

    if not _is_tz_aware(confirmation_record.created_at):
        return _write_data_blocked(
            "timestamp", "confirmation_record.created_at has no timezone info"
        )
    if not _is_tz_aware(confirmation_record.expires_at):
        return _write_data_blocked(
            "timestamp", "confirmation_record.expires_at has no timezone info"
        )
    if not _is_tz_aware(consumed_at):
        return _write_data_blocked("timestamp", "consumed_at has no timezone info")

    if not confirmation_record.confirmation_id or not confirmation_record.confirmation_id.strip():
        return _write_data_blocked(
            "confirmation_id", "confirmation_record.confirmation_id is missing or empty"
        )
    if not ticket_id or not ticket_id.strip():
        return _write_data_blocked("ticket_id", "ticket_id is missing or empty")

    try:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO confirmation_consumed_events (
                    confirmation_id, intent_id, ticket_id, consumed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    confirmation_record.confirmation_id,
                    confirmation_record.intent_id,
                    ticket_id,
                    consumed_at.isoformat(),
                    confirmation_record.expires_at.isoformat(),
                ),
            )
    except sqlite3.IntegrityError:
        return _write_duplicate(
            "confirmation_id",
            f"confirmation_id {confirmation_record.confirmation_id!r} already consumed",
        )
    except sqlite3.DatabaseError as exc:
        return _write_corrupt("db_error", f"failed to write consumed event: {exc}")

    return _written(confirmation_record.confirmation_id)


def append_ticket_created_event(
    db_path: str,
    ticket: PreparedOrderTicket,
    config: OptionsManagerConfig,
) -> StorageWriteResult:
    """Durably record that a ticket was created for a confirmation_id.
    Insert-only; a duplicate ticket_id or a second ticket for the same
    confirmation_id returns DUPLICATE rather than overwriting anything.

    Defensive re-check, same pattern as Phases 7 and 9: refuses to persist
    a ticket that isn't non-executable/dry-run-only/broker-free, even
    though the literals upstream already guarantee it today.
    """
    cfg = config
    if not cfg.storage_enabled:
        return _write_rejected(
            "storage_disabled", "storage_enabled is False; event not written"
        )

    if ticket.executable is not False:
        return _write_rejected(
            "executable", "ticket.executable is not False; refusing to store"
        )
    if ticket.dry_run_only is not True:
        return _write_rejected(
            "dry_run_only", "ticket.dry_run_only is not True; refusing to store"
        )
    if ticket.broker is not None:
        return _write_rejected("broker", "ticket.broker is not None; refusing to store")
    if ticket.broker_order_id is not None:
        return _write_rejected(
            "broker_order_id", "ticket.broker_order_id is not None; refusing to store"
        )

    if not _is_tz_aware(ticket.created_at):
        return _write_data_blocked("timestamp", "ticket.created_at has no timezone info")
    if not _is_tz_aware(ticket.expires_at):
        return _write_data_blocked("timestamp", "ticket.expires_at has no timezone info")

    try:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO ticket_created_events (
                    ticket_id, confirmation_id, created_at, expires_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    ticket.ticket_id,
                    ticket.confirmation_id,
                    ticket.created_at.isoformat(),
                    ticket.expires_at.isoformat(),
                ),
            )
    except sqlite3.IntegrityError:
        return _write_duplicate(
            "ticket_or_confirmation",
            f"ticket_id {ticket.ticket_id!r} or confirmation_id "
            f"{ticket.confirmation_id!r} already has a stored ticket-created event",
        )
    except sqlite3.DatabaseError as exc:
        return _write_corrupt("db_error", f"failed to write ticket-created event: {exc}")

    return _written(ticket.ticket_id)


def has_confirmation_consumed(
    db_path: str, confirmation_id: str, config: OptionsManagerConfig
) -> StorageReadResult:
    """Pure read: has this confirmation_id already been consumed?"""
    cfg = config
    if not cfg.storage_enabled:
        return _read_data_blocked(
            "storage_disabled", "storage_enabled is False; cannot read"
        )

    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT confirmation_id, intent_id, ticket_id, consumed_at, expires_at "
                "FROM confirmation_consumed_events WHERE confirmation_id = ?",
                (confirmation_id,),
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        return _read_corrupt("db_error", f"failed to read consumed event: {exc}")

    if row is None:
        return _not_found()

    try:
        record = {
            "confirmation_id": row["confirmation_id"],
            "intent_id": row["intent_id"],
            "ticket_id": row["ticket_id"],
            "consumed_at": _parse_tz_aware(row["consumed_at"]),
            "expires_at": _parse_tz_aware(row["expires_at"]),
        }
    except (KeyError, ValueError) as exc:
        return _read_corrupt("malformed_row", f"stored consumed event is malformed: {exc}")

    return _found(record)


def has_ticket_for_confirmation(
    db_path: str, confirmation_id: str, config: OptionsManagerConfig
) -> StorageReadResult:
    """Pure read: does this confirmation_id already have a stored ticket?"""
    cfg = config
    if not cfg.storage_enabled:
        return _read_data_blocked(
            "storage_disabled", "storage_enabled is False; cannot read"
        )

    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT ticket_id, confirmation_id, created_at, expires_at "
                "FROM ticket_created_events WHERE confirmation_id = ?",
                (confirmation_id,),
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        return _read_corrupt("db_error", f"failed to read ticket-created event: {exc}")

    if row is None:
        return _not_found()

    try:
        record = {
            "ticket_id": row["ticket_id"],
            "confirmation_id": row["confirmation_id"],
            "created_at": _parse_tz_aware(row["created_at"]),
            "expires_at": _parse_tz_aware(row["expires_at"]),
        }
    except (KeyError, ValueError) as exc:
        return _read_corrupt("malformed_row", f"stored ticket-created event is malformed: {exc}")

    return _found(record)


def _parse_tz_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"stored timestamp {value!r} is not timezone-aware")
    return parsed
