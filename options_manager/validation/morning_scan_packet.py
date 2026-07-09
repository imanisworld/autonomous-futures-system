"""options_manager/validation/morning_scan_packet.py

Morning scan packet -- Increment 25M. Everything upstream of this module
(`proof_packet.py`, `contract_quality_gate.py`) validates one trade idea
at a time. Nothing captured the broader session context a trade idea is
actually read against -- GEX regime, SPY/QQQ flip levels and trend, gap
direction, and the day's candidate watchlist -- so that context either
lived nowhere or had to be reconstructed from memory after the fact,
exactly the failure mode `proof_packet.py`'s own docstring was written
to stop. `MorningScanPacket` is a single structured record of one
session's morning read: one `MarketContext` plus zero or more
`TickerCandidate` entries.

`evaluate_morning_scan_packet()` takes an already-typed
`MorningScanPacket` and returns a `MorningScanPacketResult`: market-
context-level blocking reasons plus one `CandidateEvaluation` per
candidate. A market-context failure (missing GEX regime, missing SPY/QQQ
trend, etc.) is deliberately propagated into every candidate's own
blocking reasons too -- a candidate's entry/stop/target plan is read
against that shared context, so a candidate is not usable evidence when
the context underneath it was never captured, even if the candidate's
own fields are otherwise complete.

`check_morning_scan_packet_intake()` is the manual-payload entry point --
a loose nested dict (`{"market_context": {...}, "candidates": [...]}`),
typed in by hand -- that normalizes into a `MorningScanPacket` and runs
the same evaluation. Never raises regardless of how malformed the
payload is, the same non-throwing pattern established by
`check_proof_packet_intake()` (25J) and `check_contract_quality_intake()`
(25L).

This is advisory/manual planning only -- it never fetches a quote, a
candle, an option chain, a GEX/Signa feed, or a broker order, and it
never reads the system clock. It never places an order, changes a
scanner setting, or promotes anything to
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
from typing import Any, Mapping, Optional, Sequence

MIN_ACCEPTABLE_RISK_REWARD = 1.0
MIN_PREFERRED_RISK_REWARD = 1.5

_MARKET_CONTEXT_STR_FIELDS = (
    "scan_date",
    "session",
    "gex_regime",
    "spy_flip",
    "qqq_flip",
    "spy_trend",
    "qqq_trend",
    "location_vs_yesterday",
    "broad_market_direction",
)
_MARKET_CONTEXT_FLOAT_FIELDS = ("gap_percent",)

_CANDIDATE_STR_FIELDS = ("ticker", "name", "regime", "signa_grade", "current_candle_behavior")
_CANDIDATE_FLOAT_FIELDS = (
    "spot",
    "flip",
    "orb_high",
    "orb_low",
    "res1",
    "res2",
    "sup1",
    "distance_to_res1",
    "distance_to_sup1",
    "signa_score",
    "entry",
    "stop",
    "target",
    "risk_reward",
)
_CANDIDATE_INT_FIELDS = ("volume",)


class TickerCandidateStatus(str, Enum):
    """A watchlist candidate's own status -- set by the human filling
    this out, not derived or auto-advanced by anything in this module.
    Deliberately narrower than `proof_packet.ProofPacketStatus`: a
    morning-scan candidate has not been entered yet by definition, so
    there is no `ACTIVE`/`EXITED`/`EXPIRED` here."""

    WATCHING = "watching"
    TRIGGERED = "triggered"
    INVALIDATED = "invalidated"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True)
class MarketContext:
    """One session's broad market read. Every field is required --
    `evaluate_morning_scan_packet()` treats a blank one as a missing
    market context, which then propagates into every candidate's own
    evaluation too."""

    scan_date: str
    session: str
    gex_regime: str
    spy_flip: str
    qqq_flip: str
    spy_trend: str
    qqq_trend: str
    gap_percent: float
    location_vs_yesterday: str
    broad_market_direction: str


@dataclass(frozen=True, kw_only=True)
class TickerCandidate:
    """One ticker's morning-scan read and candidate trade plan. Entry,
    stop, target, and risk_reward are the plan itself --
    `evaluate_morning_scan_packet()` blocks a candidate missing any of
    them, and further blocks or warns on a weak risk_reward."""

    ticker: str
    name: str
    spot: float
    flip: float
    regime: str
    orb_high: float
    orb_low: float
    res1: float
    res2: float
    sup1: float
    distance_to_res1: float
    distance_to_sup1: float
    volume: int
    signa_grade: str
    signa_score: float
    entry: float
    stop: float
    target: float
    risk_reward: float
    current_candle_behavior: str
    status: TickerCandidateStatus


@dataclass(frozen=True, kw_only=True)
class MorningScanPacket:
    """One session's full morning scan: the shared market context plus
    zero or more ticker candidates. Zero candidates is a valid state --
    a session where context was captured but nothing stood out."""

    market_context: MarketContext
    candidates: tuple[TickerCandidate, ...] = ()


@dataclass(frozen=True, kw_only=True)
class CandidateEvaluation:
    """One candidate's evaluation outcome within a scan packet."""

    ticker: str
    valid: bool
    blocking_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class MorningScanPacketResult:
    """Outcome of evaluating (or intaking) a `MorningScanPacket`.
    `packet` is populated whenever the market context and every
    candidate normalized structurally -- even when a candidate is
    individually flagged invalid by a business-rule check (a weak
    risk_reward, a missing target) rather than a structural one."""

    valid: bool
    market_context_blocking_reasons: tuple[str, ...] = ()
    candidate_results: tuple[CandidateEvaluation, ...] = ()
    packet: Optional[MorningScanPacket] = None


