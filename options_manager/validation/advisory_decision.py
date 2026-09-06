"""Pure coordinator for options-manager advisory TAKE / WAIT / AVOID decisions.

The coordinator combines setup proof, contract quality, optional portfolio
risk, watchlist state, and morning context. It performs no I/O and never
changes broker or scanner state. Canonical API callers require a portfolio
snapshot; legacy/manual callers may omit it for backward-compatible analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .contract_quality_gate import ContractQualityResult, GateVerdict, check_contract_quality_intake
from .morning_scan_packet import MorningScanPacketResult, check_morning_scan_packet_intake
from .no_trade_reasons import NoTradeReason, reasons_from_intake_result
from .portfolio_risk_gate import (
    AGGREGATE_RISK_BUDGET_UNCONFIGURED,
    DEFAULT_MAX_TRADE_RISK_DOLLARS,
    PortfolioRiskResult,
    PortfolioRiskVerdict,
    check_portfolio_risk_intake,
)
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
    TAKE = "take"
    WAIT = "wait"
    AVOID = "avoid"


@dataclass(frozen=True, kw_only=True)
class AdvisoryDecisionResult:
    verdict: AdvisoryVerdict
    proof_valid: bool
    contract_verdict: GateVerdict
    portfolio_verdict: PortfolioRiskVerdict = PortfolioRiskVerdict.PASS
    watchlist_status: Optional[WatchlistCandidateStatus] = None
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    no_trade_reasons: tuple[NoTradeReason, ...] = ()
    next_required_action: str = ""
    notes: str = ""


def _contract_reason_to_no_trade_reason(reason: str) -> NoTradeReason:
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
    if "max_dollar_risk" in lowered or "planned premium-stop risk" in lowered:
        return NoTradeReason.RISK_TOO_HIGH
    if "bid" in lowered or "ask" in lowered or "strike" in lowered or "max_contracts" in lowered:
        return NoTradeReason.MISSING_CONTRACT_DATA
    return NoTradeReason.OTHER


def _no_trade_reasons_for_avoid(
    proof_result: IntakeResult,
    contract_result: ContractQualityResult,
    portfolio_result: Optional[PortfolioRiskResult],
) -> tuple[NoTradeReason, ...]:
    reasons: list[NoTradeReason] = list(reasons_from_intake_result(proof_result))
    seen = set(reasons)
    for blocking_reason in contract_result.blocking_reasons:
        mapped = _contract_reason_to_no_trade_reason(blocking_reason)
        if mapped not in seen:
            seen.add(mapped)
            reasons.append(mapped)
    if portfolio_result is not None and portfolio_result.verdict == PortfolioRiskVerdict.BLOCK:
        # An unconfigured budget is not "risk too high" -- the risk was never
        # measured against anything. Say OTHER and let blocking_reasons carry
        # the specific cause, rather than mislabel a config gap as a risk call.
        unconfigured = any(
            reason == AGGREGATE_RISK_BUDGET_UNCONFIGURED
            for reason in portfolio_result.blocking_reasons
        )
        only_unconfigured = unconfigured and len(portfolio_result.blocking_reasons) == 1
        mapped = NoTradeReason.OTHER if only_unconfigured else NoTradeReason.RISK_TOO_HIGH
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
    portfolio_result: Optional[PortfolioRiskResult] = None,
    risk_accepted: bool = False,
    notes: str = "",
) -> AdvisoryDecisionResult:
    """Combine validator outputs into one human-facing advisory verdict."""
    blocking_reasons: list[str] = [
        f"proof packet: missing {name}" for name in proof_result.missing_fields
    ]
    blocking_reasons.extend(f"proof packet: {reason}" for reason in proof_result.blocking_reasons)
    blocking_reasons.extend(
        f"contract quality: {reason}" for reason in contract_result.blocking_reasons
    )
    if portfolio_result is not None:
        blocking_reasons.extend(
            f"portfolio risk: {reason}" for reason in portfolio_result.blocking_reasons
        )

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
    elif portfolio_result is not None and portfolio_result.verdict == PortfolioRiskVerdict.BLOCK:
        verdict = AdvisoryVerdict.AVOID
        next_required_action = "Resolve portfolio-risk blocks before proceeding (see blocking_reasons)."
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
        next_required_action = "Proceed manually per this advisory verdict; no automated action follows."

    no_trade_reasons = (
        _no_trade_reasons_for_avoid(proof_result, contract_result, portfolio_result)
        if verdict == AdvisoryVerdict.AVOID
        else ()
    )

    return AdvisoryDecisionResult(
        verdict=verdict,
        proof_valid=proof_result.valid,
        contract_verdict=contract_result.verdict,
        portfolio_verdict=(
            portfolio_result.verdict
            if portfolio_result is not None
            else PortfolioRiskVerdict.PASS
        ),
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


def check_advisory_decision_intake(
    payload: Any,
    *,
    require_portfolio_risk: bool = False,
    max_trade_risk_dollars: float = DEFAULT_MAX_TRADE_RISK_DOLLARS,
    max_aggregate_open_risk_dollars: float | None = None,
) -> AdvisoryDecisionResult:
    """Normalize one nested advisory payload and evaluate it without raising.

    `require_portfolio_risk=True` is the canonical API mode. The default remains
    false so existing manual validation fixtures can continue to evaluate old
    evidence that predates portfolio snapshots.

    `max_aggregate_open_risk_dollars` has no default. Whenever portfolio risk
    is evaluated and the budget is None, the portfolio gate blocks and the
    verdict cannot be TAKE.
    """
    if not isinstance(payload, Mapping):
        return AdvisoryDecisionResult(
            verdict=AdvisoryVerdict.AVOID,
            proof_valid=False,
            contract_verdict=GateVerdict.BLOCK,
            portfolio_verdict=(
                PortfolioRiskVerdict.BLOCK
                if require_portfolio_risk
                else PortfolioRiskVerdict.PASS
            ),
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
            next_required_action="Provide a valid advisory decision payload.",
        )

    proof_result = check_proof_packet_intake(payload.get("proof_packet"))
    contract_result = check_contract_quality_intake(payload.get("contract_quality"))

    portfolio_result: Optional[PortfolioRiskResult] = None
    if require_portfolio_risk or "portfolio_risk" in payload:
        portfolio_result = check_portfolio_risk_intake(
            payload.get("portfolio_risk"),
            proof_packet=proof_result.packet,
            contract=contract_result.contract,
            max_trade_risk_dollars=max_trade_risk_dollars,
            max_aggregate_open_risk_dollars=max_aggregate_open_risk_dollars,
        )

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
        portfolio_result=portfolio_result,
        risk_accepted=risk_accepted,
        notes=notes,
    )
