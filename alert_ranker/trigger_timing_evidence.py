"""Optional, evidence-only timestamp for the FIRST scan that saw a Daily trigger.

Separate append-only JSONL journal under shared logs: no SQLite write, no broker
calls, no bar reconstruction, and no inference of the actual intrabar break time.
The first scan snapshot stays stable across rescans and process restarts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .paper_v1 import setup_episode_key

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

_LOCAL_LOCK = threading.Lock()


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_sight_r(
    *, direction: str, live_price: object, trigger: object, invalidation: object,
) -> float | None:
    """Signed movement since trigger in initial setup R; positive is favorable."""
    spot = _number(live_price)
    entry = _number(trigger)
    stop = _number(invalidation)
    if spot is None or entry is None or stop is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0 or direction not in {"CALL", "PUT"}:
        return None
    return (spot - entry) / risk if direction == "CALL" else (entry - spot) / risk


def first_observed_daily_trigger(
    *,
    journal: str | Path,
    ticker: str,
    timeframe: str,
    setup_type: str,
    direction: str,
    trigger: object,
    invalidation: object,
    live_price: object,
    now: datetime,
) -> dict[str, object]:
    """First sight per canonical Daily episode; UTC timestamp, no lookahead.

    Caller invokes this ONLY after the canonical Daily verdict triggered. If
    persistence is unavailable this raises, so the caller can label timing
    evidence as ERROR rather than silently fabricating a first-sight stamp.
    """
    if now.tzinfo is None:
        raise ValueError("first-sight timestamp must be timezone-aware")
    known_at = now.astimezone(timezone.utc).isoformat()
    episode = setup_episode_key(
        ticker=ticker,
        lane="DAILY_TRIGGER_TIMING",
        timeframe=timeframe,
        setup_type=setup_type,
        direction=direction,
        trigger=trigger,
        moment=now,
    )
    identity = hashlib.sha256(episode.encode("utf-8")).hexdigest()
    path = Path(journal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCAL_LOCK:
        with path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                for raw in handle:
                    if not raw.strip():
                        continue
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        raise ValueError("invalid timing journal row")
                    if row.get("episode_sha256") == identity:
                        if not isinstance(row.get("trigger_known_at"), str):
                            raise ValueError("existing timing row missing first sight")
                        return {
                            "trigger_known_at": row["trigger_known_at"],
                            "first_eval_price_vs_trigger_r": row.get("first_eval_price_vs_trigger_r"),
                        }
                timing = {
                    "trigger_known_at": known_at,
                    "first_eval_price_vs_trigger_r": _first_sight_r(
                        direction=direction,
                        live_price=live_price,
                        trigger=trigger,
                        invalidation=invalidation,
                    ),
                }
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps({"episode_sha256": identity, **timing}, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return timing
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
