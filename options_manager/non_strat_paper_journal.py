"""Append-only journal for the isolated non-Strat options paper track.

This journal stores only research-track facts. It never updates or deletes a
row, never stores broker credentials/account ids, and never becomes strategy
authority. The ns-v0.1 observer remains evidence; this journal records whether
an explicitly-geometrized candidate reached internal paper readiness and, when
supplied later, the deterministic internal paper round-trip result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

from .non_strat_paper_track import (
    TRACK_ID,
    TRACK_VERSION,
    NonStratPaperPreparation,
    NonStratPaperRoundTrip,
)


@dataclass(frozen=True)
class JournalWriteResult:
    status: Literal["RECORDED", "DUPLICATE", "REJECTED"]
    reason: str = ""
    row_id: int | None = None
    ticket_id: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS non_strat_paper_preparations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    track_version TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    symbol TEXT NOT NULL,
    family TEXT NOT NULL,
    direction TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    event_bar_start TEXT NOT NULL,
    event_bar_close TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    event_trigger_price REAL NOT NULL,
    decision_underlying_price REAL NOT NULL,
    decision_quote_timestamp TEXT NOT NULL,
    underlying_invalidation REAL NOT NULL,
    underlying_target REAL NOT NULL,
    geometry_rule_id TEXT NOT NULL,
    source_references_json TEXT NOT NULL,
    contract_symbol TEXT,
    contract_strike REAL NOT NULL,
    contract_expiry TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_bid REAL,
    entry_ask REAL,
    provider TEXT NOT NULL,
    local_preview_ready INTEGER NOT NULL,
    webull_submit_allowed INTEGER NOT NULL,
    webull_block_reason TEXT NOT NULL,
    row_json TEXT NOT NULL,
    UNIQUE(track_version, ticket_id)
);
CREATE TABLE IF NOT EXISTS non_strat_paper_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    track_version TEXT NOT NULL,
    ticket_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    simulated_entry_price REAL,
    simulated_exit_price REAL,
    simulated_contracts INTEGER NOT NULL,
    simulated_gross_pnl REAL,
    simulated_fees REAL NOT NULL,
    simulated_net_pnl REAL,
    row_json TEXT NOT NULL,
    UNIQUE(track_version, ticket_id),
    FOREIGN KEY(track_version, ticket_id)
        REFERENCES non_strat_paper_preparations(track_version, ticket_id)
);
"""


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


class NonStratPaperJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        return conn

    def record_preparation(
        self, preparation: NonStratPaperPreparation
    ) -> JournalWriteResult:
        if preparation.track_id != TRACK_ID or preparation.track_version != TRACK_VERSION:
            return JournalWriteResult(
                status="REJECTED",
                reason="track_identity_mismatch",
                ticket_id=preparation.ticket_id,
            )
        if not preparation.ticket_id:
            return JournalWriteResult(
                status="REJECTED",
                reason="ticket_id_missing",
                ticket_id=None,
            )

        plan = preparation.plan
        event = plan.event
        snapshot = plan.entry_snapshot
        preview_ready = bool(
            preparation.preview_result is not None
            and preparation.preview_result.preview_ready
        )
        row = {
            "track_id": preparation.track_id,
            "track_version": preparation.track_version,
            "ticket_id": preparation.ticket_id,
            "symbol": event.symbol,
            "family": event.family,
            "direction": event.direction,
            "episode_id": event.episode_id,
            "event_bar_start": event.bar_start,
            "event_bar_close": event.bar_close,
            "status": preparation.status,
            "reason": preparation.reason,
            "event_trigger_price": event.trigger_price,
            "decision_underlying_price": snapshot.underlying_price,
            "decision_quote_timestamp": (
                snapshot.quote_timestamp.isoformat()
                if snapshot.quote_timestamp is not None
                else ""
            ),
            "underlying_invalidation": plan.underlying_invalidation,
            "underlying_target": plan.underlying_target,
            "geometry_rule_id": plan.geometry_rule_id,
            "source_references": list(plan.source_references),
            "contract_symbol": snapshot.contract_symbol,
            "contract_strike": plan.contract_strike,
            "contract_expiry": plan.contract_expiry.isoformat(),
            "quantity": plan.quantity,
            "entry_bid": snapshot.bid,
            "entry_ask": snapshot.ask,
            "provider": snapshot.provider,
            "local_preview_ready": preview_ready,
            "webull_submit_allowed": preparation.webull_submit_allowed,
            "webull_block_reason": preparation.webull_block_reason,
        }
        raw = json.dumps(row, sort_keys=True, default=_json_default)

        conn = self._connect()
        try:
            try:
                cur = conn.execute(
                    """INSERT INTO non_strat_paper_preparations (
                        track_id, track_version, ticket_id, symbol, family,
                        direction, episode_id, event_bar_start, event_bar_close,
                        status, reason, event_trigger_price,
                        decision_underlying_price, decision_quote_timestamp,
                        underlying_invalidation, underlying_target,
                        geometry_rule_id, source_references_json,
                        contract_symbol, contract_strike, contract_expiry,
                        quantity, entry_bid, entry_ask, provider,
                        local_preview_ready, webull_submit_allowed,
                        webull_block_reason, row_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["track_id"],
                        row["track_version"],
                        row["ticket_id"],
                        row["symbol"],
                        row["family"],
                        row["direction"],
                        row["episode_id"],
                        row["event_bar_start"],
                        row["event_bar_close"],
                        row["status"],
                        row["reason"],
                        row["event_trigger_price"],
                        row["decision_underlying_price"],
                        row["decision_quote_timestamp"],
                        row["underlying_invalidation"],
                        row["underlying_target"],
                        row["geometry_rule_id"],
                        json.dumps(row["source_references"], sort_keys=True),
                        row["contract_symbol"],
                        row["contract_strike"],
                        row["contract_expiry"],
                        row["quantity"],
                        row["entry_bid"],
                        row["entry_ask"],
                        row["provider"],
                        1 if row["local_preview_ready"] else 0,
                        1 if row["webull_submit_allowed"] else 0,
                        row["webull_block_reason"],
                        raw,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return JournalWriteResult(
                    status="DUPLICATE",
                    reason="ticket_already_recorded",
                    ticket_id=preparation.ticket_id,
                )
            return JournalWriteResult(
                status="RECORDED",
                row_id=int(cur.lastrowid),
                ticket_id=preparation.ticket_id,
            )
        finally:
            conn.close()

    def record_round_trip(
        self, round_trip: NonStratPaperRoundTrip
    ) -> JournalWriteResult:
        if round_trip.track_id != TRACK_ID or round_trip.track_version != TRACK_VERSION:
            return JournalWriteResult(
                status="REJECTED",
                reason="track_identity_mismatch",
                ticket_id=round_trip.ticket_id,
            )
        if not round_trip.ticket_id:
            return JournalWriteResult(
                status="REJECTED", reason="ticket_id_missing", ticket_id=None
            )

        result = round_trip.result
        row = {
            "track_id": round_trip.track_id,
            "track_version": round_trip.track_version,
            "ticket_id": round_trip.ticket_id,
            "status": round_trip.status,
            "reason": round_trip.reason,
            "simulated_entry_price": (
                result.simulated_entry_price if result is not None else None
            ),
            "simulated_exit_price": (
                result.simulated_exit_price if result is not None else None
            ),
            "simulated_contracts": (
                result.simulated_contracts if result is not None else 0
            ),
            "simulated_gross_pnl": (
                result.simulated_gross_pnl if result is not None else None
            ),
            "simulated_fees": result.simulated_fees if result is not None else 0.0,
            "simulated_net_pnl": (
                result.simulated_net_pnl if result is not None else None
            ),
        }
        raw = json.dumps(row, sort_keys=True, default=_json_default)

        conn = self._connect()
        try:
            exists = conn.execute(
                """SELECT 1 FROM non_strat_paper_preparations
                   WHERE track_version=? AND ticket_id=?""",
                (TRACK_VERSION, round_trip.ticket_id),
            ).fetchone()
            if exists is None:
                return JournalWriteResult(
                    status="REJECTED",
                    reason="preparation_not_recorded",
                    ticket_id=round_trip.ticket_id,
                )
            try:
                cur = conn.execute(
                    """INSERT INTO non_strat_paper_results (
                        track_id, track_version, ticket_id, status, reason,
                        simulated_entry_price, simulated_exit_price,
                        simulated_contracts, simulated_gross_pnl, simulated_fees,
                        simulated_net_pnl, row_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["track_id"],
                        row["track_version"],
                        row["ticket_id"],
                        row["status"],
                        row["reason"],
                        row["simulated_entry_price"],
                        row["simulated_exit_price"],
                        row["simulated_contracts"],
                        row["simulated_gross_pnl"],
                        row["simulated_fees"],
                        row["simulated_net_pnl"],
                        raw,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                return JournalWriteResult(
                    status="DUPLICATE",
                    reason="round_trip_already_recorded",
                    ticket_id=round_trip.ticket_id,
                )
            return JournalWriteResult(
                status="RECORDED",
                row_id=int(cur.lastrowid),
                ticket_id=round_trip.ticket_id,
            )
        finally:
            conn.close()


__all__ = ["JournalWriteResult", "NonStratPaperJournal"]