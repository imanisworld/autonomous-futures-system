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
    components: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class StoredShadowSetup:
    id: int
    timestamp: str
    scan_id: int
    ticker: str
    direction: str
    score: int
    pattern: str
    status: str
    setup_inputs: dict[str, Any]
    provider_snapshot: dict[str, Any]
    selected_contract: dict[str, Any]
    outcome: dict[str, Any]


@dataclass(frozen=True)
class ShadowJournalSummary:
    total: int
    open: int
    closed: int
    wins: int
    losses: int
    breakeven: int
    cancelled: int
    expired: int
    win_rate_percent: float | None
    total_pnl_dollars: float
    average_pnl_percent: float | None
    rejected: int = 0


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS options_shadow_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    scan_id INTEGER NOT NULL,
                    ticker TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    status TEXT NOT NULL,
                    setup_inputs_json TEXT NOT NULL,
                    provider_snapshot_json TEXT NOT NULL,
                    selected_contract_json TEXT NOT NULL,
                    outcome_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_shadow_journal_scan "
                "ON options_shadow_journal (scan_id, ticker, timestamp)"
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

    def record_shadow_setup(
        self,
        result: ScoreResult,
        *,
        scan_id: int,
        setup_inputs: dict[str, Any],
        provider_snapshot: dict[str, Any],
        selected_contract: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        status: str = "OPEN",
        timestamp: datetime | None = None,
    ) -> int:
        stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO options_shadow_journal (
                    timestamp, scan_id, ticker, direction, score, pattern, status,
                    setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    scan_id,
                    result.ticker,
                    result.direction,
                    result.score,
                    result.pattern,
                    status,
                    json.dumps(setup_inputs, sort_keys=True, default=str),
                    json.dumps(provider_snapshot, sort_keys=True, default=str),
                    json.dumps(selected_contract or {}, sort_keys=True, default=str),
                    json.dumps(outcome or {}, sort_keys=True, default=str),
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
                       alert_sent, alert_suppression_reason, components_json, raw_json
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
                components=json.loads(row["components_json"] or "{}"),
                raw=json.loads(row["raw_json"] or "{}"),
            )
            for row in rows
        ]

    def latest_shadow_setups(
        self,
        limit: int = 25,
        *,
        ticker: str | None = None,
        status: str | None = None,
    ) -> list[StoredShadowSetup]:
        where = []
        params: list[Any] = []
        if ticker:
            where.append("ticker = ?")
            params.append(ticker.upper())
        if status:
            where.append("status = ?")
            params.append(status.upper())
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, timestamp, scan_id, ticker, direction, score, pattern, status,
                       setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json
                FROM options_shadow_journal {where_sql} ORDER BY id DESC LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        return [
            StoredShadowSetup(
                id=row["id"],
                timestamp=row["timestamp"],
                scan_id=row["scan_id"],
                ticker=row["ticker"],
                direction=row["direction"],
                score=row["score"],
                pattern=row["pattern"],
                status=row["status"],
                setup_inputs=json.loads(row["setup_inputs_json"] or "{}"),
                provider_snapshot=json.loads(row["provider_snapshot_json"] or "{}"),
                selected_contract=json.loads(row["selected_contract_json"] or "{}"),
                outcome=json.loads(row["outcome_json"] or "{}"),
            )
            for row in rows
        ]

    def latest_rh_option_setups(self, limit: int = 10) -> list[StoredShadowSetup]:
        bounded_limit = max(1, min(limit, 100))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.id, j.timestamp, j.scan_id, j.ticker, j.direction, j.score,
                       j.pattern, j.status, j.setup_inputs_json, j.provider_snapshot_json,
                       j.selected_contract_json, j.outcome_json
                FROM options_shadow_journal j
                JOIN scans s ON s.id = j.scan_id
                WHERE s.source = 'rh_options'
                ORDER BY j.id DESC
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [
            StoredShadowSetup(
                id=row["id"],
                timestamp=row["timestamp"],
                scan_id=row["scan_id"],
                ticker=row["ticker"],
                direction=row["direction"],
                score=row["score"],
                pattern=row["pattern"],
                status=row["status"],
                setup_inputs=json.loads(row["setup_inputs_json"] or "{}"),
                provider_snapshot=json.loads(row["provider_snapshot_json"] or "{}"),
                selected_contract=json.loads(row["selected_contract_json"] or "{}"),
                outcome=json.loads(row["outcome_json"] or "{}"),
            )
            for row in rows
        ]

    def get_shadow_setup(self, shadow_id: int) -> StoredShadowSetup | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, timestamp, scan_id, ticker, direction, score, pattern, status,
                       setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json
                FROM options_shadow_journal WHERE id = ?
                """,
                (shadow_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredShadowSetup(
            id=row["id"],
            timestamp=row["timestamp"],
            scan_id=row["scan_id"],
            ticker=row["ticker"],
            direction=row["direction"],
            score=row["score"],
            pattern=row["pattern"],
            status=row["status"],
            setup_inputs=json.loads(row["setup_inputs_json"] or "{}"),
            provider_snapshot=json.loads(row["provider_snapshot_json"] or "{}"),
            selected_contract=json.loads(row["selected_contract_json"] or "{}"),
            outcome=json.loads(row["outcome_json"] or "{}"),
        )

    def update_shadow_outcome(
        self,
        shadow_id: int,
        *,
        status: str,
        outcome: dict[str, Any],
    ) -> StoredShadowSetup | None:
        existing = self.get_shadow_setup(shadow_id)
        if existing is None:
            return None
        merged_outcome = dict(existing.outcome)
        merged_outcome.update(outcome)
        merged_outcome = _derive_shadow_outcome_math(existing, merged_outcome)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE options_shadow_journal
                SET status = ?, outcome_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(merged_outcome, sort_keys=True, default=str),
                    shadow_id,
                ),
            )
        return self.get_shadow_setup(shadow_id)

    def shadow_summary(
        self,
        *,
        ticker: str | None = None,
    ) -> ShadowJournalSummary:
        # Aggregate in SQL: the journal can hold thousands of rows and this is
        # polled by the dashboard, so it must never materialise every row's
        # JSON blobs in memory (that was the scanner's largest allocation).
        where = "WHERE ticker = ?" if ticker else ""
        params: tuple[Any, ...] = (ticker.upper(),) if ticker else ()
        status_counts: dict[str, int] = {}
        pnl_dollars = 0.0
        pnl_percent_values = []
        with self._connect() as conn:
            for row in conn.execute(
                f"SELECT status, COUNT(*) AS n FROM options_shadow_journal {where} GROUP BY status",
                params,
            ):
                status_counts[row["status"]] = int(row["n"])
            resolved_where = (
                f"{where} AND status != 'OPEN'" if where else "WHERE status != 'OPEN'"
            )
            for row in conn.execute(
                f"SELECT outcome_json FROM options_shadow_journal {resolved_where}",
                params,
            ):
                outcome = json.loads(row["outcome_json"] or "{}")
                dollars = _first_number(outcome, ("pnl_dollars",))
                percent = _first_number(outcome, ("pnl_percent",))
                if dollars is not None:
                    pnl_dollars += dollars
                if percent is not None:
                    pnl_percent_values.append(percent)

        wins = status_counts.get("WIN", 0)
        losses = status_counts.get("LOSS", 0)
        breakeven = status_counts.get("BREAKEVEN", 0)
        closed = wins + losses + breakeven + status_counts.get("EXPIRED", 0)
        decided = wins + losses
        win_rate = round((wins / decided) * 100.0, 2) if decided else None
        average_percent = (
            round(sum(pnl_percent_values) / len(pnl_percent_values), 2)
            if pnl_percent_values
            else None
        )
        return ShadowJournalSummary(
            total=sum(status_counts.values()),
            open=status_counts.get("OPEN", 0),
            closed=closed,
            wins=wins,
            losses=losses,
            breakeven=breakeven,
            cancelled=status_counts.get("CANCELLED", 0),
            expired=status_counts.get("EXPIRED", 0),
            win_rate_percent=win_rate,
            total_pnl_dollars=round(pnl_dollars, 2),
            average_pnl_percent=average_percent,
            rejected=status_counts.get("REJECTED", 0),
        )

    def find_open_duplicate(self, ticker: str, contract_key: str) -> int | None:
        if not contract_key:
            return None
        needle = json.dumps({"contract_key": contract_key}, sort_keys=True)[1:-1]
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM options_shadow_journal
                WHERE ticker = ? AND status = 'OPEN' AND selected_contract_json LIKE ?
                ORDER BY id DESC LIMIT 1
                """,
                (ticker.upper(), f"%{needle}%"),
            ).fetchone()
        return int(row["id"]) if row else None

    def open_setups_after(self, after_id: int, limit: int = 500) -> list[StoredShadowSetup]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, scan_id, ticker, direction, score, pattern, status,
                       setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json
                FROM options_shadow_journal
                WHERE status = 'OPEN' AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
        return [
            StoredShadowSetup(
                id=row["id"],
                timestamp=row["timestamp"],
                scan_id=row["scan_id"],
                ticker=row["ticker"],
                direction=row["direction"],
                score=row["score"],
                pattern=row["pattern"],
                status=row["status"],
                setup_inputs=json.loads(row["setup_inputs_json"] or "{}"),
                provider_snapshot=json.loads(row["provider_snapshot_json"] or "{}"),
                selected_contract=json.loads(row["selected_contract_json"] or "{}"),
                outcome=json.loads(row["outcome_json"] or "{}"),
            )
            for row in rows
        ]

    def reconcile_open_non_candidates(self, classify, now: datetime | None = None) -> int:
        """Reclassify legacy OPEN rows that never met candidate requirements.

        Non-destructive: the row and its recorded inputs are preserved verbatim;
        only status flips to REJECTED and the reconciliation note is appended to
        the outcome. Idempotent — already-reconciled rows are no longer OPEN.
        """
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        reconciled = 0
        last_id = 0
        while True:
            batch = self.open_setups_after(last_id)
            if not batch:
                break
            for setup in batch:
                last_id = setup.id
                classification = classify({**setup.setup_inputs, "direction": setup.direction})
                if classification.is_open_eligible:
                    continue
                self.update_shadow_outcome(
                    setup.id,
                    status="REJECTED",
                    outcome={
                        "closed_reason": "reconciled_non_candidate",
                        "reconciled_at": stamp,
                        "missing_fields": list(classification.missing),
                    },
                )
                reconciled += 1
        return reconciled

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _derive_shadow_outcome_math(
    setup: StoredShadowSetup,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    entry_mark = _first_number(
        outcome,
        ("entry_mark", "entry_premium", "entry_price"),
        fallback=_first_number(setup.setup_inputs, ("option_mark", "mark", "premium")),
    )
    exit_mark = _first_number(outcome, ("exit_mark", "exit_premium", "exit_price"))
    quantity = _first_number(outcome, ("quantity", "contracts"), fallback=1.0)
    if entry_mark is None or exit_mark is None or entry_mark <= 0:
        return outcome
    enriched = dict(outcome)
    enriched.setdefault("entry_mark", entry_mark)
    enriched.setdefault("exit_mark", exit_mark)
    enriched.setdefault("quantity", int(quantity or 1))
    enriched["pnl_percent"] = round(((exit_mark - entry_mark) / entry_mark) * 100.0, 2)
    enriched["pnl_dollars"] = round((exit_mark - entry_mark) * 100.0 * float(quantity or 1), 2)
    return enriched


def _first_number(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    *,
    fallback: float | None = None,
) -> float | None:
    for key in keys:
        value = payload.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return fallback
