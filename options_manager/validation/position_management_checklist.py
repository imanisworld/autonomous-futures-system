"""options_manager/validation/position_management_checklist.py

Options position management checklist -- Increment 25P. Everything
upstream of this module answers a pre-entry question: is the setup
proven (`proof_packet_intake.py`, 25J)? Is the contract tradable
(`contract_quality_gate.py`, 25L)? Should the trade be taken right now
(`advisory_decision.py`, 25O)? Nothing in this package addresses the
question that starts the moment a position is actually open: given what
the position is doing right now, what should a human check before
deciding to hold, trim, or exit? `management_cases.py` (Increment 23) is
the closest existing module, but it is strictly retrospective -- a
human's own labeled read of a decision already made, after the trade is
closed. This module is prospective: it runs a fixed checklist against
the *current* state of an *open* position and produces one advisory
`PositionAction` recommendation, before any decision has been made.

`evaluate_position_management_checklist()` takes an already-typed
`PositionManagementInput` and returns a `PositionManagementChecklistResult`:
one `ChecklistItemResult` per checklist item (thesis status, invalidation
level, position sizing vs. plan, target proximity, earnings/event risk,
DTE decay, premium decay) plus one overall `PositionAction` of
`CONTINUE_HOLD`, `CONSIDER_TRIM`, `CONSIDER_EXIT`, or `EXIT_REQUIRED`.
`check_position_management_checklist_intake()` is the manual-payload
entry point -- a loose dict, typed in by hand -- that normalizes into a
`PositionManagementInput` and runs the same evaluation. Never raises
regardless of how malformed the payload is, the same non-throwing
pattern established by every `check_*_intake()` function in this
package.

Decision order (first match wins -- more severe conditions are checked
first so a single EXIT-worthy fact is never masked by a later, milder
one):

1. `thesis_status` is `"broken"` -> EXIT_REQUIRED
2. the underlying has crossed the stated invalidation level (direction-
   aware: at/below for a CALL, at/above for a PUT) -> EXIT_REQUIRED
3. `position_sizing` is `"oversized"`, or `current_dollar_risk` exceeds
   `max_dollar_risk` -> CONSIDER_TRIM
4. the underlying has reached `target_2` (when supplied) -> CONSIDER_EXIT
5. the underlying has reached `target_1` -> CONSIDER_TRIM
6. `earnings_before_expiration` is true and `iv_event_risk` is `"high"`
   -> CONSIDER_TRIM
7. `dte` is at or below `DEFAULT_DTE_WARNING_THRESHOLD` -> CONSIDER_TRIM
8. `current_premium` has decayed by `DEFAULT_PREMIUM_DECAY_WARNING_PERCENT`
   or more from `entry_premium` -> CONSIDER_TRIM
9. otherwise -> CONTINUE_HOLD

A malformed payload, a missing required field, or an uncoercible field
value in `check_position_management_checklist_intake()` returns
`EXIT_REQUIRED` naming the problem, with `position=None` -- the same
fail-closed convention `check_contract_quality_intake()` established for
a malformed contract payload (the most severe verdict already defined,
reused rather than inventing a separate "unknown" state).

This module changes nothing about entries, orders, or broker state -- it
has no order or action field of any kind, and every `PositionAction` is
still just an advisory recommendation for a human to act on manually. It
never fetches a quote, a candle, an option chain, or a broker order, and
never reads the system clock. It never places an order, changes a
scanner setting, or promotes anything to
`FixtureStatus.CLEAN_COMPLETE_FIXTURE`, and there is no live/paper
execution pathway anywhere in this module. Performs no I/O of any kind:
no candle fetch, no option-chain fetch, no market-data fetch, no broker
call, no order placement, no execution, no alert sending, no file
access at runtime, no network calls, no MCP calls, no system-clock
reads. Does not import replay/replay_engine.py, the live
context.market_context loader, alert_ranker, options_companion,
execution, webhook, broker systems, options_manager.scanner, or
risk/risk_engine.py.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Optional

from .management_cases import PositionSizing, ThesisStatus

DEFAULT_DTE_WARNING_THRESHOLD = 5
DEFAULT_PREMIUM_DECAY_WARNING_PERCENT = 50.0

_RiskSeverity = Literal["none", "low", "moderate", "high"]
_SEVERITY_VALUES = ("none", "low", "moderate", "high")
_THESIS_STATUS_VALUES = ("intact", "broken", "unknown")
_POSITION_SIZING_VALUES = ("defined_risk", "oversized", "undefined_risk")


class PositionAction(str, Enum):
    """One coordinated advisory recommendation -- always advisory, never
    an order, and never itself a `FixtureStatus` or `GateVerdict`."""

    CONTINUE_HOLD = "continue_hold"
    CONSIDER_TRIM = "consider_trim"
    CONSIDER_EXIT = "consider_exit"
    EXIT_REQUIRED = "exit_required"


class ChecklistItemStatus(str, Enum):
    """One checklist item's own outcome, independent of the overall
    `PositionAction` -- several items can warn or fail at once."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, kw_only=True)
class ChecklistItemResult:
    """One checklist item's evaluation, reported independently of
    whether it ended up driving the overall `PositionAction`."""

    name: str
    status: ChecklistItemStatus
    detail: str = ""


