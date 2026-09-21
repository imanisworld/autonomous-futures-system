"""Fail-closed bridge from ns-v0.1 events to internal options paper simulation.

This module does NOT route the observer into the running scanner and does NOT
submit Webull orders. It prepares a fully specified, one-contract research
candidate only when an ns-v0.1 event has explicit stop/target geometry and an
exact option snapshot. It then reuses options_manager's existing risk,
contract-quality, paper-simulation, and local broker-boundary code.

Why Webull submit stays blocked here:
- the current Webull sandbox paper adapter can BUY_TO_OPEN/cancel/read detail;
- it has no proven SELL_TO_CLOSE/filled-position exit lifecycle;
- ns-v0.1 deliberately does not invent a stop/target policy.

A local Webull preview intent is produced for reconciliation/plumbing review,
but webull_submit_allowed is always False in this version. A future version
must earn a new version/approval after round-trip sandbox lifecycle proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from typing import Literal

from alert_ranker.non_strat_coverage import (
    FAMILIES,
    OBSERVER_VERSION,
    NonStratEvent,
)

from .broker_boundary import (
    OptionsBrokerPreviewRequest,
    OptionsBrokerPreviewResult,
    validate_preview_boundary,
)
from .config import OptionsManagerConfig
from .contract_quality import (
    ContractMarketSnapshot,
    ContractQualityResult,
    evaluate_contract_quality,
)
from .models import OptionTradePacket
from .paper_sim import PaperSimResult, simulate_round_trip
from .risk_gate import RiskGateResult, evaluate_packet

TRACK_ID = "OPTIONS_NON_STRAT_PAPER_TRACK"
TRACK_VERSION = "nst-v0.1"
MAX_TRACK_CONTRACTS = 1
# Forward-paper entries must use a quote captured at/after the event could first
# be known from delayed SIP, and within one scanner cadence. Historical/backfill
# events remain research outcomes; they cannot silently become forward entries.
MAX_DECISION_LAG_SECONDS = 300
MIN_REMAINING_RR = 1.0
WEBULL_BLOCK_REASON = "webull_round_trip_lifecycle_unproven"

# Pre-registered stop/target geometry rules, keyed by ``geometry_rule_id``.
# EMPTY in nst-v0.1: no rule has been pre-registered, so every plan is
# rejected with ``geometry_rule_not_registered`` until a prereg document
# adds an entry here (id -> the docs/ path that freezes its definition).
# A plan whose rule is not in this table is hindsight geometry with a label.
# Tests register a throwaway rule via ``register_geometry_rule`` and remove
# it again; runtime code never calls it.
GEOMETRY_RULES: dict[str, str] = {}


def register_geometry_rule(rule_id: str, definition_doc: str) -> None:
    """Add a pre-registered geometry rule (for preregs and tests only)."""
    key = (rule_id or "").strip()
    if not key or not (definition_doc or "").strip():
        raise ValueError("rule_id and definition_doc are required")
    GEOMETRY_RULES[key] = definition_doc.strip()


def unregister_geometry_rule(rule_id: str) -> None:
    GEOMETRY_RULES.pop((rule_id or "").strip(), None)

# OptionTradePacket's legacy Signa fields are required scalars. ns-v0.1 does
# not source Signa/GEX. Use explicit unavailable sentinels that force warnings
# rather than fabricating direction-aligned context.
SIGNA_UNAVAILABLE_SCORE = 0
SIGNA_UNAVAILABLE_GRADE = "C"
SIGNA_UNAVAILABLE_BIAS = "NEUTRAL"


@dataclass(frozen=True)
class NonStratPaperPlan:
    event: NonStratEvent
    underlying_invalidation: float
    underlying_target: float
    # Frozen/pre-registered geometry authority. A paper plan without provenance
    # is indistinguishable from hindsight-picked stop/target geometry.
    geometry_rule_id: str
    source_references: tuple[str, ...]
    contract_strike: float
    contract_expiry: date
    entry_snapshot: ContractMarketSnapshot
    quantity: int = 1
    account_tag: str = "agentic_micro_account"
    source: str = "options_non_strat_paper_track"


@dataclass(frozen=True)
class NonStratPaperPreparation:
    track_id: str
    track_version: str
    status: Literal["INTERNAL_READY", "REJECTED", "DATA_BLOCKED"]
    reason: str
    plan: NonStratPaperPlan
    packet: OptionTradePacket | None
    risk_result: RiskGateResult | None
    quality_result: ContractQualityResult | None
    preview_request: OptionsBrokerPreviewRequest | None
    preview_result: OptionsBrokerPreviewResult | None
    webull_submit_allowed: bool
    webull_block_reason: str
    ticket_id: str | None


@dataclass(frozen=True)
class NonStratPaperRoundTrip:
    track_id: str
    track_version: str
    status: Literal["SIMULATED", "REJECTED", "DATA_BLOCKED"]
    reason: str
    ticket_id: str | None
    result: PaperSimResult | None


def _stable_ticket(plan: NonStratPaperPlan) -> str:
    raw = "|".join(
        (
            TRACK_VERSION,
            plan.event.episode_id,
            plan.event.symbol,
            plan.event.family,
            plan.event.direction,
            plan.event.bar_start,
            f"{plan.contract_strike:.8f}",
            plan.contract_expiry.isoformat(),
            str(plan.entry_snapshot.contract_symbol or ""),
        )
    ).encode("utf-8")
    return "nst-" + hashlib.sha256(raw).hexdigest()[:24]


def _created_at(event: NonStratEvent) -> datetime:
    return datetime.fromisoformat(event.bar_close.replace("Z", "+00:00"))


def _event_visibility(event: NonStratEvent) -> datetime | None:
    try:
        value = datetime.fromisoformat(
            event.earliest_sip_visibility.replace("Z", "+00:00")
        )
    except (AttributeError, TypeError, ValueError):
        return None
    return value if value.tzinfo is not None else None


def _remaining_rr(
    direction: str, decision_price: float, stop: float, target: float
) -> float | None:
    if direction == "LONG":
        risk = decision_price - stop
        reward = target - decision_price
    elif direction == "SHORT":
        risk = stop - decision_price
        reward = decision_price - target
    else:
        return None
    if risk <= 0:
        return None
    return reward / risk


def _decision_geometry_reason(plan: NonStratPaperPlan) -> str | None:
    snap = plan.entry_snapshot
    if snap.underlying_price is None:
        return "underlying_price_missing"
    price = float(snap.underlying_price)
    stop = float(plan.underlying_invalidation)
    target = float(plan.underlying_target)
    if plan.event.direction == "LONG":
        if not stop < price < target:
            return "decision_price_outside_long_bracket"
    elif plan.event.direction == "SHORT":
        if not target < price < stop:
            return "decision_price_outside_short_bracket"
    else:
        return "direction_invalid"
    rr = _remaining_rr(plan.event.direction, price, stop, target)
    if rr is None or rr < MIN_REMAINING_RR:
        return "decision_price_remaining_rr_below_floor"
    return None


def _geometry_reason(plan: NonStratPaperPlan) -> str | None:
    entry = float(plan.event.trigger_price)
    stop = float(plan.underlying_invalidation)
    target = float(plan.underlying_target)
    if entry <= 0:
        return "entry_price_invalid"
    if plan.event.direction == "LONG":
        if not stop < entry < target:
            return "long_geometry_requires_stop_below_entry_below_target"
    elif plan.event.direction == "SHORT":
        if not target < entry < stop:
            return "short_geometry_requires_target_below_entry_below_stop"
    else:
        return "direction_invalid"
    return None


def _input_reason(plan: NonStratPaperPlan) -> tuple[str | None, bool]:
    event = plan.event
    if event.observer_version != OBSERVER_VERSION:
        return "observer_version_mismatch", False
    if event.family not in FAMILIES:
        return "family_not_in_ns_v0_1", False
    if not event.episode_id:
        return "episode_id_missing", True
    if not plan.geometry_rule_id.strip():
        return "geometry_rule_id_missing", True
    if plan.geometry_rule_id.strip() not in GEOMETRY_RULES:
        return "geometry_rule_not_registered", True
    if not plan.source_references or any(
        not str(ref).strip() for ref in plan.source_references
    ):
        return "geometry_source_references_missing", True
    if plan.quantity != MAX_TRACK_CONTRACTS:
        return "research_track_requires_exactly_one_contract", False
    if plan.contract_strike <= 0:
        return "contract_strike_invalid", False

    snap = plan.entry_snapshot
    if (snap.ticker or "").strip().upper() != event.symbol.strip().upper():
        return "snapshot_ticker_mismatch", False
    if not snap.contract_symbol:
        return "contract_symbol_missing", True
    if snap.ask is None or snap.ask <= 0:
        return "entry_ask_missing_or_invalid", True
    if snap.underlying_price is None:
        return "underlying_price_missing", True
    if snap.quote_timestamp is None or snap.quote_timestamp.tzinfo is None:
        return "decision_quote_timestamp_missing_or_naive", True

    visibility = _event_visibility(event)
    if visibility is None:
        return "event_visibility_timestamp_invalid", True
    lag = (snap.quote_timestamp - visibility).total_seconds()
    if lag < 0:
        return "decision_quote_precedes_event_visibility", True
    if lag > MAX_DECISION_LAG_SECONDS:
        return "decision_quote_too_late_for_forward_paper", True

    geometry = _geometry_reason(plan)
    if geometry:
        return geometry, False
    decision_geometry = _decision_geometry_reason(plan)
    if decision_geometry:
        return decision_geometry, False
    return None, False


def _packet(plan: NonStratPaperPlan, config: OptionsManagerConfig) -> OptionTradePacket:
    direction = "CALL" if plan.event.direction == "LONG" else "PUT"
    # Use the actual decision-time underlying quote, never the earlier event
    # close. This prevents delayed detection from receiving hindsight entry
    # geometry. Signa/GEX remain explicit unavailable telemetry.
    decision_price = float(plan.entry_snapshot.underlying_price)
    return OptionTradePacket(
        ticker=plan.event.symbol,
        direction=direction,
        entry_price=decision_price,
        price_target=float(plan.underlying_target),
        signa_score=SIGNA_UNAVAILABLE_SCORE,
        signa_grade=SIGNA_UNAVAILABLE_GRADE,
        signa_bias=SIGNA_UNAVAILABLE_BIAS,
        gex_regime="",
        gex_wall_above=None,
        gex_wall_below=None,
        contract_strike=float(plan.contract_strike),
        contract_expiry=plan.contract_expiry,
        # Bind the legacy full-debit cap to the observed ask rather than
        # silently substituting the configured ceiling as if it were a quote.
        max_premium=float(plan.entry_snapshot.ask),
        max_contracts=plan.quantity,
        account_tag=plan.account_tag,
        source=f"{plan.source}:{plan.geometry_rule_id}",
        created_at=_created_at(plan.event),
        status="PENDING",
        rejection_reason=None,
    )


def _preview_request(
    plan: NonStratPaperPlan,
    packet: OptionTradePacket,
    ticket_id: str,
) -> OptionsBrokerPreviewRequest:
    ask = float(plan.entry_snapshot.ask or 0.0)
    estimated_notional = ask * 100.0 * plan.quantity
    return OptionsBrokerPreviewRequest(
        ticket_id=ticket_id,
        confirmation_id=f"research:{plan.event.episode_id}",
        ticker=packet.ticker,
        direction=packet.direction,
        order_action="BUY_TO_OPEN",
        quantity=plan.quantity,
        contract_strike=plan.contract_strike,
        contract_expiry=plan.contract_expiry,
        limit_price=ask,
        estimated_notional=estimated_notional,
        account_tag=plan.account_tag,
        source=plan.source,
        dry_run_only=True,
        executable=False,
    )


def prepare_non_strat_paper_candidate(
    plan: NonStratPaperPlan,
    config: OptionsManagerConfig,
) -> NonStratPaperPreparation:
    """Validate one ns-v0.1 event for the internal research paper track."""

    input_reason, data_blocked = _input_reason(plan)
    if input_reason:
        return NonStratPaperPreparation(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED" if data_blocked else "REJECTED",
            reason=input_reason,
            plan=plan,
            packet=None,
            risk_result=None,
            quality_result=None,
            preview_request=None,
            preview_result=None,
            webull_submit_allowed=False,
            webull_block_reason=WEBULL_BLOCK_REASON,
            ticket_id=None,
        )

    packet = _packet(plan, config)
    risk = evaluate_packet(packet, config)
    if risk.status != "APPROVED":
        return NonStratPaperPreparation(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED" if risk.status == "DATA_BLOCKED" else "REJECTED",
            reason=f"risk_gate:{risk.failed_rule}:{risk.reason}",
            plan=plan,
            packet=packet,
            risk_result=risk,
            quality_result=None,
            preview_request=None,
            preview_result=None,
            webull_submit_allowed=False,
            webull_block_reason=WEBULL_BLOCK_REASON,
            ticket_id=None,
        )

    quality = evaluate_contract_quality(packet, plan.entry_snapshot, config)
    if quality.status != "APPROVED":
        return NonStratPaperPreparation(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED" if quality.status == "DATA_BLOCKED" else "REJECTED",
            reason=f"contract_quality:{quality.failed_rule}:{quality.reason}",
            plan=plan,
            packet=packet,
            risk_result=risk,
            quality_result=quality,
            preview_request=None,
            preview_result=None,
            webull_submit_allowed=False,
            webull_block_reason=WEBULL_BLOCK_REASON,
            ticket_id=None,
        )

    ticket_id = _stable_ticket(plan)
    request = _preview_request(plan, packet, ticket_id)
    preview = validate_preview_boundary(request, config)
    if not preview.preview_ready:
        return NonStratPaperPreparation(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED" if preview.status == "DATA_BLOCKED" else "REJECTED",
            reason=f"broker_boundary:{preview.failed_stage}:{preview.reason}",
            plan=plan,
            packet=packet,
            risk_result=risk,
            quality_result=quality,
            preview_request=request,
            preview_result=preview,
            webull_submit_allowed=False,
            webull_block_reason=WEBULL_BLOCK_REASON,
            ticket_id=ticket_id,
        )

    return NonStratPaperPreparation(
        track_id=TRACK_ID,
        track_version=TRACK_VERSION,
        status="INTERNAL_READY",
        reason="",
        plan=plan,
        packet=packet,
        risk_result=risk,
        quality_result=quality,
        preview_request=request,
        preview_result=preview,
        webull_submit_allowed=False,
        webull_block_reason=WEBULL_BLOCK_REASON,
        ticket_id=ticket_id,
    )


def simulate_non_strat_round_trip(
    preparation: NonStratPaperPreparation,
    exit_snapshot: ContractMarketSnapshot,
    config: OptionsManagerConfig,
) -> NonStratPaperRoundTrip:
    """Run the existing internal paper simulator on the exact same contract."""

    if preparation.status != "INTERNAL_READY":
        return NonStratPaperRoundTrip(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED" if preparation.status == "DATA_BLOCKED" else "REJECTED",
            reason=f"preparation_not_ready:{preparation.reason}",
            ticket_id=preparation.ticket_id,
            result=None,
        )
    if (
        preparation.packet is None
        or preparation.risk_result is None
        or preparation.quality_result is None
    ):
        return NonStratPaperRoundTrip(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="DATA_BLOCKED",
            reason="preparation_missing_gate_artifacts",
            ticket_id=preparation.ticket_id,
            result=None,
        )

    entry = preparation.plan.entry_snapshot
    if (exit_snapshot.ticker or "").strip().upper() != preparation.packet.ticker:
        return NonStratPaperRoundTrip(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="REJECTED",
            reason="exit_snapshot_ticker_mismatch",
            ticket_id=preparation.ticket_id,
            result=None,
        )
    if exit_snapshot.contract_symbol != entry.contract_symbol:
        return NonStratPaperRoundTrip(
            track_id=TRACK_ID,
            track_version=TRACK_VERSION,
            status="REJECTED",
            reason="exit_snapshot_contract_mismatch",
            ticket_id=preparation.ticket_id,
            result=None,
        )

    result = simulate_round_trip(
        preparation.packet,
        entry,
        exit_snapshot,
        preparation.risk_result,
        preparation.quality_result,
        config,
    )
    return NonStratPaperRoundTrip(
        track_id=TRACK_ID,
        track_version=TRACK_VERSION,
        status=result.status,
        reason=result.reason,
        ticket_id=preparation.ticket_id,
        result=result,
    )


__all__ = [
    "MAX_TRACK_CONTRACTS",
    "MAX_DECISION_LAG_SECONDS",
    "MIN_REMAINING_RR",
    "TRACK_ID",
    "TRACK_VERSION",
    "WEBULL_BLOCK_REASON",
    "GEOMETRY_RULES",
    "register_geometry_rule",
    "unregister_geometry_rule",
    "NonStratPaperPlan",
    "NonStratPaperPreparation",
    "NonStratPaperRoundTrip",
    "prepare_non_strat_paper_candidate",
    "simulate_non_strat_round_trip",
]