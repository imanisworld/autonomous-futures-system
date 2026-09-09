"""Fail-closed integrity gate for the paper-only Daily 2-2 swing state.

The Daily collector intentionally persists positions across sessions. Losing or
silently resetting that state would make forward evidence unreliable and could
admit a second simulated swing while an earlier one is unresolved. This module
adds no strategy logic; it validates the persisted evidence state before the
Daily collector is allowed to process a new bar.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from context import daily_22_swing_collector as lane
from context.bar_history import _parse_dt


class DailySwingStateIntegrityError(RuntimeError):
    """Raised when persisted Daily swing state cannot be trusted."""


def _finite_number(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def assert_state_integrity(log_dir: str | Path, cfg) -> None:
    """Refuse ambiguous persisted state; allow only a provably fresh epoch.

    A missing state file is acceptable only when the Daily audit file is also
    absent/empty, which is the observable signature of a genuinely fresh
    campaign directory. Once any Daily evidence exists, disappearing state is a
    blocker rather than permission to recreate a $5k flat ledger.
    """
    epoch = lane._epoch(cfg)
    if epoch is None:
        raise DailySwingStateIntegrityError("daily_swing_epoch_missing_or_invalid")

    state_path = lane._state_path(log_dir)
    audit_path = lane._audit_path(log_dir)

    if not state_path.exists():
        try:
            prior_evidence = audit_path.exists() and audit_path.stat().st_size > 0
        except OSError as exc:
            raise DailySwingStateIntegrityError("daily_swing_audit_state_unreadable") from exc
        if prior_evidence:
            raise DailySwingStateIntegrityError("daily_swing_state_missing_with_existing_evidence")
        return

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailySwingStateIntegrityError("daily_swing_state_unreadable_or_corrupt") from exc

    if not isinstance(raw, dict):
        raise DailySwingStateIntegrityError("daily_swing_state_not_object")
    if raw.get("epoch") != epoch.isoformat():
        raise DailySwingStateIntegrityError("daily_swing_state_epoch_mismatch")

    for field in ("balance", "peak", "max_drawdown"):
        if not _finite_number(raw.get(field)):
            raise DailySwingStateIntegrityError(f"daily_swing_state_invalid_{field}")
    if float(raw["balance"]) < 0 or float(raw["peak"]) <= 0:
        raise DailySwingStateIntegrityError("daily_swing_state_invalid_equity")
    if float(raw["max_drawdown"]) < 0:
        raise DailySwingStateIntegrityError("daily_swing_state_invalid_drawdown")
    if not isinstance(raw.get("halted"), bool):
        raise DailySwingStateIntegrityError("daily_swing_state_invalid_halted")
    if not isinstance(raw.get("seen"), list):
        raise DailySwingStateIntegrityError("daily_swing_state_invalid_seen")

    position = raw.get("position")
    if position is None:
        return
    if not isinstance(position, dict):
        raise DailySwingStateIntegrityError("daily_swing_state_invalid_position")

    required = ("direction", "entry", "stop", "target", "entry_time")
    if any(position.get(field) is None for field in required):
        raise DailySwingStateIntegrityError("daily_swing_position_missing_required_field")
    if str(position.get("direction")).upper() not in {"LONG", "SHORT"}:
        raise DailySwingStateIntegrityError("daily_swing_position_invalid_direction")
    if any(not _finite_number(position.get(field)) for field in ("entry", "stop", "target")):
        raise DailySwingStateIntegrityError("daily_swing_position_invalid_price")
    if _parse_dt(str(position.get("entry_time") or "")) is None:
        raise DailySwingStateIntegrityError("daily_swing_position_invalid_entry_time")

    entry = float(position["entry"])
    stop = float(position["stop"])
    target = float(position["target"])
    if position["direction"].upper() == "LONG" and not (stop < entry < target):
        raise DailySwingStateIntegrityError("daily_swing_position_invalid_bracket")
    if position["direction"].upper() == "SHORT" and not (target < entry < stop):
        raise DailySwingStateIntegrityError("daily_swing_position_invalid_bracket")
