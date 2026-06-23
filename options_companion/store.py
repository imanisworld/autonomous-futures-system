"""SQLite ledger for the companion options paper lane.

Separate DB (``logs/options_companion.sqlite``) from the futures journal and from
the advisory scanner DB. One table, frozen-dataclass read schema, JSON-free columns.
Mirrors the ``alert_ranker.storage.ScanStorage`` conventions (``with self._connect()``,
``sqlite3.Row`` factory, indexes).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from risk.options_risk_engine import OptionsDailyState

# Statuses that count as a "real" (formed + opened) companion trade for the
# per-underlying daily/open limits. REJECTED rows are audit-only and never counted.
_COUNTED_STATUSES = ("OPEN", "WIN", "LOSS", "EXPIRED")
_RESOLVED_LOSS = "LOSS"


@dataclass(frozen=True)
class CompanionRow:
    id: int
    created_at: str
    resolved_at: Optional[str]
    futures_instrument: str
    futures_direction: str
    futures_timestamp: Optional[str]
    underlying: str
    option_symbol: Optional[str]
    contract_type: Optional[str]
    expiry: Optional[str]
    strike: Optional[float]
    dte: Optional[int]
    entry_mark: Optional[float]
    stop_mark: Optional[float]
    target_mark: Optional[float]
    signa_grade: Optional[str]
    signa_score: Optional[float]
    signa_daily_direction: Optional[str]
    risk_result: Optional[str]
    risk_failed_rule: Optional[str]
    status: str
    paper_pnl_dollars: Optional[float]
    paper_pnl_percent: Optional[float]


_COLUMNS = (
    "created_at, resolved_at, futures_instrument, futures_direction, futures_timestamp, "
    "underlying, option_symbol, contract_type, expiry, strike, dte, "
    "entry_mark, stop_mark, target_mark, "
    "signa_grade, signa_score, signa_daily_direction, "
    "risk_result, risk_failed_rule, status, paper_pnl_dollars, paper_pnl_percent"
)


class OptionsCompanionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS options_companion (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    futures_instrument TEXT NOT NULL,
                    futures_direction TEXT NOT NULL,
                    futures_timestamp TEXT,
                    underlying TEXT NOT NULL,
                    option_symbol TEXT,
                    contract_type TEXT,
                    expiry TEXT,
                    strike REAL,
                    dte INTEGER,
                    entry_mark REAL,
                    stop_mark REAL,
                    target_mark REAL,
                    signa_grade TEXT,
                    signa_score REAL,
                    signa_daily_direction TEXT,
                    risk_result TEXT,
                    risk_failed_rule TEXT,
                    status TEXT NOT NULL,
                    paper_pnl_dollars REAL,
                    paper_pnl_percent REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_options_companion_key "
                "ON options_companion (underlying, status, created_at)"
            )

    def record(
        self,
        *,
        futures_instrument: str,
        futures_direction: str,
        underlying: str,
        status: str,
        futures_timestamp: Optional[str] = None,
        option_symbol: Optional[str] = None,
        contract_type: Optional[str] = None,
        expiry: Optional[str] = None,
        strike: Optional[float] = None,
        dte: Optional[int] = None,
        entry_mark: Optional[float] = None,
        stop_mark: Optional[float] = None,
        target_mark: Optional[float] = None,
        signa_grade: Optional[str] = None,
        signa_score: Optional[float] = None,
        signa_daily_direction: Optional[str] = None,
        risk_result: Optional[str] = None,
        risk_failed_rule: Optional[str] = None,
        paper_pnl_dollars: Optional[float] = None,
        paper_pnl_percent: Optional[float] = None,
        created_at: Optional[datetime] = None,
    ) -> int:
        stamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO options_companion ({_COLUMNS}) "
                f"VALUES ({', '.join('?' * 22)})",
                (
                    stamp,
                    None,
                    futures_instrument,
                    futures_direction,
                    futures_timestamp,
                    underlying.upper(),
                    option_symbol,
                    contract_type,
                    expiry,
                    strike,
                    dte,
                    entry_mark,
                    stop_mark,
                    target_mark,
                    signa_grade,
                    signa_score,
                    signa_daily_direction,
                    risk_result,
                    risk_failed_rule,
                    status,
                    paper_pnl_dollars,
                    paper_pnl_percent,
                ),
            )
            return int(cursor.lastrowid)

    def open_positions(self) -> list[CompanionRow]:
        return self._query("WHERE status = 'OPEN' ORDER BY id ASC")

    def all_rows(self) -> list[CompanionRow]:
        return self._query("ORDER BY id ASC")

    def resolve(
        self,
        row_id: int,
        *,
        status: str,
        paper_pnl_dollars: Optional[float],
        paper_pnl_percent: Optional[float],
        resolved_at: Optional[datetime] = None,
    ) -> None:
        stamp = (resolved_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE options_companion
                SET status = ?, paper_pnl_dollars = ?, paper_pnl_percent = ?, resolved_at = ?
                WHERE id = ?
                """,
                (status, paper_pnl_dollars, paper_pnl_percent, stamp, row_id),
            )

    def daily_state(self, underlying: str, day: object) -> OptionsDailyState:
        """Per-underlying ``OptionsDailyState`` for the given UTC date.

        ``day`` is a ``datetime.date``. Counts only formed+opened rows (REJECTED
        rows are audit-only). ``open_positions`` spans all days (an open paper
        position carries until resolved), so a still-OPEN row blocks a duplicate.
        """
        rows = self._query(
            "WHERE underlying = ? ORDER BY id ASC", (underlying.upper(),)
        )
        day_iso = day.isoformat()
        trade_count = 0
        realized = 0.0
        open_count = 0
        consecutive_losses = 0
        for row in rows:
            if row.status == "OPEN":
                open_count += 1
            same_day = (row.created_at or "")[:10] == day_iso
            if same_day and row.status in _COUNTED_STATUSES:
                trade_count += 1
            if same_day and row.status in {"WIN", "LOSS", "EXPIRED"}:
                realized += row.paper_pnl_dollars or 0.0
        # consecutive losses among the most recent resolved rows for this underlying
        for row in reversed(rows):
            if row.status in {"WIN", "EXPIRED"}:
                break
            if row.status == _RESOLVED_LOSS:
                consecutive_losses += 1
        return OptionsDailyState(
            trade_count=trade_count,
            consecutive_losses=consecutive_losses,
            has_open_position=open_count > 0,
            open_positions=open_count,
            realized_pnl_dollars=round(realized, 2),
        )

    def _query(self, where_order: str, params: tuple = ()) -> list[CompanionRow]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, {_COLUMNS} FROM options_companion {where_order}", params
            ).fetchall()
        return [_row_from(r) for r in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _row_from(r: sqlite3.Row) -> CompanionRow:
    return CompanionRow(
        id=r["id"],
        created_at=r["created_at"],
        resolved_at=r["resolved_at"],
        futures_instrument=r["futures_instrument"],
        futures_direction=r["futures_direction"],
        futures_timestamp=r["futures_timestamp"],
        underlying=r["underlying"],
        option_symbol=r["option_symbol"],
        contract_type=r["contract_type"],
        expiry=r["expiry"],
        strike=r["strike"],
        dte=r["dte"],
        entry_mark=r["entry_mark"],
        stop_mark=r["stop_mark"],
        target_mark=r["target_mark"],
        signa_grade=r["signa_grade"],
        signa_score=r["signa_score"],
        signa_daily_direction=r["signa_daily_direction"],
        risk_result=r["risk_result"],
        risk_failed_rule=r["risk_failed_rule"],
        status=r["status"],
        paper_pnl_dollars=r["paper_pnl_dollars"],
        paper_pnl_percent=r["paper_pnl_percent"],
    )
