"""Pure concentration telemetry for Phase-1 advisory options risk evidence.

This module measures concentration from explicit, caller-supplied exposure facts.
It does not choose limits, block trades, size positions, fetch provider data, or
change portfolio policy. Position count remains telemetry only.

Authority boundary
------------------

``options_manager.validation.portfolio_risk_gate`` is the only authority for
correlation-group aggregation. Its ``PortfolioRiskResult.correlation_risk``
(persisted as ``RiskPlanSnapshot.correlation_risk`` and projected by
``options_manager.risk.telemetry`` as ``RiskTelemetrySnapshot.correlation_risk``)
already sums planned dollar risk per correlation group over the open positions
plus the candidate at evaluation time. This module *reports* that canonical
tuple; it never re-aggregates correlation risk from per-position labels and it
never sums the tuples of different facts together, because each tuple is
already a portfolio-level projection and summing them would double count.

Which fact's tuple is reported is a documented selection, not a calculation:
the gate's projection attached to a candidate evaluation already covers every
open position, so ``by_correlation_group`` comes from the facts flagged
``is_candidate=True``. Tuples carried by non-candidate (already-open) facts are
earlier projections and are neither reported nor merged. Several candidate
facts must carry the identical canonical tuple; any disagreement is INVALID
rather than reconciled here.

Ticker, direction, sector, industry, expiration, DTE bucket and index overlap
remain per-fact groupings aggregated here, because no canonical authority
exposes them per group.

Label normalization rule: ticker, sector, industry, index-overlap and
correlation-group labels are stripped and upper-cased before bucketing so that
``"Technology"`` and ``"technology"`` land in one bucket. Two canonical
correlation groups that collide after normalization are INVALID (fail closed)
rather than merged, since merging would be a second aggregation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Optional

from .telemetry import RiskTelemetrySnapshot


Direction = Literal["CALL", "PUT"]

CorrelationRisk = tuple[tuple[str, float], ...]


class ConcentrationStatus(str, Enum):
    COMPLETE = "complete"
    INVALID = "invalid"


@dataclass(frozen=True, kw_only=True)
class ExposureFact:
    """Already-known exposure facts for one open or candidate options thesis.

    ``correlation_risk`` is the canonical per-group planned-risk tuple that the
    portfolio risk gate recorded when this thesis was evaluated (open positions
    plus this candidate at that time). It is portfolio-level evidence carried
    through verbatim; it is not a per-position label and is never summed across
    facts.

    Sector, industry and index-overlap labels are evidence/provenance supplied
    by the caller. Empty labels mean that dimension is unknown and are reported
    as such rather than inferred here.
    """

    ticker: str
    direction: Direction
    planned_dollar_risk: float
    full_debit: float
    dte: int
    expiration: str
    contracts: int
    correlation_risk: CorrelationRisk = ()
    sector: str = ""
    industry: str = ""
    index_overlap: tuple[str, ...] = ()
    is_candidate: bool = False


@dataclass(frozen=True)
class ConcentrationBucket:
    name: str
    planned_dollar_risk: float
    full_debit: float
    position_count: int
    contract_count: int
    share_of_planned_risk: float
    share_of_full_debit: float


@dataclass(frozen=True)
class CorrelationRiskBucket:
    """One canonical correlation group as reported by the portfolio risk gate.

    Only planned dollar risk is carried because that is all the gate records
    per group. No debit, position or contract count is invented for it.
    """

    name: str
    planned_dollar_risk: float
    share_of_planned_risk: float


@dataclass(frozen=True)
class ConcentrationSnapshot:
    total_planned_dollar_risk: float
    total_full_debit: float
    position_count: int
    contract_count: int
    candidate_count: int
    by_ticker: tuple[ConcentrationBucket, ...]
    by_direction: tuple[ConcentrationBucket, ...]
    by_correlation_group: tuple[CorrelationRiskBucket, ...]
    by_sector: tuple[ConcentrationBucket, ...]
    by_industry: tuple[ConcentrationBucket, ...]
    by_expiration: tuple[ConcentrationBucket, ...]
    by_dte_bucket: tuple[ConcentrationBucket, ...]
    by_index_overlap: tuple[ConcentrationBucket, ...]
    correlation_risk_reported: bool
    unknown_sector_count: int
    unknown_industry_count: int
    unknown_index_overlap_count: int


@dataclass(frozen=True)
class ConcentrationResult:
    status: ConcentrationStatus
    snapshot: Optional[ConcentrationSnapshot]
    reason_codes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == ConcentrationStatus.COMPLETE and self.snapshot is not None


def exposure_fact_from_risk_telemetry(
    snapshot: RiskTelemetrySnapshot,
    *,
    sector: str = "",
    industry: str = "",
    index_overlap: tuple[str, ...] = (),
    is_candidate: bool = False,
) -> ExposureFact:
    """Map canonical risk telemetry into a concentration input without new math.

    Planned risk, full debit, DTE, expiration, contract count and the canonical
    ``correlation_risk`` tuple are copied from the already-reconciled risk
    snapshot. There is no correlation-group kwarg: the gate's own aggregation is
    the single source for correlation risk. Sector/industry/index labels remain
    explicit caller evidence and are not inferred here.
    """

    return ExposureFact(
        ticker=snapshot.ticker,
        direction=snapshot.direction,
        planned_dollar_risk=snapshot.planned_total_trade_risk,
        full_debit=snapshot.full_debit_total,
        dte=snapshot.dte,
        expiration=snapshot.expiration,
        contracts=snapshot.max_contracts,
        correlation_risk=tuple(snapshot.correlation_risk),
        sector=sector,
        industry=industry,
        index_overlap=index_overlap,
        is_candidate=is_candidate,
    )


def _finite_nonnegative(value: object, label: str, reasons: list[str]) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        reasons.append(f"{label}_not_numeric")
        return None
    if not math.isfinite(parsed):
        reasons.append(f"{label}_not_finite")
        return None
    if parsed < 0:
        reasons.append(f"{label}_negative")
        return None
    return parsed


def _normalize_label(value: str) -> str:
    """Single normalization rule for every textual grouping label."""

    return value.strip().upper()


def _optional_label(value: object, label: str, reasons: list[str]) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        reasons.append(f"{label}_not_string")
        return ""
    return _normalize_label(value)


def _normalize_index_overlap(
    value: object,
    label: str,
    reasons: list[str],
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        reasons.append(f"{label}_not_sequence")
        return ()

    overlaps: list[str] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, str):
            reasons.append(f"{label}_member_not_string")
            continue
        normalized = _normalize_label(raw)
        if not normalized:
            reasons.append(f"{label}_empty")
            continue
        if normalized in seen:
            reasons.append(f"{label}_duplicate")
            continue
        seen.add(normalized)
        overlaps.append(normalized)
    return tuple(overlaps)


def _normalize_correlation_risk(
    value: object,
    label: str,
    reasons: list[str],
) -> Optional[CorrelationRisk]:
    """Validate the shape of a canonical tuple without changing its numbers.

    Group names follow the module-wide label rule. A case collision between
    two canonical groups is reported as INVALID instead of being merged.
    """

    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        reasons.append(f"{label}_not_sequence")
        return None

    pairs: list[tuple[str, float]] = []
    seen: set[str] = set()
    malformed = False
    for index, pair in enumerate(value):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            reasons.append(f"{label}_{index}_malformed")
            malformed = True
            continue
        group, raw_value = pair
        if not isinstance(group, str):
            reasons.append(f"{label}_{index}_group_not_string")
            malformed = True
            continue
        name = _normalize_label(group)
        if not name:
            reasons.append(f"{label}_{index}_group_missing")
            malformed = True
            continue
        if name in seen:
            reasons.append(f"{label}_{index}_group_case_collision")
            malformed = True
            continue
        amount = _finite_nonnegative(raw_value, f"{label}_{index}", reasons)
        if amount is None:
            malformed = True
            continue
        seen.add(name)
        pairs.append((name, amount))
    if malformed:
        return None
    return tuple(pairs)


def _dte_bucket(dte: int) -> str:
    if dte == 0:
        return "0DTE"
    if dte <= 7:
        return "1-7DTE"
    if dte <= 30:
        return "8-30DTE"
    if dte <= 60:
        return "31-60DTE"
    return "61+DTE"


def _bucketize(
    rows: list[tuple[str, float, float, int]],
    *,
    label: str,
    total_risk: float,
    total_debit: float,
    reasons: list[str],
) -> tuple[ConcentrationBucket, ...]:
    grouped: dict[str, list[float | int]] = {}
    for name, risk, debit, contracts in rows:
        bucket = grouped.setdefault(name, [0.0, 0.0, 0, 0])
        bucket[0] = float(bucket[0]) + risk
        bucket[1] = float(bucket[1]) + debit
        bucket[2] = int(bucket[2]) + 1
        bucket[3] = int(bucket[3]) + contracts

    result: list[ConcentrationBucket] = []
    for name in sorted(grouped):
        risk, debit, positions, contracts = grouped[name]
        risk_f = float(risk)
        debit_f = float(debit)
        if not math.isfinite(risk_f):
            reasons.append(f"{label}_{name}_planned_risk_not_finite")
        if not math.isfinite(debit_f):
            reasons.append(f"{label}_{name}_full_debit_not_finite")
        result.append(
            ConcentrationBucket(
                name=name,
                planned_dollar_risk=risk_f,
                full_debit=debit_f,
                position_count=int(positions),
                contract_count=int(contracts),
                share_of_planned_risk=(risk_f / total_risk if total_risk > 0 else 0.0),
                share_of_full_debit=(debit_f / total_debit if total_debit > 0 else 0.0),
            )
        )
    return tuple(result)


def _report_correlation_risk(
    canonical: Optional[CorrelationRisk],
    *,
    total_risk: float,
    reasons: list[str],
) -> tuple[CorrelationRiskBucket, ...]:
    """Report the gate's tuple as buckets. Values are copied, never re-summed."""

    if canonical is None:
        return ()
    result: list[CorrelationRiskBucket] = []
    for name, amount in sorted(canonical):
        if amount > total_risk and not math.isclose(
            amount, total_risk, rel_tol=1e-9, abs_tol=1e-9
        ):
            # The canonical projection covers more planned risk than the facts
            # supplied here; a share above 100% would be nonsense, so fail
            # closed instead of reporting it.
            reasons.append(f"correlation_risk_{name}_exceeds_total_planned_risk")
        result.append(
            CorrelationRiskBucket(
                name=name,
                planned_dollar_risk=amount,
                share_of_planned_risk=(amount / total_risk if total_risk > 0 else 0.0),
            )
        )
    return tuple(result)


