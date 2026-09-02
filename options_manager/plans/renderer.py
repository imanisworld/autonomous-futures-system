"""Pure human-facing rendering for Phase-1 options thesis updates.

This module converts an already-evaluated :class:`PlanUpdate` into one stable,
human-readable advisory update.  It performs no I/O, sends no Discord message,
fetches no market data, and has no broker/order/execution imports.

The renderer deliberately respects the plan manager's material-change decision:
unchanged polls and Signa-only telemetry updates are marked ``should_emit=False``
so a future notifier does not turn repeated upstream state into repeated trade
calls.  It also never turns ``HIGH_CONVICTION_CANDIDATE`` into a sizing
instruction; conviction remains an evidence/display label only.

Contract terms are intentionally not invented here.  ``TradePlanSnapshot``
currently stores the underlying thesis (entry, invalidation, structural targets,
proof state, lifecycle, provenance) but not the full canonical contract packet.
A later notifier/contract-plan integration must render those facts from the
canonical proof authority rather than fabricating them in this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .base import ConvictionBand, PlanStatus, PlanUpdate, TradePlanSnapshot


class PlanUpdateKind(str, Enum):
    """Stable event vocabulary for a future notifier/journal consumer."""

    NEW_THESIS = "NEW_THESIS"
    TRIGGERED = "TRIGGERED"
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    EXITED = "EXITED"
    EXPIRED = "EXPIRED"
    TARGETS_UPDATED = "TARGETS_UPDATED"
    LEVELS_UPDATED = "LEVELS_UPDATED"
    PROOF_UPDATED = "PROOF_UPDATED"
    CONVICTION_UPDATED = "CONVICTION_UPDATED"
    THESIS_UPDATED = "THESIS_UPDATED"
    TELEMETRY_ONLY = "TELEMETRY_ONLY"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True)
class RenderedPlanUpdate:
    """Transport-neutral rendering result.

    ``should_emit`` mirrors the plan manager's material-change authority.  The
    text can still be stored in a local/shadow journal when ``False``; it should
    not be interpreted as permission to notify a user.
    """

    kind: PlanUpdateKind
    should_emit: bool
    title: str
    summary: str
    body: str
    status: PlanStatus
    actionable: bool
    conviction: ConvictionBand
    material_reasons: tuple[str, ...]


def _format_number(value: float | None) -> str:
    if value is None:
        return "UNRESOLVED"
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_target(price: float | None, source: str | None) -> str:
    text = _format_number(price)
    if source:
        return f"{text} ({source})"
    return text


def _classify(update: PlanUpdate) -> PlanUpdateKind:
    snapshot = update.snapshot
    reasons = set(update.material_reasons)

    if not update.should_emit_update:
        return (
            PlanUpdateKind.TELEMETRY_ONLY
            if update.telemetry_only
            else PlanUpdateKind.UNCHANGED
        )

    if "new_plan" in reasons:
        return PlanUpdateKind.NEW_THESIS

    if "status_changed" in reasons:
        status_kind = {
            PlanStatus.TRIGGERED: PlanUpdateKind.TRIGGERED,
            PlanStatus.ACTIVE: PlanUpdateKind.ACTIVE,
            PlanStatus.INVALIDATED: PlanUpdateKind.INVALIDATED,
            PlanStatus.EXITED: PlanUpdateKind.EXITED,
            PlanStatus.EXPIRED: PlanUpdateKind.EXPIRED,
        }.get(snapshot.status)
        if status_kind is not None:
            return status_kind

    # Price-plan changes outrank softer proof/label changes because a human
    # needs to see altered trade levels first.
    if "targets_changed" in reasons:
        return PlanUpdateKind.TARGETS_UPDATED
    if "entry_or_invalidation_changed" in reasons:
        return PlanUpdateKind.LEVELS_UPDATED
    if "actionability_changed" in reasons or "blocking_reasons_changed" in reasons:
        return PlanUpdateKind.PROOF_UPDATED
    if "conviction_changed" in reasons:
        return PlanUpdateKind.CONVICTION_UPDATED
    return PlanUpdateKind.THESIS_UPDATED


def _summary(snapshot: TradePlanSnapshot, kind: PlanUpdateKind) -> str:
    if kind == PlanUpdateKind.TELEMETRY_ONLY:
        return "No material thesis change; observational telemetry changed only."
    if kind == PlanUpdateKind.UNCHANGED:
        return "Same thesis, same proof, same levels; no user-facing update required."

    if snapshot.status == PlanStatus.WATCHING:
        return "Watching only; the thesis is not actionable."
    if snapshot.status == PlanStatus.TRIGGERED:
        if snapshot.actionable:
            return "Triggered and canonically proven; advisory thesis is actionable."
        return "Mechanical trigger exists, but proof is incomplete; no entry call."
    if snapshot.status == PlanStatus.ACTIVE:
        if snapshot.actionable:
            return "Human-marked ACTIVE thesis; manage against the stated invalidation and targets."
        return (
            "ACTIVE thesis, but current proof is no longer actionable; management only, "
            "not a new-entry signal."
        )
    if snapshot.status == PlanStatus.INVALIDATED:
        return "Thesis invalidated; this lifecycle generation must not be reopened."
    if snapshot.status == PlanStatus.EXITED:
        return "Thesis exited; this lifecycle generation is complete."
    if snapshot.status == PlanStatus.EXPIRED:
        return "Thesis expired; this lifecycle generation is complete."
    return "Advisory thesis updated."


def _conviction_text(snapshot: TradePlanSnapshot) -> str:
    if snapshot.conviction == ConvictionBand.HIGH_CONVICTION_CANDIDATE:
        return (
            "HIGH_CONVICTION_CANDIDATE "
            f"({snapshot.conviction_confirmation_count} independent confirmations; "
            "evidence label only, no sizing increase)"
        )
    if snapshot.conviction == ConvictionBand.STANDARD:
        return f"STANDARD ({snapshot.conviction_confirmation_count} independent confirmations)"
    return (
        "OBSERVATIONAL "
        f"({snapshot.conviction_confirmation_count} independent confirmations)"
    )


def _body(snapshot: TradePlanSnapshot, update: PlanUpdate, kind: PlanUpdateKind) -> str:
    lines = [
        f"Status: {snapshot.status.value.upper()}",
        f"Setup: {snapshot.setup_type} / {snapshot.timeframe}",
        f"Actionable: {'YES' if snapshot.actionable else 'NO'}",
        f"Entry trigger: {_format_number(snapshot.entry_trigger)}",
        f"Stop / invalidation: {_format_number(snapshot.underlying_invalidation)}",
        f"Target 1: {_format_target(snapshot.target_1, snapshot.target_1_source)}",
        f"Target 2: {_format_target(snapshot.target_2, snapshot.target_2_source)}",
        f"R:R T1: {_format_number(snapshot.rr_1)}",
        f"R:R T2: {_format_number(snapshot.rr_2)}",
        f"Conviction: {_conviction_text(snapshot)}",
    ]

    if update.material_reasons:
        lines.append("Changed: " + ", ".join(update.material_reasons))
    elif kind == PlanUpdateKind.TELEMETRY_ONLY:
        lines.append("Changed: observational telemetry only")
    else:
        lines.append("Changed: none")

    if snapshot.blocking_reasons:
        lines.append("Blocks: " + " | ".join(snapshot.blocking_reasons))
    if snapshot.warnings:
        lines.append("Warnings: " + " | ".join(snapshot.warnings))
    if snapshot.source_references:
        lines.append("Proof refs: " + " | ".join(snapshot.source_references))

    # Signa is intentionally presented only as observational telemetry counts.
    # Direction/grade/score are not elevated into the trade plan text.
    lines.append(
        "Signa: OBSERVATIONAL ONLY "
        f"(events={snapshot.signa_event_count}, repeats={snapshot.signa_repeat_count})"
    )
    lines.append("Contract plan: canonical contract facts required from proof authority; not invented here")
    return "\n".join(lines)


def render_plan_update(update: PlanUpdate) -> RenderedPlanUpdate:
    """Render one already-evaluated thesis update without side effects."""

    snapshot = update.snapshot
    kind = _classify(update)
    title = f"[options plan] {kind.value} {snapshot.ticker} {snapshot.direction}"
    summary = _summary(snapshot, kind)
    body = _body(snapshot, update, kind)

    return RenderedPlanUpdate(
        kind=kind,
        should_emit=bool(update.should_emit_update),
        title=title,
        summary=summary,
        body=body,
        status=snapshot.status,
        actionable=snapshot.actionable,
        conviction=snapshot.conviction,
        material_reasons=update.material_reasons,
    )
