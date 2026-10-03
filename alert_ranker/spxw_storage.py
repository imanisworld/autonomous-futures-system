"""Isolated SPXW paper journal — never mixed with equity OPTIONS_PAPER_V1 rows."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alert_ranker.paper_spxw_v1 import (
    COHORT_0DTE,
    COHORT_1_PLUS,
    CONTRACT_MULTIPLIER,
    POLICY_ID,
)


CLOSED = frozenset({"WIN", "LOSS", "BREAKEVEN", "EXPIRED"})


@dataclass(frozen=True)
class SpxwJournalRow:
    id: int
    timestamp: str
    signal_underlying: str
    contract_root: str
    direction: str
    pattern: str
    status: str
    dte_cohort: str
    dte: int | None
    setup_type: str
    rejection_reason: str
    selected_contract: dict[str, Any]
    outcome: dict[str, Any]
    setup_inputs: dict[str, Any]


class SpxwStorage:
    """SQLite journal dedicated to OPTIONS_PAPER_SPXW_V1 evidence."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS options_spxw_shadow_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    signal_underlying TEXT NOT NULL,
                    contract_root TEXT NOT NULL,
                    paper_policy_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    setup_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dte_cohort TEXT NOT NULL,
                    dte INTEGER,
                    rejection_reason TEXT NOT NULL,
                    setup_inputs_json TEXT NOT NULL,
                    selected_contract_json TEXT NOT NULL,
                    outcome_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_spxw_journal_cohort "
                "ON options_spxw_shadow_journal (dte_cohort, status, timestamp)"
            )

    def open_planned_risk(self) -> float:
        total = 0.0
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT selected_contract_json FROM options_spxw_shadow_journal WHERE status = 'OPEN'"
            ):
                contract = json.loads(row["selected_contract_json"] or "{}")
                try:
                    total += float(contract.get("planned_risk_dollars") or 0.0)
                except (TypeError, ValueError):
                    continue
        return round(total, 2)

    def find_open_duplicate(self, episode_key: str) -> int | None:
        """Return an OPEN row id already carrying this episode_key, if any."""
        if not episode_key:
            return None
        needle = json.dumps({"episode_key": episode_key}, sort_keys=True)[1:-1]
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM options_spxw_shadow_journal
                WHERE status = 'OPEN' AND selected_contract_json LIKE ?
                ORDER BY id DESC LIMIT 1
                """,
                (f"%{needle}%",),
            ).fetchone()
        return int(row["id"]) if row else None

    def find_episode_duplicate(self, episode_key: str) -> int | None:
        """Return any journalled row for this episode (open or closed).

        Matches equity V1: one episode is one row whether still OPEN or resolved,
        so the 5-minute scheduler cannot reopen the same SPX trigger repeatedly.
        """
        if not episode_key:
            return None
        needle = json.dumps({"episode_key": episode_key}, sort_keys=True)[1:-1]
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM options_spxw_shadow_journal
                WHERE selected_contract_json LIKE ?
                ORDER BY id DESC LIMIT 1
                """,
                (f"%{needle}%",),
            ).fetchone()
        return int(row["id"]) if row else None

    def open_rows_after(self, after_id: int, *, limit: int = 500) -> list[SpxwJournalRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_spxw_shadow_journal
                WHERE status = 'OPEN' AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (after_id, max(1, int(limit))),
            ).fetchall()
        return [self._row(item) for item in rows]

    def update_outcome(
        self,
        row_id: int,
        *,
        status: str,
        outcome: dict[str, Any],
    ) -> SpxwJournalRow | None:
        existing = self.get(row_id)
        if existing is None:
            return None
        merged = dict(existing.outcome)
        merged.update(outcome)
        merged = _derive_spxw_outcome_math(existing, merged)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE options_spxw_shadow_journal
                SET status = ?, outcome_json = ?
                WHERE id = ?
                """,
                (status, json.dumps(merged, sort_keys=True, default=str), row_id),
            )
        return self.get(row_id)

    def get(self, row_id: int) -> SpxwJournalRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM options_spxw_shadow_journal WHERE id = ?",
                (int(row_id),),
            ).fetchone()
        return None if row is None else self._row(row)

    def record(
        self,
        *,
        direction: str,
        pattern: str,
        setup_type: str,
        status: str,
        dte_cohort: str,
        dte: int | None,
        rejection_reason: str = "",
        setup_inputs: dict[str, Any] | None = None,
        selected_contract: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> int:
        stamp = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        contract = dict(selected_contract or {})
        contract.setdefault("paper_policy_id", POLICY_ID)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO options_spxw_shadow_journal (
                    timestamp, signal_underlying, contract_root, paper_policy_id,
                    direction, pattern, setup_type, status, dte_cohort, dte,
                    rejection_reason, setup_inputs_json, selected_contract_json, outcome_json
                ) VALUES (?, 'SPX', 'SPXW', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stamp,
                    POLICY_ID,
                    direction,
                    pattern,
                    setup_type,
                    status,
                    dte_cohort,
                    dte,
                    rejection_reason or "",
                    json.dumps(setup_inputs or {}, sort_keys=True, default=str),
                    json.dumps(contract, sort_keys=True, default=str),
                    json.dumps(outcome or {}, sort_keys=True, default=str),
                ),
            )
            return int(cursor.lastrowid)

    def latest(self, *, limit: int = 50) -> list[SpxwJournalRow]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM options_spxw_shadow_journal
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row(item) for item in rows]

    def all_rows(self) -> list[SpxwJournalRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM options_spxw_shadow_journal ORDER BY id ASC"
            ).fetchall()
        return [self._row(item) for item in rows]

    @staticmethod
    def _row(item: sqlite3.Row) -> SpxwJournalRow:
        return SpxwJournalRow(
            id=int(item["id"]),
            timestamp=str(item["timestamp"]),
            signal_underlying=str(item["signal_underlying"]),
            contract_root=str(item["contract_root"]),
            direction=str(item["direction"]),
            pattern=str(item["pattern"]),
            status=str(item["status"]),
            dte_cohort=str(item["dte_cohort"]),
            dte=item["dte"],
            setup_type=str(item["setup_type"]),
            rejection_reason=str(item["rejection_reason"] or ""),
            selected_contract=json.loads(item["selected_contract_json"] or "{}"),
            outcome=json.loads(item["outcome_json"] or "{}"),
            setup_inputs=json.loads(item["setup_inputs_json"] or "{}"),
        )


def _derive_spxw_outcome_math(
    row: SpxwJournalRow,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """ASK entry / BID exit P&L using the SPXW contract multiplier (×100)."""
    entry = _num(
        outcome.get("entry_mark")
        or outcome.get("entry_premium")
        or row.selected_contract.get("option_mark")
        or row.selected_contract.get("entry_quote")
        or row.selected_contract.get("option_ask")
    )
    exit_mark = _num(
        outcome.get("exit_mark")
        or outcome.get("exit_premium")
        or outcome.get("option_bid_at_resolution")
    )
    if entry is None or exit_mark is None or entry <= 0:
        return outcome
    multiplier = _num(row.selected_contract.get("contract_multiplier")) or float(
        CONTRACT_MULTIPLIER
    )
    contracts = _num(outcome.get("contracts") or row.selected_contract.get("contracts")) or 1.0
    enriched = dict(outcome)
    enriched.setdefault("entry_mark", entry)
    enriched.setdefault("exit_mark", exit_mark)
    enriched.setdefault("contracts", int(contracts))
    enriched.setdefault("contract_multiplier", int(multiplier))
    enriched.setdefault("averaging_down", False)
    enriched["pnl_percent"] = round(((exit_mark - entry) / entry) * 100.0, 2)
    enriched["pnl_dollars"] = round((exit_mark - entry) * multiplier * contracts, 2)
    return enriched


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _pnl(row: SpxwJournalRow) -> float | None:
    try:
        value = row.outcome.get("pnl_dollars")
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def cohort_metrics(rows: list[SpxwJournalRow], cohort: str) -> dict[str, Any]:
    closed = [r for r in rows if r.dte_cohort == cohort and r.status in CLOSED]
    pnls = [p for p in (_pnl(r) for r in closed) if p is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    spread_costs = []
    for r in closed:
        try:
            spread_costs.append(float(r.selected_contract.get("spread_cost_dollars") or 0.0))
        except (TypeError, ValueError):
            continue
    return {
        "cohort": cohort,
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "pnl_dollars": round(sum(pnls), 2) if pnls else 0.0,
        "avg_winner": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loser": round(sum(losses) / len(losses), 2) if losses else None,
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None,
        "max_drawdown": round(max_dd, 2),
        "avg_spread_cost": round(sum(spread_costs) / len(spread_costs), 2) if spread_costs else None,
    }


def build_spxw_rollup(rows: list[SpxwJournalRow]) -> dict[str, Any]:
    """SPXW-only expectancy surface — never fold into equity 66-symbol cohort."""
    rejections: dict[str, int] = {}
    setups: dict[str, int] = {}
    for row in rows:
        if row.rejection_reason:
            rejections[row.rejection_reason] = rejections.get(row.rejection_reason, 0) + 1
        if row.setup_type:
            setups[row.setup_type] = setups.get(row.setup_type, 0) + 1
    open_n = sum(1 for r in rows if r.status == "OPEN")
    return {
        "lane": "SPX_SPXW_PAPER",
        "paper_policy_id": POLICY_ID,
        "mixed_with_equity_universe": False,
        "open": open_n,
        "by_cohort": {
            COHORT_0DTE: cohort_metrics(rows, COHORT_0DTE),
            COHORT_1_PLUS: cohort_metrics(rows, COHORT_1_PLUS),
        },
        "setup_type_counts": dict(sorted(setups.items())),
        "rejection_reason_counts": dict(sorted(rejections.items())),
        "rows": len(rows),
    }
