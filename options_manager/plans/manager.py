"""Pure Phase-1 options thesis/plan manager.

This is state reduction, not execution. Repeated Signa observations are
collapsed into telemetry on one thesis. Targets are delegated to the existing
``options_manager.levels.find_targets`` authority; this module does not invent a
second target algorithm.
"""

from __future__ import annotations

from typing import Optional

from options_manager.levels import LevelFinderInputs, find_targets

from .base import (
    ContractPlanSnapshot,
    ConvictionBand,
    PlanObservation,
    PlanPolicy,
    PlanStatus,
    PlanUpdate,
    RiskPlanSnapshot,
    StructuralLevel,
    TradePlanSnapshot,
)


def _identity(observation: PlanObservation) -> tuple[str, str, str, str]:
    return (
        observation.ticker.strip().upper(),
        observation.direction,
        observation.setup_type.strip(),
        observation.timeframe.strip(),
    )


def _snapshot_identity(snapshot: TradePlanSnapshot) -> tuple[str, str, str, str]:
    return (
        snapshot.ticker.strip().upper(),
        snapshot.direction,
        snapshot.setup_type.strip(),
        snapshot.timeframe.strip(),
    )


def _usable_levels(
    levels: tuple[StructuralLevel, ...],
) -> tuple[tuple[StructuralLevel, ...], list[str]]:
    usable: list[StructuralLevel] = []
    warnings: list[str] = []
    for level in levels:
        if level.price <= 0:
            warnings.append(f"invalid_structural_level_ignored:{level.source}")
            continue
        if level.is_gamma and not level.verified_gamma:
            warnings.append(f"unverified_gamma_level_ignored:{level.source}")
            continue
        usable.append(level)
    return tuple(usable), warnings


def _target_source(
    price: Optional[float], levels: tuple[StructuralLevel, ...]
) -> Optional[str]:
    if price is None:
        return None
    for level in levels:
        if abs(level.price - price) < 1e-9:
            return level.source
    return None


def _resolve_targets(observation: PlanObservation, policy: PlanPolicy):
    if observation.entry_trigger is None:
        return None, (), ("entry_trigger_missing",)
    if observation.underlying_invalidation is None:
        return None, (), ("underlying_invalidation_missing",)

    usable, warnings = _usable_levels(observation.levels)
    # The shared target finder correctly chooses numeric levels on the profit
    # side, but it intentionally merges support/resistance inputs. The plan
    # adapter has richer provenance, so do not allow a semantically wrong wall
    # (e.g. a stale SUPPORT above a CALL entry) to become a profit target.
    if observation.direction == "CALL":
        resistance = tuple(level.price for level in usable if level.side == "RESISTANCE")
        support: tuple[float, ...] = ()
    else:
        resistance = ()
        support = tuple(level.price for level in usable if level.side == "SUPPORT")

    result = find_targets(
        LevelFinderInputs(
            direction=observation.direction,
            entry=observation.entry_trigger,
            underlying_invalidation=observation.underlying_invalidation,
            resistance_levels=resistance,
            support_levels=support,
            min_rr_threshold=policy.min_rr_threshold,
            min_distance_to_target=policy.min_distance_to_target,
        )
    )
    return result, tuple(warnings), ()


def _actionability(
    observation: PlanObservation,
    target_status: str,
    target_reason_code: str,
    target_preconditions: tuple[str, ...],
) -> tuple[bool, tuple[str, ...]]:
    blocking: list[str] = list(target_preconditions)
    if not observation.mechanical_triggered:
        blocking.append("mechanical_trigger_missing")
    if target_status != "VALID":
        blocking.append(f"targets_not_valid:{target_reason_code}")
    if not observation.contract_valid:
        blocking.append("contract_not_valid")
    if not observation.portfolio_risk_valid:
        blocking.append("portfolio_risk_not_valid")
    if not observation.spy_qqq_aligned:
        blocking.append("spy_qqq_not_aligned")
    if not observation.htf_aligned:
        blocking.append("htf_not_aligned")
    if not observation.event_risk_clear:
        blocking.append("event_risk_not_clear")
    # Deliberately absent: Signa. The completed effectiveness audit assigns it
    # an observational role only; aligned/opposed/missing Signa cannot alter
    # actionability here.
    return (not blocking, tuple(blocking))


def _conviction(
    observation: PlanObservation, policy: PlanPolicy, actionable: bool
) -> ConvictionBand:
    if not actionable:
        return ConvictionBand.OBSERVATIONAL
    threshold = policy.high_conviction_min_confirmations
    if threshold is None:
        return ConvictionBand.STANDARD
    if observation.conviction_proofs.count() >= threshold:
        return ConvictionBand.HIGH_CONVICTION_CANDIDATE
    return ConvictionBand.STANDARD


