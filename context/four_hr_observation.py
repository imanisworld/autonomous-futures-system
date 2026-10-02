"""Observation-only armed-state surface for MNQ 4HR natural-1m evidence.

The wide-stop forward collector publishes the canonical machine state here.
The 1-minute observer may read an ARMED snapshot. Nothing in the executable
strategy, risk, or broker path reads this file.

Default OFF via ONE_MIN_4HR_OBSERVER_ENABLED. A missing, stale, or malformed
snapshot produces no evidence claim and never falls back to executable
strategy state.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from context.one_min_feed import ONE_MIN_LANE, one_min_enabled

ET = ZoneInfo("America/New_York")
SCHEMA = "4hr_observation_state_v1"
STRATEGY = "strat_4hr_retrigger"
RULE_VERSION = "canonical_4hr_retrigger_state_v1"
SOURCE = "wide_stop_forward_v1"
ENABLED_ENV = "ONE_MIN_4HR_OBSERVER_ENABLED"


def four_hr_observation_enabled() -> bool:
    """Active only when the generic 1m lane and this observer are both on."""
    dedicated = os.getenv(ENABLED_ENV, "").strip().lower() in {"1", "true", "yes"}
    return one_min_enabled() and dedicated


def observation_state_path(log_dir: str | Path, day: date) -> Path:
    return (
        Path(log_dir)
        / ONE_MIN_LANE
        / "4hr_observation"
        / f"state_{day.isoformat()}.json"
    )


def _aware(value: datetime) -> Optional[datetime]:
    if value.tzinfo is None:
        return None
    return value.astimezone(ET)


def _number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _snapshot(machine_state: dict, source_timestamp: datetime) -> Optional[dict]:
    available = _aware(source_timestamp)
    if available is None or not isinstance(machine_state, dict):
        return None
    trading_date = str(machine_state.get("trading_date") or "")
    status = str(machine_state.get("status") or "")
    if trading_date != available.date().isoformat() or not status:
        return None
    snapshot = {
        "schema": SCHEMA,
        "strategy": STRATEGY,
        "rule_version": RULE_VERSION,
        "source": SOURCE,
        "instrument": "MNQ",
        "trading_date": trading_date,
        "status": status,
        "source_timestamp": available.isoformat(),
        "executable": False,
        "trade_authorized": False,
        "order_authority": False,
    }
    if status == "ARMED":
        direction = str(machine_state.get("direction") or "").upper()
        trigger = _number(machine_state.get("trigger"))
        target = _number(machine_state.get("target"))
        setup_bar_ts = str(machine_state.get("setup_bar_ts") or "")
        four_am_bar_ts = str(machine_state.get("four_am_bar_ts") or "")
        if (
            direction not in {"LONG", "SHORT"}
            or trigger is None
            or target is None
            or not setup_bar_ts
            or not four_am_bar_ts
        ):
            return None
        snapshot.update(
            direction=direction,
            trigger=trigger,
            target=target,
            setup_bar_ts=setup_bar_ts,
            four_am_bar_ts=four_am_bar_ts,
        )
    return snapshot


def publish_4hr_observation(
    log_dir: str | Path,
    machine_state: dict,
    *,
    source_timestamp: datetime,
) -> bool:
    """Publish one canonical machine snapshot. Malformed input writes nothing."""
    snapshot = _snapshot(machine_state, source_timestamp)
    if snapshot is None:
        return False
    day = date.fromisoformat(snapshot["trading_date"])
    path = observation_state_path(log_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)
    return True


def read_armed_observation(
    log_dir: str | Path,
    day: date,
    *,
    as_of: datetime,
) -> Optional[dict]:
    """Return one ARMED snapshot known at ``as_of``, or None. Never raises."""
    as_of_et = _aware(as_of)
    if as_of_et is None:
        return None
    path = observation_state_path(log_dir, day)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != SCHEMA or raw.get("rule_version") != RULE_VERSION:
        return None
    if raw.get("source") != SOURCE or raw.get("strategy") != STRATEGY:
        return None
    if raw.get("instrument") != "MNQ" or raw.get("trading_date") != day.isoformat():
        return None
    if raw.get("status") != "ARMED":
        return None
    if (
        raw.get("executable") is not False
        or raw.get("trade_authorized") is not False
        or raw.get("order_authority") is not False
    ):
        return None
    direction = str(raw.get("direction") or "").upper()
    trigger = _number(raw.get("trigger"))
    target = _number(raw.get("target"))
    if direction not in {"LONG", "SHORT"} or trigger is None or target is None:
        return None
    if not raw.get("setup_bar_ts") or not raw.get("four_am_bar_ts"):
        return None
    try:
        available = datetime.fromisoformat(str(raw.get("source_timestamp")))
    except ValueError:
        return None
    available_et = _aware(available)
    if available_et is None or available_et > as_of_et:
        return None
    if available_et.date().isoformat() != day.isoformat():
        return None
    return raw