@dataclass(frozen=True, kw_only=True)
class PositionManagementInput:
    """One open position's current state, entirely as reported by the
    human filling this out -- nothing here is fetched from a quote,
    candle, or broker record. `thesis_status` and `position_sizing`
    reuse the same vocabulary `management_cases.py` uses for its
    retrospective grading, so a position's in-flight read and its
    eventual after-the-fact case share one set of terms."""

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
    """Outcome of evaluating (or intaking) a `PositionManagementInput`.
    Contains no order or action field of any kind -- `action` is a
    recommendation for a human to act on manually, not an instruction
    this or any other module executes. `position` is populated only
    when `check_position_management_checklist_intake()` normalized a
    payload cleanly; `evaluate_position_management_checklist()` callers
    already have their own `PositionManagementInput` and can ignore it."""

    action: PositionAction
    checklist_items: tuple[ChecklistItemResult, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    next_required_action: str = ""
    position: Optional[PositionManagementInput] = None
    notes: str = ""


def _invalidation_breached(direction: str, spot: float, invalidation: float) -> bool:
    if direction == "CALL":
        return spot <= invalidation
    return spot >= invalidation


def _target_reached(direction: str, spot: float, target: float) -> bool:
    if direction == "CALL":
        return spot >= target
    return spot <= target


def evaluate_position_management_checklist(
    position: PositionManagementInput,
) -> PositionManagementChecklistResult:
    """Runs the fixed checklist against an already-typed
    `PositionManagementInput`. Every item runs regardless of earlier
    results -- never partially reported. See the module docstring for
    the exact decision order that derives the overall `PositionAction`."""
    invalidation_breached = _invalidation_breached(
        position.direction, position.underlying_spot, position.underlying_invalidation
    )
    target_1_reached = _target_reached(position.direction, position.underlying_spot, position.target_1)
    target_2_reached = position.target_2 is not None and _target_reached(
        position.direction, position.underlying_spot, position.target_2
    )
    risk_over_cap = position.current_dollar_risk > position.max_dollar_risk
    oversized = position.position_sizing == "oversized"
    dte_low = position.dte <= DEFAULT_DTE_WARNING_THRESHOLD
    earnings_event_risk = position.earnings_before_expiration and position.iv_event_risk == "high"
    premium_decay_percent = (
        (position.entry_premium - position.current_premium) / position.entry_premium * 100.0
        if position.entry_premium > 0
        else 0.0
    )
    premium_decayed = premium_decay_percent >= DEFAULT_PREMIUM_DECAY_WARNING_PERCENT

    items: list[ChecklistItemResult] = [
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
            status=ChecklistItemStatus.FAIL if invalidation_breached else ChecklistItemStatus.PASS,
            detail=(
                f"underlying at {position.underlying_spot} has breached invalidation "
                f"{position.underlying_invalidation}"
                if invalidation_breached
                else "invalidation level intact"
            ),
        ),
        ChecklistItemResult(
            name="position_sizing",
            status=ChecklistItemStatus.FAIL if (oversized or risk_over_cap) else ChecklistItemStatus.PASS,
            detail=(
                f"position_sizing={position.position_sizing}, current_dollar_risk="
                f"{position.current_dollar_risk} vs max_dollar_risk={position.max_dollar_risk}"
                if (oversized or risk_over_cap)
                else "within risk plan"
            ),
        ),
        ChecklistItemResult(
            name="target_proximity",
            status=(
                ChecklistItemStatus.WARN
                if (target_1_reached or target_2_reached)
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
            status=ChecklistItemStatus.WARN if earnings_event_risk else ChecklistItemStatus.PASS,
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
                f"dte {position.dte} is at or below the {DEFAULT_DTE_WARNING_THRESHOLD}-day warning threshold"
                if dte_low
                else "dte is above the warning threshold"
            ),
        ),
        ChecklistItemResult(
            name="premium_decay",
            status=ChecklistItemStatus.WARN if premium_decayed else ChecklistItemStatus.PASS,
            detail=(
                f"current_premium {position.current_premium} is down {premium_decay_percent:.1f}% "
                f"from entry_premium {position.entry_premium}"
                if premium_decayed
                else "premium has not decayed past the warning threshold"
            ),
        ),
    ]

    blocking_reasons = tuple(item.detail for item in items if item.status == ChecklistItemStatus.FAIL)
    warnings = tuple(item.detail for item in items if item.status == ChecklistItemStatus.WARN)

    if position.thesis_status == "broken":
        action = PositionAction.EXIT_REQUIRED
        next_required_action = "Thesis is broken -- exit the position."
    elif invalidation_breached:
        action = PositionAction.EXIT_REQUIRED
        next_required_action = "Underlying has breached the stated invalidation level -- exit the position."
    elif oversized or risk_over_cap:
        action = PositionAction.CONSIDER_TRIM
        next_required_action = "Position is outside the risk plan -- trim to get back within max_dollar_risk."
    elif target_2_reached:
        action = PositionAction.CONSIDER_EXIT
        next_required_action = "Target 2 has been reached -- consider exiting the remainder into strength."
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
            "Premium has decayed significantly from entry without the thesis breaking -- "
            "re-check the thesis before continuing to hold."
        )
    else:
        action = PositionAction.CONTINUE_HOLD
        next_required_action = "No checklist item requires action -- continue holding per plan."

    return PositionManagementChecklistResult(
        action=action,
        checklist_items=tuple(items),
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


def check_position_management_checklist_intake(payload: Any) -> PositionManagementChecklistResult:
    """Normalizes a manual dict-like payload into a
    `PositionManagementInput` and evaluates it with
    `evaluate_position_management_checklist()`. Never raises regardless
    of how malformed `payload` is -- a malformed payload, a missing
    required field, or an uncoercible field value returns an
    `EXIT_REQUIRED` result naming the problem, with `position=None`."""
    if not isinstance(payload, Mapping):
        return PositionManagementChecklistResult(
            action=PositionAction.EXIT_REQUIRED,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
            next_required_action="Provide a valid position management payload before relying on this checklist.",
        )

    missing = [name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))]
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
