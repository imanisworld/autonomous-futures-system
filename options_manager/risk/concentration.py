"""Pure concentration telemetry for Phase-1 advisory options risk evidence.

This module measures concentration from explicit, caller-supplied exposure facts.
It does not choose limits, block trades, size positions, fetch provider data, or
change portfolio policy. Position count remains telemetry only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Literal, Optional


Direction = Literal["CALL", "PUT"]


class ConcentrationStatus(str, Enum):
    COMPLETE = "complete"
    INVALID = "invalid"


@dataclass(frozen=True, kw_only=True)
class ExposureFact:
    """Already-known exposure facts for one open or candidate options thesis.

    Group labels are evidence/provenance supplied by the caller. Empty labels
    mean that dimension is unknown and are reported as such rather than
    inferred here.
    """

    ticker: str
    direction: Direction
    planned_dollar_risk: float
    full_debit: float
    dte: int
    expiration: str
    contracts: int
    correlation_group: str = ""
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
class ConcentrationSnapshot:
    total_planned_dollar_risk: float
    total_full_debit: float
    position_count: int
    contract_count: int
    candidate_count: int
    by_ticker: tuple[ConcentrationBucket, ...]
    by_direction: tuple[ConcentrationBucket, ...]
    by_correlation_group: tuple[ConcentrationBucket, ...]
    by_sector: tuple[ConcentrationBucket, ...]
    by_industry: tuple[ConcentrationBucket, ...]
    by_expiration: tuple[ConcentrationBucket, ...]
    by_dte_bucket: tuple[ConcentrationBucket, ...]
    by_index_overlap: tuple[ConcentrationBucket, ...]
    unknown_correlation_count: int
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


def _finite_nonnegative(value: object, label: str, reasons: list[str]) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        reasons.append(f"{label}_not_numeric")
        return None
    if not math.isfinite(parsed):
        reasons.append(f"{label}_not_finite")
        return None
    if parsed < 0:
        reasons.append(f"{label}_negative")
        return None
    return parsed


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
    total_risk: float,
    total_debit: float,
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


def measure_concentration(exposures: Iterable[ExposureFact]) -> ConcentrationResult:
    """Measure concentration without applying a concentration policy.

    Missing grouping labels are counted as unknown rather than guessed. Index
    overlap is multi-valued by design, so its bucket shares may sum above 100%;
    the values describe overlap, not mutually-exclusive allocation.
    """

    facts = tuple(exposures)
    reasons: list[str] = []
    normalized: list[tuple[ExposureFact, float, float]] = []

    for index, fact in enumerate(facts):
        prefix = f"exposures_{index}"
        ticker = str(fact.ticker).strip().upper()
        if not ticker:
            reasons.append(f"{prefix}_ticker_missing")
        if fact.direction not in ("CALL", "PUT"):
            reasons.append(f"{prefix}_direction_invalid")
        if not str(fact.expiration).strip():
            reasons.append(f"{prefix}_expiration_missing")
        if not isinstance(fact.dte, int) or isinstance(fact.dte, bool) or fact.dte < 0:
            reasons.append(f"{prefix}_dte_invalid")
        if not isinstance(fact.contracts, int) or isinstance(fact.contracts, bool) or fact.contracts <= 0:
            reasons.append(f"{prefix}_contracts_invalid")

        risk = _finite_nonnegative(fact.planned_dollar_risk, f"{prefix}_planned_risk", reasons)
        debit = _finite_nonnegative(fact.full_debit, f"{prefix}_full_debit", reasons)

        overlaps: list[str] = []
        seen: set[str] = set()
        for raw in fact.index_overlap:
            label = str(raw).strip().upper()
            if not label:
                reasons.append(f"{prefix}_index_overlap_empty")
                continue
            if label in seen:
                reasons.append(f"{prefix}_index_overlap_duplicate")
                continue
            seen.add(label)
            overlaps.append(label)

        if reasons and (risk is None or debit is None):
            continue
        if risk is not None and debit is not None:
            normalized.append(
                (
                    ExposureFact(
                        ticker=ticker,
                        direction=fact.direction,
                        planned_dollar_risk=risk,
                        full_debit=debit,
                        dte=fact.dte,
                        expiration=str(fact.expiration).strip(),
                        contracts=fact.contracts,
                        correlation_group=str(fact.correlation_group).strip(),
                        sector=str(fact.sector).strip(),
                        industry=str(fact.industry).strip(),
                        index_overlap=tuple(overlaps),
                        is_candidate=bool(fact.is_candidate),
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

    ticker_rows: list[tuple[str, float, float, int]] = []
    direction_rows: list[tuple[str, float, float, int]] = []
    correlation_rows: list[tuple[str, float, float, int]] = []
    sector_rows: list[tuple[str, float, float, int]] = []
    industry_rows: list[tuple[str, float, float, int]] = []
    expiration_rows: list[tuple[str, float, float, int]] = []
    dte_rows: list[tuple[str, float, float, int]] = []
    index_rows: list[tuple[str, float, float, int]] = []

    unknown_correlation = 0
    unknown_sector = 0
    unknown_industry = 0
    unknown_index = 0

    for fact, risk, debit in normalized:
        row = (risk, debit, fact.contracts)
        ticker_rows.append((fact.ticker, *row))
        direction_rows.append((fact.direction, *row))
        expiration_rows.append((fact.expiration, *row))
        dte_rows.append((_dte_bucket(fact.dte), *row))

        if fact.correlation_group:
            correlation_rows.append((fact.correlation_group, *row))
        else:
            unknown_correlation += 1
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

    snapshot = ConcentrationSnapshot(
        total_planned_dollar_risk=total_risk,
        total_full_debit=total_debit,
        position_count=len(normalized),
        contract_count=sum(fact.contracts for fact, _, _ in normalized),
        candidate_count=sum(1 for fact, _, _ in normalized if fact.is_candidate),
        by_ticker=_bucketize(ticker_rows, total_risk=total_risk, total_debit=total_debit),
        by_direction=_bucketize(direction_rows, total_risk=total_risk, total_debit=total_debit),
        by_correlation_group=_bucketize(correlation_rows, total_risk=total_risk, total_debit=total_debit),
        by_sector=_bucketize(sector_rows, total_risk=total_risk, total_debit=total_debit),
        by_industry=_bucketize(industry_rows, total_risk=total_risk, total_debit=total_debit),
        by_expiration=_bucketize(expiration_rows, total_risk=total_risk, total_debit=total_debit),
        by_dte_bucket=_bucketize(dte_rows, total_risk=total_risk, total_debit=total_debit),
        by_index_overlap=_bucketize(index_rows, total_risk=total_risk, total_debit=total_debit),
        unknown_correlation_count=unknown_correlation,
        unknown_sector_count=unknown_sector,
        unknown_industry_count=unknown_industry,
        unknown_index_overlap_count=unknown_index,
    )
    return ConcentrationResult(status=ConcentrationStatus.COMPLETE, snapshot=snapshot)
