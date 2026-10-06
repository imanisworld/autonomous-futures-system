"""Setup-quality research scaffold. No scoring model lives here.

Purpose: collect candidate pre-entry factors per clean prospective signal so a
*later, preregistered* study can test whether any of them predicts outcome.
This module deliberately offers no score, weight, rank, or threshold.

Hygiene enforced:

* every factor value carries ``as_of``; a value observed after the signal's
  trigger detection time is look-ahead and is refused;
* missing factors are explicit (``status=UNAVAILABLE`` + reason), never imputed;
* the research population is only signals in a *registered* strategy epoch
  with VALID data/signal integrity and a known result -- legacy, unregistered,
  and degraded rows are excluded and counted;
* the observational setup rating (#1089) may be recorded as a factor under
  test; it is never an outcome label and never a filter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping

from .outcome import result_r_value
from .signal import IntegrityStatus, ProspectiveSignal
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


@dataclass(frozen=True)
class ResearchPopulation:
    rows: tuple[dict[str, Any], ...]
    excluded: Mapping[str, int]


def research_population(
    registry: EpochRegistry,
    joined: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]],
) -> ResearchPopulation:
    """Filter (signal_record, outcome_record, feature_row) triples to the clean population."""
    rows: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for signal_record, outcome_record, feature_row in joined:
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
        if any(
            outcome_record.get(k) != IntegrityStatus.VALID.value
            for k in ("data_integrity", "signal_integrity")
        ):
            drop("integrity")
            continue
        result = result_r_value(outcome_record)
        if result is None:
            drop("result_unavailable")
            continue
        rows.append({**feature_row, "result_r": result, "strategy_epoch": label})
    return ResearchPopulation(rows=tuple(rows), excluded=dict(sorted(excluded.items())))
