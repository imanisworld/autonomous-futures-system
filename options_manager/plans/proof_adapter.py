"""Fail-closed bridge from existing options authorities into one trade thesis.

This module does not invent a third setup, target, contract, market-context, or
risk authority. It reconciles the authorities that already exist:

* ``options_manager.scanner`` / ``evaluate_strat_212`` for the mechanical setup;
* ``options_manager.context`` for SPY/QQQ, HTF, event risk, and optional GEX;
* ``options_manager.contracts`` for scanner-side contract constraints;
* ``options_manager.validation`` for the canonical proof packet, contract
  quality, and aggregate portfolio-risk verdict; and
* ``options_manager.plans`` for target provenance and evolving-thesis state.

A caller cannot promote a thesis by passing free booleans here. The bridge
constructs the low-level ``PlanObservation`` itself only after the independent
results reconcile. Any mismatch fails closed and returns no plan update.

Pure advisory only: no market fetch, broker, order, alert, storage, config read,
or execution side effect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from options_manager.context import evaluate_market_context
from options_manager.contracts import evaluate_contract_constraints
from options_manager.scanner import ScanResult, WatchlistRow
from options_manager.strategies import (
    STRAT_212_STRATEGY_NAME,
    strat_212_mechanical_levels,
)
from options_manager.validation.advisory_decision import (
    AdvisoryDecisionResult,
    AdvisoryVerdict,
    check_advisory_decision_intake,
)
from options_manager.validation.contract_quality_gate import (
    GateVerdict,
    check_contract_quality_intake,
)
from options_manager.validation.portfolio_risk_gate import (
    PortfolioRiskVerdict,
    check_portfolio_risk_intake,
)
from options_manager.validation.proof_packet_intake import check_proof_packet_intake

from .base import (
    ContractPlanSnapshot,
    ConvictionProofs,
    PlanObservation,
    PlanPolicy,
    PlanUpdate,
    RiskPlanSnapshot,
    SignaObservation,
    StructuralLevel,
    TradePlanSnapshot,
)
from .manager import update_trade_thesis

_EPSILON = 1e-9


@dataclass(frozen=True)
class CanonicalPlanProofResult:
    """Result of reconciling all existing authorities for one thesis update."""

    valid: bool
    plan_update: Optional[PlanUpdate]
    advisory_result: AdvisoryDecisionResult
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _same_number(left: object, right: object) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return False
    return math.isfinite(a) and math.isfinite(b) and math.isclose(
        a, b, rel_tol=0.0, abs_tol=_EPSILON
    )


def _finite_number(value: object, label: str, blocking: list[str]) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        blocking.append(f"{label}_not_numeric")
        return None
    if not math.isfinite(parsed):
        blocking.append(f"{label}_not_finite")
        return None
    return parsed


def _same_optional_number(left: object, right: object) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return _same_number(left, right)


def _unique_sorted(values: tuple[float, ...] | list[float]) -> tuple[float, ...]:
    return tuple(sorted(set(float(value) for value in values)))


def _same_price_set(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    a = _unique_sorted(list(left))
    b = _unique_sorted(list(right))
    return len(a) == len(b) and all(_same_number(x, y) for x, y in zip(a, b))


def _reconcile_levels(
    row: WatchlistRow,
    structural_levels: tuple[StructuralLevel, ...],
    policy: PlanPolicy,
    blocking: list[str],
) -> None:
    level_inputs = row.level_inputs
    if level_inputs is None:
        blocking.append("scanner_level_inputs_missing")
        return

    if level_inputs.direction != row.direction:
        blocking.append("scanner_level_direction_mismatch")
    if not _same_optional_number(level_inputs.entry, row.entry_trigger):
        blocking.append("scanner_level_entry_mismatch")
    if not _same_optional_number(
        level_inputs.underlying_invalidation, row.underlying_invalidation
    ):
        blocking.append("scanner_level_invalidation_mismatch")
    if not _same_optional_number(
        level_inputs.min_rr_threshold, policy.min_rr_threshold
    ):
        blocking.append("target_rr_policy_mismatch")
    if not _same_optional_number(
        level_inputs.min_distance_to_target, policy.min_distance_to_target
    ):
        blocking.append("target_distance_policy_mismatch")

    # Canonical semantic rule: CALL targets are resistance-side levels and PUT
    # targets are support-side levels. The shared target finder accepts both
    # arrays numerically, so the bridge forbids an opposite-labelled level from
    # becoming a target by accident.
    if row.direction == "CALL":
        if level_inputs.support_levels or level_inputs.gamma_support is not None:
            blocking.append("opposite_side_target_levels_present")
        row_prices = list(level_inputs.resistance_levels)
        if level_inputs.gamma_resistance is not None:
            row_prices.append(level_inputs.gamma_resistance)
        plan_levels = tuple(
            level.price
            for level in structural_levels
            if level.side == "RESISTANCE"
            and level.price > 0
            and (not level.is_gamma or level.verified_gamma)
        )
        gamma_price = level_inputs.gamma_resistance
        gamma_side = "RESISTANCE"
    else:
        if level_inputs.resistance_levels or level_inputs.gamma_resistance is not None:
            blocking.append("opposite_side_target_levels_present")
        row_prices = list(level_inputs.support_levels)
        if level_inputs.gamma_support is not None:
            row_prices.append(level_inputs.gamma_support)
        plan_levels = tuple(
            level.price
            for level in structural_levels
            if level.side == "SUPPORT"
            and level.price > 0
            and (not level.is_gamma or level.verified_gamma)
        )
        gamma_price = level_inputs.gamma_support
        gamma_side = "SUPPORT"

    if not _same_price_set(tuple(row_prices), plan_levels):
        blocking.append("scanner_plan_structural_levels_mismatch")

    if gamma_price is not None:
        verified_gamma = any(
            level.side == gamma_side
            and level.is_gamma
            and level.verified_gamma
            and _same_number(level.price, gamma_price)
            for level in structural_levels
        )
        if not verified_gamma:
            blocking.append("scanner_gamma_level_not_verified")


def _reconcile_contracts(
    row: WatchlistRow,
    canonical_contract: object,
    blocking: list[str],
) -> None:
    scanner_contract = row.contract_constraints_inputs
    if scanner_contract is None:
        blocking.append("scanner_contract_inputs_missing")
        return

    if scanner_contract.direction != row.direction:
        blocking.append("scanner_contract_direction_mismatch")
    if (scanner_contract.ticker or "").strip().upper() != row.ticker.strip().upper():
        blocking.append("scanner_contract_ticker_mismatch")

    # The canonical contract-quality gate and scanner constraints must describe
    # the same contract. Otherwise two individually valid contracts could be
    # combined into one false proof packet.
    exact_fields = ("ticker", "direction", "expiration", "dte", "volume", "open_interest")
    numeric_fields = ("strike", "premium", "bid", "ask", "spread_percent")
    for name in exact_fields:
        left = getattr(scanner_contract, name, None)
        right = getattr(canonical_contract, name, None)
        if name == "ticker":
            same = str(left or "").strip().upper() == str(right or "").strip().upper()
        elif name == "direction":
            same = left == right
        else:
            same = left == right
        if not same:
            blocking.append(f"scanner_canonical_contract_mismatch:{name}")
    for name in numeric_fields:
        if not _same_number(
            getattr(scanner_contract, name, None), getattr(canonical_contract, name, None)
        ):
            blocking.append(f"scanner_canonical_contract_mismatch:{name}")


def update_trade_thesis_from_authorities(
    previous: Optional[TradePlanSnapshot],
    *,
    row: WatchlistRow,
    scan_result: ScanResult,
    canonical_payload: Any,
    structural_levels: tuple[StructuralLevel, ...],
    max_trade_risk_dollars: float,
    max_aggregate_open_risk_dollars: float,
    policy: PlanPolicy = PlanPolicy(),
    conviction_proofs: ConvictionProofs = ConvictionProofs(),
    signa: Optional[SignaObservation] = None,
) -> CanonicalPlanProofResult:
    """Promote one scanner result into a thesis only after all proof agrees.

    Both risk caps are required call arguments. This bridge deliberately does
    not inherit the still-unreviewed historical per-trade default or invent an
    aggregate budget. Passing a bad/non-finite value is handled by the existing
    canonical risk authority and blocks promotion.
    """

    advisory = check_advisory_decision_intake(
        canonical_payload,
        require_portfolio_risk=True,
        max_trade_risk_dollars=max_trade_risk_dollars,
        max_aggregate_open_risk_dollars=max_aggregate_open_risk_dollars,
    )
    blocking: list[str] = []
    warnings: list[str] = list(advisory.warnings)

    if not isinstance(canonical_payload, Mapping):
        return CanonicalPlanProofResult(
            valid=False,
            plan_update=None,
            advisory_result=advisory,
            blocking_reasons=("canonical_payload_not_mapping", *advisory.blocking_reasons),
            warnings=tuple(warnings),
        )

    proof_result = check_proof_packet_intake(canonical_payload.get("proof_packet"))
    contract_quality = check_contract_quality_intake(
        canonical_payload.get("contract_quality")
    )
    packet = proof_result.packet
    canonical_contract = contract_quality.contract
    # Re-run the existing pure portfolio gate so the exact measured risk/debit
    # facts can be carried into the thesis. This is the same authority and same
    # inputs used by advisory_decision; no risk rule is duplicated here.
    portfolio_result = check_portfolio_risk_intake(
        canonical_payload.get("portfolio_risk"),
        proof_packet=packet,
        contract=canonical_contract,
        max_trade_risk_dollars=max_trade_risk_dollars,
        max_aggregate_open_risk_dollars=max_aggregate_open_risk_dollars,
    )

    row_ticker = row.ticker.strip().upper()
    if not row_ticker:
        blocking.append("scanner_ticker_missing")
    if scan_result.ticker.strip().upper() != row_ticker:
        blocking.append("scanner_result_ticker_mismatch")
    if scan_result.timestamp != row.timestamp:
        blocking.append("scanner_result_timestamp_mismatch")
    if row.exclude:
        blocking.append("scanner_row_excluded")

    timeframe = (row.timeframe or "").strip()
    if not timeframe:
        blocking.append("scanner_timeframe_missing")

    signal = scan_result.signal
    if signal is None:
        blocking.append("scanner_signal_missing")
    else:
        if signal.strategy_name != STRAT_212_STRATEGY_NAME:
            blocking.append("scanner_strategy_not_canonical_strat_212")
        if signal.direction != row.direction:
            blocking.append("scanner_signal_direction_mismatch")
        if signal.candle_sequence != "strat_212":
            blocking.append("scanner_sequence_not_212")

    if scan_result.scan_status != "TRIGGERED" or scan_result.strategy_status != "VALID":
        blocking.append(f"scanner_not_triggered:{scan_result.reason_code}")

    # The bridge does not accept the strategy layer's old manual shortcut
    # booleans. It requires the real context/contract inputs so the existing
    # validators, not a caller assertion, are the proof source.
    if row.market_context.confirmed is not None:
        blocking.append("manual_market_context_override_not_allowed")
    if row.contract_constraints.constraints_met is not None:
        blocking.append("manual_contract_constraint_override_not_allowed")
    if row.target_1 is not None or row.target_2 is not None:
        blocking.append("explicit_scanner_targets_not_allowed")

    expected_entry, expected_invalidation = strat_212_mechanical_levels(
        row.bars, row.direction
    )
    if not _same_number(row.entry_trigger, expected_entry):
        blocking.append("entry_not_mechanical_previous_candle_break")
    if not _same_number(row.underlying_invalidation, expected_invalidation):
        blocking.append("invalidation_not_inside_bar_opposite_extreme")
    if not _same_number(scan_result.entry, expected_entry):
        blocking.append("scanner_result_entry_mismatch")
    if not _same_number(scan_result.invalidation, expected_invalidation):
        blocking.append("scanner_result_invalidation_mismatch")

    _reconcile_levels(row, structural_levels, policy, blocking)

    context_result = None
    if row.market_context_inputs is None:
        blocking.append("scanner_market_context_inputs_missing")
    else:
        context_inputs = row.market_context_inputs
        if context_inputs.direction != row.direction:
            blocking.append("scanner_market_context_direction_mismatch")
        if (context_inputs.ticker or "").strip().upper() != row_ticker:
            blocking.append("scanner_market_context_ticker_mismatch")
        context_result = evaluate_market_context(context_inputs)
        warnings.extend(context_result.warnings)
        if not context_result.confirmed or context_result.status == "INVALID":
            blocking.append(f"market_context_not_confirmed:{context_result.reason_code}")
        if not context_result.spy_qqq_aligned:
            blocking.append("spy_qqq_not_aligned")
        if not context_result.htf_aligned:
            blocking.append("htf_not_aligned")
        if not context_result.event_risk_clear:
            blocking.append("event_risk_not_clear")

    scanner_contract_result = None
    if row.contract_constraints_inputs is None:
        blocking.append("scanner_contract_inputs_missing")
    else:
        scanner_contract_result = evaluate_contract_constraints(
            row.contract_constraints_inputs
        )
        warnings.extend(scanner_contract_result.warnings)
        if (
            not scanner_contract_result.confirmed
            or scanner_contract_result.status == "INVALID"
        ):
            blocking.append(
                f"scanner_contract_not_confirmed:{scanner_contract_result.reason_code}"
            )

    if packet is None or not proof_result.valid:
        blocking.append("canonical_proof_packet_not_valid")
        blocking.extend(f"proof:{reason}" for reason in proof_result.blocking_reasons)
        blocking.extend(f"proof:missing:{name}" for name in proof_result.missing_fields)
    else:
        if packet.ticker.strip().upper() != row_ticker:
            blocking.append("proof_packet_ticker_mismatch")
        if packet.direction != row.direction:
            blocking.append("proof_packet_direction_mismatch")
        if signal is not None and packet.setup_type.strip() != signal.strategy_name:
            blocking.append("proof_packet_setup_type_mismatch")
        if packet.timeframe.strip() != timeframe:
            blocking.append("proof_packet_timeframe_mismatch")
        if packet.status.value != "triggered":
            blocking.append("proof_packet_status_not_triggered")

        packet_entry = _finite_number(packet.entry_trigger, "proof_entry", blocking)
        packet_invalidation = _finite_number(
            packet.underlying_invalidation, "proof_invalidation", blocking
        )
        packet_target_1 = _finite_number(packet.target_1, "proof_target_1", blocking)
        packet_target_2 = _finite_number(packet.target_2, "proof_target_2", blocking)
        if packet_entry is not None and not _same_number(packet_entry, expected_entry):
            blocking.append("proof_packet_entry_mismatch")
        if packet_invalidation is not None and not _same_number(
            packet_invalidation, expected_invalidation
        ):
            blocking.append("proof_packet_invalidation_mismatch")
        if packet_target_1 is not None and not _same_number(
            packet_target_1, scan_result.target_1
        ):
            blocking.append("proof_packet_target_1_mismatch")
        if packet_target_2 is not None and not _same_number(
            packet_target_2, scan_result.target_2
        ):
            blocking.append("proof_packet_target_2_mismatch")

    if canonical_contract is None:
        blocking.append("canonical_contract_quality_not_normalized")
    else:
        _reconcile_contracts(row, canonical_contract, blocking)

    if advisory.verdict != AdvisoryVerdict.TAKE:
        blocking.append(f"canonical_advisory_not_take:{advisory.verdict.value}")
        blocking.extend(f"advisory:{reason}" for reason in advisory.blocking_reasons)
    if advisory.contract_verdict == GateVerdict.BLOCK:
        blocking.append("canonical_contract_blocked")
    if advisory.portfolio_verdict != PortfolioRiskVerdict.PASS:
        blocking.append("canonical_portfolio_risk_not_pass")
    if portfolio_result.verdict != advisory.portfolio_verdict:
        blocking.append("canonical_portfolio_result_mismatch")
    if portfolio_result.verdict != PortfolioRiskVerdict.PASS:
        blocking.extend(f"portfolio:{reason}" for reason in portfolio_result.blocking_reasons)

    if (
        blocking
        or packet is None
        or canonical_contract is None
        or context_result is None
        or scanner_contract_result is None
        or canonical_contract.premium_stop is None
    ):
        return CanonicalPlanProofResult(
            valid=False,
            plan_update=None,
            advisory_result=advisory,
            blocking_reasons=tuple(dict.fromkeys(blocking)),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    contract_plan = ContractPlanSnapshot(
        expiration=canonical_contract.expiration,
        strike=canonical_contract.strike,
        premium=canonical_contract.premium,
        bid=canonical_contract.bid,
        ask=canonical_contract.ask,
        spread_percent=canonical_contract.spread_percent,
        volume=canonical_contract.volume,
        open_interest=canonical_contract.open_interest,
        dte=canonical_contract.dte,
        max_contracts=canonical_contract.max_contracts,
        premium_stop=canonical_contract.premium_stop,
        distance_to_target=canonical_contract.distance_to_target,
        iv_event_risk=canonical_contract.iv_event_risk,
        theta_risk=canonical_contract.theta_risk,
        trade_style=canonical_contract.trade_style,
    )
    risk_plan = RiskPlanSnapshot(
        planned_dollar_risk=portfolio_result.candidate_risk,
        capital_deployed=(
            portfolio_result.projected_capital_deployed
            - portfolio_result.aggregate_capital_deployed
        ),
        stated_max_dollar_risk=canonical_contract.max_dollar_risk,
        max_trade_risk_dollars=float(max_trade_risk_dollars),
        aggregate_open_risk=portfolio_result.aggregate_open_risk,
        projected_open_risk=portfolio_result.projected_open_risk,
        max_aggregate_open_risk_dollars=float(max_aggregate_open_risk_dollars),
        aggregate_capital_deployed=portfolio_result.aggregate_capital_deployed,
        projected_capital_deployed=portfolio_result.projected_capital_deployed,
        open_position_count=portfolio_result.open_position_count,
        correlation_risk=portfolio_result.correlation_risk,
    )

    observation = PlanObservation(
        ticker=row_ticker,
        direction=row.direction,
        setup_type=signal.strategy_name,
        timeframe=timeframe,
        observed_at=row.timestamp,
        mechanical_triggered=True,
        entry_trigger=expected_entry,
        underlying_invalidation=expected_invalidation,
        levels=structural_levels,
        # These are authority-derived, never caller-supplied booleans.
        contract_valid=(
            scanner_contract_result.confirmed
            and scanner_contract_result.status != "INVALID"
            and advisory.contract_verdict != GateVerdict.BLOCK
        ),
        portfolio_risk_valid=advisory.portfolio_verdict == PortfolioRiskVerdict.PASS,
        spy_qqq_aligned=context_result.spy_qqq_aligned,
        htf_aligned=context_result.htf_aligned,
        event_risk_clear=context_result.event_risk_clear,
        conviction_proofs=conviction_proofs,
        signa=signa,
        contract_plan=contract_plan,
        risk_plan=risk_plan,
        source_references=packet.source_references,
    )
    plan_update = update_trade_thesis(previous, observation, policy=policy)
    snapshot = plan_update.snapshot

    # Final reconciliation uses the plan manager's own target authority. If its
    # semantically filtered/provenance-aware targets differ from what the
    # scanner or proof packet claimed, discard the transient update.
    post_blocking: list[str] = []
    if not snapshot.actionable:
        post_blocking.extend(snapshot.blocking_reasons or ("plan_not_actionable",))
    if not _same_number(snapshot.target_1, scan_result.target_1):
        post_blocking.append("plan_scanner_target_1_mismatch")
    if not _same_number(snapshot.target_2, scan_result.target_2):
        post_blocking.append("plan_scanner_target_2_mismatch")
    if not _same_number(snapshot.target_1, packet.target_1):
        post_blocking.append("plan_proof_target_1_mismatch")
    if not _same_number(snapshot.target_2, packet.target_2):
        post_blocking.append("plan_proof_target_2_mismatch")
    if snapshot.target_1_source is None or snapshot.target_2_source is None:
        post_blocking.append("target_provenance_missing")
    if snapshot.contract_plan != contract_plan:
        post_blocking.append("contract_plan_not_preserved")
    if snapshot.risk_plan != risk_plan:
        post_blocking.append("risk_plan_not_preserved")

    if post_blocking:
        return CanonicalPlanProofResult(
            valid=False,
            plan_update=None,
            advisory_result=advisory,
            blocking_reasons=tuple(dict.fromkeys(post_blocking)),
            warnings=tuple(dict.fromkeys((*warnings, *snapshot.warnings))),
        )

    return CanonicalPlanProofResult(
        valid=True,
        plan_update=plan_update,
        advisory_result=advisory,
        warnings=tuple(dict.fromkeys((*warnings, *snapshot.warnings))),
    )
