"""options_manager/validation/watchlist_lifecycle.py

Watchlist candidate lifecycle -- Increment 25N. `morning_scan_packet.py`
(25M) captures a candidate's morning read as a single snapshot; nothing
tracked how that candidate's own status actually moves over a session --
a watchlist name silently becoming "entered because it moved" is exactly
the failure mode the whole advisory stack (`proof_packet.py`,
`contract_quality_gate.py`, `no_trade_reasons.py`) exists to prevent.
`WatchlistCandidate` is a small state machine for that movement: a
ticker is created in `WATCHING`, and can only move to another status
through an explicit, validated transition -- never a silent status
change, and never one derived from a live price feed.

The intended flow this lane is built around (enforced by other modules,
not this one):

    morning scan candidate -> WATCHING
    -> TRIGGERED only if the entry level actually breaks
    -> ACTIVE only if a ProofPacket and the contract quality gate both
       pass
    -> INVALIDATED / SKIPPED / EXITED / EXPIRED, each with its own
       recorded reason

`create_watchlist_candidate()` builds a new candidate, always in
`WATCHING` status, and validates the fields a usable candidate needs
(entry_trigger, invalidation, target_1, target_2, setup_type, ticker).
`transition_candidate()` takes an existing `WatchlistCandidate` and a
requested new status, and enforces the allowed-transition graph below --
a transition out of a terminal status, or into a non-adjacent status,
is rejected, not silently applied. `check_watchlist_candidate_intake()`
is the manual-payload entry point (a loose dict, typed in by hand,
possibly reconstructing an existing candidate's state) that normalizes
into a `WatchlistCandidate` -- never raising, regardless of how
malformed the payload is, the same non-throwing pattern established by
`check_proof_packet_intake()` (25J).

Allowed transitions (`ALLOWED_TRANSITIONS`):
    WATCHING  -> TRIGGERED, INVALIDATED, SKIPPED
    TRIGGERED -> ACTIVE, INVALIDATED, SKIPPED
    ACTIVE    -> EXITED, EXPIRED, SKIPPED

Terminal statuses (`TERMINAL_STATUSES`) -- `INVALIDATED`, `SKIPPED`,
`EXITED`, `EXPIRED` -- never transition anywhere, including back to
`WATCHING`/`TRIGGERED`/`ACTIVE`. A transition into `SKIPPED` (from any
non-terminal status, or at creation via manual intake) requires a
non-blank reason in `notes` -- a skip with no recorded reason is exactly
the kind of silently-disappearing decision `no_trade_reasons.py` (25K)
exists to stop.

This module never fetches a quote, a candle, an option chain, or a
broker order, and never reads the system clock or advances a status on
its own -- every transition is an explicit call with an explicit new
status, never a side effect of price movement. It never places an
order, changes a scanner setting, or promotes anything to
`FixtureStatus.CLEAN_COMPLETE_FIXTURE`. Performs no I/O of any kind: no
candle fetch, no option-chain fetch, no market-data fetch, no broker
call, no order placement, no execution, no alert sending, no file access
at runtime, no network calls, no MCP calls, no system-clock reads. Does
not import replay/replay_engine.py, the live context.market_context
loader, alert_ranker, options_companion, execution, webhook, broker
systems, options_manager.scanner, or risk/risk_engine.py.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Optional


class WatchlistCandidateStatus(str, Enum):
    """A watchlist candidate's own lifecycle status -- set only via
    `create_watchlist_candidate()` or `transition_candidate()`, never
    derived from a live price feed."""

    WATCHING = "watching"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    SKIPPED = "skipped"
    ACTIVE = "active"
    EXITED = "exited"
    EXPIRED = "expired"


TERMINAL_STATUSES = frozenset(
    (
        WatchlistCandidateStatus.INVALIDATED,
        WatchlistCandidateStatus.SKIPPED,
        WatchlistCandidateStatus.EXITED,
        WatchlistCandidateStatus.EXPIRED,
    )
)

ALLOWED_TRANSITIONS: dict[WatchlistCandidateStatus, frozenset[WatchlistCandidateStatus]] = {
    WatchlistCandidateStatus.WATCHING: frozenset(
        (
            WatchlistCandidateStatus.TRIGGERED,
            WatchlistCandidateStatus.INVALIDATED,
            WatchlistCandidateStatus.SKIPPED,
        )
    ),
    WatchlistCandidateStatus.TRIGGERED: frozenset(
        (
            WatchlistCandidateStatus.ACTIVE,
            WatchlistCandidateStatus.INVALIDATED,
            WatchlistCandidateStatus.SKIPPED,
        )
    ),
    WatchlistCandidateStatus.ACTIVE: frozenset(
        (
            WatchlistCandidateStatus.EXITED,
            WatchlistCandidateStatus.EXPIRED,
            WatchlistCandidateStatus.SKIPPED,
        )
    ),
    WatchlistCandidateStatus.INVALIDATED: frozenset(),
    WatchlistCandidateStatus.SKIPPED: frozenset(),
    WatchlistCandidateStatus.EXITED: frozenset(),
    WatchlistCandidateStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True, kw_only=True)
class WatchlistCandidate:
    """One ticker's current watchlist lifecycle state. Entirely as
    reported by the human filling this out -- nothing here is fetched
    from a quote or option chain, and `status` only ever changes via
    `transition_candidate()`."""

    ticker: str
    direction: Literal["CALL", "PUT"]
    setup_type: str
    timeframe: str
    entry_trigger: str
    invalidation: str
    target_1: str
    target_2: str
    status: WatchlistCandidateStatus
    created_at_or_session: str
    last_updated_or_session: str
    notes: str = ""
    source_reference: str = ""


@dataclass(frozen=True, kw_only=True)
class WatchlistCandidateResult:
    """Outcome of creating, transitioning, or intaking a
    `WatchlistCandidate`. `candidate` is populated whenever construction
    succeeded structurally -- even when a business-rule check (a
    missing entry_trigger, a SKIPPED with no reason) leaves `valid`
    false -- and is `None` only on a structural failure (a malformed
    payload, an uncoercible field, an illegal transition)."""

    valid: bool
    blocking_reasons: tuple[str, ...] = ()
    candidate: Optional[WatchlistCandidate] = None


def _evaluate_required_business_fields(candidate: WatchlistCandidate) -> tuple[str, ...]:
    blocking: list[str] = []
    if not candidate.ticker.strip():
        blocking.append("missing ticker")
    if not candidate.setup_type.strip():
        blocking.append("missing setup_type")
    if not candidate.entry_trigger.strip():
        blocking.append("missing entry_trigger")
    if not candidate.invalidation.strip():
        blocking.append("missing invalidation")
    if not candidate.target_1.strip():
        blocking.append("missing target_1")
    if not candidate.target_2.strip():
        blocking.append("missing target_2")
    if candidate.status == WatchlistCandidateStatus.SKIPPED and not candidate.notes.strip():
        blocking.append("SKIPPED candidate requires a reason in notes")
    return tuple(blocking)


def create_watchlist_candidate(
    *,
    ticker: str,
    direction: Literal["CALL", "PUT"],
    setup_type: str,
    timeframe: str,
    entry_trigger: str,
    invalidation: str,
    target_1: str,
    target_2: str,
    created_at_or_session: str,
    notes: str = "",
    source_reference: str = "",
) -> WatchlistCandidateResult:
    """Creates a new `WatchlistCandidate`, always in `WATCHING` status.
    Validates the fields a usable candidate needs; a missing one is a
    blocking reason, not a silently-incomplete candidate."""
    candidate = WatchlistCandidate(
        ticker=ticker,
        direction=direction,
        setup_type=setup_type,
        timeframe=timeframe,
        entry_trigger=entry_trigger,
        invalidation=invalidation,
        target_1=target_1,
        target_2=target_2,
        status=WatchlistCandidateStatus.WATCHING,
        created_at_or_session=created_at_or_session,
        last_updated_or_session=created_at_or_session,
        notes=notes,
        source_reference=source_reference,
    )
    blocking = _evaluate_required_business_fields(candidate)
    return WatchlistCandidateResult(valid=not blocking, blocking_reasons=blocking, candidate=candidate)


def transition_candidate(
    candidate: WatchlistCandidate,
    new_status: WatchlistCandidateStatus,
    *,
    last_updated_or_session: str,
    notes: str = "",
) -> WatchlistCandidateResult:
    """Attempts to move `candidate` to `new_status`. Rejects (does not
    apply) any transition out of a terminal status, or into a status not
    listed in `ALLOWED_TRANSITIONS` for the candidate's current status.
    A transition into `SKIPPED` additionally requires a non-blank reason
    -- either passed as `notes` here, or already present on the
    candidate."""
    blocking: list[str] = []

    if candidate.status in TERMINAL_STATUSES:
        blocking.append(f"cannot transition from terminal status {candidate.status.value}")
    elif new_status not in ALLOWED_TRANSITIONS.get(candidate.status, frozenset()):
        blocking.append(f"invalid transition from {candidate.status.value} to {new_status.value}")

    effective_notes = notes if notes.strip() else candidate.notes
    if new_status == WatchlistCandidateStatus.SKIPPED and not effective_notes.strip():
        blocking.append("SKIPPED transition requires a reason in notes")

    if blocking:
        return WatchlistCandidateResult(valid=False, blocking_reasons=tuple(blocking))

    new_candidate = dataclasses.replace(
        candidate,
        status=new_status,
        last_updated_or_session=last_updated_or_session,
        notes=effective_notes,
    )
    return WatchlistCandidateResult(valid=True, candidate=new_candidate)


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _coerce_direction(value: Any) -> str:
    text = str(value).strip().upper()
    if text not in ("CALL", "PUT"):
        raise ValueError(f"{value!r} is not CALL or PUT")
    return text


def _coerce_status(value: Any) -> WatchlistCandidateStatus:
    if isinstance(value, WatchlistCandidateStatus):
        return value
    text = str(value).strip().lower()
    for member in WatchlistCandidateStatus:
        if member.value == text or member.name.lower() == text:
            return member
    raise ValueError(f"{value!r} is not a valid WatchlistCandidateStatus")


_STR_FIELDS = (
    "ticker",
    "setup_type",
    "timeframe",
    "entry_trigger",
    "invalidation",
    "target_1",
    "target_2",
    "created_at_or_session",
    "last_updated_or_session",
)

_REQUIRED_FIELD_NAMES = tuple(
    f.name
    for f in dataclasses.fields(WatchlistCandidate)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
)


def check_watchlist_candidate_intake(payload: Any) -> WatchlistCandidateResult:
    """Normalizes a manual dict-like payload into a `WatchlistCandidate`
    -- including reconstructing one already past `WATCHING`, since
    `status` is accepted directly rather than forced. Never raises
    regardless of how malformed `payload` is."""
    if not isinstance(payload, Mapping):
        return WatchlistCandidateResult(
            valid=False,
            blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    missing = [name for name in _REQUIRED_FIELD_NAMES if not _is_present(payload.get(name))]
    if missing:
        return WatchlistCandidateResult(
            valid=False, blocking_reasons=tuple(f"missing {name}" for name in missing)
        )

    coercion_errors: list[str] = []
    normalized: dict[str, Any] = {}
    for name in _REQUIRED_FIELD_NAMES:
        raw_value = payload[name]
        try:
            if name == "direction":
                normalized[name] = _coerce_direction(raw_value)
            elif name == "status":
                normalized[name] = _coerce_status(raw_value)
            elif name in _STR_FIELDS:
                normalized[name] = str(raw_value)
        except (TypeError, ValueError) as exc:
            coercion_errors.append(f"invalid value for {name}: {exc}")

    if "notes" in payload and payload["notes"] is not None:
        normalized["notes"] = str(payload["notes"])
    if "source_reference" in payload and payload["source_reference"] is not None:
        normalized["source_reference"] = str(payload["source_reference"])

    if coercion_errors:
        return WatchlistCandidateResult(valid=False, blocking_reasons=tuple(coercion_errors))

    candidate = WatchlistCandidate(**normalized)
    blocking = _evaluate_required_business_fields(candidate)
    return WatchlistCandidateResult(valid=not blocking, blocking_reasons=blocking, candidate=candidate)
