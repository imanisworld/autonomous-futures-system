"""Optional, source-only 4HR 5m→natural-1m identity evidence (version 2).

No strategy, risk, journal, broker, observer epoch or experiment authority.
Defaults OFF. Never backfills old rows and never reads 1m result/P&L.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from execution.contract_identity import normalize as normalize_contract

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
ENABLED_ENV = "WIDE_STOP_4HR_JOIN_PROVENANCE_V2_ENABLED"
SCHEMA = "wide_stop_4hr_5m_join_provenance_v2"
STRATEGY = "strat_4hr_retrigger"
DIRECTORY = "wide_stop_4hr_join_v2"


def enabled() -> bool:
    return str(os.getenv(ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes"}


def _aware(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _price(raw: object) -> float | None:
    try:
        if isinstance(raw, bool):
            return None
        n = float(raw)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_5m_provenance(
    *, strategy: str, candidate: Mapping[str, Any], candidate_key: str,
    source_bar_ts: str, contract_hint: str | None, day: date,
) -> dict[str, Any]:
    """Build a *candidate identity*, not a fill or authorization.

    Preserve original machine stamp strings for exact comparison with
    one_min_trigger's arm_key. Never normalize unknown contract to root/current.
    """
    if strategy != STRATEGY:
        raise ValueError("only canonical 4HR 5m detector is supported")
    if not isinstance(candidate, Mapping) or not candidate_key:
        raise ValueError("candidate and candidate_key required")
    state = candidate.get("state")
    state = state if isinstance(state, Mapping) else {}
    row: dict[str, Any] = {
        "schema": SCHEMA, "kind": "5M_CANONICAL_CANDIDATE_IDENTITY",
        "strategy": STRATEGY, "trading_date": day.isoformat(),
        "candidate_key": candidate_key, "source_bar_ts": source_bar_ts,
        "source_timeframe": "5m", "observation_only": True,
        "broker_authorized": False, "execution_reachable": False,
        "arm_key": None, "joinability": "UNMATCHABLE", "reason": None,
    }
    source = _aware(source_bar_ts)
    closed = _aware(candidate.get("entry_time"))
    setup_ts = str(state.get("setup_bar_ts") or "")
    four_ts = str(state.get("four_am_bar_ts") or "")
    setup_dt, four_dt = _aware(setup_ts), _aware(four_ts)
    trigger, entry, stop, target, arm_target = (
        _price(state.get("trigger")), _price(candidate.get("entry")),
        _price(candidate.get("stop")), _price(candidate.get("target")),
        _price(state.get("target")),
    )
    direction = str(state.get("direction") or candidate.get("direction") or "").upper()
    claimed_date = str(state.get("trading_date") or "")
    raw_hint = contract_hint.strip() if isinstance(contract_hint, str) else ""
    normalized = normalize_contract(raw_hint or None, context_date=day)
    row.update(
        direction=direction,
        trigger=trigger, planned_entry=entry, planned_stop=stop,
        planned_target=target, setup_bar_ts=setup_ts or None,
        four_am_bar_ts=four_ts or None,
        candidate_decision_at=closed.isoformat() if closed else None,
        source_contract=normalized,
        contract_status=(
            "ASSERTED_UNMATCHED" if normalized is not None else
            "UNNORMALIZABLE" if raw_hint else "UNKNOWN"
        ),
    )
    # Cross-check real timestamps; a missing/inconsistent bar must not yield
    # a synthetic arm ID. The source bar is an OPEN stamp; signal only at close.
    if source is None or closed is None or closed != source + timedelta(minutes=5):
        row["reason"] = "INVALID_FIVE_MIN_DECISION_TIME"
    elif (
        not claimed_date or claimed_date != day.isoformat()
        or source.astimezone(ET).date() != day
    ):
        row["reason"] = "INCONSISTENT_TRADING_DATE"
    elif (
        setup_dt is None or four_dt is None or
        setup_dt.astimezone(ET).date() != day or
        four_dt.astimezone(ET).date() != day or
        setup_dt > closed or four_dt > closed
    ):
        row["reason"] = "MISSING_OR_INVALID_ARM_STAMPS"
    elif direction not in {"LONG", "SHORT"} or None in (trigger, entry, stop, target, arm_target):
        row["reason"] = "INVALID_STRUCTURAL_PRICES_OR_DIRECTION"
    elif (
        entry != trigger or target != arm_target or
        str(candidate.get("direction") or "").upper() != direction
    ):
        row["reason"] = "CANDIDATE_DIFFERS_FROM_ARM"
    elif (
        (direction == "LONG" and not (stop < entry < target)) or
        (direction == "SHORT" and not (target < entry < stop))
    ):
        row["reason"] = "INVALID_BRACKET"
    elif str(state.get("status") or "") != "TRIGGERED":
        row["reason"] = "STATE_NOT_TRIGGERED"
    else:
        # EXACT same encoding as 1m observer, so no timestamp/precision guess.
        row["arm_key"] = "|".join([
            day.isoformat(), direction, f"{trigger:.8f}", setup_ts, four_ts,
        ])
        if normalized is None:
            row["reason"] = "DATED_CONTRACT_UNPROVEN"
        else:
            row["joinability"] = "IDENTITY_AVAILABLE"
            row["reason"] = None
    return row


def compare_with_1m_touch(touch: Mapping[str, Any], five: Mapping[str, Any]) -> dict[str, str]:
    """Classify identity only; NOT broker fill, timing, risk or P&L parity."""
    if (
        five.get("schema") != SCHEMA or
        touch.get("event") != "TRIGGER_TOUCH" or
        five.get("joinability") != "IDENTITY_AVAILABLE"
    ):
        return {"status": "UNMATCHABLE", "reason": "MISSING_VERIFIED_SETUP_ID"}
    if touch.get("arm_key") != five.get("arm_key"):
        return {"status": "MISMATCH", "reason": "ARM_KEY_DIFFERENT"}
    if (
        str(touch.get("direction") or "").upper() != five.get("direction") or
        _price(touch.get("trigger")) != five.get("trigger") or
        _price(touch.get("target")) != five.get("planned_target")
    ):
        return {"status": "MISMATCH", "reason": "ARM_GEOMETRY_DIFFERENT"}
    contract_check = touch.get("contract_check")
    if not isinstance(contract_check, Mapping) or contract_check.get("status") != "MATCH":
        return {"status": "UNMATCHABLE", "reason": "NATURAL_TOUCH_CONTRACT_UNPROVEN"}
    dated = five.get("source_contract")
    if (
        not dated or dated != contract_check.get("arm_contract") or
        dated != contract_check.get("bar_contract")
    ):
        return {"status": "MISMATCH", "reason": "DATED_CONTRACT_DIFFERENT"}
    return {"status": "MATCHED_IDENTITY_ONLY", "reason": "EXECUTION_PARITY_UNPROVEN"}


def maybe_record_five_min_provenance(
    *, log_dir: str | Path, strategy: str, candidate: Mapping[str, Any],
    candidate_key: str, payload: Any, day: date,
) -> dict[str, Any] | None:
    """Default OFF, isolated idempotent sidecar; never affect active lanes."""
    if not enabled() or strategy != STRATEGY:
        return None
    try:
        row = build_5m_provenance(
            strategy=strategy, candidate=candidate,
            candidate_key=candidate_key,
            source_bar_ts=str(getattr(payload, "timestamp", "") or ""),
            contract_hint=getattr(payload, "contract_hint", None),
            day=day,
        )
        digest = hashlib.sha256(
            (day.isoformat() + "|" + candidate_key).encode("utf-8")
        ).hexdigest()
        folder = Path(log_dir) / DIRECTORY / day.isoformat()
        folder.mkdir(parents=True, exist_ok=True)
        # Exclusive create: repeated collector work cannot append duplicate
        # records or relabel settled paper/demonstration journals.
        path = folder / f"{digest}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(row, handle, sort_keys=True)
                handle.write("\n")
        except FileExistsError:
            return None
        return row
    except Exception:  # noqa: BLE001: sidecar must never block paper/runtime
        logger.warning("4HR join-provenance sidecar failed closed", exc_info=True)
        return None
