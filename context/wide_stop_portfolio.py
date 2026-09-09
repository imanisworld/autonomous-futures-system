"""Shared portfolio safety for the 4HR / 3-2-2 MNQ evidence lanes.

Per-strategy journals remain separate for attribution, but admission is shared.
This module is intentionally conservative:
- max two PaperBroker positions across the day-strategy family;
- max three daily execution slots across the family;
- max $450 combined planned open risk (the already-approved $150 + $300 caps);
- an external-demo slot is reserved *before* broker submission. A crash or
  ambiguous submission leaves that reservation in place, so restart can miss a
  trade but can never silently admit a fourth one.

Daily 2-2 is deliberately not part of this module. It remains a separate
PaperBroker-only swing ledger and has no Tradovate-demo route here.
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
MAX_COMBINED_OPEN_RISK_DOLLARS = 450.0
PORTFOLIO_DIR = "portfolio"
STATE_FILENAME = "portfolio_state.json"
_PAPER_STATE = "forward_collector_state.json"
_DEMO_STATE = "demo_collector_state.json"
_VALID_SLOT_STATUS = {"reserved", "confirmed"}


def portfolio_dir(log_dir: str | Path) -> Path:
    return Path(log_dir) / contract.JOURNAL_ROOT / PORTFOLIO_DIR


def state_path(log_dir: str | Path) -> Path:
    return portfolio_dir(log_dir) / STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {"trading_date": None, "slots": {}}


def load_state(log_dir: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(state_path(log_dir).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(raw, dict):
        return _empty_state()
    slots: dict[str, dict[str, Any]] = {}
    for key, value in dict(raw.get("slots") or {}).items():
        if not isinstance(value, dict):
            continue
        status = str(value.get("status") or "").lower()
        if status not in _VALID_SLOT_STATUS:
            continue
        slots[str(key)] = {
            "status": status,
            "route": str(value.get("route") or "unknown"),
        }
    return {"trading_date": raw.get("trading_date"), "slots": slots}


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
    if state.get("trading_date") != key:
        state["trading_date"] = key
        state["slots"] = {}


def _day_state(log_dir: str | Path, day: date) -> dict[str, Any]:
    state = load_state(log_dir)
    reset_day(state, day)
    return state


def reserve_daily_slot(
    log_dir: str | Path,
    day: date,
    candidate_key: str,
    *,
    route: str,
) -> tuple[bool, str]:
    """Persist one conservative daily slot before an execution attempt.

    Existing reservations are never treated as free. This is the crash/ambiguous
    submit guard: uncertainty consumes capacity until the trading date rolls.
    """
    state = _day_state(log_dir, day)
    key = str(candidate_key)
    slots = state["slots"]
    if key in slots:
        return False, f"slot_already_{slots[key]['status']}"
    if len(slots) >= MAX_FILLS_PER_DAY:
        return False, "portfolio_max_trades_per_day"
    slots[key] = {"status": "reserved", "route": str(route)}
    save_state(log_dir, state)
    return True, "reserved"


def confirm_daily_slot(log_dir: str | Path, day: date, candidate_key: str) -> int:
    """Mark a reserved slot as a confirmed fill.

    If the reservation file was unexpectedly lost after a real fill, record the
    confirmed fill anyway rather than hiding the breach. Subsequent admission
    remains blocked by the resulting slot count.
    """
    state = _day_state(log_dir, day)
    key = str(candidate_key)
    slot = state["slots"].get(key)
    if slot is None:
        state["slots"][key] = {"status": "confirmed", "route": "recovered"}
    else:
        slot["status"] = "confirmed"
    save_state(log_dir, state)
    return confirmed_fill_count(log_dir, day)


def release_daily_slot(log_dir: str | Path, day: date, candidate_key: str) -> bool:
    """Release only a definitely-unfilled reservation; confirmed fills stay."""
    state = _day_state(log_dir, day)
    key = str(candidate_key)
    slot = state["slots"].get(key)
    if not slot or slot.get("status") != "reserved":
        return False
    del state["slots"][key]
    save_state(log_dir, state)
    return True


def confirmed_fill_count(log_dir: str | Path, day: date) -> int:
    state = _day_state(log_dir, day)
    return sum(1 for slot in state["slots"].values() if slot.get("status") == "confirmed")


def reserved_count(log_dir: str | Path, day: date, *, route: Optional[str] = None) -> int:
    state = _day_state(log_dir, day)
    return sum(
        1
        for slot in state["slots"].values()
        if slot.get("status") == "reserved"
        and (route is None or slot.get("route") == route)
    )


def daily_slots_used(log_dir: str | Path, day: date) -> int:
    return len(_day_state(log_dir, day)["slots"])


def _state_file(log_dir: str | Path, ledger: contract.Ledger, filename: str) -> dict[str, Any]:
    path = contract.journal_dir(log_dir, ledger) / filename
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def open_positions(log_dir: str | Path) -> list[dict[str, Any]]:
    """Return day-strategy paper/demo positions; stale route state still blocks."""
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


def risk_dollars(*, instrument: str, entry: float, stop: float, contracts: int = 1) -> float:
    root = str(instrument or "").upper().replace("1!", "")
    point_value = {"MNQ": 2.0, "MES": 5.0}.get(root, 0.0)
    qty = max(1, int(contracts or 1))
    return round(abs(float(entry) - float(stop)) * point_value * qty, 2)


def planned_risk_dollars(position: dict[str, Any]) -> float:
    entry = float(position.get("entry") or position.get("planned_entry") or 0.0)
    stop = float(position.get("stop") or entry)
    return risk_dollars(
        instrument=str(position.get("instrument") or ""),
        entry=entry,
        stop=stop,
        contracts=max(1, int(position.get("contracts") or 1)),
    )


def combined_open_risk_dollars(log_dir: str | Path) -> float:
    return round(sum(planned_risk_dollars(row) for row in open_positions(log_dir)), 2)


def proposed_risk_allowed(log_dir: str | Path, proposed_risk: float) -> tuple[bool, float]:
    combined = round(combined_open_risk_dollars(log_dir) + float(proposed_risk), 2)
    return combined <= MAX_COMBINED_OPEN_RISK_DOLLARS + 1e-9, combined


def admission_snapshot(log_dir: str | Path, day: date) -> dict[str, Any]:
    positions = open_positions(log_dir)
    return {
        "open_positions": len(positions),
        "max_open_positions": MAX_OPEN_POSITIONS,
        "confirmed_fills_today": confirmed_fill_count(log_dir, day),
        "reserved_slots_today": reserved_count(log_dir, day),
        "daily_slots_used": daily_slots_used(log_dir, day),
        "max_fills_per_day": MAX_FILLS_PER_DAY,
        "combined_open_risk_dollars": round(
            sum(planned_risk_dollars(row) for row in positions), 2
        ),
        "max_combined_open_risk_dollars": MAX_COMBINED_OPEN_RISK_DOLLARS,
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
