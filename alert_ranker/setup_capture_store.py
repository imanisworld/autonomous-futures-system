"""Append-only fsynced JSONL journal for observation-only setup capture.

Mirrors the 122 prospective collector: replay via ``_load_state``, flock against
concurrent runs, tolerate a torn last line, survive collector version bumps,
and live at an absolute path outside the release tree.
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from .setup_capture import (
    CAPTURE_ID,
    CAPTURE_VERSION,
    CaptureRecord,
    STATUS_LOCKED,
    STATUS_WATCHING,
    TERMINAL_STATUSES,
)

_VERSION_PREFIX = "capture-v"
# Diagnostic-only record types must never mutate watching/terminal state.
_DIAGNOSTIC_RECORD_TYPES = frozenset(
    {"JOURNAL_REPAIR", "SOURCE_BLOCKED", "COLLECTOR_ERROR", "COLLECTOR_STATUS"}
)
_STATE_RECORD_TYPES = frozenset(
    {"WATCHING", "RESOLUTION", "RECONCILIATION", "SOURCE_DRIFT"}
)


class JournalLocked(RuntimeError):
    def __init__(self) -> None:
        super().__init__(STATUS_LOCKED)


class JournalFormatError(RuntimeError):
    """Typed journal parse failure; ``code`` is the only safe HTTP reason."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _record_from_row(row: Mapping[str, Any]) -> CaptureRecord:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return CaptureRecord(
        structure_key=str(row["structure_key"]),
        capture_id=str(row.get("capture_id") or CAPTURE_ID),
        capture_version=str(row.get("capture_version") or CAPTURE_VERSION),
        ticker=str(row.get("ticker") or ""),
        timeframe=str(row.get("timeframe") or ""),
        pattern=str(row.get("pattern") or ""),
        status=str(row.get("status") or STATUS_WATCHING),
        boundary_high=float(row.get("boundary_high") or 0.0),
        boundary_low=float(row.get("boundary_low") or 0.0),
        level_source=str(row.get("level_source") or "public_regular_30m"),
        structure_close=str(row.get("structure_close") or ""),
        knowable_at=str(row.get("knowable_at") or ""),
        persisted_at=str(row.get("persisted_at") or ""),
        first_seen_at=str(row.get("first_seen_at") or ""),
        watch_start=str(row.get("watch_start") or ""),
        watch_until=str(row.get("watch_until") or ""),
        direction=row.get("direction"),
        setup_type=row.get("setup_type"),
        observation_only=bool(row.get("observation_only", True)),
        execution_authority=False,
        trade_authority=False,
        prospective_catch=bool(row.get("prospective_catch")),
        capture_late=bool(row.get("capture_late")),
        status_reason=str(row.get("status_reason") or ""),
        geometry=row.get("geometry"),
        trigger_crossed_at=row.get("trigger_crossed_at"),
        trigger_crossed_at_ns=row.get("trigger_crossed_at_ns"),
        trigger_trade_price=row.get("trigger_trade_price"),
        trigger_trade_id=row.get("trigger_trade_id"),
        trigger_feed=row.get("trigger_feed"),
        trigger_source=row.get("trigger_source"),
        trigger_resolution=row.get("trigger_resolution"),
        crossed_window_start=row.get("crossed_window_start"),
        crossed_window_end=row.get("crossed_window_end"),
        detected_at=row.get("detected_at"),
        sip_crossed_at=row.get("sip_crossed_at"),
        true_lag_seconds=row.get("true_lag_seconds"),
        iex_lag_seconds=row.get("iex_lag_seconds"),
        gap_through=bool(row.get("gap_through")),
        first_print_price=row.get("first_print_price"),
        clock_offset_s=row.get("clock_offset_s"),
        data_delayed=bool(row.get("data_delayed")),
        revision=int(row.get("revision") or 0),
        high_water_ts=row.get("high_water_ts"),
        sibling_invalidated=bool(row.get("sibling_invalidated")),
        observation_lane=str(row.get("observation_lane") or "SETUP_CAPTURE_V0"),
        extra=dict(extra),
    )


