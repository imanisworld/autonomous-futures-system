"""Setup-quality research scaffold. No scoring model lives here.

Purpose: collect candidate pre-entry factors per clean prospective signal so a
*later, preregistered* study can test whether any of them predicts outcome.
This module deliberately offers no score, weight, rank, or threshold.

Hygiene enforced:

* every factor value carries ``as_of``; a value observed after the signal's
  trigger detection time is look-ahead and is refused;
* missing factors are explicit (``status=UNAVAILABLE`` + reason), never imputed;
* the research population is only signals in a *registered* strategy epoch
  with VALID data/signal integrity on both the canonical signal record and the
  outcome record, and a known result -- legacy, unregistered, and degraded
  rows are excluded and counted; the result never selects (losers stay in);
* the observational setup rating (#1089) may be recorded as a factor under
  test; it is never an outcome label and never a filter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping

from .outcome import PNL_BASES, SCHEMA as OUTCOME_SCHEMA, result_r_value
from .signal import IntegrityStatus, ProspectiveSignal, verify_record
from .strategy_epochs import EpochRegistry, epoch_label_for_record, LEGACY_UNVERSIONED, UNREGISTERED_EPOCH

SCHEMA = "options-setup-research-row-v1"

CANDIDATE_FACTORS = (
    "setup_family",
    "timeframe_continuity",
    "htf_alignment",
    "spy_qqq_regime",
    "gex_regime",
    "flip_relationship",
    "signa",
    "level_quality",
    "distance_to_resistance_support",
    "gap_context",
    "trigger_geometry",
    "contract_quality",
    "observation_rating_1089",
)


class FactorStatus(str, Enum):
    OBSERVED = "OBSERVED"
    UNAVAILABLE = "UNAVAILABLE"


class ResearchError(ValueError):
    pass


@dataclass(frozen=True)
class FactorValue:
    name: str
    status: FactorStatus
    value: Any = None
    as_of: datetime | None = None
    source: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.name not in CANDIDATE_FACTORS:
            raise ResearchError(f"unknown factor {self.name!r}; add it to CANDIDATE_FACTORS deliberately")
        if self.status is FactorStatus.OBSERVED:
            if self.value is None or self.as_of is None or not self.source.strip():
                raise ResearchError(f"{self.name}: OBSERVED needs value, as_of and source")
            if self.as_of.tzinfo is None:
                raise ResearchError(f"{self.name}: as_of must be timezone-aware")
            if isinstance(self.value, float) and not math.isfinite(self.value):
                raise ResearchError(f"{self.name}: value must be finite")
        else:
            if self.value is not None or not self.reason.strip():
                raise ResearchError(f"{self.name}: UNAVAILABLE carries no value and needs a reason")


def build_feature_row(
    signal: ProspectiveSignal,
    factors: Iterable[FactorValue],
) -> dict[str, Any]:
    """One research row of pre-entry factors. Refuses look-ahead and silent gaps."""
    cutoff = signal.trigger_detection_time or signal.first_seen_time
    given = {}
    for factor in factors:
        if factor.name in given:
            raise ResearchError(f"duplicate factor {factor.name}")
        if factor.status is FactorStatus.OBSERVED and factor.as_of > cutoff:  # type: ignore[operator]
            raise ResearchError(
                f"{factor.name}: observed at {factor.as_of.isoformat()} after the decision cutoff "
                f"{cutoff.isoformat()} (look-ahead)"
            )
        given[factor.name] = factor
    missing = [name for name in CANDIDATE_FACTORS if name not in given]
    if missing:
        raise ResearchError(f"factors not stated (use UNAVAILABLE with a reason): {missing}")
    return {
        "schema": SCHEMA,
        "signal_id": signal.signal_id,
        "structure_id": signal.structure_id,
        "strategy": signal.strategy,
        "strategy_epoch": signal.strategy_epoch,
        "decision_cutoff": cutoff.isoformat(),
        "factors": {
            name: {
                "status": f.status.value,
                "value": f.value,
                "as_of": f.as_of.isoformat() if f.as_of else None,
                "source": f.source,
                "reason": f.reason,
            }
            for name, f in ((n, given[n]) for n in CANDIDATE_FACTORS)
        },
        "trade_authority": False,
    }


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ResearchError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ResearchError(f"{label} is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchError(f"{label} must be timezone-aware")
    return parsed


# Exactly the keys build_feature_row writes; anything else (e.g. a "score")
# never rides into the research population.
FEATURE_ROW_KEYS = frozenset(
    {"schema", "signal_id", "structure_id", "strategy", "strategy_epoch", "decision_cutoff", "factors", "trade_authority"}
)


def _validate_feature_row(feature_row: Mapping[str, Any], signal_record: Mapping[str, Any]) -> str | None:
    if not isinstance(feature_row, Mapping):
        return "feature_row_invalid"
    if feature_row.get("schema") != SCHEMA:
        return "feature_row_schema"
    if set(feature_row) != FEATURE_ROW_KEYS:
        return "feature_row_keys"
    for key in ("signal_id", "structure_id", "strategy", "strategy_epoch"):
        if feature_row.get(key) != signal_record.get(key):
            return "feature_row_identity"
    if feature_row.get("trade_authority") is not False:
        return "feature_row_authority"
    try:
        cutoff = _parse_time(feature_row.get("decision_cutoff"), "decision_cutoff")
        expected = _parse_time(
            signal_record.get("trigger_detection_time") or signal_record.get("first_seen_time"),
            "signal decision cutoff",
        )
    except ResearchError:
        return "feature_row_cutoff"
    if cutoff != expected:
        return "feature_row_cutoff"

    factors = feature_row.get("factors")
    if not isinstance(factors, Mapping) or set(factors) != set(CANDIDATE_FACTORS):
        return "feature_row_factors"
    for name in CANDIDATE_FACTORS:
        item = factors.get(name)
        if not isinstance(item, Mapping):
            return "feature_row_factors"
        status = item.get("status")
        if status == FactorStatus.OBSERVED.value:
            if item.get("value") is None:
                return "feature_row_factors"
            if not isinstance(item.get("source"), str) or not item.get("source", "").strip():
                return "feature_row_factors"
            try:
                at = _parse_time(item.get("as_of"), f"{name}.as_of")
            except ResearchError:
                return "feature_row_factors"
            if at > cutoff:
                return "feature_row_lookahead"
            value = item.get("value")
            if isinstance(value, float) and not math.isfinite(value):
                return "feature_row_factors"
        elif status == FactorStatus.UNAVAILABLE.value:
            if item.get("value") is not None or item.get("as_of") is not None:
                return "feature_row_factors"
            if not isinstance(item.get("reason"), str) or not item.get("reason", "").strip():
                return "feature_row_factors"
        else:
            return "feature_row_factors"
    return None


def _validate_outcome_record(outcome_record: Mapping[str, Any], signal_record: Mapping[str, Any]) -> str | None:
    if not isinstance(outcome_record, Mapping):
        return "outcome_invalid"
    if outcome_record.get("schema") != OUTCOME_SCHEMA:
        return "outcome_schema"
    for key in ("signal_id", "structure_id", "strategy_epoch", "resolution_state", "prospective_catch"):
        if outcome_record.get(key) != signal_record.get(key):
            return "outcome_identity"
    # A catch is a TRIGGERED resolution (verify_record ties resolution_state to
    # history); a forged prospective_catch flag on a non-triggered signal is not.
    if signal_record.get("prospective_catch") is not True or signal_record.get("resolution_state") != "TRIGGERED":
        return "not_prospective_catch"
    executed = outcome_record.get("executed")
    if not isinstance(executed, bool):
        return "outcome_executed_type"
    basis = outcome_record.get("pnl_basis")
    if basis not in PNL_BASES:
        return "outcome_basis"
    if executed and basis != "executed":
        return "outcome_basis"
    if not executed and basis != "paper_equivalent":
        return "outcome_basis"
    if executed and outcome_record.get("execution_integrity") != IntegrityStatus.VALID.value:
        return "execution_integrity"
    return None


@dataclass(frozen=True)
class ResearchPopulation:
    rows: tuple[dict[str, Any], ...]
    excluded: Mapping[str, int]


def research_population(
    registry: EpochRegistry,
    joined: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]],
) -> ResearchPopulation:
    """Filter canonical (signal, outcome, feature) triples to the clean population.

    Admission is fail-closed: the signal must pass #1151 record verification
    against the supplied registry, the outcome must agree with the canonical
    signal/prospective-catch/trade-basis fields, and the persisted feature row
    must independently satisfy the same no-look-ahead schema that
    build_feature_row produces.
    """
    if not isinstance(registry, EpochRegistry):
        raise ResearchError("registry must be an EpochRegistry")
    rows: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    triples = list(joined)
    # One prospective signal is one research row: every triple of a signal_id
    # that appears more than once (replay, or conflicting outcomes) is excluded.
    seen_counts: dict[Any, int] = {}
    for triple in triples:
        signal_record = triple[0] if isinstance(triple, tuple) and len(triple) == 3 else None
        if isinstance(signal_record, Mapping):
            key = signal_record.get("signal_id")
            if isinstance(key, str):
                seen_counts[key] = seen_counts.get(key, 0) + 1

    for triple in triples:
        if not isinstance(triple, tuple) or len(triple) != 3:
            drop("malformed_record")
            continue
        signal_record, outcome_record, feature_row = triple
        if not all(isinstance(record, Mapping) for record in (signal_record, outcome_record, feature_row)):
            drop("malformed_record")
            continue
        if seen_counts.get(signal_record.get("signal_id"), 0) > 1:
            drop("duplicate_signal")
            continue
        ids = {signal_record.get("signal_id"), outcome_record.get("signal_id"), feature_row.get("signal_id")}
        if len(ids) != 1:
            drop("mismatched_ids")
            continue

        label = epoch_label_for_record(registry, signal_record)
        if label == LEGACY_UNVERSIONED:
            drop("legacy_unversioned")
            continue
        if label == UNREGISTERED_EPOCH:
            drop("unregistered_epoch")
            continue

        signal_problems = verify_record(signal_record, registry=registry)
        if signal_problems:
            drop("signal_record_invalid")
            continue

        if any(
            record.get(k) != IntegrityStatus.VALID.value
            for record in (signal_record, outcome_record)
            for k in ("data_integrity", "signal_integrity")
        ):
            drop("integrity")
            continue

        outcome_problem = _validate_outcome_record(outcome_record, signal_record)
        if outcome_problem is not None:
            drop(outcome_problem)
            continue

        feature_problem = _validate_feature_row(feature_row, signal_record)
        if feature_problem is not None:
            drop(feature_problem)
            continue

        result = result_r_value(outcome_record)
        if result is None:
            drop("result_unavailable")
            continue
        rows.append({**feature_row, "result_r": result, "strategy_epoch": label})
    return ResearchPopulation(rows=tuple(rows), excluded=dict(sorted(excluded.items())))
