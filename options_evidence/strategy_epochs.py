"""Strategy epoch registry: preregistered, hash-pinned strategy definitions.

A strategy epoch names one exact definition of a strategy (setup, trigger,
target, filters, authority) plus its thresholds. Any material change to those
sections is a different definition hash and therefore must be registered as a
new epoch; results from different epochs are never pooled.

The registry is observation-only and fail-closed. Rules enforced on load
(``load_registry``):

* every malformed input raises ``RegistryError`` -- never a bare
  ``TypeError`` / ``OverflowError`` / ``RecursionError`` -- and nothing is
  coerced: strings must be strings, numbers must be finite non-bool numbers;
* keys are allowlisted at every level (registry, epoch, ``oos_reference``,
  ``definition.authority``); an unknown key such as ``tradable``, ``active``
  or ``trading_authority`` is refused rather than ignored, and duplicate JSON
  object keys are refused rather than last-one-wins;
* ``definition.authority`` is exactly ``observation_only: true``,
  ``execution_authority: false``, ``risk_reservation: false``,
  ``trade_alerts: false`` (exact booleans). A registry entry can never grant
  execution, risk reservation or trade alerts;
* every epoch stores ``definition_sha256``; it must equal the hash recomputed
  from the material sections + thresholds, so an in-place edit of a frozen
  definition fails instead of silently relabelling history;
* (strategy, epoch) is unique, and no two epochs of one strategy share a
  definition hash (that would be a relabel, not a change);
* FROZEN/RETIRED epochs carry preregistration provenance (effective time,
  source commit, preregistration document); their effective windows for one
  strategy never overlap;
* the OOS reference, when present, carries an artifact hash and the raw
  per-trade R outcomes so a fitness evaluator can compare distributions;
* loaded definitions and thresholds are deep-frozen (read-only mappings and
  tuples), so a loaded epoch cannot be mutated after its hash was checked.

Historical records are never relabelled: ``epoch_label_for_record`` returns a
record's own stamped ``strategy_epoch`` only when it names a registered
FROZEN/RETIRED epoch of the record's ``strategy``, otherwise
``LEGACY_UNVERSIONED`` / ``UNREGISTERED_EPOCH`` -- it never assigns the current
epoch to an old row. In particular the 122 prospective collector's
``policy_epoch`` field is *not* a ``strategy_epoch`` stamp: collector rows carry
no ``strategy``/``strategy_epoch`` and therefore read as ``LEGACY_UNVERSIONED``.
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
from types import MappingProxyType
from typing import Any, Iterable, Mapping

REGISTRY_SCHEMA = "options-strategy-epochs-v1"
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "options_strategy_epochs.json"

MATERIAL_SECTIONS = ("setup", "trigger", "target", "filters", "authority")
LEGACY_UNVERSIONED = "LEGACY_UNVERSIONED"
UNREGISTERED_EPOCH = "UNREGISTERED_EPOCH"

# The only shape a registry entry's authority may take. Granting anything is
# outside the registry: execution authority is a separate, human-approved
# record, never a registry field.
REQUIRED_AUTHORITY = MappingProxyType(
    {
        "observation_only": True,
        "execution_authority": False,
        "risk_reservation": False,
        "trade_alerts": False,
    }
)

REGISTRY_KEYS = frozenset({"schema", "notes", "epochs"})
EPOCH_KEYS = frozenset(
    {
        "strategy",
        "epoch",
        "status",
        "definition",
        "thresholds",
        "definition_sha256",
        "effective_from",
        "effective_until",
        "source_commit",
        "preregistration_doc",
        "oos_reference",
        "supersedes",
        "notes",
    }
)
REQUIRED_EPOCH_KEYS = frozenset({"strategy", "epoch", "status", "definition", "definition_sha256"})
OOS_KEYS = frozenset({"source", "artifact_path", "artifact_sha256", "r_outcomes", "cost_model"})

_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA64 = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")

# Failures a hostile or corrupt input can raise from json/datetime/hashing
# internals. Every public entry point turns these into RegistryError.
_MALFORMED = (TypeError, ValueError, OverflowError, RecursionError, KeyError, AttributeError)


class EpochStatus(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    RETIRED = "RETIRED"


class RegistryError(ValueError):
    """The registry violates an epoch invariant; nothing may be resolved."""


def _json_default(value: Any) -> Any:
    # Frozen definitions are MappingProxyType; serialise them as plain objects.
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    )


def definition_hash(definition: Mapping[str, Any], thresholds: Mapping[str, Any]) -> str:
    """sha256 over the material sections and thresholds only.

    Provenance (dates, commit, notes) is deliberately excluded: re-recording
    provenance does not change what the strategy *is*.
    """
    material = {name: definition.get(name) for name in MATERIAL_SECTIONS}
    payload = {"definition": material, "thresholds": dict(thresholds)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    """Deep read-only copy: mappings -> MappingProxyType, lists -> tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(raw: Mapping[str, Any], key: str, label: str, *, required: bool) -> str | None:
    """A non-blank string without surrounding whitespace, or None when optional."""
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise RegistryError(f"{label}.{key} must be a non-blank string without surrounding whitespace")
    return value


