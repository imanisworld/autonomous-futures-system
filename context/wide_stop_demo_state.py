"""Crash-safe shared state for the 4HR / 3-2-2 Tradovate demo lane.

This file contains no broker calls. It exists only to make admission durable:
- at most three execution slots/day across 4HR + 3-2-2;
- one shared MNQ position/pending submission because Tradovate nets by contract;
- a slot is reserved before external submission and released only on a definite
  no-fill. Ambiguous outcomes remain reserved and block new entries.

Malformed existing state is an error, never a fresh empty ledger.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from context import wide_stop_ledger_paper as contract

MAX_FILLS_PER_DAY = 3
MAX_COMBINED_OPEN_RISK_DOLLARS = 450.0
STATE_DIR = "tradovate_demo"
STATE_FILENAME = "demo_state.json"
_VALID_SLOT_STATUS = {"reserved", "confirmed"}


class DemoStateError(RuntimeError):
    pass


def state_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / contract.JOURNAL_ROOT / STATE_DIR / STATE_FILENAME


def empty_state(day: date) -> dict[str, Any]:
    return {
        "trading_date": day.isoformat(),
        "slots": {},
        "pending": None,
        "position": None,
        "seen": [],
    }


def load_state(log_dir: str | Path, day: date) -> dict[str, Any]:
    path = state_path(log_dir)
    if not path.exists():
        return empty_state(day)
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise DemoStateError(f"demo state unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise DemoStateError("demo state must be a JSON object")
    slots_raw = raw.get("slots")
    if not isinstance(slots_raw, dict):
        raise DemoStateError("demo state slots missing/malformed")
    slots: dict[str, dict[str, str]] = {}
    for key, value in slots_raw.items():
        if not isinstance(value, dict):
            raise DemoStateError("demo slot entry malformed")
        status = str(value.get("status") or "").lower()
        if status not in _VALID_SLOT_STATUS:
            raise DemoStateError(f"invalid demo slot status: {status!r}")
        slots[str(key)] = {
            "status": status,
            "strategy": str(value.get("strategy") or ""),
        }
    pending = raw.get("pending")
    position = raw.get("position")
    if pending is not None and not isinstance(pending, dict):
        raise DemoStateError("demo pending state malformed")
    if position is not None and not isinstance(position, dict):
        raise DemoStateError("demo position state malformed")
    seen = raw.get("seen")
    if not isinstance(seen, list):
        raise DemoStateError("demo seen state malformed")
    trading_date = str(raw.get("trading_date") or "")
    if not trading_date:
        raise DemoStateError("demo trading_date missing")
    state = {
        "trading_date": trading_date,
        "slots": slots,
        "pending": pending,
        "position": position,
        "seen": [str(item) for item in seen[-500:]],
    }
    if trading_date != day.isoformat():
        if pending is not None or position is not None:
            raise DemoStateError(
                "demo date rollover with unresolved pending/position; manual reconciliation required"
            )
        return empty_state(day)
    return state


def save_state(log_dir: str | Path, state: dict[str, Any]) -> None:
    path = state_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def slots_used(state: dict[str, Any]) -> int:
    return len(state["slots"])


def confirmed_fills(state: dict[str, Any]) -> int:
    return sum(1 for slot in state["slots"].values() if slot["status"] == "confirmed")


def reserved_slots(state: dict[str, Any]) -> int:
    return sum(1 for slot in state["slots"].values() if slot["status"] == "reserved")


def reserve_slot(state: dict[str, Any], candidate_key: str, strategy: str) -> tuple[bool, str]:
    key = str(candidate_key)
    if key in state["slots"]:
        return False, f"slot_already_{state['slots'][key]['status']}"
    if slots_used(state) >= MAX_FILLS_PER_DAY:
        return False, "demo_max_trades_per_day"
    state["slots"][key] = {"status": "reserved", "strategy": str(strategy)}
    return True, "reserved"


def confirm_slot(state: dict[str, Any], candidate_key: str, strategy: str) -> None:
    key = str(candidate_key)
    if key not in state["slots"]:
        state["slots"][key] = {"status": "confirmed", "strategy": str(strategy)}
    else:
        state["slots"][key]["status"] = "confirmed"


def release_slot(state: dict[str, Any], candidate_key: str) -> bool:
    key = str(candidate_key)
    slot = state["slots"].get(key)
    if not slot or slot["status"] != "reserved":
        return False
    del state["slots"][key]
    return True


def mark_seen(state: dict[str, Any], candidate_key: str) -> None:
    key = str(candidate_key)
    if key not in state["seen"]:
        state["seen"].append(key)
        state["seen"] = state["seen"][-500:]


def risk_dollars(entry: float, stop: float, contracts: int = 1) -> float:
    # MNQ = $2.00 / point / contract.
    return round(abs(float(entry) - float(stop)) * 2.0 * max(1, int(contracts)), 2)


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    position = state.get("position")
    open_risk = (
        risk_dollars(position["entry"], position["stop"], position.get("contracts", 1))
        if isinstance(position, dict)
        else 0.0
    )
    return {
        "trading_date": state["trading_date"],
        "daily_slots_used": slots_used(state),
        "confirmed_fills": confirmed_fills(state),
        "reserved_slots": reserved_slots(state),
        "max_fills_per_day": MAX_FILLS_PER_DAY,
        "position_open": isinstance(position, dict),
        "pending_submission": isinstance(state.get("pending"), dict),
        "combined_open_risk_dollars": open_risk,
        "max_combined_open_risk_dollars": MAX_COMBINED_OPEN_RISK_DOLLARS,
    }
