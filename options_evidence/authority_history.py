"""#1184: source-only verification of options authority-history evidence.

IMPORTANT: This module cannot and does not confer trading authority. Its
trust providers are interfaces for tests/offline diagnostics, not proof of an
independently permissioned store. Until separately authenticated, durable head
anchoring and operator allowlists exist, runtime integration MUST NOT use it.
No writes, no fitness/risk/order imports, and no broker-facing APIs.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

EVENT_SCHEMA = "options-authority-event-v1"
HEAD_SCHEMA = "options-authority-head-v1"
EVALUATOR = "fitness_evaluator"
GENESIS_DIGEST = "0" * 64
HUMAN_ID = re.compile(r"operator:[a-z0-9][a-z0-9._-]*\Z", re.ASCII)
SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z", re.ASCII)
APPROVAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*\Z", re.ASCII)
DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00\Z", re.ASCII)
EVENT_FIELDS = frozenset({
    "schema", "strategy", "epoch", "seq", "event_id", "at", "actor",
    "action", "approval_ref", "prev_digest", "digest", "event_proof",
    "approval_proof",
})
HEAD_FIELDS = frozenset({
    "schema", "strategy", "epoch", "last_seq", "last_digest", "head_proof",
})
ACTIONS = frozenset({"GRANT", "RESTORE", "REVOKE", "RETIRE"})


class AuthorityHistoryError(ValueError):
    """No authority-history evidence may be accepted on this error."""


@dataclass(frozen=True)
class ReplayTrust:
    """Unwired external security interface, never a runtime trust source.

    Integrators must prove independent durable write permissions, a latest-head
    anchor immune to evaluator/collector rollback, event signer authentication,
    and approval-reference provenance separately. Caller-supplied callbacks
    are NOT production trust; the result intentionally lacks grant authority.
    """
    load_latest_head: Callable[[str, str], Mapping[str, Any]]
    verify_head: Callable[[Mapping[str, Any]], bool]
    verify_event: Callable[[Mapping[str, Any]], bool]
    verify_approval: Callable[[Mapping[str, Any]], bool]
    approved_humans: frozenset[str]


@dataclass(frozen=True)
class ReplayDiagnostic:
    strategy: str
    epoch: str
    checked_seq: int
    checked_digest: str
    grant_observed: bool
    retired: bool
    observer_enabled: bool = True
    execution_authority: bool = False  # NEVER grant from a source-only replay

    def __post_init__(self) -> None:
        if self.execution_authority is not False or self.observer_enabled is not True:
            raise AuthorityHistoryError("diagnostic cannot carry execution authority")


def _scope(value: Any, label: str) -> str:
    if not isinstance(value, str) or SCOPE.fullmatch(value) is None:
        raise AuthorityHistoryError(f"{label}_invalid")
    return value


def _human(value: Any) -> str:
    if not isinstance(value, str) or HUMAN_ID.fullmatch(value) is None:
        raise AuthorityHistoryError("actor_not_canonical_human")
    return value


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise AuthorityHistoryError(f"{label}_invalid")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise AuthorityHistoryError(f"{label}_invalid")
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or STAMP.fullmatch(value) is None:
        raise AuthorityHistoryError("event_timestamp_not_canonical_utc")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AuthorityHistoryError("event_timestamp_invalid") from exc
    if moment.utcoffset() is None or moment.utcoffset().total_seconds() != 0 or moment.isoformat() != value:
        raise AuthorityHistoryError("event_timestamp_not_canonical_utc")
    return moment.astimezone(timezone.utc)


def _event_digest(event: Mapping[str, Any]) -> str:
    signed = {k: v for k, v in event.items() if k not in {"digest", "event_proof", "approval_proof"}}
    canonical = json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in items:
        if k in out:
            raise AuthorityHistoryError("duplicate_json_key")
        out[k] = v
    return out


def read_history_jsonl(path: Path | str) -> list[Mapping[str, Any]]:
    """Read-only offline candidate loader, NOT a trusted durable event store."""
    records: list[Mapping[str, Any]] = []
    with Path(path).open("rb") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.endswith(b"\n"):
                raise AuthorityHistoryError(f"truncated_line_{number}")
            try:
                row = json.loads(line, object_pairs_hook=_unique_pairs,
                                 parse_constant=lambda _: (_ for _ in ()).throw(
                                     AuthorityHistoryError("nonfinite_json")))
            except (ValueError, UnicodeError) as exc:
                raise AuthorityHistoryError(f"invalid_json_{number}") from exc
            if not isinstance(row, dict):
                raise AuthorityHistoryError(f"event_not_object_{number}")
            records.append(row)
    return records


def replay_history(
    events: Iterable[Mapping[str, Any]], *,
    strategy: str, epoch: str, trust: ReplayTrust | None = None,
) -> ReplayDiagnostic:
    """Pure offline replay; ALL malformed/truncated/unauthenticated rows fail.

    Full sequence must exactly match a fresh externally anchored latest head.
    Even a valid diagnostic GRANT never yields execution_authority=True.
    """
    strategy = _scope(strategy, "strategy")
    epoch = _scope(epoch, "epoch")
    if trust is None or not isinstance(trust, ReplayTrust):
        raise AuthorityHistoryError("independent_trust_not_configured")
    if not isinstance(trust.approved_humans, frozenset):
        raise AuthorityHistoryError("approver_allowlist_not_frozen")
    for person in trust.approved_humans:
        _human(person)
    for name in ("load_latest_head", "verify_head", "verify_event", "verify_approval"):
        if not callable(getattr(trust, name)):
            raise AuthorityHistoryError(f"{name}_not_configured")
    try:
        head = trust.load_latest_head(strategy, epoch)
    except Exception as exc:
        raise AuthorityHistoryError("trusted_head_unavailable") from exc
    if not isinstance(head, Mapping) or set(head) != HEAD_FIELDS:
        raise AuthorityHistoryError("trusted_head_invalid")
    if (head["schema"], head["strategy"], head["epoch"]) != (HEAD_SCHEMA, strategy, epoch):
        raise AuthorityHistoryError("trusted_head_wrong_scope")
    last_seq = head["last_seq"]
    if type(last_seq) is not int or last_seq < 0:
        raise AuthorityHistoryError("trusted_head_seq_invalid")
    final_digest = _digest(head["last_digest"], "trusted_head_digest")
    if last_seq == 0 and final_digest != GENESIS_DIGEST:
        raise AuthorityHistoryError("trusted_genesis_invalid")
    _text(head["head_proof"], "head_proof")
    try:
        head_authentic = trust.verify_head(head)
    except Exception as exc:
        raise AuthorityHistoryError("trusted_head_proof_error") from exc
    if head_authentic is not True:
        raise AuthorityHistoryError("trusted_head_unverified")

    seq = 0
    previous_digest = GENESIS_DIGEST
    previous_time: datetime | None = None
    granted = False
    retired = False
    for event in events:
        if not isinstance(event, Mapping) or set(event) != EVENT_FIELDS:
            raise AuthorityHistoryError("event_fields_invalid")
        seq += 1
        if seq > last_seq:
            raise AuthorityHistoryError("unanchored_extra_event")
        if (event["schema"], event["strategy"], event["epoch"]) != (
            EVENT_SCHEMA, strategy, epoch
        ):
            raise AuthorityHistoryError("event_scope_invalid")
        if type(event["seq"]) is not int or event["seq"] != seq:
            raise AuthorityHistoryError("event_seq_gap_or_reorder")
        if event["event_id"] != f"{strategy}/{epoch}/{seq}":
            raise AuthorityHistoryError("event_id_invalid")
        at = _timestamp(event["at"])
        if previous_time is not None and at <= previous_time:
            raise AuthorityHistoryError("nonmonotonic_event_time")
        previous_time = at
        if event["prev_digest"] != previous_digest:
            raise AuthorityHistoryError("event_chain_broken")
        current_digest = _digest(event["digest"], "event_digest")
        if current_digest != _event_digest(event):
            raise AuthorityHistoryError("event_payload_modified")
        _text(event["event_proof"], "event_proof")
        try:
            authentic = trust.verify_event(event)
        except Exception as exc:
            raise AuthorityHistoryError("event_authentication_error") from exc
        if authentic is not True:
            raise AuthorityHistoryError("event_not_authenticated")

        action = event["action"]
        if not isinstance(action, str) or action not in ACTIONS:
            raise AuthorityHistoryError("event_action_invalid")
        actor = event["actor"]
        if actor != EVALUATOR:
            _human(actor)
        if actor == EVALUATOR and action != "REVOKE":
            raise AuthorityHistoryError("evaluator_may_only_revoke")
        if retired:
            raise AuthorityHistoryError("retired_epoch_is_final")
        if action in ("GRANT", "RESTORE", "RETIRE"):
            if actor not in trust.approved_humans:
                raise AuthorityHistoryError("human_principal_not_authorized")
            if not isinstance(event["approval_ref"], str) or APPROVAL_ID.fullmatch(event["approval_ref"]) is None:
                raise AuthorityHistoryError("approval_ref_invalid")
            _text(event["approval_proof"], "approval_proof")
            try:
                approval_ok = trust.verify_approval(event)
            except Exception as exc:
                raise AuthorityHistoryError("approval_verification_error") from exc
            if approval_ok is not True:
                raise AuthorityHistoryError("approval_not_authenticated")
        else:
            if event["approval_ref"] is not None or event["approval_proof"] is not None:
                raise AuthorityHistoryError("revoke_must_not_contain_approval")
        if action == "GRANT":
            if granted:
                raise AuthorityHistoryError("duplicate_active_grant")
            if seq > 1 and was_revoked:
                raise AuthorityHistoryError("revoked_requires_explicit_restore")
            granted = True
        elif action == "RESTORE":
            if granted or seq == 1 or not was_revoked:
                raise AuthorityHistoryError("restore_without_revocation")
            granted = True
        elif action == "REVOKE":
            granted = False
        elif action == "RETIRE":
            granted = False
            retired = True
        was_revoked = action == "REVOKE" or (
            action not in ("GRANT", "RESTORE") and locals().get("was_revoked", False)
        )
        previous_digest = current_digest
    if seq != last_seq or previous_digest != final_digest:
        raise AuthorityHistoryError("trusted_head_replay_or_truncation")
    return ReplayDiagnostic(
        strategy=strategy, epoch=epoch, checked_seq=seq,
        checked_digest=previous_digest, grant_observed=granted, retired=retired,
    )