def measure_concentration(exposures: Iterable[ExposureFact]) -> ConcentrationResult:
    """Measure concentration without applying a concentration policy.

    Missing grouping labels are counted as unknown rather than guessed. Index
    overlap is multi-valued by design, so its bucket shares may sum above 100%;
    the values describe overlap, not mutually-exclusive allocation.

    Correlation-group risk is reported from the canonical gate tuple carried by
    the candidate fact(s); see the module docstring for the authority boundary.
    """

    try:
        facts = tuple(exposures)
    except TypeError:
        return ConcentrationResult(
            status=ConcentrationStatus.INVALID,
            snapshot=None,
            reason_codes=("exposures_not_iterable",),
        )

    reasons: list[str] = []
    normalized: list[tuple[ExposureFact, float, float]] = []

    for index, fact in enumerate(facts):
        prefix = f"exposures_{index}"
        if not isinstance(fact, ExposureFact):
            reasons.append(f"{prefix}_wrong_type")
            continue

        if not isinstance(fact.ticker, str) or not fact.ticker.strip():
            reasons.append(f"{prefix}_ticker_missing")
            ticker = ""
        else:
            ticker = _normalize_label(fact.ticker)

        if fact.direction not in ("CALL", "PUT"):
            reasons.append(f"{prefix}_direction_invalid")

        if not isinstance(fact.expiration, str) or not fact.expiration.strip():
            reasons.append(f"{prefix}_expiration_missing")
            expiration = ""
        else:
            expiration = fact.expiration.strip()

        if not isinstance(fact.dte, int) or isinstance(fact.dte, bool) or fact.dte < 0:
            reasons.append(f"{prefix}_dte_invalid")
        if not isinstance(fact.contracts, int) or isinstance(fact.contracts, bool) or fact.contracts <= 0:
            reasons.append(f"{prefix}_contracts_invalid")
        if not isinstance(fact.is_candidate, bool):
            reasons.append(f"{prefix}_is_candidate_not_bool")

        risk = _finite_nonnegative(fact.planned_dollar_risk, f"{prefix}_planned_risk", reasons)
        debit = _finite_nonnegative(fact.full_debit, f"{prefix}_full_debit", reasons)
        correlation_risk = _normalize_correlation_risk(
            fact.correlation_risk, f"{prefix}_correlation_risk", reasons
        )
        sector = _optional_label(fact.sector, f"{prefix}_sector", reasons)
        industry = _optional_label(fact.industry, f"{prefix}_industry", reasons)
        overlaps = _normalize_index_overlap(
            fact.index_overlap, f"{prefix}_index_overlap", reasons
        )

        if risk is not None and debit is not None and correlation_risk is not None:
            normalized.append(
                (
                    ExposureFact(
                        ticker=ticker,
                        direction=fact.direction,
                        planned_dollar_risk=risk,
                        full_debit=debit,
                        dte=fact.dte,
                        expiration=expiration,
                        contracts=fact.contracts,
                        correlation_risk=correlation_risk,
                        sector=sector,
                        industry=industry,
                        index_overlap=overlaps,
                        is_candidate=fact.is_candidate,
                    ),
                    risk,
                    debit,
                )
            )

    if reasons:
        return ConcentrationResult(
            status=ConcentrationStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    total_risk = sum(risk for _, risk, _ in normalized)
    total_debit = sum(debit for _, _, debit in normalized)
    # Every addend was finite and non-negative, but the sum itself can still
    # overflow to inf. That is not a measurement; fail closed with a reason.
    if not math.isfinite(total_risk):
        reasons.append("total_planned_risk_not_finite")
    if not math.isfinite(total_debit):
        reasons.append("total_full_debit_not_finite")
    if reasons:
        return ConcentrationResult(
            status=ConcentrationStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    ticker_rows: list[tuple[str, float, float, int]] = []
    direction_rows: list[tuple[str, float, float, int]] = []
    sector_rows: list[tuple[str, float, float, int]] = []
    industry_rows: list[tuple[str, float, float, int]] = []
    expiration_rows: list[tuple[str, float, float, int]] = []
    dte_rows: list[tuple[str, float, float, int]] = []
    index_rows: list[tuple[str, float, float, int]] = []

    unknown_sector = 0
    unknown_industry = 0
    unknown_index = 0

    # Selection, not aggregation: the canonical tuple attached to a candidate
    # evaluation already covers every open position. Candidates must agree.
    canonical: Optional[CorrelationRisk] = None
    correlation_reported = False
    for fact, _, _ in normalized:
        if not fact.is_candidate:
            continue
        if not correlation_reported:
            canonical = fact.correlation_risk
            correlation_reported = True
        elif fact.correlation_risk != canonical:
            reasons.append("correlation_risk_conflict")
    if reasons:
        return ConcentrationResult(
            status=ConcentrationStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    for fact, risk, debit in normalized:
        row = (risk, debit, fact.contracts)
        ticker_rows.append((fact.ticker, *row))
        direction_rows.append((fact.direction, *row))
        expiration_rows.append((fact.expiration, *row))
        dte_rows.append((_dte_bucket(fact.dte), *row))

        if fact.sector:
            sector_rows.append((fact.sector, *row))
        else:
            unknown_sector += 1
        if fact.industry:
            industry_rows.append((fact.industry, *row))
        else:
            unknown_industry += 1
        if fact.index_overlap:
            for index_name in fact.index_overlap:
                index_rows.append((index_name, *row))
        else:
            unknown_index += 1

    def bucketize(label: str, rows: list[tuple[str, float, float, int]]) -> tuple[ConcentrationBucket, ...]:
        return _bucketize(
            rows,
            label=label,
            total_risk=total_risk,
            total_debit=total_debit,
            reasons=reasons,
        )

    by_ticker = bucketize("by_ticker", ticker_rows)
    by_direction = bucketize("by_direction", direction_rows)
    by_sector = bucketize("by_sector", sector_rows)
    by_industry = bucketize("by_industry", industry_rows)
    by_expiration = bucketize("by_expiration", expiration_rows)
    by_dte_bucket = bucketize("by_dte_bucket", dte_rows)
    by_index_overlap = bucketize("by_index_overlap", index_rows)
    by_correlation_group = _report_correlation_risk(
        canonical, total_risk=total_risk, reasons=reasons
    )
    if reasons:
        return ConcentrationResult(
            status=ConcentrationStatus.INVALID,
            snapshot=None,
            reason_codes=tuple(dict.fromkeys(reasons)),
        )

    snapshot = ConcentrationSnapshot(
        total_planned_dollar_risk=total_risk,
        total_full_debit=total_debit,
        position_count=len(normalized),
        contract_count=sum(fact.contracts for fact, _, _ in normalized),
        candidate_count=sum(1 for fact, _, _ in normalized if fact.is_candidate),
        by_ticker=by_ticker,
        by_direction=by_direction,
        by_correlation_group=by_correlation_group,
        by_sector=by_sector,
        by_industry=by_industry,
        by_expiration=by_expiration,
        by_dte_bucket=by_dte_bucket,
        by_index_overlap=by_index_overlap,
        correlation_risk_reported=correlation_reported,
        unknown_sector_count=unknown_sector,
        unknown_industry_count=unknown_industry,
        unknown_index_overlap_count=unknown_index,
    )
    return ConcentrationResult(status=ConcentrationStatus.COMPLETE, snapshot=snapshot)
