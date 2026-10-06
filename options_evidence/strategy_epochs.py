"""Strategy epoch registry: preregistered, hash-pinned strategy definitions.

A strategy epoch names one exact definition of a strategy (setup, trigger,
target, filters, authority) plus its thresholds. Any material change to those
sections is a different definition hash and therefore must be registered as a
new epoch; results from different epochs are never pooled.

Rules enforced on load (``load_registry``):

* every epoch stores ``definition_sha256``; it must equal the hash recomputed
  from the material sections + thresholds, so an in-place edit of a frozen
  definition fails instead of silently relabelling history;
* (strategy, epoch) is unique, and no two epochs of one strategy share a
  definition hash (that would be a relabel, not a change);
* FROZEN epochs carry preregistration provenance (effective time, source
  commit, preregistration document); their effective windows for one
  strategy never overlap;
* the OOS reference, when present, carries an artifact hash and the raw
  per-trade R outcomes so a fitness evaluator can compare distributions.

Historical records are never relabelled: ``epoch_label_for_record`` returns a
record's own stamped epoch only when it is registered, otherwise
``LEGACY_UNVERSIONED`` / ``UNREGISTERED_EPOCH`` -- it never assigns the current
epoch to an old row.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

REGISTRY_SCHEMA = "options-strategy-epochs-v1"
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "options_strategy_epochs.json"

MATERIAL_SECTIONS = ("setup", "trigger", "target", "filters", "authority")
LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"
UNREGISTERED_EPOCH = "UNREGISTERED_EPOCH"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


class EpochStatus(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RETIRED = "RETIRED"


class RegistryError(ValueError):
    """The registry violates an epoch invariant; nothing may be resolved."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def definition_hash(definition: Mapping[str, Any], thresholds: Mapping[str, Any]) -> str:
    """sha256 over the material sections and thresholds only.

    Provenance (dates, commit, notes) is deliberately excluded: re-recording
    provenance does not change what the strategy *is*.
    """
    material = {name: definition.get(name) for name in MATERIAL_SECTIONS}
    payload = {"definition": material, "thresholds": dict(thresholds)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_utc(value: Any, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistryError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"{label} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise RegistryError(f"{label} must carry a UTC offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class OOSReference:
    """Untouched out-of-sample expectation the forward epoch is judged against."""

    source: str
    artifact_path: str
    artifact_sha256: str
    r_outcomes: tuple[float, ...]
    cost_model: str

    @property
    def n(self) -> int:
        return len(self.r_outcomes)

    @property
    def mean_r(self) -> float:
        return sum(self.r_outcomes) / len(self.r_outcomes)

    @staticmethod
    def from_mapping(raw: Mapping[str, Any], label: str) -> "OOSReference":
        try:
            outcomes = tuple(float(x) for x in raw["r_outcomes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"{label}.oos_reference.r_outcomes must be a list of numbers") from exc
        if not outcomes or not all(math.isfinite(x) for x in outcomes):
            raise RegistryError(f"{label}.oos_reference.r_outcomes must be non-empty and finite")
        sha = str(raw.get("artifact_sha256", ""))
        if not _SHA64.match(sha):
            raise RegistryError(f"{label}.oos_reference.artifact_sha256 must be a sha256 hex digest")
        for key in ("source", "artifact_path", "cost_model"):
            if not str(raw.get(key, "")).strip():
                raise RegistryError(f"{label}.oos_reference.{key} is required")
        return OOSReference(
            source=str(raw["source"]),
            artifact_path=str(raw["artifact_path"]),
            artifact_sha256=sha,
            r_outcomes=outcomes,
            cost_model=str(raw["cost_model"]),
        )


@dataclass(frozen=True)
class StrategyEpoch:
    strategy: str
    epoch: str
    status: EpochStatus
    definition: Mapping[str, Any]
    thresholds: Mapping[str, Any]
    definition_sha256: str
    effective_from: datetime | None
    effective_until: datetime | None
    source_commit: str | None
    preregistration_doc: str | None
    oos_reference: OOSReference | None
    supersedes: str | None
    observation_only: bool
    notes: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.strategy, self.epoch)

    def covers(self, at: datetime) -> bool:
        if self.effective_from is None or at < self.effective_from:
            return False
        return self.effective_until is None or at < self.effective_until


def _epoch_from_mapping(raw: Mapping[str, Any], index: int) -> StrategyEpoch:
    label = f"epochs[{index}]"
    if not isinstance(raw, Mapping):
        raise RegistryError(f"{label} must be an object")
    strategy = str(raw.get("strategy", ""))
    epoch = str(raw.get("epoch", ""))
    if not _NAME.match(strategy) or not _NAME.match(epoch):
        raise RegistryError(f"{label}: strategy and epoch must be short identifiers")
    if epoch in (LEGACY_UNVERSIONED, UNREGISTERED_EPOCH):
        raise RegistryError(f"{label}: {epoch} is a reserved label")
    try:
        status = EpochStatus(raw.get("status"))
    except ValueError as exc:
        raise RegistryError(f"{label}: status must be one of {[s.value for s in EpochStatus]}") from exc
    definition = raw.get("definition")
    if not isinstance(definition, Mapping):
        raise RegistryError(f"{label}.definition must be an object")
    missing = [name for name in MATERIAL_SECTIONS if name not in definition]
    if missing:
        raise RegistryError(f"{label}.definition missing material sections {missing}")
    extra = sorted(set(definition) - set(MATERIAL_SECTIONS))
    if extra:
        raise RegistryError(f"{label}.definition has non-material keys {extra}; put them in notes")
    thresholds = raw.get("thresholds", {})
    if not isinstance(thresholds, Mapping):
        raise RegistryError(f"{label}.thresholds must be an object")
    try:
        computed = definition_hash(definition, thresholds)
    except ValueError as exc:  # NaN/inf in a threshold
        raise RegistryError(f"{label}: definition/thresholds must be finite JSON") from exc
    stored = str(raw.get("definition_sha256", ""))
    if stored != computed:
        raise RegistryError(
            f"{label} ({strategy}/{epoch}): definition_sha256 {stored!r} != computed {computed}; "
            "a material change requires a new epoch, not an in-place edit"
        )
    authority = definition.get("authority")
    observation_only = bool(isinstance(authority, Mapping) and authority.get("observation_only") is True)
    if not isinstance(authority, Mapping) or "observation_only" not in authority:
        raise RegistryError(f"{label}.definition.authority.observation_only must be stated")
    if isinstance(authority, Mapping) and authority.get("execution_authority") is not False:
        # Execution authority is never granted by a registry entry. It is a
        # separate, human-approved record (see fitness.AuthorityState).
        raise RegistryError(f"{label}.definition.authority.execution_authority must be false")
    effective_from = _parse_utc(raw.get("effective_from"), f"{label}.effective_from")
    effective_until = _parse_utc(raw.get("effective_until"), f"{label}.effective_until")
    if effective_from and effective_until and effective_until <= effective_from:
        raise RegistryError(f"{label}: effective_until must be after effective_from")
    source_commit = raw.get("source_commit")
    if source_commit is not None and not _SHA40.match(str(source_commit)):
        raise RegistryError(f"{label}.source_commit must be a full 40-hex git SHA")
    prereg = raw.get("preregistration_doc")
    if status in (EpochStatus.FROZEN, EpochStatus.RETIRED):
        if effective_from is None or source_commit is None or not prereg:
            raise RegistryError(
                f"{label}: {status.value} epochs require effective_from, source_commit and preregistration_doc"
            )
    oos_raw = raw.get("oos_reference")
    oos = OOSReference.from_mapping(oos_raw, label) if oos_raw is not None else None
    return StrategyEpoch(
        strategy=strategy,
        epoch=epoch,
        status=status,
        definition=definition,
        thresholds=thresholds,
        definition_sha256=computed,
        effective_from=effective_from,
        effective_until=effective_until,
        source_commit=str(source_commit) if source_commit is not None else None,
        preregistration_doc=str(prereg) if prereg else None,
        oos_reference=oos,
        supersedes=raw.get("supersedes"),
        observation_only=observation_only,
        notes=str(raw.get("notes", "")),
    )


@dataclass(frozen=True)
class EpochRegistry:
    epochs: tuple[StrategyEpoch, ...]

    def get(self, strategy: str, epoch: str) -> StrategyEpoch | None:
        for item in self.epochs:
            if item.key == (strategy, epoch):
                return item
        return None

    def for_strategy(self, strategy: str) -> tuple[StrategyEpoch, ...]:
        return tuple(e for e in self.epochs if e.strategy == strategy)


def validate_epochs(epochs: Iterable[StrategyEpoch]) -> EpochRegistry:
    items = tuple(epochs)
    keys = [e.key for e in items]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise RegistryError(f"duplicate strategy/epoch keys: {dupes}")
    by_strategy: dict[str, list[StrategyEpoch]] = {}
    for item in items:
        by_strategy.setdefault(item.strategy, []).append(item)
    for strategy, group in by_strategy.items():
        hashes = [e.definition_sha256 for e in group]
        if len(hashes) != len(set(hashes)):
            raise RegistryError(
                f"{strategy}: two epochs share one definition hash; that is a relabel, not a new epoch"
            )
        names = {e.epoch for e in group}
        for e in group:
            if e.supersedes is not None and e.supersedes not in names:
                raise RegistryError(f"{strategy}/{e.epoch} supersedes unknown epoch {e.supersedes!r}")
        dated = sorted(
            (e for e in group if e.status is not EpochStatus.DRAFT),
            key=lambda e: e.effective_from,  # type: ignore[arg-type, return-value]
        )
        for earlier, later in zip(dated, dated[1:]):
            if earlier.effective_until is None or earlier.effective_until > later.effective_from:  # type: ignore[operator]
                raise RegistryError(
                    f"{strategy}: epochs {earlier.epoch} and {later.epoch} overlap; close the earlier "
                    "epoch (effective_until) before the later one starts"
                )
    return EpochRegistry(items)


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> EpochRegistry:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"cannot read epoch registry {path}: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"epoch registry schema must be {REGISTRY_SCHEMA}")
    entries = raw.get("epochs")
    if not isinstance(entries, list):
        raise RegistryError("epoch registry 'epochs' must be a list")
    return validate_epochs(_epoch_from_mapping(item, i) for i, item in enumerate(entries))


@dataclass(frozen=True)
class EpochResolution:
    label: str
    epoch: StrategyEpoch | None
    reason: str

    @property
    def registered(self) -> bool:
        return self.epoch is not None


def resolve_epoch(
    registry: EpochRegistry,
    *,
    strategy: str,
    definition: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    at: datetime,
) -> EpochResolution:
    """Stamp a *new* observation with the epoch whose frozen definition it runs.

    The running definition must hash-match a FROZEN epoch that covers ``at``.
    Anything else is ``UNREGISTERED_EPOCH``: the observation is still recorded
    by observers, but it is excluded from every epoch's fitness population.
    """
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    digest = definition_hash(definition, thresholds)
    for epoch in registry.for_strategy(strategy):
        if epoch.definition_sha256 != digest:
            continue
        if epoch.status is not EpochStatus.FROZEN:
            return EpochResolution(UNREGISTERED_EPOCH, None, f"epoch {epoch.epoch} is {epoch.status.value}")
        if not epoch.covers(at.astimezone(timezone.utc)):
            return EpochResolution(UNREGISTERED_EPOCH, None, f"epoch {epoch.epoch} not effective at {at.isoformat()}")
        return EpochResolution(epoch.epoch, epoch, "definition_hash_match")
    return EpochResolution(UNREGISTERED_EPOCH, None, "running definition matches no registered epoch")


def epoch_label_for_record(registry: EpochRegistry, record: Mapping[str, Any]) -> str:
    """Read-time label for a stored record. Never assigns; never relabels.

    * no ``strategy_epoch`` on the record -> ``LEGACY_UNVERSIONED``;
    * stamped epoch not in the registry -> ``UNREGISTERED_EPOCH``;
    * otherwise the record's own stamped epoch.
    """
    stamped = record.get("strategy_epoch")
    strategy = record.get("strategy")
    if not stamped:
        return LEGACY_UNVERSIONED
    if not strategy or registry.get(str(strategy), str(stamped)) is None:
        return UNREGISTERED_EPOCH
    return str(stamped)