def _status(
    previous: Optional[TradePlanSnapshot],
    observation: PlanObservation,
    actionable: bool,
    blocking: list[str],
) -> PlanStatus:
    if observation.expired:
        return PlanStatus.EXPIRED
    if observation.invalidation_hit:
        return PlanStatus.INVALIDATED
    if observation.mark_exited:
        if previous is None or previous.status != PlanStatus.ACTIVE:
            raise ValueError("mark_exited requires an existing ACTIVE thesis")
        return PlanStatus.EXITED
    # Once the human has marked a proven plan active, later observations update
    # its management state; they do not silently demote the held position back
    # to TRIGGERED merely because ``mark_active`` was not repeated every poll.
    if previous is not None and previous.status == PlanStatus.ACTIVE:
        return PlanStatus.ACTIVE
    if observation.mark_active:
        if actionable:
            return PlanStatus.ACTIVE
        blocking.append("active_requires_actionable_plan")
    if observation.mechanical_triggered:
        return PlanStatus.TRIGGERED
    return PlanStatus.WATCHING


def _append_sources(
    previous: Optional[TradePlanSnapshot], observation: PlanObservation
) -> tuple[str, ...]:
    refs = list(previous.source_references if previous is not None else ())
    candidates = (*observation.source_references, observation.source_reference)
    for source in candidates:
        if source and source not in refs:
            refs.append(source)
    return tuple(refs)


def _signa_state(previous: Optional[TradePlanSnapshot], observation: PlanObservation):
    event_count = previous.signa_event_count if previous is not None else 0
    repeat_count = previous.signa_repeat_count if previous is not None else 0
    last_fingerprint = previous.last_signa_fingerprint if previous is not None else None
    latest = previous.latest_signa if previous is not None else None
    changed = False
    repeated = False

    if observation.signa is not None:
        fingerprint = observation.signa.fingerprint()
        latest = observation.signa
        if last_fingerprint is not None and fingerprint == last_fingerprint:
            repeat_count += 1
            repeated = True
        else:
            event_count += 1
            changed = True
            last_fingerprint = fingerprint

    return event_count, repeat_count, last_fingerprint, latest, changed, repeated


def _contract_material_key(plan: Optional[ContractPlanSnapshot]) -> object:
    """Return only contract facts that change the actual plan, not every quote.

    Premium/bid/ask/spread/volume/OI/target-distance are useful current facts and
    stay on the snapshot, but making each tick user-facing would recreate the
    repeated-alert problem this thesis manager exists to solve. A contract
    identity, stop, allowed size, or categorical risk/style change is material.
    """
    if plan is None:
        return None
    return (
        plan.expiration,
        plan.strike,
        plan.max_contracts,
        plan.premium_stop,
        plan.iv_event_risk,
        plan.theta_risk,
        plan.trade_style,
    )


def _risk_material_key(plan: Optional[RiskPlanSnapshot]) -> object:
    """Return risk-policy facts that merit a user-facing plan update.

    Current aggregate exposure and candidate quote-derived risk are retained as
    telemetry on the snapshot. They do not independently create another alert
    while the canonical risk authority still passes. Crossing a gate changes
    actionability/blocking upstream; changing an explicit risk policy or stated
    plan risk limit is itself material here.
    """
    if plan is None:
        return None
    return (
        plan.stated_max_dollar_risk,
        plan.max_trade_risk_dollars,
        plan.max_aggregate_open_risk_dollars,
    )


def _material_changes(
    previous: Optional[TradePlanSnapshot], current: TradePlanSnapshot
) -> tuple[str, ...]:
    if previous is None:
        return ("new_plan",)

    reasons: list[str] = []
    if previous.status != current.status:
        reasons.append("status_changed")
    if previous.actionable != current.actionable:
        reasons.append("actionability_changed")
    if previous.conviction != current.conviction:
        reasons.append("conviction_changed")
    if (previous.entry_trigger, previous.underlying_invalidation) != (
        current.entry_trigger,
        current.underlying_invalidation,
    ):
        reasons.append("entry_or_invalidation_changed")
    if (previous.target_1, previous.target_2) != (current.target_1, current.target_2):
        reasons.append("targets_changed")
    if _contract_material_key(previous.contract_plan) != _contract_material_key(
        current.contract_plan
    ):
        reasons.append("contract_plan_changed")
    if _risk_material_key(previous.risk_plan) != _risk_material_key(current.risk_plan):
        reasons.append("risk_plan_changed")
    if previous.blocking_reasons != current.blocking_reasons:
        reasons.append("blocking_reasons_changed")
    return tuple(reasons)