def _evaluate_market_context(context: MarketContext) -> tuple[str, ...]:
    blocking: list[str] = []
    for name in _MARKET_CONTEXT_STR_FIELDS:
        if not getattr(context, name).strip():
            blocking.append(f"missing {name}")
    return tuple(blocking)


def _evaluate_candidate(candidate: TickerCandidate) -> CandidateEvaluation:
    blocking: list[str] = []
    warnings: list[str] = []

    if not candidate.ticker.strip():
        blocking.append("missing ticker")
    if not candidate.name.strip():
        blocking.append("missing name")

    if candidate.entry <= 0:
        blocking.append("missing entry")
    if candidate.stop <= 0:
        blocking.append("missing stop")
    if candidate.target <= 0:
        blocking.append("missing target")

    if candidate.risk_reward <= 0:
        blocking.append("missing/invalid risk_reward")
    elif candidate.risk_reward < MIN_ACCEPTABLE_RISK_REWARD:
        blocking.append(
            f"risk_reward {candidate.risk_reward:.2f} is below the "
            f"{MIN_ACCEPTABLE_RISK_REWARD:.2f} minimum acceptable ratio"
        )
    elif candidate.risk_reward < MIN_PREFERRED_RISK_REWARD:
        warnings.append(
            f"risk_reward {candidate.risk_reward:.2f} is below the preferred "
            f"{MIN_PREFERRED_RISK_REWARD:.2f} ratio"
        )

    return CandidateEvaluation(
        ticker=candidate.ticker,
        valid=not blocking,
        blocking_reasons=tuple(blocking),
        warnings=tuple(warnings),
    )


def evaluate_morning_scan_packet(packet: MorningScanPacket) -> MorningScanPacketResult:
    """Evaluates an already-typed `MorningScanPacket`. A market-context
    failure is propagated into every candidate's own blocking reasons --
    a candidate plan is only as good as the shared context it was read
    against."""
    market_context_blocking = _evaluate_market_context(packet.market_context)

    candidate_results: list[CandidateEvaluation] = []
    for candidate in packet.candidates:
        evaluation = _evaluate_candidate(candidate)
        if market_context_blocking:
            combined_blocking = evaluation.blocking_reasons + (
                f"missing market context: {', '.join(market_context_blocking)}",
            )
            evaluation = CandidateEvaluation(
                ticker=evaluation.ticker,
                valid=False,
                blocking_reasons=combined_blocking,
                warnings=evaluation.warnings,
            )
        candidate_results.append(evaluation)

    overall_valid = not market_context_blocking and all(c.valid for c in candidate_results)

    return MorningScanPacketResult(
        valid=overall_valid,
        market_context_blocking_reasons=market_context_blocking,
        candidate_results=tuple(candidate_results),
        packet=packet,
    )


def _is_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _coerce_status(value: Any) -> TickerCandidateStatus:
    if isinstance(value, TickerCandidateStatus):
        return value
    text = str(value).strip().lower()
    for member in TickerCandidateStatus:
        if member.value == text or member.name.lower() == text:
            return member
    raise ValueError(f"{value!r} is not a valid TickerCandidateStatus")


