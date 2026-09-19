"""Durable response proof for the prospective 1m trigger observers.

This module is evidence-only. It records the actual runner response associated
with a natural 4HR/3-2-2 observer event so the preregistered isolation checks
can be reviewed after later alerts overwrite latest_webhook.json.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


RECORD_TYPE = "ONE_MIN_OBSERVER_RESPONSE_AUDIT"


def _event_day(payload: Any) -> date:
    raw = getattr(payload, "timestamp", None)
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.now(timezone.utc).date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date()


def _event_dict(value: Any) -> dict | None:
    return value if isinstance(value, dict) and value.get("event") else None


def append_observer_response_audit(
    log_dir: str, payload: Any, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Append one response audit only when an observer emitted an event."""
    four_hr = _event_dict(result.get("one_min_trigger"))
    first_live = _event_dict(result.get("one_min_322_observer"))
    if four_hr is None and first_live is None:
        return None

    row: dict[str, Any] = {
        "record_type": RECORD_TYPE,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "timestamp": str(getattr(payload, "timestamp", "") or ""),
            "ticker": str(getattr(payload, "ticker", "") or ""),
            "timeframe": str(getattr(payload, "timeframe", "") or ""),
            "event_id": getattr(payload, "event_id", None),
        },
        "response": {
            "decision": result.get("decision"),
            "fill_is_none": result.get("fill") is None,
            "risk_is_none": result.get("risk") is None,
            "execution_reachable": result.get("execution_reachable"),
            "resolution": result.get("resolution"),
        },
        "one_min_trigger": four_hr,
        "one_min_322_observer": first_live,
        "one_min_error": result.get("one_min_error"),
        "one_min_322_observer_error": result.get("one_min_322_observer_error"),
    }

    day = _event_day(payload)
    path = (
        Path(log_dir)
        / "tf1m"
        / f"observer_response_audit_{day.isoformat()}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    return row
