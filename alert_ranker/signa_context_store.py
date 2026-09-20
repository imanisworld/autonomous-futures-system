"""Read-only storage for options Signa/manual context evidence.

This module records discovery/context rows only. It has no scanner, strategy,
risk, broker, order, or execution authority and never promotes rows into the
options shadow journal.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredSignaContext:
    id: int
    timestamp: str
    ticker: str | None
    source: str
    endpoint: str | None
    direction: str | None
    status: str
    candidate_key: str
    observation_only: bool
    trade_authority: bool
    payload: dict[str, Any]


class SignaContextStore:
    """SQLite sink for read-only Signa/context evidence rows."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS options_signa_context (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    ticker TEXT,
                    source TEXT NOT NULL,
                    endpoint TEXT,
                    direction TEXT,
                    status TEXT NOT NULL,
                    candidate_key TEXT NOT NULL UNIQUE,
                    observation_only INTEGER NOT NULL,
                    trade_authority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_signa_context_symbol "
                "ON options_signa_context (ticker, timestamp, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_signa_context_source "
                "ON options_signa_context (source, timestamp, id)"
            )

    def record(self, payload: dict[str, Any], *, timestamp: datetime | None = None) -> int:
        row = self._normalized_payload(payload, timestamp=timestamp)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO options_signa_context (
                    timestamp, ticker, source, endpoint, direction, status,
                    candidate_key, observation_only, trade_authority, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"],
                    row.get("ticker"),
                    row["source"],
                    row.get("endpoint"),
                    row.get("direction"),
                    row["status"],
                    row["candidate_key"],
                    1,
                    0,
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                ),
            )
            if cursor.lastrowid:
                return int(cursor.lastrowid)
            existing = conn.execute(
                "SELECT id FROM options_signa_context WHERE candidate_key = ?",
                (row["candidate_key"],),
            ).fetchone()
            return int(existing[0]) if existing else 0

    def record_many(self, rows: list[dict[str, Any]], *, timestamp: datetime | None = None) -> list[int]:
        return [self.record(row, timestamp=timestamp) for row in rows]

    def latest(self, *, limit: int = 25, ticker: str | None = None, source: str | None = None) -> list[StoredSignaContext]:
        bounded = max(1, min(int(limit), 200))
        clauses: list[str] = []
        params: list[Any] = []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, timestamp, ticker, source, endpoint, direction, status, "
                "candidate_key, observation_only, trade_authority, payload_json "
                f"FROM options_signa_context{where} ORDER BY id DESC LIMIT ?",
                (*params, bounded),
            ).fetchall()
        return [self._stored_from_row(row) for row in rows]

    def _normalized_payload(self, payload: dict[str, Any], *, timestamp: datetime | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("signa context payload must be an object")
        stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        row = dict(payload)
        ticker = row.get("ticker") or row.get("symbol")
        if ticker is not None:
            row["ticker"] = str(ticker).upper().strip() or None
        row["source"] = str(row.get("source") or "manual").strip() or "manual"
        row["endpoint"] = row.get("endpoint")
        row["direction"] = row.get("direction")
        row["status"] = str(row.get("status") or "SIGNA_CONTEXT").upper()
        row["timestamp"] = str(row.get("timestamp") or stamp)
        row["observation_only"] = True
        row["trade_authority"] = False
        row["candidate_key"] = str(row.get("candidate_key") or self._candidate_key(row))
        return row

    def _candidate_key(self, row: dict[str, Any]) -> str:
        basis = {
            "ticker": row.get("ticker"),
            "source": row.get("source"),
            "endpoint": row.get("endpoint"),
            "direction": row.get("direction"),
            "status": row.get("status"),
            "timestamp": row.get("timestamp"),
            "payload": row,
        }
        blob = json.dumps(basis, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:24]

    def _stored_from_row(self, row: sqlite3.Row) -> StoredSignaContext:
        payload = json.loads(row[10])
        return StoredSignaContext(
            id=int(row[0]),
            timestamp=str(row[1]),
            ticker=row[2],
            source=str(row[3]),
            endpoint=row[4],
            direction=row[5],
            status=str(row[6]),
            candidate_key=str(row[7]),
            observation_only=bool(row[8]),
            trade_authority=bool(row[9]),
            payload=payload,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn
