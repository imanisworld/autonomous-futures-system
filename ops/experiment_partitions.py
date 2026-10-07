"""Chronological experiment partitions and once-only OOS consumption (U2).

Research/evidence plumbing only. Extends the existing Experiment Runner —
does not create a second runner, alter strategy/risk/broker/runtime behavior,
or rewrite historical evidence.

Partition contract (when declared on a spec):
  development → validation → untouched_oos
with non-overlapping, chronologically ordered half-open windows [start, end)
in UTC.

Active evaluation partition is mandatory when chronological_partitions are
declared. Scored trade_execution rows must have signal_ts inside the active
window. Coverage may not claim untouched_oos without timestamp membership
proof (timestamps are never invented).

OOS consumption is once-only per exact approved trial_id. experiment_id
renames, equivalent window reformatting, and OOS window changes under the
same trial cannot grant a second look. The normalized OOS window fingerprint
is recorded as evidence only — it is not the reuse key. There is no
family-wide OOS lock.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

PARTITION_DEVELOPMENT = "development"
PARTITION_VALIDATION = "validation"
PARTITION_UNTOUCHED_OOS = "untouched_oos"
ORDERED_PARTITIONS = (
    PARTITION_DEVELOPMENT,
    PARTITION_VALIDATION,
    PARTITION_UNTOUCHED_OOS,
)
KNOWN_PARTITIONS = frozenset(ORDERED_PARTITIONS)

OOS_RECEIPT_SCHEMA_VERSION = "1.1.0"
OOS_LEDGER_REL = "docs/research-oos-consumption-ledger.jsonl"
OOS_LEDGER_LOCK_REL = "docs/research-oos-consumption-ledger.jsonl.lock"
OOS_RECEIPT_FILENAME = "oos_consumption_receipt.json"

# Optional coverage membership timestamp keys (never invented by the runner).
COVERAGE_MEMBERSHIP_TS_KEYS = ("signal_ts", "timestamp", "event_ts", "bar_ts")


class PartitionContractError(ValueError):
    """Fail-closed chronological partition / OOS-consumption violation."""


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_boundary(value: Any, *, field_name: str) -> datetime:
    """Parse a partition boundary as a timezone-aware UTC datetime.

    Accepts YYYY-MM-DD (treated as 00:00:00Z) or ISO-8601 timestamps.
    Naive timestamps are rejected; all comparisons use UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise PartitionContractError(f"{field_name} must be a non-empty date/timestamp")
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise PartitionContractError(
                f"{field_name} is not a valid YYYY-MM-DD date: {value!r}"
            ) from exc
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PartitionContractError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise PartitionContractError(f"{field_name} must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def parse_membership_ts(value: Any, *, field_name: str) -> datetime:
    """Parse a scored-row timestamp for partition membership (UTC)."""
    return parse_boundary(value, field_name=field_name)


def _normalize_window(raw: Any, *, partition: str) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise PartitionContractError(
            f"chronological_partitions.{partition} must be an object with start/end"
        )
    if "start" not in raw or "end" not in raw:
        raise PartitionContractError(
            f"chronological_partitions.{partition} missing start or end boundary"
        )
    start = parse_boundary(raw["start"], field_name=f"{partition}.start")
    end = parse_boundary(raw["end"], field_name=f"{partition}.end")
    if end <= start:
        raise PartitionContractError(
            f"chronological_partitions.{partition} requires end > start"
        )
    # Store UTC-normalized forms so equivalent raw strings fingerprint identically.
    return {"start": _format_utc(start), "end": _format_utc(end)}


def freeze_chronological_partitions(raw: Any) -> dict[str, dict[str, str]]:
    """Validate and return a frozen partition bundle with UTC-normalized windows."""
    if raw is None:
        raise PartitionContractError("chronological_partitions is missing")
    if not isinstance(raw, Mapping):
        raise PartitionContractError("chronological_partitions must be an object")
    unknown = sorted(set(raw) - KNOWN_PARTITIONS)
    if unknown:
        raise PartitionContractError(
            f"chronological_partitions has unsupported keys: {unknown}"
        )
    missing = [name for name in ORDERED_PARTITIONS if name not in raw]
    if missing:
        raise PartitionContractError(
            "chronological_partitions missing required partitions: " + ", ".join(missing)
        )
    frozen = {
        name: _normalize_window(raw[name], partition=name) for name in ORDERED_PARTITIONS
    }
    bounds = {
        name: (
            parse_boundary(frozen[name]["start"], field_name=f"{name}.start"),
            parse_boundary(frozen[name]["end"], field_name=f"{name}.end"),
        )
        for name in ORDERED_PARTITIONS
    }
    if bounds[PARTITION_DEVELOPMENT][1] > bounds[PARTITION_VALIDATION][0]:
        raise PartitionContractError(
            "development must end before validation begins (no overlap)"
        )
    if bounds[PARTITION_VALIDATION][1] > bounds[PARTITION_UNTOUCHED_OOS][0]:
        raise PartitionContractError(
            "validation must end before untouched_oos begins (no overlap)"
        )
    return frozen


def window_bounds(window: Mapping[str, str]) -> tuple[datetime, datetime]:
    start = parse_boundary(window["start"], field_name="window.start")
    end = parse_boundary(window["end"], field_name="window.end")
    return start, end


def timestamp_in_window(ts: datetime, window: Mapping[str, str]) -> bool:
    """Half-open membership: start <= ts < end (UTC)."""
    if ts.tzinfo is None:
        raise PartitionContractError("membership timestamp must be timezone-aware UTC")
    start, end = window_bounds(window)
    utc_ts = ts.astimezone(timezone.utc)
    return start <= utc_ts < end


def partitions_declared(spec: Mapping[str, Any]) -> bool:
    return isinstance(spec.get("chronological_partitions"), Mapping)


def resolve_active_partition(
    spec: Mapping[str, Any],
    *,
    cli_partition: Optional[str] = None,
) -> tuple[Optional[str], Optional[dict[str, str]], Optional[dict[str, dict[str, str]]]]:
    """Resolve active evaluation partition and its normalized window.

    Returns (active_partition, active_window, frozen_partitions).

    Rules:
    - No declared partitions + no active partition → legacy OK (all None).
    - Declared partitions + no active partition → INVALID.
    - CLI --partition must not silently contradict spec evaluation_partition.
    """
    has_partitions = partitions_declared(spec)
    spec_raw = spec.get("evaluation_partition")
    spec_partition: Optional[str] = None
    if _present(spec_raw):
        text = str(spec_raw).strip()
        if text not in KNOWN_PARTITIONS:
            raise PartitionContractError(
                f"unknown evaluation_partition {text!r}; expected one of "
                f"{list(ORDERED_PARTITIONS)}"
            )
        spec_partition = text

    cli_resolved: Optional[str] = None
    if cli_partition is not None and str(cli_partition).strip() != "":
        text = str(cli_partition).strip()
        if text not in KNOWN_PARTITIONS:
            raise PartitionContractError(
                f"unknown CLI --partition {text!r}; expected one of "
                f"{list(ORDERED_PARTITIONS)}"
            )
        cli_resolved = text

    if (
        cli_resolved is not None
        and spec_partition is not None
        and cli_resolved != spec_partition
    ):
        raise PartitionContractError(
            f"CLI --partition {cli_resolved!r} contradicts spec "
            f"evaluation_partition {spec_partition!r}"
        )

    active = cli_resolved if cli_resolved is not None else spec_partition

    if not has_partitions:
        if active is not None:
            raise PartitionContractError(
                "evaluation_partition requires chronological_partitions on the spec"
            )
        return None, None, None

    frozen = freeze_chronological_partitions(spec.get("chronological_partitions"))
    if active is None:
        raise PartitionContractError(
            "chronological_partitions declared but no active evaluation_partition; "
            "set evaluation_partition on the spec or pass --partition"
        )
    return active, dict(frozen[active]), frozen


def validate_spec_partitions(
    spec: Mapping[str, Any],
    *,
    cli_partition: Optional[str] = None,
) -> list[str]:
    """Return error strings for partition contract violations (empty if ok)."""
    try:
        resolve_active_partition(spec, cli_partition=cli_partition)
    except PartitionContractError as exc:
        return [str(exc)]
    return []


def oos_window_fingerprint(partitions: Mapping[str, Mapping[str, str]]) -> str:
    """Fingerprint of the UTC-normalized untouched_oos window (evidence only)."""
    window = partitions[PARTITION_UNTOUCHED_OOS]
    digest = hashlib.sha256(_canonical_json(dict(window)).encode("utf-8")).hexdigest()
    return f"oos-{digest[:32]}"


def receipt_reuse_key(*, trial_id: str) -> str:
    """Once-only OOS reuse key: exact approved trial_id only."""
    text = str(trial_id or "").strip()
    if not text:
        raise PartitionContractError("OOS receipt requires trial_id")
    return f"trial:{text}"


def oos_ledger_path(root: Path) -> Path:
    return root / OOS_LEDGER_REL


def oos_ledger_lock_path(root: Path) -> Path:
    return root / OOS_LEDGER_LOCK_REL


@contextmanager
def oos_ledger_lock(root: Path) -> Iterator[None]:
    """Exclusive fcntl lock around OOS receipt check + append."""
    lock_path = oos_ledger_lock_path(root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _iter_ledger_receipts(root: Path) -> list[dict[str, Any]]:
    path = oos_ledger_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PartitionContractError(
                f"corrupt OOS consumption ledger at line {lineno}: {exc}"
            ) from exc
        if isinstance(row, dict):
            rows.append(row)
    return rows


def find_oos_consumption(root: Path, *, trial_id: str) -> Optional[dict[str, Any]]:
    """Find prior OOS consumption for this exact trial_id (reuse key)."""
    key = receipt_reuse_key(trial_id=trial_id)
    for row in _iter_ledger_receipts(root):
        if str(row.get("receipt_identity") or "") == key:
            return row
        if str(row.get("trial_id") or "").strip() == str(trial_id).strip():
            return row
    return None


def assert_oos_available(root: Path, *, trial_id: str) -> None:
    """Fail closed if this exact trial already consumed its OOS look."""
    existing = find_oos_consumption(root, trial_id=trial_id)
    if existing is not None:
        raise PartitionContractError(
            "untouched_oos already consumed for this exact trial "
            f"(receipt_identity={receipt_reuse_key(trial_id=trial_id)}; "
            f"consumed_at={existing.get('consumed_at')!r}; "
            f"prior_experiment_id={existing.get('experiment_id')!r})"
        )


def assert_consumed_trial_partitions_unchanged(
    root: Path,
    *,
    trial_id: str,
    frozen_partitions: Optional[Mapping[str, Mapping[str, str]]],
) -> None:
    """Fail closed when a trial that consumed its OOS look is re-scored on
    redrawn or removed partitions (U4).

    Once a receipt exists, every later run of that exact trial must declare
    chronological partitions whose normalized untouched_oos window equals the
    window recorded on the receipt. Removing partitions (legacy path) or moving
    the OOS window — and with it the validation/development windows, which must
    end at or before the OOS start — cannot re-score the consumed dates.
    """
    if not str(trial_id or "").strip():
        return
    receipt = find_oos_consumption(root, trial_id=trial_id)
    if receipt is None:
        return
    if frozen_partitions is None:
        raise PartitionContractError(
            "trial already consumed its untouched_oos look; it cannot be re-scored "
            "without its recorded chronological_partitions "
            f"(receipt_identity={receipt_reuse_key(trial_id=trial_id)})"
        )
    recorded = receipt.get("untouched_oos_window")
    try:
        recorded_window = _normalize_window(recorded, partition=PARTITION_UNTOUCHED_OOS)
    except PartitionContractError as exc:
        raise PartitionContractError(
            f"OOS receipt for trial_id={trial_id!r} has no usable untouched_oos_window: {exc}"
        ) from exc
    current = dict(frozen_partitions[PARTITION_UNTOUCHED_OOS])
    if current != recorded_window:
        raise PartitionContractError(
            "trial already consumed its untouched_oos look on window "
            f"[{recorded_window['start']}, {recorded_window['end']}); "
            f"redrawn window [{current['start']}, {current['end']}) is refused"
        )


def _coverage_membership_ts(row: Mapping[str, Any]) -> Optional[datetime]:
    for key in COVERAGE_MEMBERSHIP_TS_KEYS:
        if _present(row.get(key)):
            return parse_membership_ts(row[key], field_name=key)
    return None


def assert_members_match_active_partition(
    members: Sequence[Mapping[str, Any]],
    *,
    evidence_type: str,
    active_partition: Optional[str],
    active_window: Optional[Mapping[str, str]],
) -> list[str]:
    """Fail closed when scored rows fall outside the active half-open window.

    trade_execution: every row's signal_ts must satisfy start <= ts < end.
    coverage: if membership timestamps are present they must fall in-window;
    untouched_oos without any membership timestamp cannot claim the partition.
    """
    if active_partition is None or active_window is None:
        return []
    errors: list[str] = []
    if evidence_type == "trade_execution":
        if not members:
            return ["trade_execution arm produced no members for partition check"]
        for index, row in enumerate(members):
            if not isinstance(row, Mapping):
                errors.append(f"member[{index}]: row must be an object")
                continue
            if not _present(row.get("signal_ts")):
                errors.append(
                    f"member[{index}]: trade_execution requires signal_ts inside "
                    f"active partition {active_partition}"
                )
                continue
            try:
                ts = parse_membership_ts(row["signal_ts"], field_name="signal_ts")
            except PartitionContractError as exc:
                errors.append(f"member[{index}]: {exc}")
                continue
            if not timestamp_in_window(ts, active_window):
                errors.append(
                    f"member[{index}]: signal_ts {row['signal_ts']!r} outside "
                    f"active partition {active_partition} window "
                    f"[{active_window['start']}, {active_window['end']})"
                )
        return errors

    # coverage
    seen_any = False
    for index, row in enumerate(members):
        if not isinstance(row, Mapping):
            continue
        try:
            ts = _coverage_membership_ts(row)
        except PartitionContractError as exc:
            errors.append(f"member[{index}]: {exc}")
            continue
        if ts is None:
            continue
        seen_any = True
        if not timestamp_in_window(ts, active_window):
            errors.append(
                f"member[{index}]: coverage timestamp outside active partition "
                f"{active_partition} window "
                f"[{active_window['start']}, {active_window['end']})"
            )
    if active_partition == PARTITION_UNTOUCHED_OOS and not seen_any and not errors:
        errors.append(
            "coverage evidence cannot claim untouched_oos without timestamp "
            "membership proof (refusing to invent timestamps)"
        )
    return errors


def build_oos_receipt(
    *,
    spec: Mapping[str, Any],
    partitions: Mapping[str, Mapping[str, str]],
    code_sha: str,
    runner_version: str,
    consumed_at: str,
    evidence_path: Optional[str] = None,
    runner_report_sha256: Optional[str] = None,
) -> dict[str, Any]:
    fingerprint = oos_window_fingerprint(partitions)
    experiment_id = str(spec["experiment_id"])
    trial_id = str(spec["trial_id"])
    receipt = {
        "schema_version": OOS_RECEIPT_SCHEMA_VERSION,
        "receipt_identity": receipt_reuse_key(trial_id=trial_id),
        "experiment_id": experiment_id,
        "trial_id": trial_id,
        "partition": PARTITION_UNTOUCHED_OOS,
        "untouched_oos_window": dict(partitions[PARTITION_UNTOUCHED_OOS]),
        "oos_fingerprint": fingerprint,
        "code_sha": code_sha,
        "runner_version": runner_version,
        "consumed_at": consumed_at,
        "scope": "exact_trial",
        "family_wide_lock": False,
    }
    if _present(evidence_path):
        receipt["evidence_path"] = str(evidence_path)
    if _present(runner_report_sha256):
        receipt["runner_report_sha256"] = str(runner_report_sha256)
    data = spec.get("data") if isinstance(spec.get("data"), Mapping) else {}
    if _present(data.get("dataset_hash")):
        receipt["dataset_hash"] = str(data["dataset_hash"])
    return receipt


def append_oos_receipt(root: Path, receipt: Mapping[str, Any]) -> Path:
    """Append a durable OOS consumption receipt. Fail closed on trial collision.

    Caller must hold oos_ledger_lock. Does not write evidence bundles.
    """
    trial_id = str(receipt["trial_id"])
    existing = find_oos_consumption(root, trial_id=trial_id)
    if existing is not None:
        raise PartitionContractError(
            "refusing to write OOS receipt: untouched_oos already consumed for "
            f"trial_id={trial_id!r}"
        )
    path = oos_ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(receipt), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return path


def consume_oos_receipt(root: Path, receipt: Mapping[str, Any]) -> Path:
    """Atomically check + append OOS receipt under an exclusive lock."""
    with oos_ledger_lock(root):
        assert_oos_available(root, trial_id=str(receipt["trial_id"]))
        return append_oos_receipt(root, receipt)


def write_oos_receipt_artifact(evidence_dir: Path, receipt: Mapping[str, Any]) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / OOS_RECEIPT_FILENAME
    path.write_text(
        json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def partition_check_results(
    spec: Mapping[str, Any],
    *,
    cli_partition: Optional[str] = None,
) -> list[tuple[str, bool, str]]:
    """Return (name, passed, evidence) tuples for runner integrity checks."""
    errors = validate_spec_partitions(spec, cli_partition=cli_partition)
    if not partitions_declared(spec) and not errors:
        return [
            (
                "chronological_partitions",
                True,
                "chronological_partitions omitted (legacy/compatible path)",
            )
        ]
    if errors:
        return [("chronological_partitions", False, "; ".join(errors))]
    active, window, frozen = resolve_active_partition(
        spec, cli_partition=cli_partition
    )
    assert frozen is not None and active is not None and window is not None
    return [
        (
            "chronological_partitions",
            True,
            "development→validation→untouched_oos ordered without overlap "
            f"(active={active}; window=[{window['start']}, {window['end']}); "
            f"oos_fingerprint={oos_window_fingerprint(frozen)})",
        )
    ]
