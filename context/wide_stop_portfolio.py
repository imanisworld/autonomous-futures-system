"""Shared portfolio accounting for the 4HR / 3-2-2 wide-stop evidence lanes.

The per-strategy ledgers stay separate for attribution, but admission limits are
portfolio-wide: at most two simulated open positions and at most three actual
fills per trading day across the family.  This module also mirrors resolved NET
P&L into one portfolio journal so combined drawdown can be measured against any
candidate bankroll ($1,500, $5,000, etc.) without changing trade selection.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Optional

from context import wide_stop_ledger_paper as contract
from journal.journal_logger import JournalLogger

MAX_OPEN_POSITIONS = 2
MAX_FILLS_PER_DAY = 3
PORTFOLIO_DIR = "portfolio"
STATE_FILENAME = "portfolio_state.json"
_PAPER_STATE = "forward_collector_state.json"
_DEMO_STATE = "demo_collector_state.json"


def portfolio_dir(log_dir: str | Path) -> Path:
    return Path(log_dir) / contract.JOURNAL_ROOT / PORTFOLIO_DIR


def state_path(log_dir: str | Path) -> Path:
    return portfolio_dir(log_dir) / STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {"filled_date": None, "filled_count": 0}


def load_state(log_dir: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(state_path(log_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    return {
        "filled_date": raw.get("filled_date"),
        "filled_count": max(0, int(raw.get("filled_count") or 0)),
    }


def save_state(log_dir: str | Path, state: dict[str, Any]) -> None:
    path = state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def reset_day(state: dict[str, Any], day: date) -> None:
    key = day.isoformat()
    if state.get("filled_date") != key:
        state["filled_date"] = key
        state["filled_count"] = 0


def filled_count(log_dir: str | Path, day: date) -> int:
    state = load_state(log_dir)
    reset_day(state, day)
    return int(state.get("filled_count") or 0)


def register_fill(log_dir: str | Path, day: date) -> int:
    """Count one CONFIRMED fill. No-fill/cancelled attempts never call this."""
    state = load_state(log_dir)
    reset_day(state, day)
    state["filled_count"] = int(state.get("filled_count") or 0) + 1
    save_state(log_dir, state)
    return int(state["filled_count"])


def _state_file(log_dir: str | Path, ledger: contract.Ledger, filename: str) -> dict[str, Any]:
    path = contract.journal_dir(log_dir, ledger) / filename
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def open_positions(log_dir: str | Path) -> list[dict[str, Any]]:
    """Open paper/demo positions. A stale open from either route blocks route-switching."""
    out: list[dict[str, Any]] = []
    for ledger in contract.LEDGERS.values():
        for filename, route in ((_PAPER_STATE, "paper_sim"), (_DEMO_STATE, "tradovate_demo")):
            position = _state_file(log_dir, ledger, filename).get("position")
            if isinstance(position, dict):
                row = dict(position)
                row["ledger"] = ledger.name
                row["execution_route"] = route
                out.append(row)
    return out


def same_instrument_open(log_dir: str | Path, instrument: str) -> bool:
    root = str(instrument or "").upper().replace("1!", "")
    return any(
        str(row.get("instrument") or "").upper().replace("1!", "") == root
        for row in open_positions(log_dir)
    )


def planned_risk_dollars(position: dict[str, Any]) -> float:
    """Static stop risk for one micro position, using its actual stored entry."""
    root = str(position.get("instrument") or "").upper().replace("1!", "")
    point_value = {"MNQ": 2.0, "MES": 5.0}.get(root, 0.0)
    entry = float(position.get("entry") or position.get("planned_entry") or 0.0)
    stop = float(position.get("stop") or entry)
    qty = max(1, int(position.get("contracts") or 1))
    return round(abs(entry - stop) * point_value * qty, 2)


def combined_open_risk_dollars(log_dir: str | Path) -> float:
    return round(sum(planned_risk_dollars(row) for row in open_positions(log_dir)), 2)


def admission_snapshot(log_dir: str | Path, day: date) -> dict[str, Any]:
    positions = open_positions(log_dir)
    return {
        "open_positions": len(positions),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "filled_trades_today": filled_count(log_dir, day),
        "max_fills_per_day": MAX_FILLS_PER_DAY,
        "combined_open_risk_dollars": round(
            sum(planned_risk_dollars(row) for row in positions), 2
        ),
    }


def portfolio_journal(log_dir: str | Path) -> JournalLogger:
    return JournalLogger(log_dir=str(portfolio_dir(log_dir)))


def record_outcome(
    *,
    log_dir: str | Path,
    for_date: Optional[date],
    instrument: str,
    session: str,
    strategy: str,
    result: str,
    entry_price: float,
    exit_price: float,
    exit_reason: str,
    pnl_ticks: float,
    net_pnl_dollars: float,
    contracts: int = 1,
    signal_timestamp: Optional[str] = None,
    paper_order_id: Optional[str] = None,
    client_order_id: Optional[str] = None,
) -> None:
    """Mirror one resolved NET outcome for family-level drawdown measurement."""
    portfolio_journal(log_dir).log_outcome(
        instrument=instrument,
        session=session,
        result=result,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_ticks=pnl_ticks,
        pnl_dollars=net_pnl_dollars,
        contracts=contracts,
        for_date=for_date,
        strategy=strategy,
        signal_timestamp=signal_timestamp,
        paper_order_id=paper_order_id,
        client_order_id=client_order_id,
    )


def performance(log_dir: str | Path, starting_balance: float) -> dict[str, Any]:
    stats = portfolio_journal(log_dir).get_performance_stats(float(starting_balance))
    max_dd = float(stats.get("max_drawdown") or 0.0)
    stats["starting_balance"] = float(starting_balance)
    stats["max_drawdown_percent"] = (
        round(max_dd / float(starting_balance) * 100.0, 2)
        if float(starting_balance) > 0
        else None
    )
    return stats
