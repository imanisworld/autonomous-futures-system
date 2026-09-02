"""Pure reducer: causal forward outcome events -> one outcome summary.

Folds the events of ONE thesis, in source-time order, into exactly one of:

    NO_SETUP · SETUP_NOT_TRIGGERED · TRIGGERED_CONTRACT_BLOCKED ·
    TRIGGERED_ACTIONABLE · INVALIDATED · T1_HIT · T2_HIT

plus trigger / invalidation / T1 / T2 timestamps, MFE / MAE and timing
from PRICE_PATH events *after* the trigger, and the market-context and
contract observations that were current at the trigger. Anything not
causally available stays None.

A target level touched without a preceding actionable TRIGGER is theoretical,
not a trade outcome. Events after an INVALIDATION never resurrect the thesis.
Mixed thesis/identity facts, missing timestamps, or unknown vocabulary yield
UNDETERMINED. No I/O, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from .events import EVENT_TYPES, SETUP_STATES, ForwardOutcomeEvent

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
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _minutes(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60.0, 3)


def _identity_value(item: Mapping[str, Any], name: str) -> str:
    value = item.get(name)
    return value.strip().upper() if isinstance(value, str) else ""


def reduce_forward_outcome(events: Sequence[Any]) -> ForwardOutcomeSummary:
    reasons: list[str] = []
    rows: list[tuple[datetime, int, dict[str, Any]]] = []
    thesis_ids: set[str] = set()
    session_ids: set[str] = set()
    tickers: set[str] = set()
    setup_types: set[str] = set()
    timeframes: set[str] = set()
    directional_values: set[str] = set()

    try:
        iterator = enumerate(events)
    except TypeError:
        return ForwardOutcomeSummary(
            thesis_id=None,
            outcome=UNDETERMINED,
            reasons=("events is not a sequence",),
        )

    for index, raw in iterator:
        item = _as_dict(raw)
        if item is None:
            return ForwardOutcomeSummary(
                thesis_id=None,
                outcome=UNDETERMINED,
                reasons=(f"event {index} is not an event",),
            )
        when = _ts(item.get("event_at"))
        if when is None:
            return ForwardOutcomeSummary(
                thesis_id=item.get("thesis_id"),
                outcome=UNDETERMINED,
                reasons=(f"event {index} has no timezone-aware event_at",),
            )
        if item.get("event_type") not in EVENT_TYPES:
            return ForwardOutcomeSummary(
                thesis_id=item.get("thesis_id"),
                outcome=UNDETERMINED,
                reasons=(
                    f"event {index} has unknown event_type {item.get('event_type')!r}",
                ),
            )
        if item.get("setup_state") not in SETUP_STATES:
            return ForwardOutcomeSummary(
                thesis_id=item.get("thesis_id"),
                outcome=UNDETERMINED,
                reasons=(
                    f"event {index} has unknown setup_state {item.get('setup_state')!r}",
                ),
            )

        thesis = _identity_value(item, "thesis_id")
        session = _identity_value(item, "session_id")
        ticker = _identity_value(item, "ticker")
        setup_type = _identity_value(item, "setup_type")
        timeframe = _identity_value(item, "timeframe")
        direction_value = _identity_value(item, "direction")
        if not all((thesis, session, ticker, setup_type, timeframe)):
            return ForwardOutcomeSummary(
                thesis_id=thesis or None,
                outcome=UNDETERMINED,
                reasons=(f"event {index} has incomplete thesis identity",),
            )
        if direction_value not in ("CALL", "PUT", "NONE"):
            return ForwardOutcomeSummary(
                thesis_id=thesis,
                outcome=UNDETERMINED,
                reasons=(f"event {index} has invalid direction {direction_value!r}",),
            )

        thesis_ids.add(thesis)
        session_ids.add(session)
        tickers.add(ticker)
        setup_types.add(setup_type)
        timeframes.add(timeframe)
        if direction_value in ("CALL", "PUT"):
            directional_values.add(direction_value)
        rows.append((when, index, item))

    if not rows:
        return ForwardOutcomeSummary(
            thesis_id=None,
            outcome="NO_SETUP",
            reasons=("no events",),
        )
    if len(thesis_ids) != 1:
        return ForwardOutcomeSummary(
            thesis_id=None,
            outcome=UNDETERMINED,
            reasons=(f"events span thesis ids {sorted(thesis_ids)!r}",),
        )
    thesis_id = next(iter(thesis_ids))
    if any(
        len(values) != 1
        for values in (session_ids, tickers, setup_types, timeframes)
    ):
        return ForwardOutcomeSummary(
            thesis_id=thesis_id,
            outcome=UNDETERMINED,
            reasons=("events disagree on session/ticker/setup/timeframe identity",),
        )
    if len(directional_values) > 1:
        return ForwardOutcomeSummary(
            thesis_id=thesis_id,
            outcome=UNDETERMINED,
            reasons=(f"events disagree on direction {sorted(directional_values)!r}",),
        )

    rows.sort(key=lambda row: (row[0], row[1]))

    outcome = "NO_SETUP"
    setup_seen = False
    direction: Optional[str] = (
        next(iter(directional_values)) if directional_values else None
    )
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

        if kind == "MARKET_CONTEXT":
            latest_market = dict(item.get("market_context") or {})
            continue
        if kind == "CONTRACT_OBSERVATION":
            latest_contract = dict(item.get("contract_facts") or {})
            if "contract_valid" in obs:
                raw_valid = obs["contract_valid"]
                latest_contract["contract_valid"] = (
                    raw_valid if isinstance(raw_valid, bool) else None
                )
                if not isinstance(raw_valid, bool):
                    reasons.append("contract_valid observation was not boolean")
            continue
        if invalidation_at is not None and kind in (
            "TRIGGER",
            "TARGET_1",
            "TARGET_2",
            "SETUP_STATE",
            "PRICE_PATH",
        ):
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
                continue
            setup_seen = True
            trigger_at = when
            entry_price = _finite(obs.get("entry_price", obs.get("level")))
            if entry_price is not None and entry_price <= 0:
                entry_price = None
                reasons.append("trigger entry_price was not positive")
            market_at_trigger = dict(latest_market)
            contract_at_trigger = dict(latest_contract)
            valid = latest_contract.get("contract_valid")
            contract_valid = valid if isinstance(valid, bool) else None
            if contract_valid is True:
                outcome = "TRIGGERED_ACTIONABLE"
            else:
                outcome = "TRIGGERED_CONTRACT_BLOCKED"
                reasons.append(
                    "no valid contract observation at trigger"
                    if contract_valid is None
                    else "contract observation at trigger was not valid"
                )
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
                if high < low:
                    reasons.append(
                        f"PRICE_PATH@{when.isoformat()} high below low; point ignored"
                    )
                    continue
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
            if kind == "TARGET_2" and t2_at is None:
                t2_at = when
            if contract_valid is not True:
                reasons.append(
                    f"{kind}@{when.isoformat()} occurred after a contract-blocked trigger; not promoted to a trade outcome"
                )
                continue
            if kind == "TARGET_1":
                if outcome == "TRIGGERED_ACTIONABLE":
                    outcome = "T1_HIT"
            else:
                outcome = "T2_HIT"
            continue

    mfe = mae = None
    if trigger_at is not None and entry_price is not None and highs and lows:
        if direction == "CALL":
            mfe = round(max(0.0, max(highs) - entry_price), 6)
            mae = round(max(0.0, entry_price - min(lows)), 6)
        elif direction == "PUT":
            mfe = round(max(0.0, entry_price - min(lows)), 6)
            mae = round(max(0.0, max(highs) - entry_price), 6)
    if untriggered:
        reasons.append(
            f"{len(untriggered)} target touch(es) without a trigger: theoretical, not counted"
        )
    if ignored_after_invalidation:
        reasons.append(
            f"{ignored_after_invalidation} event(s) after invalidation ignored"
        )
    if trigger_at is not None and entry_price is None:
        reasons.append("trigger event carried no usable entry_price; MFE/MAE not computable")

    return ForwardOutcomeSummary(
        thesis_id=thesis_id,
        outcome=outcome,
        reasons=tuple(dict.fromkeys(reasons)),
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
