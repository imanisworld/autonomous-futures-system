"""Adapter: #1145 setup-capture journal rows → canonical prospective signals.

#1145 (``alert_ranker/setup_capture*.py``) is the source of truth for
detection, WATCHING persistence, dedupe, lateness and gap classification. This
adapter only *reads* its append-only JSONL rows (or dicts with the same shape)
and folds them into ``SignalJournal`` events. It never re-classifies, never
writes the #1145 journal, and never duplicates its storage.

Mapping (one #1145 ``structure_key`` → exactly one canonical signal per
strategy/epoch; ``structure_id == structure_key``):

=====================  ==========================================  =========================
#1145 row              canonical event                             notes
=====================  ==========================================  =========================
WATCHING (first)       OPENED (two-sided ``Levels``)                first_seen_at, knowable_at
WATCHING (re-persist)  none, or LEVELS if revision increased        dedupe
SOURCE_DRIFT           LEVELS (revision + boundaries)              identity unchanged
RESOLUTION TRIGGERED   STATE TRIGGERED + OBSERVATION + INTEGRITY   market time = SIP cross, else first cross
RESOLUTION MISSED_LATE STATE MISSED_LATE (+ obs/integrity)         unchanged classification
GAP_THROUGH_OPEN       STATE MISSED_GAP (gap_through)              unchanged classification
EXPIRED / NO_TRIGGER   STATE EXPIRED                               NO_TRIGGER kept in reason
INVALIDATED            STATE INVALIDATED
DATA_BLOCKED           STATE DATA_BLOCKED                          data integrity INVALID
AMBIGUOUS              STATE AMBIGUOUS                             data integrity INVALID
RECONCILIATION         OBSERVATION (+ INTEGRITY), or STATE
                       DATA_BLOCKED when #1145 demoted it
COLLECTOR_* / SOURCE_BLOCKED / JOURNAL_REPAIR  ignored (diagnostic in #1145 too)
=====================  ==========================================  =========================

Integrity derivation:

* ``data_integrity``: INVALID for DATA_BLOCKED/AMBIGUOUS, DEGRADED when
  ``data_delayed``, VALID once resolved otherwise, UNKNOWN while WATCHING.
* ``signal_integrity``: VALID only for a #1145 prospective catch
  (``is_prospective_catch``); UNKNOWN for a TRIGGERED row still pending SIP;
  DEGRADED for late/gap/missed captures; VALID for a clean EXPIRED/INVALIDATED;
  INVALID for DATA_BLOCKED/AMBIGUOUS.

Epochs: ``strategy_epoch`` is validated against the #1150 registry passed as
``registry`` (default: the committed registry). The default
``DEFAULT_EPOCH`` (the capture version) is not a registered epoch, so default
folds open every signal under ``UNREGISTERED_EPOCH`` and never emit VALID
signal integrity (capped to DEGRADED). VALID is emitted only for a registered,
context-matching epoch and an #1145 prospective catch; the canonical signal
re-checks that itself.

Integrity (signal and data) is only ever demoted by later rows, never
promoted or reset. #1145 reconciliation does not re-check pre-arming, so a
``prospective_catch`` whose SIP cross precedes first sight or setup-ready is
passed on as ``False`` (the canonical journal would refuse it).

A row claiming execution or trade authority is refused: observation data can
never gain authority through adaptation. Authority fields must be exact
booleans (``"false"`` is a claim, not a denial).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from alert_ranker.setup_capture import CAPTURE_VERSION, is_prospective_catch

from .strategy_epochs import EpochRegistry
from .signal import (
    IntegrityStatus,
    LifecycleError,
    LifecycleState,
    Levels,
    OBSERVATION_TIME_KEYS,
    ProspectiveSignal,
    SignalJournal,
    SignalLinks,
    StructureIdentity,
    capture_structure_key,
    integrity_event,
    levels_event,
    link_event,
    observation_event,
    open_signal,
    prearmed_at,
    state_event,
)

DEFAULT_STRATEGY = "options_setup_capture"
DEFAULT_EPOCH = CAPTURE_VERSION  # not a registered strategy epoch → never tradable

STATE_ROW_TYPES = frozenset({"WATCHING", "RESOLUTION", "RECONCILIATION", "SOURCE_DRIFT"})

_STATUS_MAP = {
    "TRIGGERED": LifecycleState.TRIGGERED,
    "MISSED_LATE": LifecycleState.MISSED_LATE,
    "GAP_THROUGH_OPEN": LifecycleState.MISSED_GAP,
    "EXPIRED": LifecycleState.EXPIRED,
    "NO_TRIGGER": LifecycleState.EXPIRED,
    "INVALIDATED": LifecycleState.INVALIDATED,
    "DATA_BLOCKED": LifecycleState.DATA_BLOCKED,
    "AMBIGUOUS": LifecycleState.AMBIGUOUS,
}

_EVIDENCE_KEYS = (
    "capture_late",
    "prospective_catch",
    "gap_through",
    "data_delayed",
    "sip_crossed_at",
    "true_lag_seconds",
    "iex_lag_seconds",
    "trigger_crossed_at",
    "trigger_trade_price",
    "trigger_feed",
    "trigger_source",
    "trigger_resolution",
    "first_print_price",
    "status_reason",
    "setup_type",
    "capture_version",
)


class AdapterError(ValueError):
    pass


@dataclass
class CaptureFold:
    journal: SignalJournal
    strategy: str
    strategy_epoch: str
    signal_by_key: dict[str, str] = field(default_factory=dict)
    ignored: Counter = field(default_factory=Counter)

    def signal_for(self, structure_key: str) -> ProspectiveSignal | None:
        sid = self.signal_by_key.get(structure_key)
        return self.journal.get(sid) if sid else None


def read_capture_journal(path: Path | str) -> Iterator[dict[str, Any]]:
    """Read-only iteration over complete #1145 journal lines (torn tail skipped)."""
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.endswith("\n"):
                break  # torn trailing line: #1145 owns repair; never touch it here
            if line.strip():
                yield json.loads(line)


