"""Append-only options-manager storage.

Durable persistence for three advisory/replay event families:
  - a given confirmation_id has been consumed (used) exactly once;
  - a given confirmation_id has produced exactly one order ticket; and
  - immutable thesis snapshots used to restore one evolving options thesis
    across process restarts.

This module performs no broker calls, no HTTP, no order placement, and creates
no order queue. It only inserts new rows, reads existing rows, or creates
schema objects. Existing rows are never modified or removed once written.
Every stored fact is an already-happened event, never a forward-looking order
instruction.

Thesis persistence reuses this same SQLite database and the same
``OptionsManagerConfig.storage_enabled`` boundary. It does not create a second
plan database. Snapshot events are append-only: the latest event is rebuilt
into ``TradePlanSnapshot`` on restart, while terminal history remains durable.
A different thesis_id for the same ticker/direction/setup/timeframe is rejected
while the latest generation is non-terminal and is allowed only after the
prior generation reaches a terminal state.

Every ticket write defensively re-validates the same invariants already proven
upstream: the ticket must be non-executable, dry-run-only, and broker-free.
Storage never weakens those guarantees.

No live-options lock bypass exists here because no order path exists in this
module. It never reads or mutates LIVE_OPTIONS_TRADING_ENABLED and never
imports live_lock.

Independent of alert_ranker/storage.py and options_companion/store.py; neither
is imported here.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from .config import OptionsManagerConfig
from .outcomes import ForwardOutcomeEvent, event_content_hash, validate_forward_outcome_event
from .human_confirm import ConfirmationRecord
from .order_ticket import PreparedOrderTicket
from .plans.base import (
    ContractPlanSnapshot,
    ConvictionBand,
    PlanStatus,
    RiskPlanSnapshot,
    SignaObservation,
    TradePlanSnapshot,
)


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
    """Create append-only tables if they do not already exist."""
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS thesis_snapshot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thesis_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(thesis_id, snapshot_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS forward_outcome_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    thesis_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    setup_state TEXT NOT NULL,
                    event_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_updated_at TEXT,
                    system_commit_sha TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, thesis_id, content_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_forward_outcome_session
                ON forward_outcome_events (session_id, thesis_id, event_at, id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_thesis_snapshot_identity
                ON thesis_snapshot_events (
                    ticker, direction, setup_type, timeframe, id
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
    """Durably record that a confirmation_id has been consumed."""
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
    """Durably record that a ticket was created for a confirmation_id."""
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


def _signa_to_payload(signa: Optional[SignaObservation]) -> Optional[dict]:
    if signa is None:
        return None
    return {
        "direction": signa.direction,
        "grade": signa.grade,
        "score": signa.score,
        "requested_tf": signa.requested_tf,
        "signal_timestamp": signa.signal_timestamp,
        "technicals_as_of": signa.technicals_as_of,
        "stale_minutes": signa.stale_minutes,
        "retrieved_at": signa.retrieved_at,
        "parser_version": signa.parser_version,
    }


def _contract_plan_to_payload(plan: ContractPlanSnapshot) -> dict:
    return {
        "expiration": plan.expiration,
        "strike": plan.strike,
        "premium": plan.premium,
        "bid": plan.bid,
        "ask": plan.ask,
        "spread_percent": plan.spread_percent,
        "volume": plan.volume,
        "open_interest": plan.open_interest,
        "dte": plan.dte,
        "max_contracts": plan.max_contracts,
        "premium_stop": plan.premium_stop,
        "distance_to_target": plan.distance_to_target,
        "iv_event_risk": plan.iv_event_risk,
        "theta_risk": plan.theta_risk,
        "trade_style": plan.trade_style,
    }


def _risk_plan_to_payload(plan: RiskPlanSnapshot) -> dict:
    return {
        "planned_dollar_risk": plan.planned_dollar_risk,
        "capital_deployed": plan.capital_deployed,
        "stated_max_dollar_risk": plan.stated_max_dollar_risk,
        "max_trade_risk_dollars": plan.max_trade_risk_dollars,
        "aggregate_open_risk": plan.aggregate_open_risk,
        "projected_open_risk": plan.projected_open_risk,
        "max_aggregate_open_risk_dollars": plan.max_aggregate_open_risk_dollars,
        "aggregate_capital_deployed": plan.aggregate_capital_deployed,
        "projected_capital_deployed": plan.projected_capital_deployed,
        "open_position_count": plan.open_position_count,
        "correlation_risk": [list(item) for item in plan.correlation_risk],
    }


def _snapshot_to_payload(snapshot: TradePlanSnapshot) -> dict:
    payload = {
        "ticker": snapshot.ticker,
        "direction": snapshot.direction,
        "setup_type": snapshot.setup_type,
        "timeframe": snapshot.timeframe,
        "observed_at": snapshot.observed_at,
        "status": snapshot.status.value,
        "actionable": snapshot.actionable,
        "conviction": snapshot.conviction.value,
        "conviction_confirmation_count": snapshot.conviction_confirmation_count,
        "entry_trigger": snapshot.entry_trigger,
        "underlying_invalidation": snapshot.underlying_invalidation,
        "target_1": snapshot.target_1,
        "target_2": snapshot.target_2,
        "target_1_source": snapshot.target_1_source,
        "target_2_source": snapshot.target_2_source,
        "rr_1": snapshot.rr_1,
        "rr_2": snapshot.rr_2,
        "target_status": snapshot.target_status,
        "target_reason_code": snapshot.target_reason_code,
        "blocking_reasons": list(snapshot.blocking_reasons),
        "warnings": list(snapshot.warnings),
        "signa_event_count": snapshot.signa_event_count,
        "signa_repeat_count": snapshot.signa_repeat_count,
        "last_signa_fingerprint": (
            list(snapshot.last_signa_fingerprint)
            if snapshot.last_signa_fingerprint is not None
            else None
        ),
        "latest_signa": _signa_to_payload(snapshot.latest_signa),
        "source_references": list(snapshot.source_references),
    }
    # Omit absent new fields so snapshots written before this extension keep
    # their exact canonical JSON/hash and remain readable after upgrade.
    if snapshot.contract_plan is not None:
        payload["contract_plan"] = _contract_plan_to_payload(snapshot.contract_plan)
    if snapshot.risk_plan is not None:
        payload["risk_plan"] = _risk_plan_to_payload(snapshot.risk_plan)
    return payload


def _snapshot_from_payload(payload: dict) -> TradePlanSnapshot:
    signa_payload = payload.get("latest_signa")
    latest_signa = SignaObservation(**signa_payload) if signa_payload is not None else None
    fingerprint = payload.get("last_signa_fingerprint")
    contract_payload = payload.get("contract_plan")
    risk_payload = payload.get("risk_plan")
    contract_plan = (
        ContractPlanSnapshot(**contract_payload)
        if isinstance(contract_payload, dict)
        else None
    )
    risk_plan = None
    if isinstance(risk_payload, dict):
        risk_values = dict(risk_payload)
        risk_values["correlation_risk"] = tuple(
            (str(item[0]), float(item[1]))
            for item in risk_values.get("correlation_risk", ())
        )
        risk_plan = RiskPlanSnapshot(**risk_values)
    return TradePlanSnapshot(
        ticker=str(payload["ticker"]),
        direction=payload["direction"],
        setup_type=str(payload["setup_type"]),
        timeframe=str(payload["timeframe"]),
        observed_at=str(payload["observed_at"]),
        status=PlanStatus(payload["status"]),
        actionable=bool(payload["actionable"]),
        conviction=ConvictionBand(payload["conviction"]),
        conviction_confirmation_count=int(payload["conviction_confirmation_count"]),
        entry_trigger=payload.get("entry_trigger"),
        underlying_invalidation=payload.get("underlying_invalidation"),
        target_1=payload.get("target_1"),
        target_2=payload.get("target_2"),
        target_1_source=payload.get("target_1_source"),
        target_2_source=payload.get("target_2_source"),
        rr_1=payload.get("rr_1"),
        rr_2=payload.get("rr_2"),
        target_status=str(payload["target_status"]),
        target_reason_code=str(payload["target_reason_code"]),
        blocking_reasons=tuple(payload.get("blocking_reasons") or ()),
        warnings=tuple(payload.get("warnings") or ()),
        signa_event_count=int(payload.get("signa_event_count", 0)),
        signa_repeat_count=int(payload.get("signa_repeat_count", 0)),
        last_signa_fingerprint=tuple(fingerprint) if fingerprint is not None else None,
        latest_signa=latest_signa,
        source_references=tuple(payload.get("source_references") or ()),
        contract_plan=contract_plan,
        risk_plan=risk_plan,
    )


def _snapshot_json_and_hash(snapshot: TradePlanSnapshot) -> tuple[str, str]:
    text = json.dumps(
        _snapshot_to_payload(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalized_thesis_identity(
    ticker: str, direction: str, setup_type: str, timeframe: str
) -> tuple[str, str, str, str]:
    ticker_value = str(ticker or "").strip().upper()
    direction_value = str(direction or "").strip().upper()
    setup_value = str(setup_type or "").strip()
    timeframe_value = str(timeframe or "").strip()
    if not ticker_value or not setup_value or not timeframe_value:
        raise ValueError("ticker, setup_type, and timeframe are required")
    if direction_value not in ("CALL", "PUT"):
        raise ValueError("direction must be CALL or PUT")
    return ticker_value, direction_value, setup_value, timeframe_value


def append_thesis_snapshot_event(
    db_path: str,
    thesis_id: str,
    snapshot: TradePlanSnapshot,
    config: OptionsManagerConfig,
    *,
    recorded_at: datetime,
) -> StorageWriteResult:
    """Append one immutable thesis snapshot to the existing options database.

    ``thesis_id`` distinguishes lifecycle generations. A new generation for the
    same four-part setup identity is accepted only after the previously latest
    generation is terminal. Exact event retries are idempotent via snapshot
    hash and return DUPLICATE.
    """
    cfg = config
    if not cfg.storage_enabled:
        return _write_rejected(
            "storage_disabled", "storage_enabled is False; thesis event not written"
        )
    thesis_value = str(thesis_id or "").strip()
    if not thesis_value:
        return _write_data_blocked("thesis_id", "thesis_id is required")
    if not _is_tz_aware(recorded_at):
        return _write_data_blocked("timestamp", "recorded_at has no timezone info")
    try:
        _parse_tz_aware(snapshot.observed_at)
        identity = _normalized_thesis_identity(
            snapshot.ticker,
            snapshot.direction,
            snapshot.setup_type,
            snapshot.timeframe,
        )
        snapshot_json, snapshot_hash = _snapshot_json_and_hash(snapshot)
    except (TypeError, ValueError) as exc:
        return _write_data_blocked("snapshot", f"thesis snapshot is not storable: {exc}")

    ticker, direction, setup_type, timeframe = identity
    terminal_statuses = {
        PlanStatus.INVALIDATED.value,
        PlanStatus.EXITED.value,
        PlanStatus.EXPIRED.value,
    }

    try:
        with _connect(db_path) as conn:
            latest = conn.execute(
                """
                SELECT thesis_id, status, snapshot_hash
                FROM thesis_snapshot_events
                WHERE ticker = ? AND direction = ? AND setup_type = ? AND timeframe = ?
                ORDER BY id DESC LIMIT 1
                """,
                identity,
            ).fetchone()
            if latest is not None:
                if latest["thesis_id"] == thesis_value and latest["snapshot_hash"] == snapshot_hash:
                    return _write_duplicate(
                        "snapshot_hash",
                        f"thesis_id {thesis_value!r} already has this stored snapshot",
                    )
                latest_terminal = latest["status"] in terminal_statuses
                if latest["thesis_id"] != thesis_value and not latest_terminal:
                    return _write_rejected(
                        "active_thesis_exists",
                        "a different non-terminal thesis already owns this setup identity",
                    )
                if latest["thesis_id"] == thesis_value and latest_terminal:
                    return _write_rejected(
                        "terminal_thesis_closed",
                        "terminal thesis cannot accept another snapshot event",
                    )

            conn.execute(
                """
                INSERT INTO thesis_snapshot_events (
                    thesis_id, ticker, direction, setup_type, timeframe,
                    recorded_at, observed_at, status, snapshot_hash, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thesis_value,
                    ticker,
                    direction,
                    setup_type,
                    timeframe,
                    recorded_at.isoformat(),
                    snapshot.observed_at,
                    snapshot.status.value,
                    snapshot_hash,
                    snapshot_json,
                ),
            )
    except sqlite3.IntegrityError:
        return _write_duplicate(
            "snapshot_hash", f"thesis_id {thesis_value!r} already has this stored snapshot"
        )
    except sqlite3.DatabaseError as exc:
        return _write_corrupt("db_error", f"failed to write thesis snapshot event: {exc}")

    return _written(thesis_value)


