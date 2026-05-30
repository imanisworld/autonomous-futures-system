"""SQLite persistence for advisory scanner results."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .scorer import ScoreResult


@dataclass(frozen=True)
class StoredScan:
    id: int
    timestamp: str
    ticker: str
    direction: str
    score: int
    pattern: str
    alert_sent: bool
    alert_suppression_reason: str


class ScanStorage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    alert_sent INTEGER NOT NULL,
                    alert_suppression_reason TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scans_alert_key ON scans "
                "(ticker, direction, pattern, timestamp)"
            )

    def record_scan(
        self,
        result: ScoreResult,
        *,
        source: str,
        alert_sent: bool,
        alert_suppression_reason: str,
        timestamp: datetime | None = None,
    ) -> int:
        stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scans (
                    timestamp, source, ticker, direction, score, pattern,
                    components_json, raw_json, alert_sent, alert_suppression_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    source,
                    result.ticker,
                    result.direction,
                    result.score,
                    result.pattern,
                    json.dumps(result.components, sort_keys=True),
                    json.dumps(result.raw, sort_keys=True, default=str),
                    1 if alert_sent else 0,
                    alert_suppression_reason,
                ),
            )
            return int(cursor.lastrowid)

    def recent_alert_exists(
        self,
        ticker: str,
        direction: str,
        pattern: str,
        *,
        window_minutes: int,
        now: datetime | None = None,
    ) -> bool:
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(minutes=window_minutes)).astimezone(
            timezone.utc
        )
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM scans
                WHERE ticker = ? AND direction = ? AND pattern = ?
                  AND alert_sent = 1 AND timestamp >= ?
                LIMIT 1
                """,
                (ticker, direction, pattern, cutoff.isoformat()),
            ).fetchone()
            return row is not None

    def latest(self, limit: int = 25) -> list[StoredScan]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, ticker, direction, score, pattern,
                       alert_sent, alert_suppression_reason
                FROM scans ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            StoredScan(
                id=row["id"],
                timestamp=row["timestamp"],
                ticker=row["ticker"],
                direction=row["direction"],
                score=row["score"],
                pattern=row["pattern"],
                alert_sent=bool(row["alert_sent"]),
                alert_suppression_reason=row["alert_suppression_reason"],
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn
