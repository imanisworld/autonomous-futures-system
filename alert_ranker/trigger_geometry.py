"""Pure family-specific Strat geometry rules for trigger-time research.

Only geometry explicitly supported by the research ruling is encoded.
Unproven target/stop rules remain unresolved rather than inheriting the generic
level finder silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .causal_bars import Bar
from .trigger_time import ArmedStratTrigger, TriggerResolution

GeometryStatus = Literal[
    "DEFINED",
    "STOP_ONLY",
    "TARGET_ONLY",
    "NO_OWN_MAGNITUDE",
    "UNRESOLVED",
]


@dataclass(frozen=True)
class CanonicalGeometry:
    family: str
    subtype: str | None
    status: GeometryStatus
    target: float | None
    target_source: str | None
    stop: float | None
    stop_source: str | None
    notes: str


def _directional_extreme(bar: Bar, direction: str) -> float:
    if direction == "LONG":
        return float(bar.high)
    if direction == "SHORT":
        return float(bar.low)
    raise ValueError("direction must be LONG or SHORT")


def geometry_for_trigger(
    *,
    armed: ArmedStratTrigger,
    result: TriggerResolution,
    parent_bar: Bar,
) -> CanonicalGeometry:
    """Return only source-supported family geometry for one trigger."""

    if result.status != "TRIGGERED" or result.family is None or result.direction is None:
        raise ValueError("canonical geometry requires a triggered family")

    family = result.family
    subtype = result.subtype
    target = _directional_extreme(parent_bar, result.direction)

    if family == "STRAT_212_REVERSAL":
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="DEFINED",
            target=target,
            target_source="parent_2_far_extreme",
            stop=float(result.invalidation_level),
            stop_source="other_side_of_inside_bar",
            notes="2-1-2 reversal: inside-bar break, opposite side of 1 stop, parent 2 magnitude.",
        )

    if family == "STRAT_212_CONTINUATION":
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="STOP_ONLY",
            target=None,
            target_source=None,
            stop=float(result.invalidation_level),
            stop_source="other_side_of_inside_bar",
            notes="Inside-bar risk box is defined; no source-frozen standalone continuation magnitude is encoded.",
        )

    if family == "STRAT_312":
        if subtype == "REVERSAL":
            return CanonicalGeometry(
                family=family,
                subtype=subtype,
                status="DEFINED",
                target=target,
                target_source="parent_3_far_extreme",
                stop=float(result.invalidation_level),
                stop_source="other_side_of_inside_bar",
                notes="3-1-2 reversal uses the inside bar for risk and parent 3 extreme for magnitude.",
            )
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="STOP_ONLY",
            target=None,
            target_source=None,
            stop=float(result.invalidation_level),
            stop_source="other_side_of_inside_bar",
            notes="3-1-2 continuation target is not frozen by the reversal-rule source.",
        )

    if family == "STRAT_222_REVERSAL":
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="TARGET_ONLY",
            target=target,
            target_source="bar_before_trigger_bar_far_extreme",
            stop=None,
            stop_source="entry_bar_stop_requires_lower_timeframe_definition",
            notes="2-2 reversal magnitude is X far extreme; stop is entry-bar based and is not inferred here.",
        )

    if family == "STRAT_322_REVERSAL":
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="TARGET_ONLY",
            target=target,
            target_source="parent_3_far_extreme",
            stop=None,
            stop_source="entry_bar_stop_requires_lower_timeframe_definition",
            notes="3-2-2 reversal magnitude is the 3 far extreme; stop is not inferred from the 30m trigger bar.",
        )

    if family == "OTHER:strat_122":
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="UNRESOLVED",
            target=None,
            target_source=None,
            stop=None,
            stop_source="entry_bar_stop_requires_lower_timeframe_definition",
            notes="1-2-2 stop is entry-bar based; target formula remains unresolved pending explicit family proof.",
        )

    if family in {"STRAT_32_CONTINUATION", "STRAT_32_REVERSAL"}:
        return CanonicalGeometry(
            family=family,
            subtype=subtype,
            status="NO_OWN_MAGNITUDE",
            target=None,
            target_source="higher_timeframe_required",
            stop=None,
            stop_source="tight_at_trigger_line_not_far_side_of_3",
            notes="Direct 3-2 has no magnitude of its own and must not use the far side of the outside bar as stop.",
        )

    return CanonicalGeometry(
        family=family,
        subtype=subtype,
        status="UNRESOLVED",
        target=None,
        target_source=None,
        stop=None,
        stop_source=None,
        notes="No source-frozen family geometry rule.",
    )