def load_latest_thesis_for_identity(
    db_path: str,
    *,
    ticker: str,
    direction: str,
    setup_type: str,
    timeframe: str,
    config: OptionsManagerConfig,
) -> StorageReadResult:
    """Load the latest persisted thesis generation for a four-part identity."""
    if not config.storage_enabled:
        return _read_data_blocked(
            "storage_disabled", "storage_enabled is False; cannot read thesis state"
        )
    try:
        identity = _normalized_thesis_identity(ticker, direction, setup_type, timeframe)
    except ValueError as exc:
        return _read_data_blocked("identity", str(exc))

    try:
        with _connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT thesis_id, ticker, direction, setup_type, timeframe,
                       recorded_at, observed_at, status, snapshot_hash, snapshot_json
                FROM thesis_snapshot_events
                WHERE ticker = ? AND direction = ? AND setup_type = ? AND timeframe = ?
                ORDER BY id DESC LIMIT 1
                """,
                identity,
            ).fetchone()
    except sqlite3.DatabaseError as exc:
        return _read_corrupt("db_error", f"failed to read thesis snapshot event: {exc}")

    if row is None:
        return _not_found()

    try:
        recorded_at = _parse_tz_aware(row["recorded_at"])
        _parse_tz_aware(row["observed_at"])
        payload = json.loads(row["snapshot_json"])
        if not isinstance(payload, dict):
            raise ValueError("snapshot_json must decode to an object")
        snapshot = _snapshot_from_payload(payload)
        snapshot_text, snapshot_hash = _snapshot_json_and_hash(snapshot)
        if snapshot_hash != row["snapshot_hash"]:
            raise ValueError("snapshot hash does not match stored payload")
        if snapshot_text != row["snapshot_json"]:
            raise ValueError("snapshot payload is not canonical")
        if _normalized_thesis_identity(
            snapshot.ticker,
            snapshot.direction,
            snapshot.setup_type,
            snapshot.timeframe,
        ) != identity:
            raise ValueError("snapshot identity does not match indexed row identity")
        if snapshot.observed_at != row["observed_at"]:
            raise ValueError("snapshot observed_at does not match indexed row")
        if snapshot.status.value != row["status"]:
            raise ValueError("snapshot status does not match indexed row")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return _read_corrupt("malformed_row", f"stored thesis event is malformed: {exc}")

    return _found(
        {
            "thesis_id": row["thesis_id"],
            "recorded_at": recorded_at,
            "snapshot": snapshot,
        }
    )


def _parse_tz_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"stored timestamp {value!r} is not timezone-aware")
    return parsed


def append_forward_outcome_event(
    db_path: str,
    event: ForwardOutcomeEvent,
    config: OptionsManagerConfig,
    *,
    recorded_at: datetime,
) -> StorageWriteResult:
    """Append one causal forward outcome event. Append-only and idempotent:
    an identical event (same session, thesis, and content hash) returns
    DUPLICATE instead of a second row. Fails closed when the event is not
    storable or when its source timestamp is later than ``recorded_at``
    (a source cannot report the future)."""
    if not config.storage_enabled:
        return _write_rejected("storage_disabled", "storage_enabled is False; outcome event not written")
    if not _is_tz_aware(recorded_at):
        return _write_data_blocked("timestamp", "recorded_at has no timezone info")
    problems = validate_forward_outcome_event(event)
    if problems:
        return _write_data_blocked("event", "; ".join(problems))
    if event.event_at > recorded_at:
        return _write_data_blocked(
            "causality", f"event_at {event.event_at.isoformat()} is after recorded_at {recorded_at.isoformat()}"
        )
    if event.provider_updated_at is not None and event.provider_updated_at > recorded_at:
        return _write_data_blocked("causality", "provider_updated_at is after recorded_at")
    payload = event.to_payload()
    payload["recorded_at"] = recorded_at.isoformat()
    content_hash = event_content_hash(event)
    try:
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        return _write_data_blocked("event", f"outcome event is not serializable: {exc}")
    try:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO forward_outcome_events (
                    session_id, thesis_id, ticker, direction, setup_type, timeframe,
                    event_type, setup_state, event_at, recorded_at, provider,
                    provider_updated_at, system_commit_sha, content_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id.strip(),
                    event.thesis_id.strip(),
                    event.ticker.strip().upper(),
                    event.direction,
                    event.setup_type.strip(),
                    event.timeframe.strip(),
                    event.event_type,
                    event.setup_state,
                    event.event_at.isoformat(),
                    recorded_at.isoformat(),
                    event.provider.strip(),
                    event.provider_updated_at.isoformat() if event.provider_updated_at else None,
                    event.system_commit_sha.strip(),
                    content_hash,
                    payload_json,
                ),
            )
    except sqlite3.IntegrityError:
        return _write_duplicate("content_hash", f"session {event.session_id!r} thesis {event.thesis_id!r} already has this event")
    except sqlite3.DatabaseError as exc:
        return _write_corrupt("db_error", f"failed to write forward outcome event: {exc}")
    return _written(content_hash)


def load_forward_outcome_events(
    db_path: str,
    config: OptionsManagerConfig,
    *,
    session_id: Optional[str] = None,
    thesis_id: Optional[str] = None,
) -> StorageReadResult:
    """Read events in causal order (event_at, then insertion id). A row whose
    payload does not parse is reported CORRUPT rather than skipped."""
    if not config.storage_enabled:
        return _read_data_blocked("storage_disabled", "storage_enabled is False; outcome events not read")
    clauses: list[str] = []
    params: list[str] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id.strip())
    if thesis_id:
        clauses.append("thesis_id = ?")
        params.append(thesis_id.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        with _connect(db_path) as conn:
            rows = conn.execute(
                f"SELECT payload_json, content_hash, recorded_at FROM forward_outcome_events {where} ORDER BY event_at, id",
                params,
            ).fetchall()
    except sqlite3.DatabaseError as exc:
        return _read_corrupt("db_error", f"failed to read forward outcome events: {exc}")
    events: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            return _read_corrupt("payload", f"stored outcome event {row['content_hash']} is corrupt: {exc}")
        if not isinstance(payload, dict):
            return _read_corrupt("payload", f"stored outcome event {row['content_hash']} is not an object")
        payload["content_hash"] = row["content_hash"]
        events.append(payload)
    return _found({"events": events, "count": len(events)})
