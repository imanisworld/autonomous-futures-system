"""
webhook/event_id.py

Correlation/event id helper.

Every accepted webhook event gets a stable event_id that is propagated through
logs (maintenance rejections, dedupe skips, decision journal, error logs) and
into the Discord signal message, so one alert can be traced end to end.

Rules:
  - If the incoming payload already carries a valid event_id, preserve it.
  - If it is missing or malformed, generate a fresh one.
"""

from __future__ import annotations

import re
import uuid

# Conservative allow-list: TradingView/operator-supplied ids must look sane to be
# preserved. Anything else is treated as malformed and replaced.
_VALID_EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def is_valid_event_id(value: object) -> bool:
    return isinstance(value, str) and bool(_VALID_EVENT_ID.match(value.strip()))


def new_event_id() -> str:
    return uuid.uuid4().hex


def ensure_event_id(value: object = None) -> str:
    """Preserve a valid incoming event_id, otherwise mint a new one."""
    if is_valid_event_id(value):
        return value.strip()  # type: ignore[union-attr]
    return new_event_id()
