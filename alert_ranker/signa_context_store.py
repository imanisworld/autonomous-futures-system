"""Read-only storage for shared Signa/manual context evidence.

This module records discovery/context rows only. It has no scanner, strategy,
risk, broker, order, or execution authority and never promotes rows into the
options shadow journal. The table is intentionally a shared provider cache so
options and future futures-context consumers can reuse the same Signa pull.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHARED_PROXY_SYMBOLS = {"SPY", "QQQ", "IWM", "DIA", "VIX", "SVXY", "UVXY", "TLT", "GLD", "USO", "XLE"}
EXPECTED_TICKER_SOURCES = ("scan", "action_card", "enhanced_signal", "options_flow", "dark_pool")
EXPECTED_MARKET_SOURCES = ("market_tide", "signal_index")
GOOD_CONTEXT_STATUSES = {"SIGNA_CONTEXT", "SIGNA_CANDIDATE"}



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
    timeframe: str | None = None
    data_as_of: str | None = None
    provider_timestamp: str | None = None
    consumers: tuple[str, ...] = ()


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
                    payload_json TEXT NOT NULL,
                    timeframe TEXT,
                    data_as_of TEXT,
                    provider_timestamp TEXT,
                    consumers_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            self._migrate_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_signa_context_symbol "
                "ON options_signa_context (ticker, timestamp, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_signa_context_source "
                "ON options_signa_context (source, timestamp, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_signa_context_provider_key "
                "ON options_signa_context (ticker, source, endpoint, timeframe, data_as_of, provider_timestamp)"
            )

    def record(self, payload: dict[str, Any], *, timestamp: datetime | None = None) -> int:
        row = self._normalized_payload(payload, timestamp=timestamp)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO options_signa_context (
                    timestamp, ticker, source, endpoint, direction, status,
                    candidate_key, observation_only, trade_authority, payload_json,
                    timeframe, data_as_of, provider_timestamp, consumers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    row.get("timeframe"),
                    row.get("data_as_of"),
                    row.get("provider_timestamp"),
                    json.dumps(row.get("consumers", []), sort_keys=True, separators=(",", ":")),
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
                "candidate_key, observation_only, trade_authority, payload_json, "
                "timeframe, data_as_of, provider_timestamp, consumers_json "
                f"FROM options_signa_context{where} ORDER BY id DESC LIMIT ?",
                (*params, bounded),
            ).fetchall()
        return [self._stored_from_row(row) for row in rows]


    def context_for_tickers(self, tickers: list[str], *, limit_per_ticker: int = 6) -> dict[str, list[dict[str, Any]]]:
        """Latest context-only rows keyed by ticker for setup/report display.

        This is a presentation helper only. It returns evidence summaries from
        the shared provider cache and never writes to the scanner, journal,
        risk, broker, order, or execution paths.
        """
        symbols = sorted({str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()})
        if not symbols:
            return {}
        bounded = max(1, min(int(limit_per_ticker), 10))
        out: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            seen_sources: set[str] = set()
            rows: list[dict[str, Any]] = []
            items = self.latest(limit=50, ticker=symbol)
            fallback_by_key = self._good_fallbacks(items)
            for item in items:
                if item.source in seen_sources:
                    continue
                seen_sources.add(item.source)
                key = (item.ticker or "MARKET", item.source)
                rows.append(self._summary(item, fallback=fallback_by_key.get(key)))
                if len(rows) >= bounded:
                    break
            out[symbol] = rows
        return out

    def context_for_ticker(self, ticker: str, *, limit: int = 6) -> list[dict[str, Any]]:
        return self.context_for_tickers([ticker], limit_per_ticker=limit).get(ticker.upper(), [])

    def board(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Latest context-only board grouped by ticker and source.

        Partial Signa failures remain visible. If the newest row for a
        ticker/source is an error but an older good row exists, the board keeps
        the error status and marks the displayed fields as a stale fallback.
        """
        grouped: dict[str, dict[str, Any]] = {}
        items = self.latest(limit=limit)
        fallback_by_key = self._good_fallbacks(items)
        for item in items:
            ticker = item.ticker or "MARKET"
            entry = grouped.setdefault(
                ticker,
                {
                    "ticker": ticker,
                    "context_only": True,
                    "observation_only": True,
                    "trade_authority": False,
                    "consumers": set(),
                    "sources": {},
                },
            )
            entry["consumers"].update(item.consumers)
            if item.source in entry["sources"]:
                continue
            key = (ticker, item.source)
            entry["sources"][item.source] = self._summary(
                item, fallback=fallback_by_key.get(key)
            )
        out: list[dict[str, Any]] = []
        for entry in grouped.values():
            entry["consumers"] = sorted(entry["consumers"])
            expected = EXPECTED_MARKET_SOURCES if entry["ticker"] == "MARKET" else EXPECTED_TICKER_SOURCES
            present = set(entry["sources"])
            entry["expected_sources"] = list(expected)
            entry["missing_sources"] = [source for source in expected if source not in present]
            entry["error_sources"] = [
                source for source, data in entry["sources"].items() if data.get("healthy") is False
            ]
            entry["stale_sources"] = [
                source for source, data in entry["sources"].items() if data.get("stale_fallback") is True
            ]
            out.append(entry)
        return sorted(out, key=lambda row: row["ticker"])

    def _good_fallbacks(self, items: list[StoredSignaContext]) -> dict[tuple[str, str], StoredSignaContext]:
        fallback: dict[tuple[str, str], StoredSignaContext] = {}
        for item in items:
            if not _is_good_context(item):
                continue
            key = (item.ticker or "MARKET", item.source)
            fallback.setdefault(key, item)
        return fallback

    def _summary(
        self,
        item: StoredSignaContext,
        *,
        fallback: StoredSignaContext | None = None,
    ) -> dict[str, Any]:
        payload = item.payload
        source_payload = fallback.payload if fallback is not None and not _is_good_context(item) else payload
        fields = {
            key: source_payload[key]
            for key in (
                "grade", "score", "confidence", "sentiment", "count",
                "callPremium", "putPremium", "totalPremium", "netPremium",
                "callVolume", "putVolume", "putCallRatio",
                "gamma_wall", "gammaWall", "flip", "zero_gamma", "zeroGamma",
                "support", "resistance", "call_pct", "put_pct", "row_count",
                "signal", "trade_count", "buy_count", "sell_count",
            )
            if key in source_payload
        }
        error = payload.get("error")
        http_status = payload.get("http_status")
        request_ok = payload.get("request_ok")
        cached = bool(payload.get("cached"))
        backoff_active = bool(payload.get("backoff_active"))
        healthy = _is_good_context(item) and request_ok is not False
        if error is not None:
            fields["error"] = error
        if http_status is not None:
            fields["http_status"] = http_status
        if cached:
            fields["cached"] = True
        if backoff_active:
            fields["backoff_active"] = True
        summary = {
            "source": item.source,
            "status": item.status,
            "direction": fallback.direction if fallback is not None and not healthy else item.direction,
            "timestamp": item.timestamp,
            "endpoint": item.endpoint,
            "timeframe": item.timeframe,
            "data_as_of": item.data_as_of,
            "provider_timestamp": item.provider_timestamp,
            "candidate_key": item.candidate_key,
            "context_only": True,
            "observation_only": True,
            "trade_authority": False,
            "healthy": healthy,
            "error": error,
            "http_status": http_status,
            "request_ok": request_ok,
            "cached": cached,
            "backoff_active": backoff_active,
            "stale_fallback": fallback is not None and not healthy,
            "consumers": list(item.consumers),
            "fields": fields,
        }
        if fallback is not None and not healthy:
            summary["fallback_timestamp"] = fallback.timestamp
            summary["fallback_data_as_of"] = fallback.data_as_of
            summary["fallback_candidate_key"] = fallback.candidate_key
        return summary

    def _normalized_payload(self, payload: dict[str, Any], *, timestamp: datetime | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("signa context payload must be an object")
        stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        row = dict(payload)
        ticker = row.get("ticker") or row.get("symbol")
        if ticker is not None:
            row["ticker"] = str(ticker).upper().strip() or None
        row["source"] = str(row.get("source") or "manual").strip() or "manual"
        row["endpoint"] = _none_if_blank(row.get("endpoint"))
        row["direction"] = _none_if_blank(row.get("direction"))
        row["status"] = str(row.get("status") or "SIGNA_CONTEXT").upper()
        row["timestamp"] = str(row.get("timestamp") or stamp)
        row["timeframe"] = _none_if_blank(row.get("timeframe") or row.get("tf"))
        row["data_as_of"] = _none_if_blank(row.get("data_as_of") or row.get("as_of"))
        row["provider_timestamp"] = _none_if_blank(
            row.get("provider_timestamp")
            or row.get("provider_ts")
            or row.get("server_time")
            or row.get("source_timestamp")
        )
        row["consumers"] = _normalize_consumers(row.get("consumers"), row.get("ticker"))
        row["observation_only"] = True
        row["trade_authority"] = False
        row["candidate_key"] = str(row.get("candidate_key") or self._candidate_key(row))
        return row

    def _candidate_key(self, row: dict[str, Any]) -> str:
        # Shared provider-cache identity. This intentionally omits retrieved_at
        # and local timestamp so the same provider snapshot pulled by options
        # and futures is stored once.
        provider_marker = row.get("data_as_of") or row.get("provider_timestamp")
        if provider_marker is None:
            provider_marker = _stable_payload_hash(row)
        basis = {
            "ticker": row.get("ticker"),
            "source": row.get("source"),
            "endpoint": row.get("endpoint"),
            "timeframe": row.get("timeframe"),
            "provider_marker": provider_marker,
        }
        blob = json.dumps(basis, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:24]

    def _stored_from_row(self, row: sqlite3.Row) -> StoredSignaContext:
        payload = json.loads(row[10])
        consumers = tuple(json.loads(row[14] or "[]"))
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
            timeframe=row[11],
            data_as_of=row[12],
            provider_timestamp=row[13],
            consumers=consumers,
        )

    def _migrate_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(options_signa_context)")}
        migrations = {
            "timeframe": "ALTER TABLE options_signa_context ADD COLUMN timeframe TEXT",
            "data_as_of": "ALTER TABLE options_signa_context ADD COLUMN data_as_of TEXT",
            "provider_timestamp": "ALTER TABLE options_signa_context ADD COLUMN provider_timestamp TEXT",
            "consumers_json": "ALTER TABLE options_signa_context ADD COLUMN consumers_json TEXT NOT NULL DEFAULT '[]'",
        }
        for column, sql in migrations.items():
            if column not in existing:
                conn.execute(sql)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _is_good_context(item: StoredSignaContext) -> bool:
    if item.status not in GOOD_CONTEXT_STATUSES:
        return False
    if item.payload.get("request_ok") is False:
        return False
    return True


def _none_if_blank(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_consumers(value: Any, ticker: Any) -> list[str]:
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part).strip() for part in value]
    else:
        raw = []
    consumers = {item for item in raw if item}
    consumers.add("options")
    if ticker and str(ticker).upper().strip() in SHARED_PROXY_SYMBOLS:
        consumers.update({"shared_proxy", "futures"})
    return sorted(consumers)


def _stable_payload_hash(row: dict[str, Any]) -> str:
    excluded = {"timestamp", "retrieved_at", "candidate_key", "consumers"}
    stable = {key: value for key, value in row.items() if key not in excluded}
    blob = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:24]
