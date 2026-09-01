"""Advisory checklist for managing an already-open options position.

The checklist is pure and non-executable. It reports whether the thesis,
invalidation, sizing, targets, event risk, DTE, or premium decay require a
human reassessment. It never submits, changes, or prepares an order.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from .management_cases import PositionSizing, ThesisStatus

DEFAULT_DTE_WARNING_THRESHOLD = 5
DEFAULT_PREMIUM_DECAY_WARNING_PERCENT = 25.0

_RiskSeverity = Literal["none", "low", "moderate", "high"]
_SEVERITY_VALUES = ("none", "low", "moderate", "high")
_THESIS_STATUS_VALUES = ("intact", "broken", "unknown")
_POSITION_SIZING_VALUES = ("defined_risk", "oversized", "undefined_risk")


class PositionAction(str, Enum):
    CONTINUE_HOLD = "continue_hold"
    CONSIDER_TRIM = "consider_trim"
    CONSIDER_EXIT = "consider_exit"
    EXIT_REQUIRED = "exit_required"


class ChecklistItemStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, kw_only=True)
class ChecklistItemResult:
    name: str
    status: ChecklistItemStatus
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class PositionManagementInput:
    ticker: str
    direction: Literal["CALL", "PUT"]
    strike: float
    expiration: str
    dte: int
    entry_premium: float
    current_premium: float
    contracts_held: int
    underlying_spot: float
    underlying_invalidation: float
    target_1: float
    thesis_status: ThesisStatus
    position_sizing: PositionSizing
    max_dollar_risk: float
    current_dollar_risk: float
    iv_event_risk: _RiskSeverity
    earnings_before_expiration: bool
    target_2: Optional[float] = None
    notes: str = ""


@dataclass(frozen=True, kw_only=True)
class PositionManagementChecklistResult:
    action: PositionAction
    checklist_items: tuple[ChecklistItemResult, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    next_required_action: str = ""
    position: Optional[PositionManagementInput] = None
    notes: str = ""


def _invalidation_breached(direction: str, spot: float, invalidation: float) -> bool:
    return spot <= invalidation if direction == "CALL" else spot >= invalidation


def _target_reached(direction: str, spot: float, target: float) -> bool:
    return spot >= target if direction == "CALL" else spot <= target


def evaluate_position_management_checklist(
    position: PositionManagementInput,
) -> PositionManagementChecklistResult:
    invalidation_breached = _invalidation_breached(
        position.direction, position.underlying_spot, position.underlying_invalidation
    )
    target_1_reached = _target_reached(
        position.direction, position.underlying_spot, position.target_1
    )
    target_2_reached = position.target_2 is not None and _target_reached(
        position.direction, position.underlying_spot, position.target_2
    )
    risk_over_cap = position.current_dollar_risk > position.max_dollar_risk
    oversized = position.position_sizing == "oversized"
    dte_low = position.dte <= DEFAULT_DTE_WARNING_THRESHOLD
    earnings_event_risk = (
        position.earnings_before_expiration and position.iv_event_risk == "high"
    )
    premium_decay_percent = (
        (position.entry_premium - position.current_premium)
        / position.entry_premium
        * 100.0
        if position.entry_premium > 0
        else 0.0
    )
    premium_decayed = premium_decay_percent >= DEFAULT_PREMIUM_DECAY_WARNING_PERCENT

    items = (
        ChecklistItemResult(
            name="thesis_status",
            status=(
                ChecklistItemStatus.FAIL
                if position.thesis_status == "broken"
                else ChecklistItemStatus.WARN
                if position.thesis_status == "unknown"
                else ChecklistItemStatus.PASS
            ),
            detail=f"thesis_status is {position.thesis_status}",
        ),
        ChecklistItemResult(
            name="invalidation_level",
            status=(
                ChecklistItemStatus.FAIL
                if invalidation_breached
                else ChecklistItemStatus.PASS
            ),
            detail=(
                f"underlying at {position.underlying_spot} has breached invalidation "
                f"{position.underlying_invalidation}"
                if invalidation_breached
                else "invalidation level intact"
            ),
        ),
        ChecklistItemResult(
            name="position_sizing",
            status=(
                ChecklistItemStatus.FAIL
                if oversized or risk_over_cap
                else ChecklistItemStatus.PASS
            ),
            detail=(
                f"position_sizing={position.position_sizing}, current_dollar_risk="
                f"{position.current_dollar_risk} vs max_dollar_risk={position.max_dollar_risk}"
                if oversized or risk_over_cap
                else "within risk plan"
            ),
        ),
        ChecklistItemResult(
            name="target_proximity",
            status=(
                ChecklistItemStatus.WARN
                if target_1_reached or target_2_reached
                else ChecklistItemStatus.PASS
            ),
            detail=(
                f"target_2 ({position.target_2}) reached"
                if target_2_reached
                else f"target_1 ({position.target_1}) reached"
                if target_1_reached
                else "no target reached yet"
            ),
        ),
        ChecklistItemResult(
            name="earnings_event_risk",
            status=(
                ChecklistItemStatus.WARN
                if earnings_event_risk
                else ChecklistItemStatus.PASS
            ),
            detail=(
                "earnings before expiration with high IV/event risk"
                if earnings_event_risk
                else "no high-severity earnings/event risk before expiration"
            ),
        ),
        ChecklistItemResult(
            name="dte_decay",
            status=ChecklistItemStatus.WARN if dte_low else ChecklistItemStatus.PASS,
            detail=(
                f"dte {position.dte} is at or below the "
                f"{DEFAULT_DTE_WARNING_THRESHOLD}-day warning threshold"
                if dte_low
                else "dte is above the warning threshold"
            ),
        ),
        ChecklistItemResult(
            name="premium_decay",
            status=(
                ChecklistItemStatus.WARN
                if premium_decayed
                else ChecklistItemStatus.PASS
            ),
            detail=(
                f"current_premium {position.current_premium} is down "
                f"{premium_decay_percent:.1f}% from entry_premium {position.entry_premium}"
                if premium_decayed
                else "premium has not decayed past the warning threshold"
            ),
        ),
    )

    blocking_reasons = tuple(
        item.detail for item in items if item.status == ChecklistItemStatus.FAIL
    )
    warnings = tuple(
        item.detail for item in items if item.status == ChecklistItemStatus.WARN
    )

    if position.thesis_status == "broken":
        action = PositionAction.EXIT_REQUIRED
        next_required_action = "Thesis is broken -- exit the position."
    elif invalidation_breached:
        action = PositionAction.EXIT_REQUIRED
        next_required_action = (
            "Underlying has breached the stated invalidation level -- exit the position."
        )
    elif oversized or risk_over_cap:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = (
            "Position is outside the risk plan -- trim to get back within max_dollar_risk."
        )
    elif target_2_reached:
        action = PositionAction.CONSIDER_EXIT
        next_required_action = (
            "Target 2 has been reached -- consider exiting the remainder into strength."
        )
    elif target_1_reached:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = "Target 1 has been reached -- consider trimming into strength."
    elif earnings_event_risk:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = (
            "Earnings/event risk is high before expiration -- consider trimming or closing ahead of the event."
        )
    elif dte_low:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = (
            f"DTE is at or below {DEFAULT_DTE_WARNING_THRESHOLD} -- theta decay is accelerating, "
            "consider trimming or closing."
        )
    elif premium_decayed:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = (
            "Premium is down at least 25% from entry -- reassess the thesis immediately; "
            "trim or exit if the setup has weakened, and exit if underlying invalidation is hit."
        )
    else:
        action = PositionAction.CONTINUE_HOLD
        next_required_action = "No checklist item requires action -- continue holding per plan."

    return PositionManagementChecklistResult(
        action=action,
        checklist_items=items,
        blocking_reasons=blocking_reasons,
        warnings=warnings,
        next_required_action=next_required_action,
        position=position,
        notes=position.notes,
    )


_STR_FIELDS = ("ticker", "expiration")
_FLOAT_FIELDS = (
    "strike",
    "entry_premium",
    "current_premium",
    "underlying_spot",
    "underlying_invalidation",
    "target_1",
    "max_dollar_risk",
    "current_dollar_risk",
)
_INT_FIELDS = ("dte", "contracts_held")

_REQUIRED_FIELD_NAMES = tuple(
    f.name
    for f in dataclasses.fields(PositionManagementInput)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _coerce_direction(value: Any) -> str:
    text = str(value).strip().upper()
    if text not in ("CALL", "PUT"):
        raise ValueError(f"{value!r} is not CALL or PUT")
    return text


def _coerce_choice(value: Any, valid: tuple[str, ...]) -> str:
    text = str(value).strip().lower()
    if text not in valid:
        raise ValueError(f"{value!r} is not one of {valid}")
    return text


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
    raise ValueError(f"{value!r} is not a valid boolean")


def check_position_management_checklist_intake(
    payload: Any,
) -> PositionManagementChecklistResult:
    if not isinstance(payload, Mapping):
        return PositionManagementChecklistResult(
            action=PositionAction.EXIT_REQUIRED,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
            next_required_action=(
                "Provide a valid position management payload before relying on this checklist."
            ),
        )

    missing = [
        name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))
    ]
    if missing:
        return PositionManagementChecklistResult(
            action=PositionAction.EXIT_REQUIRED,
            blocking_reasons=tuple(f"missing {name}" for name in missing),
            next_required_action="Supply the missing fields before relying on this checklist.",
        )

    coercion_errors: list[str] = []
    normalized: dict[str, Any] = {}

    for name in _REQUIRED_FIELD_NAMES:
        raw_value = payload[name]
        try:
            if name in _STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _FLOAT_FIELDS:
                normalized[name] = float(raw_value)
            elif name in _INT_FIELDS:
                normalized[name] = int(raw_value)
            elif name == "direction":
                normalized[name] = _coerce_direction(raw_value)
            elif name == "thesis_status":
                normalized[name] = _coerce_choice(raw_value, _THESIS_STATUS_VALUES)
            elif name == "position_sizing":
                normalized[name] = _coerce_choice(raw_value, _POSITION_SIZING_VALUES)
            elif name == "iv_event_risk":
                normalized[name] = _coerce_choice(raw_value, _SEVERITY_VALUES)
            elif name == "earnings_before_expiration":
                normalized[name] = _coerce_bool(raw_value)
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    if _is_present(payload.get("target_2")):
        try:
            normalized["target_2"] = float(payload["target_2"])
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for target_2: {exc}")

    if "notes" in payload and payload["notes"] is not None:
        normalized["notes"] = str(payload["notes"])

    if coercion_errors:
        return PositionManagementChecklistResult(
            action=PositionAction.EXIT_REQUIRED,
            blocking_reasons=tuple(coercion_errors),
            next_required_action="Fix the invalid fields before relying on this checklist.",
        )

    position = PositionManagementInput(**normalized)
    return evaluate_position_management_checklist(position)
