"""options_manager/validation/proof_packet_intake.py

Manual intake helper for `proof_packet.py` -- Increment 25J. `ProofPacket`
and `validate_proof_packet()` (Increment 25I) give the pre-trade proof
model real teeth, but using them during market hours still meant hand-
constructing a frozen dataclass with 25+ keyword arguments -- too slow
for "I see a setup, does this pass?" This module is the fast path: take
a loose manual dict (typed in by hand, in a hurry, possibly incomplete or
with a typo'd key), normalize it into a `ProofPacket` where at all
possible, run it through the same `validate_proof_packet()` from
Increment 25I, and return one structured, non-throwing result.

This is advisory/manual intake only -- it never fetches a quote, a
candle, an option chain, or a broker order, and it never reads the
system clock. Every value the result reports comes from the payload the
human typed in, or from `validate_proof_packet()`'s own structural
checks. Nothing here auto-fills `created_at`, infers a missing field
from a post-trade outcome, or changes a `ProofPacket`'s `status`.

`check_proof_packet_intake()` never raises for a malformed payload (wrong
container type, wrong field type, unknown keys) -- every failure mode is
reported in the returned `IntakeResult` instead, because a helper meant
to be used quickly during market hours must never crash the one time a
value gets typed wrong.

Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
alert sending, no file access at runtime, no network calls, no MCP calls,
no system-clock reads. Does not import replay/replay_engine.py, the live
context.market_context loader, alert_ranker, options_companion,
execution, webhook, broker systems, options_manager.scanner, or
risk/risk_engine.py.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .proof_packet import ProofPacket, ProofPacketStatus, validate_proof_packet

_STR_FIELDS = (
    "ticker",
    "created_at",
    "setup_type",
    "timeframe",
    "entry_trigger",
    "underlying_invalidation",
    "premium_stop",
    "target_1",
    "target_2",
    "expiration",
    "spy_context",
    "qqq_context",
    "gex_context",
    "signa_context",
)

_FLOAT_FIELDS = ("strike", "premium", "bid", "ask", "spread_percent", "max_dollar_risk")

_INT_FIELDS = ("volume", "open_interest", "max_contracts")

_REQUIRED_FIELD_NAMES = tuple(
    f.name
    for f in dataclasses.fields(ProofPacket)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)

_KNOWN_FIELD_NAMES = frozenset(f.name for f in dataclasses.fields(ProofPacket))


@dataclass(frozen=True, kw_only=True)
class IntakeResult:
    """One manual intake check's outcome. `valid` is only ever true when
    both the payload normalized into a real `ProofPacket` and that
    packet passed `validate_proof_packet()` with no errors. `packet` is
    the normalized `ProofPacket` when construction succeeded (even if the
    packet itself is invalid), or `None` when normalization failed
    outright (a malformed payload, a value that could not be coerced to
    its field's type)."""

    valid: bool
    missing_fields: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    packet: Optional[ProofPacket] = None


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (tuple, list)):
        return len(value) > 0
    return True


def _coerce_status(value: Any) -> ProofPacketStatus:
    if isinstance(value, ProofPacketStatus):
        return value
    text = str(value).strip().lower()
    for member in ProofPacketStatus:
        if member.value == text or member.name.lower() == text:
            return member
    raise ValueError(f"{value!r} is not a valid ProofPacketStatus")


def _coerce_source_references(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value if _is_present(item))
    raise ValueError("source_references must be a string or a list/tuple of strings")


_PLACEHOLDER_BY_FIELD_KIND = {
    "str": "",
    "float": 0.0,
    "int": 0,
    "direction": "CALL",
    "status": ProofPacketStatus.WATCHING,
    "source_references": (),
}


def _field_kind(name: str) -> str:
    if name in _STR_FIELDS:
        return "str"
    if name in _FLOAT_FIELDS:
        return "float"
    if name in _INT_FIELDS:
        return "int"
    return name  # "direction", "status", "source_references"


def check_proof_packet_intake(payload: Any) -> IntakeResult:
    """Normalizes a manual dict-like payload into a `ProofPacket`,
    validates it with `validate_proof_packet()`, and returns one
    structured `IntakeResult` -- never raises, regardless of how
    malformed `payload` is.

    Missing required fields and structurally-invalid present fields
    (e.g. a strike of 0) are BOTH reported in the same pass -- a missing
    field never suppresses the other checks -- but `result.packet` is
    only ever populated when every required field was genuinely present
    and coerced cleanly; a packet built from placeholder stand-ins for
    missing fields is never exposed as if it were real data."""
    if not isinstance(payload, Mapping):
        return IntakeResult(
            valid=False,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    warnings: list[str] = []
    unknown_keys = sorted(set(payload.keys()) - _KNOWN_FIELD_NAMES)
    if unknown_keys:
        warnings.append(f"unrecognized field(s) ignored: {', '.join(unknown_keys)}")

    missing_fields = tuple(
        name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))
    )

    coercion_errors: list[str] = []
    normalized: dict[str, Any] = {}

    for name in _REQUIRED_FIELD_NAMES:
        if name in missing_fields:
            normalized[name] = _PLACEHOLDER_BY_FIELD_KIND[_field_kind(name)]
            continue
        raw_value = payload[name]
        try:
            if name in _STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _FLOAT_FIELDS:
                normalized[name] = float(raw_value)
            elif name in _INT_FIELDS:
                normalized[name] = int(raw_value)
            elif name == "direction":
                text = str(raw_value).strip().upper()
                if text not in ("CALL", "PUT"):
                    raise ValueError(f"{raw_value!r} is not CALL or PUT")
                if text != raw_value:
                    warnings.append(f"direction normalized to {text!r}")
                normalized[name] = text
            elif name == "status":
                normalized[name] = _coerce_status(raw_value)
            elif name == "source_references":
                normalized[name] = _coerce_source_references(raw_value)
            else:  # pragma: no cover - defensive, every required field is classified above
                normalized[name] = raw_value
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    if coercion_errors:
        # A genuine type error on a present field makes further
        # validation unreliable -- report it directly rather than
        # papering over it with a placeholder-derived packet.
        return IntakeResult(
            valid=False,
            missing_fields=missing_fields,
            blocking_reasons=tuple(coercion_errors),
            warnings=tuple(warnings),
        )

    # Fields with defaults on ProofPacket (post-trade outcome fields) are
    # passed through only if present in the payload -- never fabricated,
    # and never used to substitute for a missing pre-trade field above.
    for name in ("actual_entry_time", "actual_exit_time", "outcome_notes"):
        if name in payload and payload[name] is not None:
            normalized[name] = str(payload[name])
    for name in ("actual_entry_premium", "actual_exit_premium", "realized_pnl_dollars", "realized_pnl_percent"):
        if name in payload and payload[name] is not None:
            try:
                normalized[name] = float(payload[name])
            except (TypeError, ValueError) as exc:
                coercion_errors.append(f"invalid value for {name}: {exc}")

    if coercion_errors:
        return IntakeResult(
            valid=False,
            missing_fields=missing_fields,
            blocking_reasons=tuple(coercion_errors),
            warnings=tuple(warnings),
        )

    packet = ProofPacket(**normalized)
    is_valid, validation_errors = validate_proof_packet(packet)

    return IntakeResult(
        valid=is_valid and not missing_fields,
        missing_fields=missing_fields,
        blocking_reasons=validation_errors,
        warnings=tuple(warnings),
        packet=packet if not missing_fields else None,
    )
