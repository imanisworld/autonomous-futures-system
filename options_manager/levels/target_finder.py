"""options_manager/levels/target_finder.py

Advisory-only level/target finder — Increment 2. Pure function of caller-
supplied entry/invalidation/level inputs -> TargetFinderResult. Performs
no I/O of any kind: no market-data fetch, no broker call, no order
placement, no execution.

Target selection rule: all candidate levels (resistance_levels,
support_levels, gamma_resistance, gamma_support) are merged into one set,
then filtered to the correct side of entry and sorted by distance from
entry (nearest first). target_1 is the nearest valid level; target_2 is
the next-nearest valid level — regardless of whether either candidate
came from a plain support/resistance level or a gamma level. This keeps
selection a single, deterministic rule rather than two separate
"prefer gamma" vs "prefer plain level" rules that could disagree.

Not wired into options_manager/strategies/strat_212.py — this is an
additive, standalone module. Integration (if any) is a separate,
explicitly-scoped increment.
"""

from __future__ import annotations

from .base import LevelFinderInputs, TargetFinderResult, _invalid


def find_targets(inputs: LevelFinderInputs) -> TargetFinderResult:
    """Pure function of its explicit inputs -> TargetFinderResult.

    Fails closed to INVALID for: an invalid direction, a missing entry or
    invalidation, invalidation on the wrong side of entry, no valid
    target_1/target_2 candidate on the correct side of entry, a target
    landing on the wrong side of entry (defensive re-check), a target_1
    closer than min_distance_to_target, or an rr_1 below
    min_rr_threshold. A target finder must never assume a favorable
    default for data it wasn't given.
    """
    direction = inputs.direction
    if direction not in ("CALL", "PUT"):
        return _invalid("invalid_direction", f"direction {direction!r} must be CALL or PUT")

    entry = inputs.entry
    if entry is None:
        return _invalid("missing_entry", "entry is required")

    invalidation = inputs.underlying_invalidation
    if invalidation is None:
        return _invalid("missing_invalidation", "underlying_invalidation is required")

    if direction == "CALL" and invalidation >= entry:
        return _invalid(
            "invalidation_wrong_side",
            "underlying_invalidation must be below entry for CALL",
        )
    if direction == "PUT" and invalidation <= entry:
        return _invalid(
            "invalidation_wrong_side",
            "underlying_invalidation must be above entry for PUT",
        )

    risk_amount = abs(entry - invalidation)

    candidate_levels: set[float] = set(inputs.resistance_levels) | set(inputs.support_levels)
    if inputs.gamma_resistance is not None:
        candidate_levels.add(inputs.gamma_resistance)
    if inputs.gamma_support is not None:
        candidate_levels.add(inputs.gamma_support)

    if direction == "CALL":
        valid_levels = sorted(level for level in candidate_levels if level > entry)
    else:
        valid_levels = sorted(
            (level for level in candidate_levels if level < entry), reverse=True
        )

    if not valid_levels:
        return _invalid(
            "no_target_1", "no valid level found on the correct side of entry"
        )
    target_1 = valid_levels[0]

    if len(valid_levels) < 2:
        return _invalid(
            "no_target_2", "only one valid level found on the correct side of entry"
        )
    target_2 = valid_levels[1]

    # Defensive re-check: the filter above already guarantees this, but a
    # target finder must not approve on top of a broken invariant.
    if direction == "CALL" and (target_1 <= entry or target_2 <= entry):
        return _invalid("target_wrong_side", "targets must be above entry for CALL")
    if direction == "PUT" and (target_1 >= entry or target_2 >= entry):
        return _invalid("target_wrong_side", "targets must be below entry for PUT")

    distance_to_target_1 = abs(target_1 - entry)
    distance_to_target_2 = abs(target_2 - entry)

    if (
        inputs.min_distance_to_target is not None
        and distance_to_target_1 < inputs.min_distance_to_target
    ):
        return _invalid(
            "target_too_close",
            f"distance_to_target_1 {distance_to_target_1} is below minimum "
            f"{inputs.min_distance_to_target}",
        )

    reward_1 = distance_to_target_1
    reward_2 = distance_to_target_2
    rr_1 = reward_1 / risk_amount if risk_amount else None
    rr_2 = reward_2 / risk_amount if risk_amount else None

    if (
        inputs.min_rr_threshold is not None
        and rr_1 is not None
        and rr_1 < inputs.min_rr_threshold
    ):
        return _invalid(
            "rr_below_threshold",
            f"rr_1 {rr_1} is below minimum {inputs.min_rr_threshold}",
        )

    return TargetFinderResult(
        status="VALID",
        reason_code="valid_targets",
        target_1=target_1,
        target_2=target_2,
        distance_to_target_1=distance_to_target_1,
        distance_to_target_2=distance_to_target_2,
        risk_amount=risk_amount,
        reward_1=reward_1,
        reward_2=reward_2,
        rr_1=rr_1,
        rr_2=rr_2,
    )
