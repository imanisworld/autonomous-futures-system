"""Prospective path-v0.2 capture integration for the 2-1-2 floor study.

This module is deliberately narrow. It owns only immutable session-file and
manifest mechanics plus the allowed blind stopping readout. It does not fetch
provider data, score outcomes, read ordinary out-v0.1 products, or touch broker
or execution state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from alert_ranker.session_calendar import nyse_session_for
from ops.options_212c_floor_outcome_monitor import (
    ELIGIBLE_START,
    EPISODE_SNAPSHOT_FIELDS,
    FAMILY,
    INELIGIBLE_THROUGH,
    PATH_RECORD_ROOT,
    PATH_RECORD_VERSION,
    REDUCER_VERSION,
    SESSION_SEAL_FIELDS,
    TRIAL_ID,
    V1_UNIVERSE,
    canonical_seal_bytes,
    study_readout,
)
from ops.options_212c_floor_outcome_study import SealedSessionArtifact

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset({"session_date", "byte_length", "sha256"})
_SOURCE_FIELDS = frozenset(
    {
        "provider",
        "request_start",
        "request_end",
        "observer_run_id",
        "observer_ran_at",
        "source_sha",
    }
)
_BAR_FIELDS = frozenset({"start", "open", "high", "low", "close"})


class CaptureIntegrationError(RuntimeError):
    """Fail-closed capture/integrity refusal with a stable reason token."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SealCheck:
    state: str
    session_date: str
    path: str
    byte_length: int | None = None
    sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == "valid"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "session_date": self.session_date,
            "path": self.path,
            "byte_length": self.byte_length,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class CaptureProgress:
    """Only preregistered blind-monitor fields plus the next seal date."""

    enabled: bool
    eligible_start: str | None
    sessions_elapsed: int
    stop_condition_met: bool
    stop_condition: str | None
    advance_refused: bool
    next_session: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "eligible_start": self.eligible_start,
            "sessions_elapsed": self.sessions_elapsed,
            "stop_condition_met": self.stop_condition_met,
            "stop_condition": self.stop_condition,
            "advance_refused": self.advance_refused,
            "next_session": self.next_session,
        }


@dataclass(frozen=True)
class CaptureDecision:
    required: bool
    reason: str
    progress: CaptureProgress
    existing: SealCheck | None = None

    def to_public_dict(self) -> dict[str, Any]:
        out = {
            "required": self.required,
            "reason": self.reason,
            "progress": self.progress.to_public_dict(),
        }
        if self.existing is not None:
            out["existing"] = self.existing.to_public_dict()
        return out


def seal_directory(root: Path) -> Path:
    return Path(root) / PATH_RECORD_ROOT


def seal_path(root: Path, session_date: str) -> Path:
    return seal_directory(root) / f"{session_date}.json"


def manifest_path(root: Path) -> Path:
    return seal_directory(root) / "manifest.jsonl"


def _validate_session_date(value: str, field: str = "session_date") -> str:
    if not isinstance(value, str) or not value:
        raise CaptureIntegrationError("session_date_invalid", field)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise CaptureIntegrationError("session_date_invalid", value) from exc
    if parsed.isoformat() != value or nyse_session_for(parsed) is None:
        raise CaptureIntegrationError("session_date_invalid", value)
    return value


def _validate_eligible_start(value: str | None) -> str | None:
    if value is None:
        return None
    day = _validate_session_date(value, "eligible_start")
    if day <= INELIGIBLE_THROUGH:
        raise CaptureIntegrationError(
            "eligible_start_invalid",
            f"{day} must be strictly after {INELIGIBLE_THROUGH}",
        )
    return day


def _next_session(value: str) -> str:
    day = date.fromisoformat(value)
    for _ in range(370):
        day += timedelta(days=1)
        if nyse_session_for(day) is not None:
            return day.isoformat()
    raise CaptureIntegrationError("next_session_unavailable", value)


def _validate_artifact_schema(record: Mapping[str, Any]) -> None:
    """Refuse any capture body that drifts from the frozen path-v0.2 schema."""

    if set(record) != set(SESSION_SEAL_FIELDS):
        raise CaptureIntegrationError("artifact_session_fields_invalid")
    if (
        record.get("path_record_version") != PATH_RECORD_VERSION
        or record.get("trial_id") != TRIAL_ID
    ):
        raise CaptureIntegrationError("artifact_identity_invalid")
    session_date = _validate_session_date(
        str(record.get("session_date") or "")
    )
    source = record.get("source")
    if not isinstance(source, Mapping) or set(source) != _SOURCE_FIELDS:
        raise CaptureIntegrationError("artifact_source_fields_invalid")
    episodes = record.get("episodes")
    if not isinstance(episodes, list):
        raise CaptureIntegrationError("artifact_episodes_invalid")
    for episode in episodes:
        if (
            not isinstance(episode, Mapping)
            or set(episode) != set(EPISODE_SNAPSHOT_FIELDS)
        ):
            raise CaptureIntegrationError("artifact_episode_fields_invalid")
        if (
            episode.get("session_date") != session_date
            or episode.get("family") != FAMILY
            or episode.get("symbol") not in V1_UNIVERSE
            or episode.get("reducer_version") != REDUCER_VERSION
        ):
            raise CaptureIntegrationError("artifact_episode_identity_invalid")
        bars = episode.get("bars")
        if not isinstance(bars, list):
            raise CaptureIntegrationError("artifact_bars_invalid")
        for bar in bars:
            if not isinstance(bar, Mapping) or set(bar) != _BAR_FIELDS:
                raise CaptureIntegrationError("artifact_bar_fields_invalid")


