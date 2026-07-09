"""options_manager/validation/advisory_decision.py

Options advisory decision coordinator -- Increment 25O. Every validator
below this module answers one question on its own: is the setup proven
(`proof_packet_intake.py`, 25J)? Is the contract tradable
(`contract_quality_gate.py`, 25L)? Is the candidate actually triggered
(`watchlist_lifecycle.py`, 25N)? Was the morning context captured
(`morning_scan_packet.py`, 25M)? Answering "should I take this trade
right now" still meant calling all four by hand and combining the
answers manually. This module automates exactly that combination --
never the trade itself.

`evaluate_advisory_decision()` takes the already-computed results from
those four validators (the two proof/contract ones are required, the
watchlist/morning-scan ones are optional) and produces one
`AdvisoryDecisionResult` with a single `AdvisoryVerdict` of `TAKE`,
`WAIT`, or `AVOID`. `check_advisory_decision_intake()` is the manual-
payload entry point -- one nested dict
(`{"proof_packet": {...}, "contract_quality": {...},
"watchlist_candidate": {...}, "morning_scan_packet": {...},
"risk_accepted": bool, "notes": str}`) -- that runs the underlying
`check_*_intake()` functions itself and evaluates the result. Never
raises regardless of how malformed the payload is, the same non-
throwing pattern established by every intake function in this package.

Decision order (first match wins -- more severe conditions are checked
first so a single AVOID-worthy fact is never masked by a later, milder
one):

1. Proof packet invalid -> AVOID
2. Contract quality BLOCK -> AVOID
3. Watchlist status is INVALIDATED/SKIPPED/EXITED/EXPIRED -> AVOID
   (a terminal watchlist status is disqualifying regardless of whether
   the proof and contract checks would otherwise pass)
4. Watchlist payload supplied but unreadable -> WAIT (status unknown)
5. Watchlist status is WATCHING (not yet TRIGGERED) -> WAIT
6. Morning scan packet supplied but invalid (missing market context,
   or a candidate failure) -> WAIT
7. Contract quality WARN and `risk_accepted` is not explicitly set ->
   WAIT
8. Otherwise -> TAKE

`no_trade_reasons` is populated only when the verdict is `AVOID`,
derived automatically from the proof packet's own missing/blocking
fields (via `no_trade_reasons.reasons_from_intake_result()`) plus a
best-effort mapping of the contract gate's blocking reasons onto the
same `NoTradeReason` vocabulary -- so an AVOID verdict never needs to be
re-diagnosed by hand.

This coordinator changes nothing about entries, orders, or broker
state -- it has no order or action fields of any kind, and a `TAKE`
verdict is still just an advisory recommendation for a human to act on
manually. It never fetches a quote, a candle, an option chain, or a
broker order, and never reads the system clock. It never places an
order, changes a scanner setting, or promotes anything to
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

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .contract_quality_gate import ContractQualityResult, GateVerdict, check_contract_quality_intake
from .morning_scan_packet import MorningScanPacketResult, check_morning_scan_packet_intake
from .no_trade_reasons import NoTradeReason, reasons_from_intake_result
from .proof_packet_intake import IntakeResult, check_proof_packet_intake
from .watchlist_lifecycle import WatchlistCandidateResult, WatchlistCandidateStatus, check_watchlist_candidate_intake

_TERMINAL_WATCHLIST_STATUSES = frozenset(
    (
        WatchlistCandidateStatus.INVALIDATED,
        WatchlistCandidateStatus.SKIPPED,
        WatchlistCandidateStatus.EXITED,
        WatchlistCandidateStatus.EXPIRED,
    )
)


class AdvisoryVerdict(str, Enum):
    """A single combined recommendation -- always advisory, never an
    order, and never itself a `FixtureStatus`."""

    TAKE = "take"
    WAIT = "wait"
    AVOID = "avoid"


@dataclass(frozen=True, kw_only=True)
class AdvisoryDecisionResult:
    """One coordinated advisory decision. Contains no order or action
    field of any kind -- `verdict` is a recommendation for a human to
    act on manually, not an instruction this or any other module
    executes."""

    verdict: AdvisoryVerdict
    proof_valid: bool
    contract_verdict: GateVerdict
    watchlist_status: Optional[WatchlistCandidateStatus] = None
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    no_trade_reasons: tuple[NoTradeReason, ...] = ()
    next_required_action: str = ""
    notes: str = ""


def _contract_reason_to_no_trade_reason(reason: str) -> NoTradeReason:
    """Best-effort mapping of a contract_quality_gate blocking reason
    onto the no_trade_reasons vocabulary. Deliberately conservative --
    anything not clearly recognized maps to OTHER rather than being
    force-fit onto the wrong reason."""
    lowered = reason.lower()
    if "spread too wide" in lowered or "spread_percent" in lowered:
        return NoTradeReason.WIDE_SPREAD
    if "volume" in lowered:
        return NoTradeReason.LOW_VOLUME
    if "open_interest" in lowered or "open interest" in lowered:
        return NoTradeReason.LOW_OPEN_INTEREST
    if "dte" in lowered:
        return NoTradeReason.TOO_SHORT_DTE
    if "premium" in lowered and ("exceeds" in lowered or "cap" in lowered):
        return NoTradeReason.PREMIUM_TOO_EXPENSIVE
    if "max_dollar_risk" in lowered:
        return NoTradeReason.RISK_TOO_HIGH
    if "bid" in lowered or "ask" in lowered or "strike" in lowered or "max_contracts" in lowered:
        return NoTradeReason.MISSING_CONTRACT_DATA
    return NoTradeReason.OTHER


def _no_trade_reasons_for_avoid(
    proof_result: IntakeResult, contract_result: ContractQualityResult
) -> tuple[NoTradeReason, ...]:
    reasons: list[NoTradeReason] = list(reasons_from_intake_result(proof_result))
    seen = set(reasons)
    for blocking_reason in contract_result.blocking_reasons:
        mapped = _contract_reason_to_no_trade_reason(blocking_reason)
        if mapped not in seen:
            seen.add(mapped)
            reasons.append(mapped)
    if not reasons:
        reasons.append(NoTradeReason.OTHER)
    return tuple(reasons)


def evaluate_advisory_decision(
    proof_result: IntakeResult,
    contract_result: ContractQualityResult,
    watchlist_result: Optional[WatchlistCandidateResult] = None,
    morning_scan_result: Optional[MorningScanPacketResult] = None,
    *,
    risk_accepted: bool = False,
    notes: str = "",
) -> AdvisoryDecisionResult:
    """Combines already-computed validator results into one advisory
    verdict. See the module docstring for the exact decision order."""
    blocking_reasons: list[str] = [f"proof packet: missing {name}" for name in proof_result.missing_fields]
    blocking_reasons.extend(f"proof packet: {reason}" for reason in proof_result.blocking_reasons)
    blocking_reasons.extend(f"contract quality: {reason}" for reason in contract_result.blocking_reasons)

    warnings: list[str] = [f"proof packet: {warning}" for warning in proof_result.warnings]
    warnings.extend(f"contract quality: {warning}" for warning in contract_result.warnings)

    watchlist_status = (
        watchlist_result.candidate.status
        if watchlist_result is not None and watchlist_result.candidate is not None
        else None
    )

    if not proof_result.valid:
        verdict = AdvisoryVerdict.AVOID
        next_required_action = "Complete the proof packet before proceeding (see blocking_reasons)."
    elif contract_result.verdict == GateVerdict.BLOCK:
        verdict = AdvisoryVerdict.AVOID
        next_required_action = "Resolve contract quality blocks before proceeding (see blocking_reasons)."
    elif watchlist_status in _TERMINAL_WATCHLIST_STATUSES:
        verdict = AdvisoryVerdict.AVOID
        next_required_action = f"Candidate is already {watchlist_status.value} -- no further action available."
    elif watchlist_result is not None and watchlist_result.candidate is None:
        verdict = AdvisoryVerdict.WAIT
        next_required_action = "Watchlist payload could not be read -- confirm candidate status before proceeding."
        warnings.append("watchlist candidate status could not be determined")
    elif watchlist_status == WatchlistCandidateStatus.WATCHING:
        verdict = AdvisoryVerdict.WAIT
        next_required_action = "Wait for the entry trigger to fire (candidate is still WATCHING)."
    elif morning_scan_result is not None and not morning_scan_result.valid:
        verdict = AdvisoryVerdict.WAIT
        next_required_action = "Confirm missing morning market context before proceeding."
        warnings.append("morning scan packet is incomplete or invalid")
    elif contract_result.verdict == GateVerdict.WARN and not risk_accepted:
        verdict = AdvisoryVerdict.WAIT
        next_required_action = "Review contract quality warnings, or explicitly accept the risk to proceed."
    else:
        verdict = AdvisoryVerdict.TAKE
        next_required_action = "Proceed manually per this advisory verdict -- no order is placed automatically."

    no_trade_reasons = (
        _no_trade_reasons_for_avoid(proof_result, contract_result)
        if verdict == AdvisoryVerdict.AVOID
        else ()
    )

    return AdvisoryDecisionResult(
        verdict=verdict,
        proof_valid=proof_result.valid,
        contract_verdict=contract_result.verdict,
        watchlist_status=watchlist_status,
        blocking_reasons=tuple(blocking_reasons),
        warnings=tuple(warnings),
        no_trade_reasons=no_trade_reasons,
        next_required_action=next_required_action,
        notes=notes,
    )


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


def check_advisory_decision_intake(payload: Any) -> AdvisoryDecisionResult:
    """Normalizes a manual nested dict payload
    (`{"proof_packet": {...}, "contract_quality": {...},
    "watchlist_candidate": {...}, "morning_scan_packet": {...},
    "risk_accepted": bool, "notes": str}`) and evaluates it. Never
    raises regardless of how malformed `payload` is -- an unreadable
    top-level payload, or unreadable required sub-payloads, resolves to
    `AVOID` rather than an exception, since `check_proof_packet_intake()`
    and `check_contract_quality_intake()` already handle malformed
    input for their own sections without raising."""
    if not isinstance(payload, Mapping):
        return AdvisoryDecisionResult(
            verdict=AdvisoryVerdict.AVOID,
            proof_valid=False,
            contract_verdict=GateVerdict.BLOCK,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
            next_required_action="Provide a valid advisory decision payload.",
        )

    proof_result = check_proof_packet_intake(payload.get("proof_packet"))
    contract_result = check_contract_quality_intake(payload.get("contract_quality"))

    watchlist_result: Optional[WatchlistCandidateResult] = None
    if payload.get("watchlist_candidate") is not None:
        watchlist_result = check_watchlist_candidate_intake(payload["watchlist_candidate"])

    morning_scan_result: Optional[MorningScanPacketResult] = None
    if payload.get("morning_scan_packet") is not None:
        morning_scan_result = check_morning_scan_packet_intake(payload["morning_scan_packet"])

    raw_risk_accepted = payload.get("risk_accepted", False)
    try:
        risk_accepted = _coerce_bool(raw_risk_accepted) if raw_risk_accepted is not None else False
    except ValueError:
        risk_accepted = False

    notes = str(payload["notes"]) if payload.get("notes") is not None else ""

    return evaluate_advisory_decision(
        proof_result,
        contract_result,
        watchlist_result,
        morning_scan_result,
        risk_accepted=risk_accepted,
        notes=notes,
    )