def _ts(row: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value:
            return str(value)
    return None


def _evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    # Absent / None evidence stays absent: nothing is synthesized.
    return {k: row[k] for k in _EVIDENCE_KEYS if k in row and row[k] is not None}


_RANK = {"VALID": 0, "DEGRADED": 1, "INVALID": 2}


def _demotion(now: str, wanted: str) -> str | None:
    """``wanted`` if it is a first value or a demotion of ``now``; never a promotion or reset."""
    if wanted == now:
        return None
    if now == "UNKNOWN" or (wanted in _RANK and _RANK[wanted] > _RANK.get(now, -1)):
        return wanted
    return None


def _integrity_update(current: ProspectiveSignal, row: Mapping[str, Any]) -> dict[str, str]:
    """Integrity statuses to append: never a promotion, reset or unearned VALID."""
    wanted = _integrity(row, current.state)
    if wanted["signal_integrity"] == "VALID" and current.valid_integrity_problem() is not None:
        # Unregistered epoch, not pre-armed against the true cross, revoked, late...: never VALID.
        wanted["signal_integrity"] = "DEGRADED"
    out: dict[str, str] = {}
    for name in ("signal_integrity", "data_integrity"):
        status = _demotion(getattr(current, name).value, wanted[name])
        if status is not None:
            out[name] = status
    return out


def _levels(key: str, row: Mapping[str, Any]) -> Levels:
    revision = row.get("revision")
    try:
        # Exact types: no int() coercion of "3", 2.9 or True.
        return Levels(row["boundary_high"], row["boundary_low"], 0 if revision is None else revision)
    except LifecycleError as exc:
        raise AdapterError(f"{key}: {exc}") from exc


def _catch_evidence(current: ProspectiveSignal, evidence: dict[str, Any]) -> dict[str, Any]:
    """#1145 does not re-check pre-arming on reconciliation; a cross before first sight is no catch."""
    if evidence.get("prospective_catch") is True:
        merged = {**current.capture, **{k: v for k, v in evidence.items() if k in OBSERVATION_TIME_KEYS}}
        if prearmed_at(current.first_seen_time, current.setup_ready_time, current.trigger_market_time, merged) is False:
            evidence = {**evidence, "prospective_catch": False}
    return evidence


def _integrity(row: Mapping[str, Any], state: LifecycleState) -> dict[str, str]:
    if state in (LifecycleState.DATA_BLOCKED, LifecycleState.AMBIGUOUS):
        return {"data_integrity": "INVALID", "signal_integrity": "INVALID"}
    data = "DEGRADED" if row.get("data_delayed") else "VALID"
    if state is LifecycleState.TRIGGERED:
        if is_prospective_catch(row):
            sig = "VALID"
        elif row.get("capture_late") or row.get("gap_through"):
            sig = "DEGRADED"
        else:
            sig = "UNKNOWN"  # provisional, pending SIP reconciliation
    elif state in (LifecycleState.MISSED_LATE, LifecycleState.MISSED_GAP):
        sig = "DEGRADED"
    else:
        sig = "VALID"  # clean EXPIRED / INVALIDATED
    return {"data_integrity": data, "signal_integrity": sig}


def _refuse_authority(row: Mapping[str, Any]) -> None:
    for name in ("execution_authority", "trade_authority", "risk_reservation"):
        value = row.get(name)
        if value is not None and value is not False:
            raise AdapterError(
                f"capture row {row.get('structure_key')!r} claims authority; observation data cannot"
            )
    value = row.get("observation_only")
    if value is not None and value is not True:
        raise AdapterError(f"capture row {row.get('structure_key')!r} is not observation-only")


def fold_capture_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    strategy: str = DEFAULT_STRATEGY,
    strategy_epoch: str = DEFAULT_EPOCH,
    registry: EpochRegistry | None = None,
) -> CaptureFold:
    fold = CaptureFold(SignalJournal(registry), strategy, strategy_epoch)
    j = fold.journal
    for row in rows:
        record_type = str(row.get("record_type") or "")
        key = str(row.get("structure_key") or "")
        if record_type not in STATE_ROW_TYPES or not key or key.startswith("_"):
            fold.ignored[record_type or "untyped"] += 1
            continue
        _refuse_authority(row)
        observed = _ts(row, "observed_at", "persisted_at", "detected_at")
        if observed is None:
            raise AdapterError(f"{key}: row has no observed/persisted time")
        sid = fold.signal_by_key.get(key)

        if record_type == "WATCHING":
            levels = _levels(key, row)
            if sid is None:
                identity = StructureIdentity(
                    ticker=row["ticker"],
                    timeframe=row["timeframe"],
                    pattern=row["pattern"],
                    structure_close_time=row["structure_close"],
                )
                if identity.structure_id != key:
                    raise AdapterError(f"{key}: structure_key does not match its own fields")
                event = open_signal(
                    identity,
                    strategy=strategy,
                    strategy_epoch=strategy_epoch,
                    setup_ready_time=_ts(row, "knowable_at", "structure_close"),  # type: ignore[arg-type]
                    first_seen_time=_ts(row, "first_seen_at", "persisted_at"),  # type: ignore[arg-type]
                    data_source=f"{row.get('capture_id', 'OPTIONS_SETUP_CAPTURE')}:{row.get('level_source', 'unknown')}",
                    levels=levels,
                    observation_only=True,
                    registry=j.registry,
                )
                j.append(event)
                fold.signal_by_key[key] = event.signal_id
            else:
                current = j.get(sid)
                assert current is not None
                if current.state is LifecycleState.WATCHING and levels.revision > current.levels.revision:
                    j.append(levels_event(j, sid, levels, detected_at=observed))  # type: ignore[arg-type]
            continue

        if sid is None:
            raise AdapterError(f"{key}: {record_type} before any WATCHING row")
        current = j.get(sid)
        assert current is not None

        if record_type == "SOURCE_DRIFT":
            levels = _levels(key, row)
            if current.state is LifecycleState.WATCHING and levels.revision > current.levels.revision:
                j.append(levels_event(j, sid, levels, detected_at=observed))  # type: ignore[arg-type]
            continue

        status = str(row.get("status") or "")
        target = _STATUS_MAP.get(status)
        evidence = _evidence(row)

        if record_type == "RECONCILIATION" and target is not current.state:
            if target is LifecycleState.DATA_BLOCKED and not current.terminal:
                j.append(state_event(j, sid, target, detected_at=observed,  # type: ignore[arg-type]
                                     reason=str(row.get("status_reason") or "reconciliation_blocked")))
                continue  # DATA_BLOCKED itself sets INVALID integrity
            raise AdapterError(f"{key}: unsupported reconciliation {current.state.value} -> {status}")

        if record_type == "RESOLUTION":
            if target is None:
                raise AdapterError(f"{key}: unknown #1145 status {status!r}")
            if status == "WATCHING":
                continue
            payload: dict[str, Any] = {}
            market_time = None
            if target in (LifecycleState.TRIGGERED, LifecycleState.MISSED_LATE, LifecycleState.MISSED_GAP):
                market_time = _ts(row, "sip_crossed_at", "trigger_crossed_at")
                if market_time is None or row.get("direction") not in ("LONG", "SHORT"):
                    # Missing evidence stays missing: record the gap, do not invent a time.
                    target = LifecycleState.DATA_BLOCKED
                    payload = {}
                    row = {**row, "status_reason": "capture_record_missing_trigger_evidence"}
                else:
                    payload = {
                        "direction": row["direction"],
                        "trigger_detected_at": _ts(row, "detected_at") or observed,
                    }
                    if target is LifecycleState.MISSED_GAP:
                        payload["gap_through"] = True
            reason = str(row.get("status_reason") or status.lower())
            if status == "NO_TRIGGER":
                reason = f"no_trigger:{reason}"
            try:
                j.append(state_event(j, sid, target, detected_at=observed, reason=reason,  # type: ignore[arg-type]
                                     market_time=market_time, payload=payload))  # type: ignore[arg-type]
            except LifecycleError as exc:
                raise AdapterError(f"{key}: {exc}") from exc
            current = j.get(sid)
            assert current is not None

        try:
            if evidence and not current.terminal:
                evidence = _catch_evidence(current, evidence)
                current = j.append(observation_event(j, sid, detected_at=observed, **evidence))  # type: ignore[arg-type]
            if not current.terminal or record_type == "RESOLUTION":
                update = _integrity_update(current, row)
                if update:
                    j.append(integrity_event(j, sid, detected_at=observed, **update))  # type: ignore[arg-type]
        except LifecycleError as exc:
            raise AdapterError(f"{key}: {exc}") from exc
    return fold


def link_scanner_sightings(
    fold: CaptureFold, sightings: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Attach scanner sightings to existing signals; never create a signal.

    A sighting needs ``id``, ``ticker``, ``timeframe``, ``structure_close``,
    ``pattern`` (the #1145 pattern token) and ``seen_at``. It maps through the
    same structure key, so repeated sightings of one structure only extend
    ``scanner_sighting_ids``. Unmatched sightings are returned unchanged.
    """
    unmatched: list[Mapping[str, Any]] = []
    for sighting in sightings:
        key = capture_structure_key(
            sighting["ticker"], sighting["timeframe"], sighting["structure_close"], sighting["pattern"]
        )
        sid = fold.signal_by_key.get(key)
        if sid is None:
            unmatched.append(sighting)
            continue
        fold.journal.append(
            link_event(
                fold.journal,
                sid,
                SignalLinks(scanner_sighting_ids=(str(sighting["id"]),)),
                detected_at=sighting["seen_at"],
            )
        )
    return unmatched
