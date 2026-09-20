"""Shared Signa snapshot storage.

Passive shared cache for raw Signa response snapshots. Options and futures can
reference the same snapshot_id instead of pulling identical proxy symbols twice.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_SOURCE = "signa"
DEFAULT_TIMEFRAME = "unspecified"
DEFAULT_BUCKET_SECONDS = 900


@dataclass(frozen=True)
class StoredSignaSnapshot:
    id: int
    snapshot_id: str
    source: str
    endpoint: str
    symbol: str
    timeframe: str
    params_hash: str
    snapshot_bucket: str
    retrieved_at: str
    data_as_of: str | None
    status: str
    http_status: int | None
    payload_sha256: str
    observation_only: bool
    trade_authority: bool
    payload: dict[str, Any]

    def to_reference(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "endpoint": self.endpoint,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "params_hash": self.params_hash,
            "snapshot_bucket": self.snapshot_bucket,
            "retrieved_at": self.retrieved_at,
            "data_as_of": self.data_as_of,
            "status": self.status,
            "http_status": self.http_status,
            "observation_only": True,
            "trade_authority": False,
        }


class SignaSnapshotStore:
    """Append-only shared cache for read-only Signa snapshots."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signa_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    params_hash TEXT NOT NULL,
                    snapshot_bucket TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    data_as_of TEXT,
                    status TEXT NOT NULL,
                    http_status INTEGER,
                    payload_sha256 TEXT NOT NULL,
                    observation_only INTEGER NOT NULL,
                    trade_authority INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signa_snapshots_lookup "
                "ON signa_snapshots (source, endpoint, symbol, timeframe, params_hash, retrieved_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_signa_snapshots_bucket "
                "ON signa_snapshots (source, endpoint, symbol, timeframe, params_hash, snapshot_bucket)"
            )

    def record_snapshot(
        self,
        *,
        endpoint: str,
        symbol: str,
        payload: dict[str, Any],
        source: str = DEFAULT_SOURCE,
        timeframe: str | None = None,
        params: dict[str, Any] | None = None,
        snapshot_bucket: str | None = None,
        retrieved_at: datetime | str | None = None,
        data_as_of: datetime | str | None = None,
        status: str = "OK",
        http_status: int | None = None,
    ) -> StoredSignaSnapshot:
        if not isinstance(payload, dict):
            raise TypeError("Signa snapshot payload must be a JSON object")
        row = self._normalized_row(
            source=source,
            endpoint=endpoint,
            symbol=symbol,
            timeframe=timeframe,
            params=params,
            snapshot_bucket=snapshot_bucket,
            retrieved_at=retrieved_at,
            data_as_of=data_as_of,
            status=status,
            http_status=http_status,
            payload=payload,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO signa_snapshots (
                    snapshot_id, source, endpoint, symbol, timeframe, params_hash,
                    snapshot_bucket, retrieved_at, data_as_of, status, http_status,
                    payload_sha256, observation_only, trade_authority, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["snapshot_id"],
                    row["source"],
                    row["endpoint"],
                    row["symbol"],
                    row["timeframe"],
                    row["params_hash"],
                    row["snapshot_bucket"],
                    row["retrieved_at"],
                    row["data_as_of"],
                    row["status"],
                    row["http_status"],
                    row["payload_sha256"],
                    1,
                    0,
                    json.dumps(row["payload"], sort_keys=True, separators=(",", ":"), default=str),
                ),
            )
            stored = self._select_by_snapshot_id(conn, row["snapshot_id"])
        if stored is None:
            raise RuntimeError("failed to store Signa snapshot")
        return self._stored_from_row(stored)

    def get(self, snapshot_id: str) -> StoredSignaSnapshot | None:
        with self._connect() as conn:
            row = self._select_by_snapshot_id(conn, snapshot_id)
        return self._stored_from_row(row) if row is not None else None

    def latest(
        self,
        *,
        symbol: str | None = None,
        endpoint: str | None = None,
        source: str | None = None,
        timeframe: str | None = None,
        limit: int = 25,
    ) -> list[StoredSignaSnapshot]:
        clauses: list[str] = []
        params: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(normalize_symbol(symbol))
        if endpoint is not None:
            clauses.append("endpoint = ?")
            params.append(normalize_endpoint(endpoint))
        if source is not None:
            clauses.append("source = ?")
            params.append(normalize_source(source))
        if timeframe is not None:
            clauses.append("timeframe = ?")
            params.append(normalize_timeframe(timeframe))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        bounded = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, snapshot_id, source, endpoint, symbol, timeframe, params_hash, "
                "snapshot_bucket, retrieved_at, data_as_of, status, http_status, payload_sha256, "
                "observation_only, trade_authority, payload_json "
                f"FROM signa_snapshots{where} ORDER BY retrieved_at DESC, id DESC LIMIT ?",
                (*params, bounded),
            ).fetchall()
        return [self._stored_from_row(row) for row in rows]

    def find_fresh(
        self,
        *,
        endpoint: str,
        symbol: str,
        max_age_seconds: int | float,
        source: str = DEFAULT_SOURCE,
        timeframe: str | None = None,
        params: dict[str, Any] | None = None,
        now: datetime | str | None = None,
        status: str = "OK",
    ) -> StoredSignaSnapshot | None:
        cutoff = parse_utc(now) - timedelta(seconds=float(max_age_seconds))
        params_hash = make_params_hash(params)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, snapshot_id, source, endpoint, symbol, timeframe, params_hash, "
                "snapshot_bucket, retrieved_at, data_as_of, status, http_status, payload_sha256, "
                "observation_only, trade_authority, payload_json "
                "FROM signa_snapshots "
                "WHERE source = ? AND endpoint = ? AND symbol = ? AND timeframe = ? "
                "AND params_hash = ? AND status = ? AND retrieved_at >= ? "
                "ORDER BY retrieved_at DESC, id DESC LIMIT 1",
                (
                    normalize_source(source),
                    normalize_endpoint(endpoint),
                    normalize_symbol(symbol),
                    normalize_timeframe(timeframe),
                    params_hash,
                    normalize_status(status),
                    format_utc(cutoff),
                ),
            ).fetchone()
        return self._stored_from_row(row) if row is not None else None

    def _normalized_row(
        self,
        *,
        source: str,
        endpoint: str,
        symbol: str,
        timeframe: str | None,
        params: dict[str, Any] | None,
        snapshot_bucket: str | None,
        retrieved_at: datetime | str | None,
        data_as_of: datetime | str | None,
        status: str,
        http_status: int | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        retrieved = parse_utc(retrieved_at)
        normalized = {
            "source": normalize_source(source),
            "endpoint": normalize_endpoint(endpoint),
            "symbol": normalize_symbol(symbol),
            "timeframe": normalize_timeframe(timeframe),
            "params_hash": make_params_hash(params),
            "snapshot_bucket": snapshot_bucket or make_snapshot_bucket(retrieved),
            "retrieved_at": format_utc(retrieved),
            "data_as_of": format_utc(parse_utc(data_as_of)) if data_as_of is not None else None,
            "status": normalize_status(status),
            "http_status": int(http_status) if http_status is not None else None,
            "payload": dict(payload),
        }
        normalized["payload_sha256"] = payload_sha256(payload)
        normalized["snapshot_id"] = make_snapshot_id(
            source=normalized["source"],
            endpoint=normalized["endpoint"],
            symbol=normalized["symbol"],
            timeframe=normalized["timeframe"],
            params_hash=normalized["params_hash"],
            snapshot_bucket=normalized["snapshot_bucket"],
        )
        return normalized

    def _select_by_snapshot_id(self, conn: sqlite3.Connection, snapshot_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT id, snapshot_id, source, endpoint, symbol, timeframe, params_hash, "
            "snapshot_bucket, retrieved_at, data_as_of, status, http_status, payload_sha256, "
            "observation_only, trade_authority, payload_json "
            "FROM signa_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()

    def _stored_from_row(self, row: sqlite3.Row) -> StoredSignaSnapshot:
        payload = json.loads(row[15])
        return StoredSignaSnapshot(
            id=int(row[0]),
            snapshot_id=str(row[1]),
            source=str(row[2]),
            endpoint=str(row[3]),
            symbol=str(row[4]),
            timeframe=str(row[5]),
            params_hash=str(row[6]),
            snapshot_bucket=str(row[7]),
            retrieved_at=str(row[8]),
            data_as_of=row[9],
            status=str(row[10]),
            http_status=row[11],
            payload_sha256=str(row[12]),
            observation_only=bool(row[13]),
            trade_authority=bool(row[14]),
            payload=payload,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def normalize_source(value: str | None) -> str:
    text = str(value or DEFAULT_SOURCE).strip().lower()
    return text or DEFAULT_SOURCE


def normalize_endpoint(value: str) -> str:
    text = str(value or "").strip().strip("/").lower()
    if not text:
        raise ValueError("Signa endpoint is required")
    return text


def normalize_symbol(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise ValueError("Signa snapshot symbol is required")
    return text


def normalize_timeframe(value: str | None) -> str:
    text = str(value or DEFAULT_TIMEFRAME).strip().lower()
    return text or DEFAULT_TIMEFRAME


def normalize_status(value: str | None) -> str:
    text = str(value or "OK").strip().upper()
    return text or "OK"


def parse_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_snapshot_bucket(value: datetime | str | None = None, *, bucket_seconds: int = DEFAULT_BUCKET_SECONDS) -> str:
    if bucket_seconds <= 0:
        raise ValueError("bucket_seconds must be positive")
    dt = parse_utc(value)
    epoch = int(dt.timestamp())
    bucket_epoch = epoch - (epoch % int(bucket_seconds))
    return format_utc(datetime.fromtimestamp(bucket_epoch, tz=timezone.utc))


def make_params_hash(params: dict[str, Any] | None) -> str:
    blob = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def payload_sha256(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def make_snapshot_id(
    *,
    source: str,
    endpoint: str,
    symbol: str,
    timeframe: str,
    params_hash: str,
    snapshot_bucket: str,
) -> str:
    basis = {
        "source": normalize_source(source),
        "endpoint": normalize_endpoint(endpoint),
        "symbol": normalize_symbol(symbol),
        "timeframe": normalize_timeframe(timeframe),
        "params_hash": str(params_hash),
        "snapshot_bucket": str(snapshot_bucket),
    }
    blob = json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
    return "signa_" + hashlib.sha256(blob).hexdigest()[:24]