def _manifest_line(entry: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(entry), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n"


def _parse_manifest_text(text: str, path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    prior: str | None = None
    for index, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            raise CaptureIntegrationError("manifest_blank_line", f"{path}:{index}")
        try:
            item = json.loads(raw)
        except ValueError as exc:
            raise CaptureIntegrationError(
                "manifest_unparseable", f"{path}:{index}"
            ) from exc
        if not isinstance(item, dict) or set(item) != _MANIFEST_FIELDS:
            raise CaptureIntegrationError(
                "manifest_fields_invalid", f"{path}:{index}"
            )
        session_date = _validate_session_date(str(item.get("session_date") or ""))
        length = item.get("byte_length")
        digest = item.get("sha256")
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or length <= 0
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
        ):
            raise CaptureIntegrationError(
                "manifest_entry_invalid", f"{path}:{index}"
            )
        if session_date in seen:
            raise CaptureIntegrationError("manifest_duplicate_session", session_date)
        if prior is not None and session_date <= prior:
            raise CaptureIntegrationError(
                "manifest_order_invalid", f"{prior}->{session_date}"
            )
        seen.add(session_date)
        prior = session_date
        entries.append(
            {
                "session_date": session_date,
                "byte_length": length,
                "sha256": digest,
            }
        )
    return entries


def _manifest_entries(root: Path) -> list[dict[str, Any]]:
    path = manifest_path(root)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CaptureIntegrationError("manifest_unreadable", str(path)) from exc
    return _parse_manifest_text(text, path)


def _entry_map(root: Path) -> dict[str, dict[str, Any]]:
    return {entry["session_date"]: entry for entry in _manifest_entries(root)}


def _verify_with_entry(
    root: Path, session_date: str, entry: Mapping[str, Any] | None
) -> tuple[SealCheck, dict[str, Any] | None]:
    day = _validate_session_date(session_date)
    path = seal_path(root, day)
    exists = path.exists()
    if entry is None and not exists:
        return SealCheck("missing", day, str(path)), None
    if entry is None or not exists:
        raise CaptureIntegrationError(
            "seal_manifest_partial",
            f"{day}: file={exists} manifest={entry is not None}",
        )
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise CaptureIntegrationError("seal_unreadable", str(path)) from exc
    if len(body) != entry["byte_length"]:
        raise CaptureIntegrationError("seal_byte_length_mismatch", day)
    digest = hashlib.sha256(body).hexdigest()
    if digest != entry["sha256"]:
        raise CaptureIntegrationError("seal_sha256_mismatch", day)
    try:
        record = json.loads(body)
    except ValueError as exc:
        raise CaptureIntegrationError("seal_unparseable", day) from exc
    if not isinstance(record, dict):
        raise CaptureIntegrationError("seal_record_invalid", day)
    if record.get("session_date") != day:
        raise CaptureIntegrationError("seal_session_mismatch", day)
    if canonical_seal_bytes(record) != body:
        raise CaptureIntegrationError("seal_not_canonical", day)
    return (
        SealCheck("valid", day, str(path), len(body), digest),
        {"sha256": digest, "record": record},
    )


def inspect_seal(root: Path, session_date: str) -> SealCheck:
    entry = _entry_map(root).get(_validate_session_date(session_date))
    check, _ = _verify_with_entry(root, session_date, entry)
    return check


def load_verified_seals(root: Path) -> list[dict[str, Any]]:
    """Load only hash-bound canonical seals for the internal blind monitor."""

    directory = seal_directory(root)
    entries = _manifest_entries(root)
    by_date = {entry["session_date"]: entry for entry in entries}

    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                day = _validate_session_date(path.stem)
            except CaptureIntegrationError as exc:
                raise CaptureIntegrationError(
                    "unexpected_seal_file", str(path)
                ) from exc
            if day not in by_date:
                raise CaptureIntegrationError(
                    "seal_manifest_partial", f"{day}: file=True manifest=False"
                )

    bound: list[dict[str, Any]] = []
    for entry in entries:
        _check, item = _verify_with_entry(root, entry["session_date"], entry)
        assert item is not None
        bound.append(item)
    return bound


def capture_progress(root: Path) -> CaptureProgress:
    """Return blind progress using only the source-registered eligible start.

    There is intentionally no runtime/CLI start override. Tests may monkeypatch
    this module constant; production changes require a reviewed source amendment.
    """
    start = _validate_eligible_start(ELIGIBLE_START)
    if start is None:
        directory = seal_directory(root)
        if manifest_path(root).exists() or (
            directory.exists() and any(directory.glob("*.json"))
        ):
            raise CaptureIntegrationError("seal_exists_before_start")
        return CaptureProgress(
            enabled=False,
            eligible_start=None,
            sessions_elapsed=0,
            stop_condition_met=False,
            stop_condition=None,
            advance_refused=True,
            next_session=None,
        )

    seals = load_verified_seals(root)
    readout = study_readout(seals, eligible_start=start)
    if readout.get("advance_refused"):
        raise CaptureIntegrationError("blind_monitor_refused")
    elapsed = int(readout["sessions_elapsed"])
    if elapsed != len(seals):
        reason = (
            "seal_after_stop"
            if readout.get("stop_condition_met") and len(seals) > elapsed
            else "blind_monitor_count_invalid"
        )
        raise CaptureIntegrationError(
            reason, f"sealed={len(seals)} elapsed={elapsed}"
        )

    next_session: str | None = None
    if not readout.get("stop_condition_met"):
        next_session = start
        for _ in range(elapsed):
            next_session = _next_session(next_session)

    return CaptureProgress(
        enabled=True,
        eligible_start=start,
        sessions_elapsed=elapsed,
        stop_condition_met=bool(readout["stop_condition_met"]),
        stop_condition=readout.get("stop_condition"),
        advance_refused=False,
        next_session=next_session,
    )


def capture_decision(root: Path, session_date: str) -> CaptureDecision:
    day = _validate_session_date(session_date)
    start = _validate_eligible_start(ELIGIBLE_START)
    if start is None:
        progress = capture_progress(root)
        return CaptureDecision(False, "eligible_start_unset", progress)
    if day < start:
        progress = capture_progress(root)
        return CaptureDecision(False, "before_eligible_start", progress)

    progress = capture_progress(root)
    existing = inspect_seal(root, day)
    if existing.ok:
        return CaptureDecision(False, "already_sealed", progress, existing)
    if progress.stop_condition_met:
        return CaptureDecision(False, "stop_condition_met", progress)
    if day != progress.next_session:
        raise CaptureIntegrationError(
            "capture_out_of_sequence",
            f"requested={day} expected={progress.next_session}",
        )
    return CaptureDecision(True, "next_eligible_session", progress, existing)


def write_artifact_once(
    root: Path,
    artifact: SealedSessionArtifact,
) -> SealCheck:
    """Write one immutable session file and one manifest line, exactly once."""

    _validate_artifact_schema(artifact.record)
    session_date = _validate_session_date(
        str(artifact.record.get("session_date") or "")
    )
    expected_manifest = {
        "session_date": session_date,
        "byte_length": len(artifact.body),
        "sha256": artifact.sha256,
    }
    if artifact.manifest != expected_manifest:
        raise CaptureIntegrationError("artifact_manifest_mismatch", session_date)
    if hashlib.sha256(artifact.body).hexdigest() != artifact.sha256:
        raise CaptureIntegrationError("artifact_sha256_mismatch", session_date)
    if canonical_seal_bytes(artifact.record) != artifact.body:
        raise CaptureIntegrationError("artifact_not_canonical", session_date)

    decision = capture_decision(root, session_date)
    if decision.reason == "already_sealed" and decision.existing is not None:
        if (
            artifact.sha256 != decision.existing.sha256
            or len(artifact.body) != decision.existing.byte_length
        ):
            raise CaptureIntegrationError(
                "seal_already_exists_mismatch", session_date
            )
        return decision.existing
    if not decision.required:
        raise CaptureIntegrationError(
            "capture_not_allowed", f"{session_date}:{decision.reason}"
        )

    directory = seal_directory(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = seal_path(root, session_date)
    manifest = manifest_path(root)

    current = _entry_map(root)
    if path.exists() or session_date in current:
        raise CaptureIntegrationError("seal_race_or_partial", session_date)

    try:
        with path.open("xb") as handle:
            handle.write(artifact.body)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o444)
    except OSError as exc:
        raise CaptureIntegrationError("seal_write_failed", str(path)) from exc

    line = _manifest_line(expected_manifest)
    try:
        with manifest.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            existing_text = handle.read()
            existing_entries = (
                _parse_manifest_text(existing_text, manifest)
                if existing_text
                else []
            )
            if any(e["session_date"] == session_date for e in existing_entries):
                raise CaptureIntegrationError(
                    "manifest_duplicate_session", session_date
                )
            if (
                existing_entries
                and session_date <= existing_entries[-1]["session_date"]
            ):
                raise CaptureIntegrationError(
                    "manifest_order_invalid",
                    f"{existing_entries[-1]['session_date']}->{session_date}",
                )
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except CaptureIntegrationError:
        raise
    except OSError as exc:
        raise CaptureIntegrationError(
            "manifest_write_failed", str(manifest)
        ) from exc

    check = inspect_seal(root, session_date)
    if not check.ok:
        raise CaptureIntegrationError(
            "seal_postwrite_verify_failed", session_date
        )
    return check
