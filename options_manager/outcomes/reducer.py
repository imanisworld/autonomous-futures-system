"""Pure reducer: causal forward outcome events -> one outcome summary.

Folds the events of ONE thesis, in source-time order, into exactly one of:

    NO_SETUP · SETUP_NOT_TRIGGERED · TRIGGERED_CONTRACT_BLOCKED ·
    TRIGGERED_ACTIONABLE · INVALIDATED · T1_HIT · T2_HIT

plus trigger / invalidation / T1 / T2 timestamps, MFE / MAE and timing
from PRICE_PATH events *after* the trigger, and the market-context and
contract observations that were current at the trigger. Anything not
causally available stays None.

A target level touched without a preceding TRIGGER event is a
theoretical move, not a trade: it is counted in
``untriggered_target_touches`` and never becomes T1_HIT / T2_HIT.
Events after an INVALIDATION never resurrect the thesis. Mixed thesis
ids, missing timestamps, or unknown vocabulary yield UNDETERMINED.
No I/O, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from .events import EVENT_TYPES, ForwardOutcomeEvent

UNDETERMINED = "UNDETERMINED"
OUTCOMES: tuple[str, ...] = (
    "NO_SETUP",
    "SETUP_NOT_TRIGGERED",
    "TRIGGERED_CONTRACT_BLOCKED",
    "TRIGGERED_ACTIONABLE",
    "INVALIDATED",
    "T1_HIT",
    "T2_HIT",
    UNDETERMINED,
)


@dataclass(frozen=True, kw_only=True)
class ForwardOutcomeSummary:
    thesis_id: Optional[str]
    outcome: str
    reasons: tuple[str, ...] = ()
    event_count: int = 0
    first_event_at: Optional[str] = None
    last_event_at: Optional[str] = None
    setup_seen: bool = False
    trigger_at: Optional[str] = None
    entry_price: Optional[float] = None
    direction: Optional[str] = None
    contract_valid_at_trigger: Optional[bool] = None
    invalidation_at: Optional[str] = None
    t1_at: Optional[str] = None
    t2_at: Optional[str] = None
    minutes_to_t1: Optional[float] = None
    minutes_to_t2: Optional[float] = None
    minutes_to_invalidation: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
    price_path_points: int = 0
    untriggered_target_touches: tuple[str, ...] = ()
    post_invalidation_events_ignored: int = 0
    market_context_at_trigger: Mapping[str, Any] = field(default_factory=dict)
    contract_at_trigger: Mapping[str, Any] = field(default_factory=dict)


def _as_dict(event: Any) -> Optional[dict[str, Any]]:
    if isinstance(event, ForwardOutcomeEvent):
        return event.to_payload()
    if isinstance(event, Mapping):
        return dict(event)
    return None


def _ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _minutes(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60.0, 3)


def reduce_forward_outcome(events: Sequence[Any]) -> ForwardOutcomeSummary:
    reasons: list[str] = []
    rows: list[tuple[datetime, int, dict[str, Any]]] = []
    thesis_ids: set[str] = set()
    for index, raw in enumerate(events):
        item = _as_dict(raw)
        if item is None:
            return ForwardOutcomeSummary(thesis_id=None, outcome=UNDETERMINED, reasons=(f"event {index} is not an event",))
        when = _ts(item.get("event_at"))
        if when is None:
            return ForwardOutcomeSummary(thesis_id=item.get("thesis_id"), outcome=UNDETERMINED, reasons=(f"event {index} has no timezone-aware event_at",))
        if item.get("event_type") not in EVENT_TYPES:
            return ForwardOutcomeSummary(thesis_id=item.get("thesis_id"), outcome=UNDETERMINED, reasons=(f"event {index} has unknown event_type {item.get('event_type')!r}",))
        thesis_ids.add(str(item.get("thesis_id") or ""))
        rows.append((when, index, item))
    if not rows:
        return ForwardOutcomeSummary(thesis_id=None, outcome="NO_SETUP", reasons=("no events",))
    if len(thesis_ids) != 1 or "" in thesis_ids:
        return ForwardOutcomeSummary(thesis_id=None, outcome=UNDETERMINED, reasons=(f"events span thesis ids {sorted(thesis_ids)!r}",))
    thesis_id = next(iter(thesis_ids))
    rows.sort(key=lambda r: (r[0], r[1]))

    outcome = "NO_SETUP"
    setup_seen = False
    direction: Optional[str] = None
    trigger_at: Optional[datetime] = None
    entry_price: Optional[float] = None
    contract_valid: Optional[bool] = None
    invalidation_at: Optional[datetime] = None
    t1_at: Optional[datetime] = None
    t2_at: Optional[datetime] = None
    untriggered: list[str] = []
    ignored_after_invalidation = 0
    latest_market: dict[str, Any] = {}
    latest_contract: dict[str, Any] = {}
    market_at_trigger: dict[str, Any] = {}
    contract_at_trigger: dict[str, Any] = {}
    highs: list[float] = []
    lows: list[float] = []
    path_points = 0

    for when, _index, item in rows:
        kind = item["event_type"]
        obs = item.get("observations") if isinstance(item.get("observations"), Mapping) else {}
        if item.get("direction") in ("CALL", "PUT"):
            direction = item["direction"]
        if kind == "MARKET_CONTEXT":
            latest_market = dict(item.get("market_context") or {})
            continue
        if kind == "CONTRACT_OBSERVATION":
            latest_contract = dict(item.get("contract_facts") or {})
            if "contract_valid" in obs:
                latest_contract["contract_valid"] = bool(obs["contract_valid"])
            continue
        if invalidation_at is not None and kind in ("TRIGGER", "TARGET_1", "TARGET_2", "SETUP_STATE", "PRICE_PATH"):
            ignored_after_invalidation += 1
            continue
        if kind == "SETUP_STATE":
            state = item.get("setup_state")
            if state == "SETUP_NOT_TRIGGERED" and trigger_at is None:
                setup_seen = True
                outcome = "SETUP_NOT_TRIGGERED"
            elif state == "NO_SETUP" and trigger_at is None:
                outcome = "NO_SETUP"
            continue
        if kind == "TRIGGER":
            if trigger_at is not None:
                continue  # the first trigger is the trigger
            setup_seen = True
            trigger_at = when
            entry_price = _finite(obs.get("entry_price", obs.get("level")))
            market_at_trigger = dict(latest_market)
            contract_at_trigger = dict(latest_contract)
            valid = latest_contract.get("contract_valid")
            contract_valid = bool(valid) if isinstance(valid, bool) else None
            if contract_valid is True:
                outcome = "TRIGGERED_ACTIONABLE"
            else:
                outcome = "TRIGGERED_CONTRACT_BLOCKED"
                reasons.append("no valid contract observation at trigger" if contract_valid is None else "contract observation at trigger was not valid")
            continue
        if kind == "INVALIDATION":
            invalidation_at = when
            outcome = "INVALIDATED"
            continue
        if kind == "PRICE_PATH":
            if trigger_at is None:
                continue
            high = _finite(obs.get("high", obs.get("price")))
            low = _finite(obs.get("low", obs.get("price")))
            if high is not None and low is not None:
                highs.append(high)
                lows.append(low)
                path_points += 1
            continue
        if kind in ("TARGET_1", "TARGET_2"):
            if trigger_at is None:
                untriggered.append(f"{kind}@{when.isoformat()}")
                continue
            if kind == "TARGET_1" and t1_at is None:
                t1_at = when
                if outcome in ("TRIGGERED_ACTIONABLE", "TRIGGERED_CONTRACT_BLOCKED"):
                    outcome = "T1_HIT"
            if kind == "TARGET_2" and t2_at is None:
                t2_at = when
                outcome = "T2_HIT"
            continue

    mfe = mae = None
    if trigger_at is not None and entry_price is not None and highs and lows:
        if direction == "CALL":
            mfe, mae = round(max(highs) - entry_price, 6), round(entry_price - min(lows), 6)
        elif direction == "PUT":
            mfe, mae = round(entry_price - min(lows), 6), round(max(highs) - entry_price, 6)
    if untriggered:
        reasons.append(f"{len(untriggered)} target touch(es) without a trigger: theoretical, not counted")
    if ignored_after_invalidation:
        reasons.append(f"{ignored_after_invalidation} event(s) after invalidation ignored")
    if trigger_at is not None and entry_price is None:
        reasons.append("trigger event carried no entry_price; MFE/MAE not computable")

    return ForwardOutcomeSummary(
        thesis_id=thesis_id,
        outcome=outcome,
        reasons=tuple(reasons),
        event_count=len(rows),
        first_event_at=rows[0][0].isoformat(),
        last_event_at=rows[-1][0].isoformat(),
        setup_seen=setup_seen,
        trigger_at=trigger_at.isoformat() if trigger_at else None,
        entry_price=entry_price,
        direction=direction,
        contract_valid_at_trigger=contract_valid,
        invalidation_at=invalidation_at.isoformat() if invalidation_at else None,
        t1_at=t1_at.isoformat() if t1_at else None,
        t2_at=t2_at.isoformat() if t2_at else None,
        minutes_to_t1=_minutes(trigger_at, t1_at),
        minutes_to_t2=_minutes(trigger_at, t2_at),
        minutes_to_invalidation=_minutes(trigger_at, invalidation_at),
        mfe=mfe,
        mae=mae,
        price_path_points=path_points,
        untriggered_target_touches=tuple(untriggered),
        post_invalidation_events_ignored=ignored_after_invalidation,
        market_context_at_trigger=market_at_trigger,
        contract_at_trigger=contract_at_trigger,
    )
