"""Append-only option-contract mark storage for Options Paper Test V1.

Kept separate from ScanStorage's legacy schema so the first V1 evidence
population does not rewrite or reinterpret older shadow rows.  All operations
use the existing SQLite connection context and are advisory/paper-only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def ensure_schema(storage) -> None:
    with storage._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS options_contract_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shadow_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                option_symbol TEXT NOT NULL,
                bid REAL,
                ask REAL,
                mid REAL,
                volume REAL,
                open_interest REAL,
                delta REAL,
                gamma REAL,
                theta REAL,
                implied_volatility REAL,
                quote_timestamp TEXT,
                error TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_options_contract_marks_shadow "
            "ON options_contract_marks (shadow_id, timestamp)"
        )


def record_contract_mark(
    storage,
    *,
    shadow_id: int,
    option_symbol: str,
    timestamp: datetime,
    bid: float | None = None,
    ask: float | None = None,
    mid: float | None = None,
    volume: float | None = None,
    open_interest: float | None = None,
    delta: float | None = None,
    gamma: float | None = None,
    theta: float | None = None,
    implied_volatility: float | None = None,
    quote_timestamp: str | None = None,
    error: str = "",
    raw: dict[str, Any] | None = None,
) -> int:
    ensure_schema(storage)
    stamp = timestamp.astimezone(timezone.utc).isoformat()
    with storage._connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO options_contract_marks (
                shadow_id, timestamp, option_symbol, bid, ask, mid, volume,
                open_interest, delta, gamma, theta, implied_volatility,
                quote_timestamp, error, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(shadow_id),
                stamp,
                option_symbol,
                bid,
                ask,
                mid,
                volume,
                open_interest,
                delta,
                gamma,
                theta,
                implied_volatility,
                quote_timestamp,
                error,
                json.dumps(raw or {}, sort_keys=True, default=str),
            ),
        )
        return int(cursor.lastrowid)


def contract_marks(storage, shadow_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
    ensure_schema(storage)
    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, shadow_id, timestamp, option_symbol, bid, ask, mid,
                   volume, open_interest, delta, gamma, theta,
                   implied_volatility, quote_timestamp, error, raw_json
            FROM options_contract_marks
            WHERE shadow_id = ? ORDER BY id ASC LIMIT ?
            """,
            (int(shadow_id), int(limit)),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "shadow_id": row["shadow_id"],
            "timestamp": row["timestamp"],
            "option_symbol": row["option_symbol"],
            "bid": row["bid"],
            "ask": row["ask"],
            "mid": row["mid"],
            "volume": row["volume"],
            "open_interest": row["open_interest"],
            "delta": row["delta"],
            "gamma": row["gamma"],
            "theta": row["theta"],
            "implied_volatility": row["implied_volatility"],
            "quote_timestamp": row["quote_timestamp"],
            "error": row["error"],
            "raw": json.loads(row["raw_json"] or "{}"),
        }
        for row in rows
    ]


def aggregate_open_planned_risk(storage) -> float:
    """Sum planned risk only for ACTIVE V1 OPEN rows.

    COUNTERFACTUAL observer rows deliberately model trades that filters rejected;
    they never reserve the active $1,000 budget. Missing/non-numeric risk on an
    active V1 OPEN row is still treated as infinite risk so damaged state cannot
    make the portfolio look safer than it is.
    """
    total = 0.0
    last_id = 0
    while True:
        batch = storage.open_setups_after(last_id)
        if not batch:
            break
        for setup in batch:
            last_id = setup.id
            contract = setup.selected_contract or {}
            if contract.get("paper_policy_id") != "OPTIONS_PAPER_V1":
                continue
            if (
                contract.get("paper_evidence_lane") == "COUNTERFACTUAL"
                or contract.get("risk_budget_consumed") is False
            ):
                continue
            try:
                risk = float(contract["planned_risk_dollars"])
            except (KeyError, TypeError, ValueError):
                return float("inf")
            if risk < 0:
                return float("inf")
            total += risk
    return round(total, 2)
