"""options_manager/validation/no_trade_reasons.py

No-trade reason tracker -- Increment 25K. A rejected or skipped trade
idea has, until now, disappeared the moment it was passed on -- nothing
in this repo recorded *why* a setup was skipped, so there was no way to
tell whether the validation layers above (`proof_packet.py`,
`proof_packet_intake.py`) were actually saving anyone from bad trades or
just adding friction. `NoTradeDecision` turns a "WAIT" or "AVOID" call
into structured evidence: a ticker, the direction that was being
considered, when the call was made, which reasons applied, whether it
was a hard block or just a flagged concern, and where the reasoning came
from.

Two ways to produce one:

- `record_no_trade_decision()` takes a raw manual dict (typed in by
  hand -- ticker, attempted_direction, timestamp_or_session, reasons,
  blocking, and optional notes/source) and returns a structured
  `NoTradeDecisionResult`, the same non-throwing pattern
  `check_proof_packet_intake()` established in Increment 25J. This is
  the path for purely human judgment calls that no automated check can
  see: `CHASING_CANDLE`, `EMOTIONAL_TRADE`, `AGAINST_GEX_REGIME`, and so
  on.
- `build_no_trade_decision_from_intake()` takes a prior
  `proof_packet_intake.IntakeResult` and derives the applicable
  `NoTradeReason` values automatically from its `missing_fields` and
  `blocking_reasons` -- so a rejected `ProofPacket` becomes a
  `NoTradeDecision` without re-typing the same reasons by hand.
  `reasons_from_intake_result()` is the pure mapping function underneath
  it, exposed separately so the mapping itself can be tested and reused.

This module records reasons; it does not act on them. Nothing here
places an order, changes a scanner setting, or promotes anything to
`FixtureStatus.CLEAN_COMPLETE_FIXTURE`. It performs no I/O of any kind:
no candle fetch, no option-chain fetch, no market-data fetch, no broker
call, no order placement, no execution, no alert sending, no file access
at runtime, no network calls, no MCP calls, no system-clock reads. Does
not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, options_manager.scanner, or risk/risk_engine.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from .proof_packet_intake import IntakeResult


class NoTradeReason(str, Enum):
    """Why a trade idea was skipped or rejected. Deliberately covers both
    checkable failures (a `ProofPacket` field was missing or invalid) and
    purely human judgment calls (`CHASING_CANDLE`, `EMOTIONAL_TRADE`)
    that no automated check can see."""

    MISSING_SETUP = "missing_setup"
    MISSING_TRIGGER = "missing_trigger"
    MISSING_INVALIDATION = "missing_invalidation"
    MISSING_TARGET = "missing_target"
    MISSING_CONTRACT_DATA = "missing_contract_data"
    WIDE_SPREAD = "wide_spread"
    LOW_VOLUME = "low_volume"
    LOW_OPEN_INTEREST = "low_open_interest"
    TOO_SHORT_DTE = "too_short_dte"
    PREMIUM_TOO_EXPENSIVE = "premium_too_expensive"
    RISK_TOO_HIGH = "risk_too_high"
    AGAINST_SPY_QQQ_CONTEXT = "against_spy_qqq_context"
    AGAINST_GEX_REGIME = "against_gex_regime"
    NO_SOURCE_REFERENCE = "no_source_reference"
    CHASING_CANDLE = "chasing_candle"
    EMOTIONAL_TRADE = "emotional_trade"
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class NoTradeDecision:
    """One recorded "no trade" call. `reasons` may hold more than one
    `NoTradeReason` -- a real skip is rarely down to exactly one cause.
    `blocking` distinguishes a hard block (the trade could not have been
    taken as described) from a flagged-but-not-necessarily-fatal concern
    (e.g. a single WARN-level liquidity note)."""

    ticker: str
    attempted_direction: str
    timestamp_or_session: str
    reasons: tuple[NoTradeReason, ...]
    blocking: bool
    notes: str = ""
    source: str = ""


@dataclass(frozen=True, kw_only=True)
class NoTradeDecisionResult:
    """Outcome of `record_no_trade_decision()`. `decision` is populated
    only when `payload` normalized cleanly; `errors` names every problem
    found, never just the first -- mirroring
    `proof_packet_intake.IntakeResult`."""

    valid: bool
    errors: tuple[str, ...] = ()
    decision: Optional[NoTradeDecision] = None


# Maps a ProofPacket field name to the NoTradeReason it implies when
# that field is missing (from IntakeResult.missing_fields) or reported
# as structurally invalid (parsed out of IntakeResult.blocking_reasons).
# Liquidity fields deliberately map to distinct, more specific reasons
# (LOW_VOLUME, LOW_OPEN_INTEREST, WIDE_SPREAD) rather than one generic
# bucket, since a rejected trade's own liquidity story is worth keeping
# separate from a plain missing-quote case.
_FIELD_TO_REASON: dict[str, NoTradeReason] = {
    "setup_type": NoTradeReason.MISSING_SETUP,
    "entry_trigger": NoTradeReason.MISSING_TRIGGER,
    "underlying_invalidation": NoTradeReason.MISSING_INVALIDATION,
    "premium_stop": NoTradeReason.MISSING_INVALIDATION,
    "target_1": NoTradeReason.MISSING_TARGET,
    "target_2": NoTradeReason.MISSING_TARGET,
    "bid": NoTradeReason.MISSING_CONTRACT_DATA,
    "ask": NoTradeReason.MISSING_CONTRACT_DATA,
    "strike": NoTradeReason.MISSING_CONTRACT_DATA,
    "premium": NoTradeReason.MISSING_CONTRACT_DATA,
    "max_contracts": NoTradeReason.MISSING_CONTRACT_DATA,
    "max_dollar_risk": NoTradeReason.MISSING_CONTRACT_DATA,
    "volume": NoTradeReason.LOW_VOLUME,
    "open_interest": NoTradeReason.LOW_OPEN_INTEREST,
    "spread_percent": NoTradeReason.WIDE_SPREAD,
    "source_references": NoTradeReason.NO_SOURCE_REFERENCE,
}

_BLOCKING_REASON_PREFIXES = ("missing/invalid ", "missing ")


def _field_name_from_blocking_reason(reason: str) -> Optional[str]:
    for prefix in _BLOCKING_REASON_PREFIXES:
        if reason.startswith(prefix):
            return reason[len(prefix) :]
    return None


def reasons_from_intake_result(intake_result: IntakeResult) -> tuple[NoTradeReason, ...]:
    """Pure mapping: derives the `NoTradeReason` values implied by an
    `IntakeResult`'s `missing_fields` and `blocking_reasons`. Returns an
    empty tuple when the intake was valid -- there is nothing to
    report. Order is stable and duplicate-free."""
    if intake_result.valid:
        return ()

    reasons: list[NoTradeReason] = []
    seen: set[NoTradeReason] = set()

    def _add(reason: Optional[NoTradeReason]) -> None:
        if reason is not None and reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    for field_name in intake_result.missing_fields:
        _add(_FIELD_TO_REASON.get(field_name, NoTradeReason.OTHER))

    for blocking_reason in intake_result.blocking_reasons:
        field_name = _field_name_from_blocking_reason(blocking_reason)
        if field_name is None:
            _add(NoTradeReason.OTHER)
        else:
            _add(_FIELD_TO_REASON.get(field_name, NoTradeReason.OTHER))

    if not reasons:
        # Invalid with no field-level detail available -- still record
        # that something blocked it, rather than silently reporting no
        # reasons at all.
        reasons.append(NoTradeReason.OTHER)

    return tuple(reasons)


def build_no_trade_decision_from_intake(
    *,
    ticker: str,
    attempted_direction: str,
    timestamp_or_session: str,
    intake_result: IntakeResult,
    notes: str = "",
    source: str = "proof_packet_intake",
) -> NoTradeDecision:
    """Builds a `NoTradeDecision` directly from a prior
    `proof_packet_intake.check_proof_packet_intake()` result -- no
    re-typing the reasons a `ProofPacket` already failed on. `blocking`
    is simply `not intake_result.valid`; `reasons` comes from
    `reasons_from_intake_result()`."""
    return NoTradeDecision(
        ticker=ticker,
        attempted_direction=attempted_direction,
        timestamp_or_session=timestamp_or_session,
        reasons=reasons_from_intake_result(intake_result),
        blocking=not intake_result.valid,
        notes=notes,
        source=source,
    )


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _coerce_reason(value: Any) -> NoTradeReason:
    if isinstance(value, NoTradeReason):
        return value
    text = str(value).strip().lower()
    for member in NoTradeReason:
        if member.value == text or member.name.lower() == text:
            return member
    raise ValueError(f"{value!r} is not a valid NoTradeReason")


def _coerce_blocking(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
    raise ValueError(f"{value!r} is not a valid boolean")


def record_no_trade_decision(payload: Any) -> NoTradeDecisionResult:
    """Normalizes a manual dict-like payload into a `NoTradeDecision`.
    Never raises regardless of how malformed `payload` is -- every
    problem is collected into `NoTradeDecisionResult.errors` instead."""
    if not isinstance(payload, Mapping):
        return NoTradeDecisionResult(
            valid=False,
            errors=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    errors: list[str] = []

    for name in ("ticker", "attempted_direction", "timestamp_or_session"):
        if not _is_present(payload.get(name)):
            errors.append(f"missing {name}")

    raw_reasons = payload.get("reasons")
    reasons: tuple[NoTradeReason, ...] = ()
    if not _is_present(raw_reasons):
        errors.append("missing reasons")
    else:
        candidates = raw_reasons if isinstance(raw_reasons, (list, tuple)) else (raw_reasons,)
        coerced: list[NoTradeReason] = []
        for candidate in candidates:
            try:
                coerced.append(_coerce_reason(candidate))
            except ValueError as exc:
                errors.append(f"invalid value in reasons: {exc}")
        reasons = tuple(coerced)

    if "blocking" not in payload or payload.get("blocking") is None:
        errors.append("missing blocking")
        blocking = False
    else:
        try:
            blocking = _coerce_blocking(payload["blocking"])
        except ValueError as exc:
            errors.append(f"invalid value for blocking: {exc}")
            blocking = False

    if errors:
        return NoTradeDecisionResult(valid=False, errors=tuple(errors))

    decision = NoTradeDecision(
        ticker=str(payload["ticker"]),
        attempted_direction=str(payload["attempted_direction"]),
        timestamp_or_session=str(payload["timestamp_or_session"]),
        reasons=reasons,
        blocking=blocking,
        notes=str(payload["notes"]) if _is_present(payload.get("notes")) else "",
        source=str(payload["source"]) if _is_present(payload.get("source")) else "",
    )
    return NoTradeDecisionResult(valid=True, decision=decision)