def _identifier(raw: Mapping[str, Any], key: str, label: str, *, required: bool = True) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise RegistryError(f"{label}.{key} must be a short identifier string")
    return value


def _parse_utc(value: Any, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistryError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise RegistryError(f"{label} must carry a UTC offset")
        return parsed.astimezone(timezone.utc)
    except RegistryError:
        raise
    except (ValueError, OverflowError) as exc:
        raise RegistryError(f"{label} is not a representable ISO-8601 UTC time: {value!r}") from exc


def _check_keys(raw: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise RegistryError(f"{label} has unknown keys {unknown}; allowed: {sorted(allowed)}")


def _check_authority(authority: Any, label: str) -> None:
    if not isinstance(authority, Mapping):
        raise RegistryError(f"{label}.definition.authority must be an object")
    _check_keys(authority, frozenset(REQUIRED_AUTHORITY), f"{label}.definition.authority")
    for key, required in REQUIRED_AUTHORITY.items():
        if key not in authority:
            raise RegistryError(f"{label}.definition.authority.{key} must be stated")
        value = authority[key]
        if not isinstance(value, bool) or value is not required:
            raise RegistryError(
                f"{label}.definition.authority.{key} must be {str(required).lower()}; "
                "a registry entry is observation-only and grants nothing"
            )


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
    def from_mapping(raw: Any, label: str) -> "OOSReference":
        olabel = f"{label}.oos_reference"
        if not isinstance(raw, Mapping):
            raise RegistryError(f"{olabel} must be an object")
        _check_keys(raw, OOS_KEYS, olabel)
        values = raw.get("r_outcomes")
        if not isinstance(values, list) or not values:
            raise RegistryError(f"{olabel}.r_outcomes must be a non-empty list of finite numbers")
        outcomes: list[float] = []
        for i, value in enumerate(values):
            if not _is_number(value):
                raise RegistryError(f"{olabel}.r_outcomes[{i}] must be a number, not {type(value).__name__}")
            try:
                number = float(value)
            except OverflowError as exc:
                raise RegistryError(f"{olabel}.r_outcomes[{i}] is out of float range") from exc
            if not math.isfinite(number):
                raise RegistryError(f"{olabel}.r_outcomes must be non-empty and finite")
            outcomes.append(number)
        sha = raw.get("artifact_sha256")
        if not isinstance(sha, str) or not _SHA64.fullmatch(sha):
            raise RegistryError(f"{olabel}.artifact_sha256 must be a sha256 hex digest")
        source = _text(raw, "source", olabel, required=True)
        artifact_path = _text(raw, "artifact_path", olabel, required=True)
        cost_model = _text(raw, "cost_model", olabel, required=True)
        return OOSReference(
            source=source,  # type: ignore[arg-type]
            artifact_path=artifact_path,  # type: ignore[arg-type]
            artifact_sha256=sha,
            r_outcomes=tuple(outcomes),
            cost_model=cost_model,  # type: ignore[arg-type]
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

    def __post_init__(self) -> None:
        # Read-only from construction on, whether built by the loader or directly.
        object.__setattr__(self, "definition", _freeze(self.definition))
        object.__setattr__(self, "thresholds", _freeze(self.thresholds))
        # The authority invariants hold for every epoch, not only file-loaded
        # ones: a directly constructed epoch cannot opt out of them.
        assert_observation_only(self)

    @property
    def key(self) -> tuple[str, str]:
        return (self.strategy, self.epoch)

    def covers(self, at: datetime) -> bool:
        if self.effective_from is None or at < self.effective_from:
            return False
        return self.effective_until is None or at < self.effective_until


def assert_observation_only(epoch: StrategyEpoch) -> StrategyEpoch:
    """Raise RegistryError unless ``epoch`` is an observation-only epoch.

    The shared authority boundary: ``StrategyEpoch`` construction,
    ``validate_epochs`` and ``EpochRegistry`` all call it, so the invariants do
    not depend on whether an epoch came from the registry file or from code.
    Consumers that accept a ``StrategyEpoch`` may call it again at use time.
    """
    if not isinstance(epoch, StrategyEpoch):
        raise RegistryError(f"expected StrategyEpoch, not {type(epoch).__name__}")
    label = f"{epoch.strategy}/{epoch.epoch}"
    if not isinstance(epoch.status, EpochStatus):
        raise RegistryError(f"{label}: status must be an EpochStatus, not {type(epoch.status).__name__}")
    if epoch.observation_only is not True:
        raise RegistryError(f"{label}: observation_only must be true; a strategy epoch grants nothing")
    if not isinstance(epoch.definition, Mapping):
        raise RegistryError(f"{label}.definition must be a mapping")
    _check_authority(epoch.definition.get("authority"), label)
    return epoch


def _epoch_from_mapping(raw: Any, index: int) -> StrategyEpoch:
    label = f"epochs[{index}]"
    if not isinstance(raw, Mapping):
        raise RegistryError(f"{label} must be an object")
    _check_keys(raw, EPOCH_KEYS, label)
    missing_keys = sorted(REQUIRED_EPOCH_KEYS - set(raw))
    if missing_keys:
        raise RegistryError(f"{label} is missing required keys {missing_keys}")
    strategy = _identifier(raw, "strategy", label)
    epoch = _identifier(raw, "epoch", label)
    if epoch in (LEGACY_UNVERSIONED, UNREGISTERED_EPOCH):
        raise RegistryError(f"{label}: {epoch} is a reserved label")
    status_raw = raw.get("status")
    if not isinstance(status_raw, str) or status_raw not in EpochStatus.__members__:
        raise RegistryError(f"{label}: status must be one of {[s.value for s in EpochStatus]}")
    status = EpochStatus(status_raw)

    definition = raw.get("definition")
    if not isinstance(definition, Mapping):
        raise RegistryError(f"{label}.definition must be an object")
    missing = [name for name in MATERIAL_SECTIONS if name not in definition]
    if missing:
        raise RegistryError(f"{label}.definition missing material sections {missing}")
    extra = sorted(str(key) for key in definition if key not in MATERIAL_SECTIONS)
    if extra:
        raise RegistryError(f"{label}.definition has non-material keys {extra}; put them in notes")
    _check_authority(definition.get("authority"), label)

    thresholds = raw.get("thresholds", {})
    if not isinstance(thresholds, Mapping):
        raise RegistryError(f"{label}.thresholds must be an object")
    for key, value in thresholds.items():
        if not isinstance(key, str) or not _NAME.fullmatch(key):
            raise RegistryError(f"{label}.thresholds key {key!r} must be a short identifier")
        if not _is_number(value) or (isinstance(value, float) and not math.isfinite(value)):
            raise RegistryError(f"{label}.thresholds.{key} must be a finite number (not bool/string)")

    try:
        computed = definition_hash(definition, thresholds)
    except _MALFORMED as exc:  # NaN/inf, unserialisable or absurdly deep values
        raise RegistryError(f"{label}: definition/thresholds must be finite JSON") from exc
    stored = raw.get("definition_sha256")
    if not isinstance(stored, str) or stored != computed:
        raise RegistryError(
            f"{label} ({strategy}/{epoch}): definition_sha256 {stored!r} != computed {computed}; "
            "a material change requires a new epoch, not an in-place edit"
        )

    effective_from = _parse_utc(raw.get("effective_from"), f"{label}.effective_from")
    effective_until = _parse_utc(raw.get("effective_until"), f"{label}.effective_until")
    if effective_from and effective_until and effective_until <= effective_from:
        raise RegistryError(f"{label}: effective_until must be after effective_from")
    source_commit = raw.get("source_commit")
    if source_commit is not None and (not isinstance(source_commit, str) or not _SHA40.fullmatch(source_commit)):
        raise RegistryError(f"{label}.source_commit must be a full 40-hex git SHA")
    prereg = _text(raw, "preregistration_doc", label, required=False)
    if status in (EpochStatus.FROZEN, EpochStatus.RETIRED):
        if effective_from is None or source_commit is None or prereg is None:
            raise RegistryError(
                f"{label}: {status.value} epochs require effective_from, source_commit and preregistration_doc"
            )
    oos_raw = raw.get("oos_reference")
    oos = OOSReference.from_mapping(oos_raw, label) if oos_raw is not None else None
    supersedes = _identifier(raw, "supersedes", label, required=False)
    if supersedes == epoch:
        raise RegistryError(f"{label}: an epoch cannot supersede itself")
    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise RegistryError(f"{label}.notes must be a string")
    return StrategyEpoch(
        strategy=strategy,  # type: ignore[arg-type]
        epoch=epoch,  # type: ignore[arg-type]
        status=status,
        definition=definition,
        thresholds=thresholds,
        definition_sha256=computed,
        effective_from=effective_from,
        effective_until=effective_until,
        source_commit=source_commit,
        preregistration_doc=prereg,
        oos_reference=oos,
        supersedes=supersedes,
        observation_only=True,
        notes=notes,
    )


@dataclass(frozen=True)
class EpochRegistry:
    epochs: tuple[StrategyEpoch, ...]

    def __post_init__(self) -> None:
        epochs = tuple(self.epochs)
        for item in epochs:
            assert_observation_only(item)
        object.__setattr__(self, "epochs", epochs)

    def get(self, strategy: str, epoch: str) -> StrategyEpoch | None:
        for item in self.epochs:
            if item.key == (strategy, epoch):
                return item
        return None

    def for_strategy(self, strategy: str) -> tuple[StrategyEpoch, ...]:
        return tuple(e for e in self.epochs if e.strategy == strategy)


def validate_epochs(epochs: Iterable[StrategyEpoch]) -> EpochRegistry:
    items = tuple(epochs)
    for item in items:
        if not isinstance(item, StrategyEpoch):
            raise RegistryError(f"registry entries must be StrategyEpoch, not {type(item).__name__}")
        assert_observation_only(item)
        if not isinstance(item.strategy, str) or not isinstance(item.epoch, str):
            raise RegistryError("strategy and epoch must be strings")
        if item.supersedes is not None and not isinstance(item.supersedes, str):
            raise RegistryError(f"{item.strategy}/{item.epoch}: supersedes must be a string")
        if item.supersedes == item.epoch:
            raise RegistryError(f"{item.strategy}/{item.epoch}: an epoch cannot supersede itself")
        if item.status is not EpochStatus.DRAFT and not isinstance(item.effective_from, datetime):
            raise RegistryError(f"{item.strategy}/{item.epoch}: {item.status.value} epochs require effective_from")
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


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise RegistryError(f"duplicate JSON key {key!r}; the registry must be unambiguous")
        seen[key] = value
    return seen


def _reject_constant(name: str) -> Any:
    raise RegistryError(f"non-finite JSON number {name} is not allowed in the registry")


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> EpochRegistry:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise RegistryError(f"cannot read epoch registry {path}: {exc}") from exc
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant)
        if not isinstance(raw, Mapping) or raw.get("schema") != REGISTRY_SCHEMA:
            raise RegistryError(f"epoch registry schema must be {REGISTRY_SCHEMA}")
        _check_keys(raw, REGISTRY_KEYS, "epoch registry")
        if not isinstance(raw.get("notes", ""), str):
            raise RegistryError("epoch registry notes must be a string")
        entries = raw.get("epochs")
        if not isinstance(entries, list):
            raise RegistryError("epoch registry 'epochs' must be a list")
        return validate_epochs(_epoch_from_mapping(item, i) for i, item in enumerate(entries))
    except RegistryError:
        raise
    except _MALFORMED as exc:
        raise RegistryError(f"malformed epoch registry {path}: {type(exc).__name__}") from exc


@dataclass(frozen=True)
class EpochResolution:
    label: str
    epoch: StrategyEpoch | None
    reason: str

    @property
    def registered(self) -> bool:
        return self.epoch is not None


def _unregistered(reason: str) -> EpochResolution:
    return EpochResolution(UNREGISTERED_EPOCH, None, reason)


def resolve_epoch(
    registry: EpochRegistry,
    *,
    strategy: str,
    definition: Mapping[str, Any],
    thresholds: Mapping[str, Any],
    at: datetime,
) -> EpochResolution:
    """Stamp a *new* observation with the epoch whose frozen definition it runs.

    The running definition must consist of exactly the material sections and
    hash-match a FROZEN epoch that covers ``at``. Anything else -- including a
    malformed or non-finite running definition -- is ``UNREGISTERED_EPOCH``:
    the observation is still recorded by observers, but it is excluded from
    every epoch's fitness population. An invalid ``at`` is a caller error and
    raises ``RegistryError``.
    """
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise RegistryError("at must be a timezone-aware datetime")
    try:
        at_utc = at.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise RegistryError("at is not representable in UTC") from exc
    if not isinstance(strategy, str) or not strategy:
        return _unregistered("strategy must be a non-empty string")
    if not isinstance(definition, Mapping) or not isinstance(thresholds, Mapping):
        return _unregistered("running definition/thresholds must be mappings")
    if set(definition) != set(MATERIAL_SECTIONS):
        return _unregistered("running definition must contain exactly the material sections")
    try:
        digest = definition_hash(definition, thresholds)
    except _MALFORMED:
        return _unregistered("running definition is not finite canonical JSON")
    for epoch in registry.for_strategy(strategy):
        if epoch.definition_sha256 != digest:
            continue
        if epoch.status is not EpochStatus.FROZEN:
            return _unregistered(f"epoch {epoch.epoch} is {epoch.status.value}")
        if not epoch.covers(at_utc):
            return _unregistered(f"epoch {epoch.epoch} not effective at {at_utc.isoformat()}")
        return EpochResolution(epoch.epoch, epoch, "definition_hash_match")
    return _unregistered("running definition matches no registered epoch")


def epoch_label_for_record(registry: EpochRegistry, record: Mapping[str, Any]) -> str:
    """Read-time label for a stored record. Never assigns; never relabels.

    * no ``strategy_epoch`` on the record -> ``LEGACY_UNVERSIONED``;
    * stamp/strategy not strings, or the stamp names no FROZEN/RETIRED epoch
      of the record's strategy -> ``UNREGISTERED_EPOCH``;
    * otherwise the record's own stamped epoch.

    Only ``strategy`` + ``strategy_epoch`` are read. Other version fields --
    notably the 122 collector's ``policy_epoch`` -- are never promoted to an
    epoch stamp, so collector rows stay ``LEGACY_UNVERSIONED``.
    """
    if not isinstance(record, Mapping):
        return UNREGISTERED_EPOCH
    stamped = record.get("strategy_epoch")
    if stamped is None or stamped == "":
        return LEGACY_UNVERSIONED
    strategy = record.get("strategy")
    if not isinstance(stamped, str) or not isinstance(strategy, str) or not strategy:
        return UNREGISTERED_EPOCH
    epoch = registry.get(strategy, stamped)
    if epoch is None or epoch.status is EpochStatus.DRAFT:
        return UNREGISTERED_EPOCH
    return stamped
