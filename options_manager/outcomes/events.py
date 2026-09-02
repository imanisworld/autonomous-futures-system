"""ForwardOutcomeEvent: one causal observation in a forward-proof session.

The event is what a later reducer folds into NO SETUP / SETUP BUT NOT
TRIGGERED / TRIGGERED BUT CONTRACT BLOCKED / TRIGGERED AND ACTIONABLE /
INVALIDATED / T1 HIT / T2 HIT. Nothing here derives an outcome; it only
preserves what was seen and when. ``event_at`` is the *source* timestamp
(bar close, quote update, chain snapshot); ``recorded_at`` is supplied by
the writer at storage time and must not precede ``event_at``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

EVENT_TYPES: tuple[str, ...] = (
    "SESSION_STAGE",          # a 09:26 / 09:46 / 10:03 stage was captured
    "SETUP_STATE",            # setup_state changed or was (re)asserted
    "TRIGGER",                # mechanical entry level broken (with bar/quote proof)
    "INVALIDATION",           # underlying invalidation level broken
    "TARGET_1",               # T1 level reached
    "TARGET_2",               # T2 level reached
    "CONTRACT_OBSERVATION",   # read-only chain facts at event_at
    "MARKET_CONTEXT",         # SPY/QQQ context at event_at
    "PRICE_PATH",             # bar-level MFE/MAE bookkeeping point
    "NOTE",                   # free-form, never an outcome claim
)
SETUP_STATES: tuple[str, ...] = (
    "NO_SETUP",
    "SETUP_NOT_TRIGGERED",
    "TRIGGERED_CONTRACT_BLOCKED",
    "TRIGGERED_ACTIONABLE",
    "INVALIDATED",
    "T1_HIT",
    "T2_HIT",
)
_DIRECTIONS = ("CALL", "PUT", "NONE")


@dataclass(frozen=True, kw_only=True)
class ForwardOutcomeEvent:
    session_id: str
    thesis_id: str
    ticker: str
    direction: str  # CALL / PUT / NONE (no thesis yet)
    setup_type: str
    timeframe: str
    event_type: str
    event_at: datetime  # source timestamp, tz-aware
    provider: str
    system_commit_sha: str
    setup_state: str
    contract_facts: Mapping[str, Any] = field(default_factory=dict)
    market_context: Mapping[str, Any] = field(default_factory=dict)
    observations: Mapping[str, Any] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    provider_updated_at: Optional[datetime] = None  # provider's own timestamp when it has one

    def to_payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_at"] = self.event_at.isoformat()
        data["provider_updated_at"] = self.provider_updated_at.isoformat() if self.provider_updated_at else None
        data["reason_codes"] = list(self.reason_codes)
        data["contract_facts"] = dict(self.contract_facts)
        data["market_context"] = dict(self.market_context)
        data["observations"] = dict(self.observations)
        return data


def _is_tz_aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _json_safe(value: Any) -> bool:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True


def _has_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return value != value or value in (float("inf"), float("-inf"))
    if isinstance(value, Mapping):
        return any(_has_non_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_non_finite(v) for v in value)
    return False


def validate_forward_outcome_event(event: ForwardOutcomeEvent) -> tuple[str, ...]:
    """Reasons the event is not storable; empty when it is. Never raises."""
    reasons: list[str] = []
    for name in ("session_id", "thesis_id", "ticker", "setup_type", "timeframe", "provider", "system_commit_sha"):
        if not str(getattr(event, name, "") or "").strip():
            reasons.append(f"missing {name}")
    if event.direction not in _DIRECTIONS:
        reasons.append(f"direction {event.direction!r} must be CALL, PUT, or NONE")
    if event.event_type not in EVENT_TYPES:
        reasons.append(f"event_type {event.event_type!r} not in vocabulary")
    if event.setup_state not in SETUP_STATES:
        reasons.append(f"setup_state {event.setup_state!r} not in vocabulary")
    if not _is_tz_aware(event.event_at):
        reasons.append("event_at must be timezone-aware")
    if event.provider_updated_at is not None and not _is_tz_aware(event.provider_updated_at):
        reasons.append("provider_updated_at must be timezone-aware when supplied")
    if len(str(event.system_commit_sha or "")) < 7:
        reasons.append("system_commit_sha too short")
    for name in ("contract_facts", "market_context", "observations"):
        value = getattr(event, name)
        if not isinstance(value, Mapping):
            reasons.append(f"{name} must be a mapping")
            continue
        if not _json_safe(dict(value)):
            reasons.append(f"{name} is not JSON-serializable")
        elif _has_non_finite(value):
            reasons.append(f"{name} contains a non-finite number")
    if not isinstance(event.reason_codes, tuple) or not all(isinstance(r, str) for r in event.reason_codes):
        reasons.append("reason_codes must be a tuple of strings")
    return tuple(reasons)


def event_content_hash(event: ForwardOutcomeEvent) -> str:
    """sha256 of the canonical payload; identical events dedupe on it."""
    canonical = json.dumps(event.to_payload(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