def _normalize_market_context(raw: Any) -> tuple[Optional[MarketContext], tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return None, (f"malformed market_context: expected a dict-like mapping, got {type(raw).__name__}",)

    required = tuple(
        f.name
        for f in dataclasses.fields(MarketContext)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    )
    missing = [name for name in required if not _is_present(raw.get(name))]
    if missing:
        return None, tuple(f"missing {name}" for name in missing)

    errors: list[str] = []
    normalized: dict[str, Any] = {}
    for name in required:
        raw_value = raw[name]
        try:
            if name in _MARKET_CONTEXT_STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _MARKET_CONTEXT_FLOAT_FIELDS:
                normalized[name] = float(raw_value)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid value for {name}: {exc}")

    if errors:
        return None, tuple(errors)

    return MarketContext(**normalized), ()


def _normalize_candidate(raw: Any) -> tuple[Optional[TickerCandidate], tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return None, (
            f"malformed candidate payload: expected a dict-like mapping, got {type(raw).__name__}",
        )

    required = tuple(f.name for f in dataclasses.fields(TickerCandidate))  # every field is required
    missing = [name for name in required if not _is_present(raw.get(name))]
    if missing:
        return None, tuple(f"missing {name}" for name in missing)

    errors: list[str] = []
    normalized: dict[str, Any] = {}
    for name in required:
        raw_value = raw[name]
        try:
            if name in _CANDIDATE_STR_FIELDS:
                normalized[name] = str(raw_value)
            elif name in _CANDIDATE_FLOAT_FIELDS:
                normalized[name] = float(raw_value)
            elif name in _CANDIDATE_INT_FIELDS:
                normalized[name] = int(raw_value)
            elif name == "status":
                normalized[name] = _coerce_status(raw_value)
        except (TypeError, ValueError) as exc:
            errors.append(f"invalid value for {name}: {exc}")

    if errors:
        return None, tuple(errors)

    return TickerCandidate(**normalized), ()


def check_morning_scan_packet_intake(payload: Any) -> MorningScanPacketResult:
    """Normalizes a manual nested dict payload
    (`{"market_context": {...}, "candidates": [...]}`) into a
    `MorningScanPacket` and evaluates it. Never raises regardless of how
    malformed `payload` is."""
    if not isinstance(payload, Mapping):
        return MorningScanPacketResult(
            valid=False,
            market_context_blocking_reasons=(
                f"malformed payload: expected a dict-like mapping, got {type(payload).__name__}",
            ),
        )

    market_context, market_context_errors = _normalize_market_context(payload.get("market_context"))

    raw_candidates = payload.get("candidates", ())
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, (str, bytes)):
        return MorningScanPacketResult(
            valid=False,
            market_context_blocking_reasons=market_context_errors,
            candidate_results=(
                CandidateEvaluation(
                    ticker="<unknown>",
                    valid=False,
                    blocking_reasons=(
                        f"malformed candidates: expected a list, got {type(raw_candidates).__name__}",
                    ),
                ),
            ),
        )

    candidates: list[TickerCandidate] = []
    candidate_results: list[CandidateEvaluation] = []
    all_candidates_normalized = True

    for raw_candidate in raw_candidates:
        candidate, candidate_errors = _normalize_candidate(raw_candidate)
        if candidate is None:
            all_candidates_normalized = False
            ticker = raw_candidate.get("ticker", "<unknown>") if isinstance(raw_candidate, Mapping) else "<unknown>"
            candidate_results.append(
                CandidateEvaluation(ticker=str(ticker), valid=False, blocking_reasons=candidate_errors)
            )
            continue
        candidates.append(candidate)
        evaluation = _evaluate_candidate(candidate)
        if market_context_errors:
            evaluation = CandidateEvaluation(
                ticker=evaluation.ticker,
                valid=False,
                blocking_reasons=evaluation.blocking_reasons
                + (f"missing market context: {', '.join(market_context_errors)}",),
                warnings=evaluation.warnings,
            )
        candidate_results.append(evaluation)

    if market_context is None or not all_candidates_normalized:
        return MorningScanPacketResult(
            valid=False,
            market_context_blocking_reasons=market_context_errors,
            candidate_results=tuple(candidate_results),
        )

    packet = MorningScanPacket(market_context=market_context, candidates=tuple(candidates))
    overall_valid = not market_context_errors and all(c.valid for c in candidate_results)

    return MorningScanPacketResult(
        valid=overall_valid,
        market_context_blocking_reasons=market_context_errors,
        candidate_results=tuple(candidate_results),
        packet=packet,
    )
