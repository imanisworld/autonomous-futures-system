"""Forward-proof readiness gate for an options strategy epoch.

Answers one question: may an official forward-proof window (Day 1) start for
this epoch? The answer is READY only when every prerequisite is met. Day 1 is
never declared by this module; it returns the evidence an operator needs.

Prerequisites derived from the epoch registry (cannot be asserted by hand):

* ``strategy_epoch_frozen``      -- epoch exists and is FROZEN;
* ``outcome_definitions_frozen`` -- no target/stop/runner field is UNRESOLVED;
* ``oos_reference_registered``   -- hash-pinned untouched OOS R distribution;
* ``costs_frozen``               -- the OOS reference states its cost model;
* ``no_threshold_changes``       -- definition hash matches the registry pin
  (load-time validation already refuses drift, so a loaded FROZEN epoch passes).

Prerequisites that need runtime evidence (an ``EvidenceRef`` with a source and
a timestamp; a bare True is refused):

* ``capture_integrity``          -- timely pre-trigger capture proven live by
  the #1145 setup-capture observer; build it with ``capture_integrity_evidence``
  from #1145 records (``is_prospective_catch`` / ``catch_count``), never by hand;
* ``dedupe``                     -- one #1145 ``structure_key`` -> one canonical
  signal per epoch, proven on live journal output;
* ``data_source_frozen``         -- the live feed matches the epoch's source;
* ``integrity_monitoring_active``-- read-only observer status shows OK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from alert_ranker.setup_capture import (
    STATUS_AMBIGUOUS,
    STATUS_DATA_BLOCKED,
    STATUS_MISSED_LATE,
    STATUS_TRIGGERED,
    catch_count,
    is_prospective_catch,
)

from .strategy_epochs import EpochRegistry, EpochStatus, StrategyEpoch

DERIVED = (
    "strategy_epoch_frozen",
    "outcome_definitions_frozen",
    "oos_reference_registered",
    "costs_frozen",
    "no_threshold_changes",
)
RUNTIME = (
    "capture_integrity",
    "dedupe",
    "data_source_frozen",
    "integrity_monitoring_active",
)


@dataclass(frozen=True)
class EvidenceRef:
    passed: bool
    source: str
    observed_at: datetime
    note: str = ""

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("runtime evidence needs a source (PR, artifact, or status output)")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass(frozen=True)
class Readiness:
    strategy: str
    epoch: str
    ready: bool
    checks: Mapping[str, tuple[bool, str]]

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(f"{name}: {why}" for name, (ok, why) in self.checks.items() if not ok)


def capture_integrity_evidence(
    records: Iterable[Any],
    *,
    source: str,
    observed_at: datetime,
) -> EvidenceRef:
    """``capture_integrity`` evidence from #1145 current records for a window.

    Fails closed. Passes only when the window holds at least one #1145
    prospective catch and *every* triggered structure is one: a MISSED_LATE,
    a TRIGGERED row that is late or still pending SIP, a DATA_BLOCKED or an
    AMBIGUOUS row each fail it. GAP_THROUGH_OPEN, INVALIDATED, EXPIRED and
    NO_TRIGGER are market outcomes, not capture failures.
    """
    rows = list(records)

    def status(row: Any) -> str:
        return str(row.get("status") if isinstance(row, Mapping) else getattr(row, "status", ""))

    catches = catch_count(rows)
    failures = {
        "triggered_not_catch": sum(1 for r in rows if status(r) == STATUS_TRIGGERED and not is_prospective_catch(r)),
        "missed_late": sum(1 for r in rows if status(r) == STATUS_MISSED_LATE),
        "data_blocked": sum(1 for r in rows if status(r) == STATUS_DATA_BLOCKED),
        "ambiguous": sum(1 for r in rows if status(r) == STATUS_AMBIGUOUS),
    }
    passed = catches >= 1 and not any(failures.values())
    note = f"catch_count={catches} " + " ".join(f"{k}={v}" for k, v in failures.items())
    return EvidenceRef(passed, source, observed_at, note)


def _unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() == "UNRESOLVED"
    if isinstance(value, Mapping):
        return any(_unresolved(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_unresolved(v) for v in value)
    return False


def _derived(epoch: StrategyEpoch | None) -> dict[str, tuple[bool, str]]:
    if epoch is None:
        return {name: (False, "epoch not registered") for name in DERIVED}
    frozen = epoch.status is EpochStatus.FROZEN
    oos = epoch.oos_reference
    return {
        "strategy_epoch_frozen": (frozen, f"status {epoch.status.value}"),
        "outcome_definitions_frozen": (
            not _unresolved(epoch.definition.get("target")),
            "target/stop/runner resolved" if not _unresolved(epoch.definition.get("target")) else
            "target section still contains UNRESOLVED",
        ),
        "oos_reference_registered": (oos is not None, "present" if oos else "no OOS reference registered"),
        "costs_frozen": (bool(oos and oos.cost_model.strip()), "cost model stated" if oos else "no OOS cost model"),
        "no_threshold_changes": (frozen, "registry hash validated at load" if frozen else "epoch not frozen"),
    }


def evaluate_readiness(
    registry: EpochRegistry,
    *,
    strategy: str,
    epoch: str,
    runtime_evidence: Mapping[str, EvidenceRef] | None = None,
) -> Readiness:
    target = registry.get(strategy, epoch)
    checks = _derived(target)
    supplied = dict(runtime_evidence or {})
    unknown = sorted(set(supplied) - set(RUNTIME))
    if unknown:
        raise ValueError(f"unknown runtime prerequisites {unknown}; derived checks cannot be asserted")
    for name in RUNTIME:
        ref = supplied.get(name)
        if ref is None:
            checks[name] = (False, "no runtime evidence supplied")
        else:
            checks[name] = (ref.passed, f"{ref.source} @ {ref.observed_at.isoformat()}" + (f" ({ref.note})" if ref.note else ""))
    return Readiness(
        strategy=strategy,
        epoch=epoch,
        ready=all(ok for ok, _ in checks.values()),
        checks=dict(checks),
    )