class SetupCaptureJournal:
    def __init__(self, path: Path | str, *, create: bool = True):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_handle = None
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def acquire(self, *, blocking: bool = False) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.lock_path, "a+", encoding="utf-8")
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            handle.close()
            raise JournalLocked()
        self._lock_handle = handle

    def release(self) -> None:
        handle = self._lock_handle
        self._lock_handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "SetupCaptureJournal":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def append(self, row: Mapping[str, Any]) -> None:
        payload = dict(row)
        payload.setdefault("capture_id", CAPTURE_ID)
        payload.setdefault("capture_version", CAPTURE_VERSION)
        line = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def append_record(self, record_type: str, record: CaptureRecord, **extra: Any) -> None:
        row = record.to_dict()
        row["record_type"] = record_type
        row.update(extra)
        self.append(row)

    def _read_lines(self) -> tuple[list[str], str | None, bool]:
        if not self.path.exists():
            return [], None, False
        text = self.path.read_text(encoding="utf-8")
        if not text:
            return [], None, False
        ends_with_nl = text.endswith("\n")
        raw_lines = text.splitlines()
        if not ends_with_nl and raw_lines:
            torn = raw_lines[-1]
            return raw_lines[:-1], torn, True
        return raw_lines, None, False

    def _rewrite_complete_lines(self, lines: list[str]) -> None:
        """Atomically replace the journal with complete lines (torn tail dropped)."""
        tmp_path = self.path.with_name(self.path.name + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for raw in lines:
                handle.write(raw)
                if not raw.endswith("\n"):
                    handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self.path)
        dir_fd = os.open(str(self.path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _build_state(
        self, lines: list[str], *, repaired_torn_line: bool
    ) -> dict[str, Any]:
        watching: dict[str, CaptureRecord] = {}
        terminal: dict[str, CaptureRecord] = {}
        fingerprints: dict[str, str] = {}
        high_water: dict[str, str] = {}
        errors: list[dict[str, Any]] = []
        clock_records: list[dict[str, Any]] = []
        source_blocked_reasons: dict[str, str] = {}
        for number, raw in enumerate(lines, start=1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise JournalFormatError(f"journal_corrupt_line_{number}") from exc
            if not isinstance(row, dict) or not row.get("structure_key"):
                raise JournalFormatError(f"journal_invalid_row_{number}")
            version = str(row.get("capture_version") or "")
            if version and not version.startswith(_VERSION_PREFIX):
                raise JournalFormatError(f"journal_collector_version_mismatch_{number}")
            key = str(row["structure_key"])
            record_type = str(row.get("record_type") or "")
            if record_type in _DIAGNOSTIC_RECORD_TYPES:
                if record_type == "COLLECTOR_ERROR":
                    errors.append(row)
                if key == "_clock" and record_type in {"COLLECTOR_ERROR", "COLLECTOR_STATUS"}:
                    clock_records.append(row)
                if record_type == "SOURCE_BLOCKED":
                    source_blocked_reasons[key] = str(row.get("status_reason") or "")
                continue
            if record_type in _STATE_RECORD_TYPES:
                rec = _record_from_row(row)
                if row.get("fingerprint"):
                    fingerprints[key] = str(row["fingerprint"])
                if rec.high_water_ts:
                    high_water[key] = rec.high_water_ts
                if rec.status == STATUS_WATCHING and rec.structure_key not in terminal:
                    watching[key] = rec
                if rec.status in TERMINAL_STATUSES:
                    terminal[key] = rec
                    watching.pop(key, None)
        current = dict(terminal)
        current.update(watching)
        return {
            "watching": watching,
            "terminal": terminal,
            "current": current,
            "fingerprints": fingerprints,
            "high_water": high_water,
            "errors": errors,
            "clock_records": clock_records,
            "source_blocked_reasons": source_blocked_reasons,
            "repaired_torn_line": repaired_torn_line,
        }

    def load_state(self, *, repair: bool = True) -> dict[str, Any]:
        lines, torn, had_torn = self._read_lines()
        if had_torn and repair:
            self._rewrite_complete_lines(lines)
            self.append(
                {
                    "record_type": "JOURNAL_REPAIR",
                    "structure_key": "_journal",
                    "status_reason": "torn_trailing_line_quarantined",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "torn_preview": (torn or "")[:80],
                }
            )
            lines, _, _ = self._read_lines()
            return self._build_state(lines, repaired_torn_line=True)
        return self._build_state(lines, repaired_torn_line=False)

    def peek_state(self) -> dict[str, Any]:
        """Pure read for status surfaces: never appends, never truncates."""
        return self.load_state(repair=False)

    def get(self, key: str) -> CaptureRecord | None:
        return self.load_state()["current"].get(key)

    def watching(self) -> list[CaptureRecord]:
        return list(self.load_state()["watching"].values())

    def list_all(self, *, limit: int = 200) -> list[CaptureRecord]:
        current = list(self.load_state()["current"].values())
        current.sort(key=lambda row: row.persisted_at, reverse=True)
        return current[:limit]

    def counts(self, *, repair: bool = True) -> dict[str, Any]:
        state = self.load_state(repair=repair)
        return self._counts_from_state(state)

    def read_counts(self) -> dict[str, Any]:
        """Status-only counts: byte-identical journal, no mkdir side effects."""
        return self.counts(repair=False)

    @staticmethod
    def _counts_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for rec in state["current"].values():
            by_status[rec.status] = by_status.get(rec.status, 0) + 1
        return {
            "total": sum(by_status.values()),
            "watching": by_status.get(STATUS_WATCHING, 0),
            "triggered": by_status.get("TRIGGERED", 0),
            "invalidated": by_status.get("INVALIDATED", 0),
            "expired": by_status.get("EXPIRED", 0),
            "missed_late": by_status.get("MISSED_LATE", 0),
            "gap_through_open": by_status.get("GAP_THROUGH_OPEN", 0),
            "data_blocked": by_status.get("DATA_BLOCKED", 0),
            "by_status": by_status,
            "structure_count": len(state["current"]),
            "repaired_torn_line": state["repaired_torn_line"],
        }

    def unique_structure_count(self, keys: Iterable[str] | None = None) -> int:
        current = self.load_state()["current"]
        if keys is None:
            return len(current)
        wanted = set(keys)
        return sum(1 for key in current if key in wanted)
