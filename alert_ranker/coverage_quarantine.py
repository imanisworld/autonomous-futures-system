"""Blind-window quarantine for the ordinary options coverage products.

A registered forward trial scores a population the ordinary collector also
measures. Its daily ``outcomes_<D>_<D>.{json,csv,md}`` and the cumulative
``episodes_<from>_<to>.json`` would otherwise print, every session, the exact
TARGET_FIRST / INVALIDATION_FIRST / UNRESOLVED counts, direction, and
``would_qualify_floor_rule`` activations the trial preregistration keeps hidden
until its one look. Anyone opening those products would break the blind.

This module is **presentation isolation only**:

* it never touches the observer sqlite, the raw ``cov-v0.1`` events, the
  ``ep-v0.1`` reduction, gate decisions, setup generation, watchlist, risk or
  execution — the immutable session seal is built from the raw store, which is
  left exactly as it was;
* rows belonging to an active blind window are removed from the public
  products and written, byte-exact and canonical, to a per-window quarantine
  file whose SHA-256 is recorded in a manifest; nothing is deleted;
* the public product keeps a small block proving the collector ran under
  quarantine and binding it to the quarantine file (window id, trial ids,
  structural row count, path, byte length, SHA-256). It carries no direction,
  no activation count, no W/L, no R and no outcome decomposition.

The quarantine population is deliberately the structural superset (every
``family`` row on the window's symbols, all gate buckets), so the row count it
exposes is the structural setup count, which the trial already reports, and
never the activation count.

Policy lives in a JSON file shipped with the release (``blind_windows.json``).
A window is active for a session when ``quarantine_from <= session_date`` and
``released_at`` is null. Loading is fail-closed: a missing or malformed policy
is an error, never "no quarantine".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "QUARANTINE_VERSION",
    "DEFAULT_POLICY_RELPATH",
    "BlindWindow",
    "QuarantinePolicyError",
    "load_blind_windows",
    "active_windows",
    "window_for_row",
    "partition_rows",
    "canonical_bytes",
    "quarantine_record",
    "quarantine_relpath",
    "write_quarantine",
    "verify_quarantine_block",
    "public_block_fields",
]

QUARANTINE_VERSION = "quar-v0.1"
DEFAULT_POLICY_RELPATH = "research/coverage/blind_windows.json"
QUARANTINE_DIRNAME = "quarantine"
MANIFEST_NAME = "manifest.jsonl"
KINDS = ("outcomes", "episodes")
_WINDOW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_TRIAL_ID = re.compile(r"^T-\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-\d{2}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")

# What the public product may carry about a quarantine. Nothing else.
public_block_fields = (
    "quarantine_version",
    "window_id",
    "trial_ids",
    "kind",
    "family",
    "symbols_count",
    "quarantine_from",
    "date_from",
    "date_to",
    "quarantined_rows",
    "path",
    "byte_length",
    "sha256",
)


class QuarantinePolicyError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason, self.detail = reason, detail


@dataclass(frozen=True)
class BlindWindow:
    window_id: str
    trial_ids: tuple[str, ...]
    family: str
    symbols: frozenset[str]
    quarantine_from: str
    released_at: str | None
    note: str = ""

    def active_on(self, session_date: str) -> bool:
        return self.released_at is None and self.quarantine_from <= session_date

    def matches(self, row: Mapping[str, Any]) -> bool:
        session_date = row.get("session_date")
        return (
            isinstance(session_date, str)
            and self.active_on(session_date)
            and row.get("family") == self.family
            and row.get("symbol") in self.symbols
        )

    def to_public(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "trial_ids": list(self.trial_ids),
            "family": self.family,
            "symbols_count": len(self.symbols),
            "quarantine_from": self.quarantine_from,
        }


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #


def _date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise QuarantinePolicyError("policy_field_invalid", field)
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise QuarantinePolicyError("policy_field_invalid", field) from exc


def _window(raw: Any, index: int) -> BlindWindow:
    label = f"windows[{index}]"
    if not isinstance(raw, Mapping):
        raise QuarantinePolicyError("policy_window_invalid", label)
    window_id = raw.get("window_id")
    if not isinstance(window_id, str) or not _WINDOW_ID.match(window_id):
        raise QuarantinePolicyError("policy_field_invalid", f"{label}.window_id")
    trial_ids = raw.get("trial_ids")
    if (
        not isinstance(trial_ids, list)
        or not trial_ids
        or any(not isinstance(t, str) or not _TRIAL_ID.match(t) for t in trial_ids)
        or len(set(trial_ids)) != len(trial_ids)
    ):
        raise QuarantinePolicyError("policy_field_invalid", f"{label}.trial_ids")
    family = raw.get("family")
    if not isinstance(family, str) or not family:
        raise QuarantinePolicyError("policy_field_invalid", f"{label}.family")
    symbols = raw.get("symbols")
    if (
        not isinstance(symbols, list)
        or not symbols
        or any(not isinstance(s, str) or not _SYMBOL.match(s) for s in symbols)
        or len(set(symbols)) != len(symbols)
    ):
        raise QuarantinePolicyError("policy_field_invalid", f"{label}.symbols")
    quarantine_from = _date(raw.get("quarantine_from"), f"{label}.quarantine_from")
    released_at = raw.get("released_at")
    if released_at is not None:
        if not isinstance(released_at, str):
            raise QuarantinePolicyError("policy_field_invalid", f"{label}.released_at")
        try:
            parsed = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QuarantinePolicyError("policy_field_invalid", f"{label}.released_at") from exc
        if parsed.tzinfo is None:
            raise QuarantinePolicyError("policy_field_invalid", f"{label}.released_at")
    note = raw.get("note", "")
    if not isinstance(note, str):
        raise QuarantinePolicyError("policy_field_invalid", f"{label}.note")
    extra = set(raw) - {"window_id", "trial_ids", "family", "symbols", "quarantine_from", "released_at", "note"}
    if extra:
        raise QuarantinePolicyError("policy_field_unknown", f"{label}: {sorted(extra)}")
    return BlindWindow(
        window_id=window_id,
        trial_ids=tuple(trial_ids),
        family=family,
        symbols=frozenset(symbols),
        quarantine_from=quarantine_from,
        released_at=released_at,
        note=note,
    )


def load_blind_windows(path: Path) -> tuple[BlindWindow, ...]:
    """Parse the policy file. Fail closed on anything unexpected."""
    if not path.exists():
        raise QuarantinePolicyError("policy_missing", str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QuarantinePolicyError("policy_unparseable", f"{path}: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise QuarantinePolicyError("policy_schema_version", str(payload.get("schema_version") if isinstance(payload, Mapping) else payload))
    windows_raw = payload.get("windows")
    if not isinstance(windows_raw, list):
        raise QuarantinePolicyError("policy_windows_invalid")
    windows = tuple(_window(raw, index) for index, raw in enumerate(windows_raw))
    ids = [w.window_id for w in windows]
    if len(set(ids)) != len(ids):
        raise QuarantinePolicyError("policy_window_duplicate")
    return windows


def active_windows(windows: Iterable[BlindWindow], session_date: str) -> list[BlindWindow]:
    return [w for w in windows if w.active_on(session_date)]


def window_for_row(row: Mapping[str, Any], windows: Iterable[BlindWindow]) -> BlindWindow | None:
    for window in windows:
        if window.matches(row):
            return window
    return None


def partition_rows(
    rows: Sequence[Mapping[str, Any]], windows: Sequence[BlindWindow]
) -> tuple[list[Mapping[str, Any]], dict[str, list[Mapping[str, Any]]]]:
    """Split rows into (public, {window_id: held}). Order is preserved."""
    public: list[Mapping[str, Any]] = []
    held: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        window = window_for_row(row, windows)
        if window is None:
            public.append(row)
        else:
            held.setdefault(window.window_id, []).append(row)
    return public, held


# --------------------------------------------------------------------------- #
# quarantine file
# --------------------------------------------------------------------------- #


def canonical_bytes(record: Mapping[str, Any]) -> bytes:
    """Compact UTF-8 JSON, sorted keys at every level, one trailing newline."""
    return (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def quarantine_relpath(window: BlindWindow, kind: str, date_from: str, date_to: str) -> str:
    if kind not in KINDS:
        raise QuarantinePolicyError("kind_invalid", kind)
    return f"{QUARANTINE_DIRNAME}/{window.window_id}/{kind}_{date_from}_{date_to}.json"


def quarantine_record(
    window: BlindWindow,
    kind: str,
    date_from: str,
    date_to: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    source: Mapping[str, Any],
    quarantined_at: datetime | None = None,
) -> dict[str, Any]:
    """The full held rows plus enough provenance to stand alone later."""
    if kind not in KINDS:
        raise QuarantinePolicyError("kind_invalid", kind)
    stamp = (quarantined_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for row in rows:
        if not window.matches(row):
            raise QuarantinePolicyError("row_outside_window", str(row.get("symbol")))
    return {
        "quarantine_version": QUARANTINE_VERSION,
        "window_id": window.window_id,
        "trial_ids": list(window.trial_ids),
        "kind": kind,
        "family": window.family,
        "symbols": sorted(window.symbols),
        "quarantine_from": window.quarantine_from,
        "date_from": date_from,
        "date_to": date_to,
        "source": dict(source),
        "quarantined_at": stamp.isoformat(),
        "rows": [dict(row) for row in rows],
    }


def write_quarantine(out_dir: Path, record: Mapping[str, Any], *, window: BlindWindow) -> dict[str, Any]:
    """Write the canonical quarantine file once and append its digest to the manifest.

    An existing file at the path is never overwritten: it is renamed with a
    ``.superseded.<stamp>`` suffix so a collector re-run keeps every byte it
    ever produced. Returns the public block for the product summary.
    """
    rel = quarantine_relpath(window, str(record["kind"]), str(record["date_from"]), str(record["date_to"]))
    path = out_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = canonical_bytes(record)
    digest = _sha256(body)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path.rename(path.with_name(f"{path.name}.superseded.{stamp}"))
    path.write_bytes(body)
    manifest_line = {
        "kind": record["kind"],
        "date_from": record["date_from"],
        "date_to": record["date_to"],
        "byte_length": len(body),
        "sha256": digest,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    with (path.parent / MANIFEST_NAME).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest_line, sort_keys=True) + "\n")
    return {
        "quarantine_version": QUARANTINE_VERSION,
        **window.to_public(),
        "kind": record["kind"],
        "date_from": record["date_from"],
        "date_to": record["date_to"],
        "quarantined_rows": len(record["rows"]),
        "path": rel,
        "byte_length": len(body),
        "sha256": digest,
    }


def verify_quarantine_block(out_dir: Path, block: Mapping[str, Any]) -> list[str]:
    """Problems binding a public block to its quarantine file; empty when verified.

    Reads the file only to hash it and count rows. Returns nothing about the
    rows themselves.
    """
    problems: list[str] = []
    if set(block) != set(public_block_fields):
        problems.append("quarantine_block_fields")
        return problems
    if block.get("quarantine_version") != QUARANTINE_VERSION:
        problems.append(f"quarantine_version:{block.get('quarantine_version')}")
    rel = block.get("path")
    if not isinstance(rel, str) or not rel.startswith(f"{QUARANTINE_DIRNAME}/") or ".." in rel:
        problems.append("quarantine_path_invalid")
        return problems
    path = out_dir / rel
    if not path.exists():
        problems.append(f"quarantine_file_missing:{rel}")
        return problems
    body = path.read_bytes()
    if len(body) != block.get("byte_length"):
        problems.append("quarantine_byte_length")
    if _sha256(body) != block.get("sha256"):
        problems.append("quarantine_sha256")
    try:
        record = json.loads(body.decode("utf-8"))
    except ValueError:
        problems.append("quarantine_file_unparseable")
        return problems
    if not isinstance(record, Mapping) or not isinstance(record.get("rows"), list):
        problems.append("quarantine_file_malformed")
        return problems
    if canonical_bytes(record) != body:
        problems.append("quarantine_not_canonical")
    for key in ("window_id", "kind", "date_from", "date_to", "family"):
        if record.get(key) != block.get(key):
            problems.append(f"quarantine_identity:{key}")
    if sorted(record.get("trial_ids") or []) != sorted(block.get("trial_ids") or []):
        problems.append("quarantine_identity:trial_ids")
    if len(record["rows"]) != block.get("quarantined_rows"):
        problems.append("quarantine_row_count")
    return problems