def update_trade_thesis(
    previous: Optional[TradePlanSnapshot],
    observation: PlanObservation,
    *,
    policy: PlanPolicy = PlanPolicy(),
) -> PlanUpdate:
    """Create or update one advisory thesis from one caller-supplied observation.

    The function performs no I/O. Terminal theses cannot be reopened in place;
    a later setup must start a new thesis instead of mutating history.
    """

    policy.validate()

    if observation.direction not in ("CALL", "PUT"):
        raise ValueError("direction must be CALL or PUT")
    if not observation.ticker.strip():
        raise ValueError("ticker is required")
    if not observation.setup_type.strip():
        raise ValueError("setup_type is required")
    if not observation.timeframe.strip():
        raise ValueError("timeframe is required")

    if previous is not None:
        if previous.terminal:
            raise ValueError("terminal thesis cannot be reopened; create a new thesis")
        if _snapshot_identity(previous) != _identity(observation):
            raise ValueError("observation identity does not match existing thesis")

    target_result, target_warnings, target_preconditions = _resolve_targets(
        observation, policy
    )
    if target_result is None:
        target_status = "INVALID"
        target_reason_code = (
            target_preconditions[0] if target_preconditions else "targets_unresolved"
        )
        target_1 = target_2 = rr_1 = rr_2 = None
        usable_levels, level_warnings = _usable_levels(observation.levels)
        warnings = tuple((*target_warnings, *level_warnings))
    else:
        target_status = target_result.status
        target_reason_code = target_result.reason_code
        target_1 = target_result.target_1
        target_2 = target_result.target_2
        rr_1 = target_result.rr_1
        rr_2 = target_result.rr_2
        usable_levels, level_warnings = _usable_levels(observation.levels)
        warnings = tuple(
            (*target_warnings, *level_warnings, *target_result.warnings)
        )

    actionable, base_blocking = _actionability(
        observation, target_status, target_reason_code, target_preconditions
    )
    blocking = list(base_blocking)
    status = _status(previous, observation, actionable, blocking)
    conviction = _conviction(observation, policy, actionable)

    (
        signa_event_count,
        signa_repeat_count,
        last_signa_fingerprint,
        latest_signa,
        signa_changed,
        signa_repeated,
    ) = _signa_state(previous, observation)

    # Canonical proof observations carry these facts every pass. Direct/manual
    # management observations may omit them; omission must not erase previously
    # validated contract/risk facts from an existing thesis generation.
    contract_plan = (
        observation.contract_plan
        if observation.contract_plan is not None
        else previous.contract_plan if previous is not None else None
    )
    risk_plan = (
        observation.risk_plan
        if observation.risk_plan is not None
        else previous.risk_plan if previous is not None else None
    )

    current = TradePlanSnapshot(
        ticker=observation.ticker.strip().upper(),
        direction=observation.direction,
        setup_type=observation.setup_type.strip(),
        timeframe=observation.timeframe.strip(),
        observed_at=observation.observed_at,
        status=status,
        actionable=actionable,
        conviction=conviction,
        conviction_confirmation_count=observation.conviction_proofs.count(),
        entry_trigger=observation.entry_trigger,
        underlying_invalidation=observation.underlying_invalidation,
        target_1=target_1,
        target_2=target_2,
        target_1_source=_target_source(target_1, usable_levels),
        target_2_source=_target_source(target_2, usable_levels),
        rr_1=rr_1,
        rr_2=rr_2,
        target_status=target_status,
        target_reason_code=target_reason_code,
        blocking_reasons=tuple(blocking),
        warnings=warnings,
        signa_event_count=signa_event_count,
        signa_repeat_count=signa_repeat_count,
        last_signa_fingerprint=last_signa_fingerprint,
        latest_signa=latest_signa,
        source_references=_append_sources(previous, observation),
        contract_plan=contract_plan,
        risk_plan=risk_plan,
    )

    material_reasons = _material_changes(previous, current)
    should_emit = bool(material_reasons)
    telemetry_only = not should_emit and (signa_changed or signa_repeated)

    return PlanUpdate(
        snapshot=current,
        should_emit_update=should_emit,
        material_reasons=material_reasons,
        telemetry_only=telemetry_only,
        signa_changed=signa_changed,
        signa_repeated=signa_repeated,
    )
