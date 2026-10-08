"""Read-only canonical fold of NEW dedicated 122-IEX-E1 collector rows.

Does not touch #1145, the collector, the registry or its JSONL journal.
A returned prospective catch remains observation-only, not a fitness P&L
observation, a proof-window start, or execution authority.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, Mapping

from scripts.options_122_prospective_collect import (
    CANONICAL_BINDING_SCHEMA, COLLECTOR_ID, COLLECTOR_VERSION,
    POLICY_EPOCH, _canonical_binding,
)
from scripts.options_122_iex_provisional_audit import Arm, reconcile_pair
from .signal import (
    IntegrityStatus, LifecycleState, Levels, SignalJournal,
    StructureIdentity, default_registry, integrity_event, observation_event,
    open_signal, state_event, to_record, verify_record,
)
from .strategy_epochs import EpochRegistry

DEFINITION_SHA = "2938a9074a7efce850f7ed97e05d255501fc4c3d50fd2647e00d47e5d7a93514"
_ALLOWED = frozenset({"ARMED", "RESOLUTION", "RECONCILIATION", "SOURCE_DRIFT"})
_PRIMARY_20 = frozenset({
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR",
    "INTC", "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
})


class AdapterError(ValueError):
    """A journal lifecycle or provenance claim is unsafe, not repairable."""


@dataclass
class Canonical122Fold:
    journal: SignalJournal
    signal_by_setup: dict[str, str] = field(default_factory=dict)
    excluded: Counter = field(default_factory=Counter)
    verified_catches: tuple[str, ...] = ()

    def signal_for(self, setup_id: str):
        sid = self.signal_by_setup.get(setup_id)
        return self.journal.get(sid) if sid else None


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("duplicate_json_key")
        result[key] = value
    return result


def read_122_journal(path: Path | str) -> Iterator[dict[str, Any]]:
    """Never mutate, truncate, silently skip a torn tail or repair a record."""
    with Path(path).open("rb") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.endswith(b"\n"):
                raise AdapterError(f"torn_journal_tail_{number}")
            try:
                record = json.loads(line, object_pairs_hook=_unique_pairs)
            except (ValueError, UnicodeError) as exc:
                raise AdapterError(f"invalid_json_{number}") from exc
            if not isinstance(record, dict):
                raise AdapterError(f"invalid_record_{number}")
            yield record


def _time(value: Any, key: str) -> datetime:
    if not isinstance(value, str):
        raise AdapterError(f"{key}_missing")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdapterError(f"{key}_invalid") from exc
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise AdapterError(f"{key}_naive")
    return moment.astimezone(timezone.utc)


def _field(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = row.get(key)
    if not isinstance(value, dict):
        raise AdapterError(f"{key}_missing")
    return value


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterError(f"{key}_not_number")
    number = float(value)
    if not math.isfinite(number):
        raise AdapterError(f"{key}_nonfinite")
    return number


def _stamp(row: Mapping[str, Any], epoch: Any) -> Mapping[str, Any]:
    if (row.get("collector_id"), row.get("collector_version"), row.get("policy_epoch")) != (
        COLLECTOR_ID, COLLECTOR_VERSION, POLICY_EPOCH
    ):
        raise AdapterError("wrong_collector_or_version")
    binding = _field(row, "canonical_binding")
    obs = _field(row, "observation")
    try:
        expected = _canonical_binding(SimpleNamespace(**obs))
    except (ValueError, TypeError) as exc:
        raise AdapterError("incomplete_structure_identity") from exc
    if binding != expected or binding.get("schema") != CANONICAL_BINDING_SCHEMA:
        raise AdapterError("binding_mismatch")
    if epoch is None or epoch.definition_sha256 != DEFINITION_SHA:
        raise AdapterError("frozen_registry_definition_mismatch")
    if binding["strategy"] != epoch.strategy or binding["strategy_epoch"] != epoch.epoch:
        raise AdapterError("epoch_identity_mismatch")
    if binding["timeframe"] != epoch.definition["setup"]["timeframe"]:
        raise AdapterError("timeframe_mismatch")
    if binding["universe"] != epoch.definition["setup"]["universe"]:
        raise AdapterError("universe_mismatch")
    if (binding["arm_source"], binding["provisional_trigger_source"],
            binding["authoritative_reconciliation_source"]) != (
        epoch.definition["trigger"]["arm_source"],
        epoch.definition["trigger"]["provisional_source"],
        epoch.definition["trigger"]["authoritative_reconciliation"],
    ):
        raise AdapterError("source_vocabulary_mismatch")
    if obs.get("ticker") not in _PRIMARY_20 or obs.get("source_timeframe") != "30Min":
        raise AdapterError("universe_or_timeframe_mismatch")
    if obs.get("reference_direction") not in {"two_up", "two_down"}:
        raise AdapterError("reference_direction_missing")
    if obs.get("setup_id") != row.get("setup_id"):
        raise AdapterError("setup_id_mismatch")
    _number(obs.get("boundary_high"), "boundary_high")
    _number(obs.get("boundary_low"), "boundary_low")
    if obs["boundary_high"] <= obs["boundary_low"]:
        raise AdapterError("inverted_levels")
    return binding


def _proof(source: Mapping[str, Any], feed: str, *, raw_root: Path | None, setup_id: str) -> bool:
    if source.get("status") != "PROVEN" or source.get("source") != f"alpaca_{feed}":
        raise AdapterError(f"{feed}_not_proven")
    if source.get("break_side") not in {"HIGH", "LOW"} or source.get("direction") not in {"LONG", "SHORT"}:
        raise AdapterError(f"{feed}_invalid_first_break")
    if (source["break_side"] == "HIGH") != (source["direction"] == "LONG"):
        raise AdapterError(f"{feed}_side_direction_mismatch")
    ns = source.get("timestamp_ns")
    if type(ns) is not int or ns <= 0:
        raise AdapterError(f"{feed}_missing_ns")
    crossed = _time(source.get("timestamp"), f"{feed}_timestamp")
    if abs(crossed.timestamp() - ns / 1_000_000_000) > 0.001:
        raise AdapterError(f"{feed}_timestamp_ns_mismatch")
    digest = source.get("raw_trade_sha256")
    filename = source.get("raw_trade_file")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not isinstance(filename, str) or not filename):
        raise AdapterError(f"{feed}_missing_raw_provenance")
    # Source claims alone are NOT trusted evidence. An explicit authorized raw
    # directory is required, without following paths outside that directory.
    if raw_root is None:
        return False
    try:
        root = raw_root.resolve(strict=True)
        literal = Path(filename)
        if literal.is_symlink():
            raise AdapterError(f"{feed}_raw_symlink_forbidden")
        artifact = literal.resolve(strict=True)
        if not artifact.is_relative_to(root) or artifact.name != f"{setup_id}.{feed}.jsonl":
            raise AdapterError(f"{feed}_raw_path_mismatch")
        if not artifact.is_file() or artifact.is_symlink():
            raise AdapterError(f"{feed}_raw_file_invalid")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
            raise AdapterError(f"{feed}_raw_hash_mismatch")
    except (OSError, RuntimeError) as exc:
        raise AdapterError(f"{feed}_raw_read_failed") from exc
    return True


def fold_122_rows(rows: Iterable[Mapping[str, Any]], *, registry: EpochRegistry | None = None,
                  raw_root: Path | None = None,
                  max_quote_age_seconds: float | None = None) -> Canonical122Fold:
    """Strict ordered fold; never upgrades legacy or assigns a provisional win."""
    if max_quote_age_seconds is not None:
        max_quote_age_seconds = _number(max_quote_age_seconds, "max_quote_age_seconds")
        if not 0 < max_quote_age_seconds <= 120:
            raise AdapterError("quote_freshness_limit_untrusted")
    reg = registry if registry is not None else default_registry()
    epoch = reg.get("options_122", POLICY_EPOCH)
    if epoch is None or epoch.definition_sha256 != DEFINITION_SHA:
        raise AdapterError("frozen_registry_definition_mismatch")
    out = Canonical122Fold(SignalJournal(reg))
    seen: set[str] = set()
    groups: dict[str, list[Mapping[str, Any]]] = {}
    legacy: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise AdapterError("invalid_row")
        kind = row.get("record_type")
        if kind not in _ALLOWED:
            if row.get("setup_id"):
                seen.add(str(row["setup_id"]))
            out.excluded["DIAGNOSTIC"] += 1
            continue
        sid = row.get("setup_id")
        if not isinstance(sid, str) or not sid:
            raise AdapterError("setup_id_missing")
        if row.get("canonical_binding") is None:
            if sid in groups:
                raise AdapterError("mixed_bound_unbound_lifecycle")
            legacy.add(sid)
            seen.add(sid)
            out.excluded["LEGACY_UNVERSIONED"] += 1
            continue
        if sid in legacy:
            raise AdapterError("legacy_setup_upgrade")
        if kind == "ARMED":
            if sid in seen:
                raise AdapterError("duplicate_or_late_armed")
            groups[sid] = [row]
            seen.add(sid)
        else:
            if sid not in groups:
                raise AdapterError("bound_row_without_arm")
            group = groups[sid]
            permitted = {"RESOLUTION": ("ARMED",), "RECONCILIATION": ("RESOLUTION",),
                         "SOURCE_DRIFT": ("ARMED", "RESOLUTION")}
            if group[-1]["record_type"] not in permitted[kind]:
                raise AdapterError("illegal_lifecycle_order")
            if group[-1]["record_type"] == "SOURCE_DRIFT":
                raise AdapterError("source_drift_not_admissible")
            group.append(row)

    catches: list[str] = []
    for sid, group in groups.items():
        previous = None
        arm = group[0]
        stamp = _stamp(arm, epoch)
        arm_obs = _field(arm, "observation")
        first = _time(arm.get("observed_at"), "armed_time")
        close = _time(stamp["structure_close_time"], "structure_close")
        watch_start = _time(arm_obs.get("watch_start"), "watch_start")
        watch_end = _time(arm_obs.get("watch_until"), "watch_end")
        if not close <= watch_start <= first < watch_end:
            raise AdapterError("arm_not_contemporaneous")
        identity = StructureIdentity(arm_obs["ticker"], stamp["timeframe"], stamp["pattern"], close)
        opened = open_signal(
            identity, strategy=stamp["strategy"], strategy_epoch=stamp["strategy_epoch"],
            setup_ready_time=close, first_seen_time=first,
            data_source=stamp["data_source"],
            levels=Levels(arm_obs["boundary_high"], arm_obs["boundary_low"]),
            registry=reg,
        )
        out.journal.append(opened)
        out.signal_by_setup[sid] = opened.signal_id
        for row in group:
            bound = _stamp(row, epoch)
            if bound != stamp:
                raise AdapterError("changed_stamp")
            observed = _time(row.get("observed_at"), "observed_at")
            if previous is not None and observed < previous:
                raise AdapterError("nonmonotonic_row_time")
            previous = observed
            obs = _field(row, "observation")
            if obs.get("setup_fingerprint") != arm_obs.get("setup_fingerprint"):
                raise AdapterError("fingerprint_drift")
            if (obs.get("boundary_high"), obs.get("boundary_low"),
                    obs.get("watch_start"), obs.get("watch_until")) != (
                arm_obs.get("boundary_high"), arm_obs.get("boundary_low"),
                arm_obs.get("watch_start"), arm_obs.get("watch_until")
            ):
                raise AdapterError("arm_geometry_drift")
        if group[-1]["record_type"] == "SOURCE_DRIFT":
            raise AdapterError("source_drift_not_admissible")
        if len(group) == 1:
            out.excluded["PENDING_ARM"] += 1
            continue
        resolution = group[1]
        kind = resolution.get("source_outcome")
        at = _time(resolution.get("observed_at"), "resolution_time")
        iex = _field(resolution, "trigger_source")
        if resolution.get("prearmed_at") != arm.get("observed_at"):
            raise AdapterError("prearmed_time_mismatch")
        if kind == "REVERSAL":
            iex_raw_verified = _proof(iex, "iex", raw_root=raw_root, setup_id=sid)
            trade_at = _time(iex["timestamp"], "iex_timestamp")
            expected_side = "LOW" if arm_obs["reference_direction"] == "two_up" else "HIGH"
            if (iex["break_side"] != expected_side or iex.get("family_side") != "REVERSAL"
                    or not first <= trade_at <= at or not watch_start <= trade_at < watch_end
                    or _field(resolution, "observation").get("status") != "TRIGGERED"):
                raise AdapterError("reversal_provenance_invalid")
            out.journal.append(state_event(
                out.journal, opened.signal_id, LifecycleState.TRIGGERED,
                detected_at=at, market_time=trade_at,
                payload={"direction": iex["direction"], "trigger_detected_at": at},
                reason="provisional_iex_reversal_pending_sip",
            ))
        elif kind == "CONTINUATION":
            _proof(iex, "iex", raw_root=raw_root, setup_id=sid)
            if (iex.get("family_side") != "CONTINUATION"
                    or _field(resolution, "observation").get("status") != "CANCELLED"):
                raise AdapterError("continuation_inconsistent")
            out.journal.append(state_event(
                out.journal, opened.signal_id, LifecycleState.INVALIDATED,
                detected_at=at, reason="same_direction_first_break",
            ))
        elif kind == "NO_BREAK":
            if iex.get("status") != "NO_BREAK":
                raise AdapterError("no_break_inconsistent")
            # IEX NO_BREAK cannot be concluded until SIP reconciles; a SIP-only
            # reversal is MISSED_LATE, never a fabricated IEX catch.
        else:
            raise AdapterError("unknown_source_outcome")
        if len(group) == 2:
            out.excluded["PENDING_SIP"] += 1
            continue
        rec = group[2]
        rec_at = _time(rec.get("observed_at"), "reconciliation_time")
        if rec_at < watch_end + timedelta(minutes=16):
            raise AdapterError("premature_sip_reconciliation")
        sip, final_iex = _field(rec, "sip"), _field(rec, "iex")
        policy = _field(rec, "policy")
        if final_iex != ({"status": "NO_BREAK"} if kind == "NO_BREAK" else iex):
            raise AdapterError("reconciliation_iex_changed")
        if sip.get("status") == "PROVEN":
            sip_raw_verified = _proof(sip, "sip", raw_root=raw_root, setup_id=sid)
        if sip.get("status") not in {"PROVEN", "NO_BREAK", "DATA_BLOCKED"}:
            raise AdapterError("unknown_sip_status")
        shape = Arm(
            session_date=str(arm_obs["session_date"]), symbol=arm_obs["ticker"],
            watch_start=watch_start, watch_end=watch_end,
            boundary_high=float(arm_obs["boundary_high"]),
            boundary_low=float(arm_obs["boundary_low"]),
            reference_direction=arm_obs["reference_direction"],
        )
        computed = reconcile_pair(arm=shape, sip=sip, iex=final_iex)
        if policy != computed:
            raise AdapterError("reconciliation_policy_mismatch")
        if kind != "REVERSAL":
            if kind == "NO_BREAK" and sip.get("status") == "PROVEN" and sip.get("family_side") == "REVERSAL":
                out.journal.append(state_event(
                    out.journal, opened.signal_id, LifecycleState.MISSED_LATE,
                    detected_at=rec_at, market_time=_time(sip.get("timestamp"), "sip_timestamp"),
                    payload={"direction": sip["direction"], "trigger_detected_at": rec_at},
                    reason="sip_reversal_missed_by_iex",
                ))
                out.excluded["MISSED_BY_IEX"] += 1
            else:
                if kind == "NO_BREAK":
                    out.journal.append(state_event(
                        out.journal, opened.signal_id, LifecycleState.EXPIRED,
                        detected_at=rec_at, reason="no_iex_reversal",
                    ))
                out.excluded["NOT_REVERSAL"] += 1
            continue
        if policy["reconciliation"] != "CONFIRMED_SAME_REVERSAL":
            out.journal.append(state_event(
                out.journal, opened.signal_id, LifecycleState.DATA_BLOCKED,
                detected_at=rec_at, reason=f"sip_{policy['reconciliation']}",
            ))
            out.excluded["NOT_CONFIRMED"] += 1
            continue
        trade_at = _time(iex["timestamp"], "iex_timestamp")
        sip_at = _time(sip["timestamp"], "sip_timestamp")
        option = resolution.get("option_evidence")
        if not isinstance(option, dict):
            option = {}
        captured = option.get("captured_at")
        evidence = option.get("selector_evidence")
        eligible = (
            resolution.get("capture_gate_eligible") is True
            and resolution.get("option_evidence_usable") is True
            and option.get("status") == "CAPTURED"
            and isinstance(evidence, dict)
            and evidence.get("status") == "CAPTURED"
            and evidence.get("production_replay_parity") is True
            and isinstance(option.get("selected_contract"), str)
            and bool(option.get("selected_contract"))
            and iex_raw_verified and sip_raw_verified
            and max_quote_age_seconds is not None
        )
        if eligible:
            taken = _time(captured, "option_capture_time")
            eligible = (trade_at <= taken <= rec_at and
                        (taken - trade_at).total_seconds() <= 120 and first <= sip_at)
            for age in ("option_quote_age_seconds", "underlying_quote_age_seconds"):
                try:
                    _number(option.get(age), age)
                except AdapterError:
                    eligible = False
                else:
                    if option[age] < 0 or option[age] > max_quote_age_seconds:
                        eligible = False
        if not eligible:
            out.journal.append(state_event(
                out.journal, opened.signal_id, LifecycleState.DATA_BLOCKED,
                detected_at=rec_at, reason="missing_or_late_selector_evidence",
            ))
            out.excluded["CAPTURE_UNUSABLE"] += 1
            continue
        out.journal.append(observation_event(
            out.journal, opened.signal_id, detected_at=rec_at,
            trigger_crossed_at=trade_at.isoformat(),
            sip_crossed_at=sip_at.isoformat(),
            trigger_source=stamp["provisional_trigger_source"],
            trigger_feed="alpaca_iex",
            prospective_catch=True, capture_late=False, gap_through=False,
            capture_version=CANONICAL_BINDING_SCHEMA,
        ))
        out.journal.append(integrity_event(
            out.journal, opened.signal_id, detected_at=rec_at,
            data_integrity=IntegrityStatus.VALID.value,
            signal_integrity=IntegrityStatus.VALID.value,
        ))
        signal = out.journal.get(opened.signal_id)
        assert signal is not None
        snapshot = to_record(signal)
        problems = verify_record(snapshot, registry=reg)
        if problems or not signal.is_prospective_catch or snapshot["execution_authority"] is not False:
            raise AdapterError(f"canonical_verification_failed:{problems}")
        catches.append(signal.signal_id)
    # Every output, including provisional, blocked and missed observations,
    # must pass the hardened #1183 canonical record contract.
    for signal in out.journal.signals():
        errors = verify_record(to_record(signal), registry=reg)
        if errors:
            raise AdapterError(f"canonical_record_invalid:{errors}")
    out.verified_catches = tuple(catches)
    return out
