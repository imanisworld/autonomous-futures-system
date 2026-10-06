"""Chronological experiment partitions and once-only OOS consumption (U2).

Research/evidence plumbing only. Extends the existing Experiment Runner —
does not create a second runner, alter strategy/risk/broker/runtime behavior,
or rewrite historical evidence.

Partition contract (when declared on a spec):
  development → validation → untouched_oos
with non-overlapping, chronologically ordered windows.

OOS consumption is scoped to the exact approved experiment_id + trial_id
identity (plus the declared untouched_oos window fingerprint). There is no
silent family-wide OOS lock.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

PARTITION_DEVELOPMENT = "development"
PARTITION_VALIDATION = "validation"
PARTITION_UNTOUCHED_OOS = "untouched_oos"
ORDERED_PARTITIONS = (
    PARTITION_DEVELOPMENT,
    PARTITION_VALIDATION,
    PARTITION_UNTOUCHED_OOS,
)
KNOWN_PARTITIONS = frozenset(ORDERED_PARTITIONS)

OOS_RECEIPT_SCHEMA_VERSION = "1.0.0"
OOS_LEDGER_REL = "docs/research-oos-consumption-ledger.jsonl"
OOS_RECEIPT_FILENAME = "oos_consumption_receipt.json"


class PartitionContractError(ValueError):
    """Fail-closed chronological partition / OOS-consumption violation."""


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def parse_boundary(value: Any, *, field_name: str) -> datetime:
    """Parse a partition boundary as a timezone-aware UTC datetime.

    Accepts YYYY-MM-DD (treated as 00:00:00Z) or ISO-8601 timestamps.
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
    return {"start": str(raw["start"]).strip(), "end": str(raw["end"]).strip()}


def freeze_chronological_partitions(raw: Any) -> dict[str, dict[str, str]]:
    """Validate and return a frozen partition bundle."""
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
    # Chronological non-overlap: development ends before validation begins, etc.
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


def resolve_evaluation_partition(
    spec: Mapping[str, Any],
    *,
    override: Optional[str] = None,
) -> Optional[str]:
    """Return the active evaluation partition, or None when not declared."""
    raw = override if override is not None else spec.get("evaluation_partition")
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    if text not in KNOWN_PARTITIONS:
        raise PartitionContractError(
            f"unknown evaluation_partition {text!r}; expected one of "
            f"{list(ORDERED_PARTITIONS)}"
        )
    return text


def partitions_declared(spec: Mapping[str, Any]) -> bool:
    return isinstance(spec.get("chronological_partitions"), Mapping)


def validate_spec_partitions(spec: Mapping[str, Any]) -> list[str]:
    """Return error strings for partition contract violations (empty if ok)."""
    errors: list[str] = []
    has_partitions = partitions_declared(spec)
    try:
        evaluation = resolve_evaluation_partition(spec)
    except PartitionContractError as exc:
        return [str(exc)]

    if not has_partitions:
        if evaluation is not None:
            errors.append(
                "evaluation_partition requires chronological_partitions on the spec"
            )
        return errors

    try:
        freeze_chronological_partitions(spec.get("chronological_partitions"))
    except PartitionContractError as exc:
        errors.append(str(exc))
    return errors


def oos_window_fingerprint(partitions: Mapping[str, Mapping[str, str]]) -> str:
    window = partitions[PARTITION_UNTOUCHED_OOS]
    digest = hashlib.sha256(_canonical_json(dict(window)).encode("utf-8")).hexdigest()
    return f"oos-{digest[:32]}"


def receipt_identity_key(
    *,
    experiment_id: str,
    trial_id: str,
    oos_fingerprint: str,
) -> str:
    return f"{experiment_id}|{trial_id}|{oos_fingerprint}"


def oos_ledger_path(root: Path) -> Path:
    return root / OOS_LEDGER_REL


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


def find_oos_consumption(
    root: Path,
    *,
    experiment_id: str,
    trial_id: str,
    oos_fingerprint: str,
) -> Optional[dict[str, Any]]:
    key = receipt_identity_key(
        experiment_id=experiment_id,
        trial_id=trial_id,
        oos_fingerprint=oos_fingerprint,
    )
    for row in _iter_ledger_receipts(root):
        if str(row.get("receipt_identity") or "") == key:
            return row
        # Defensive match on explicit fields if identity missing on older rows.
        if (
            str(row.get("experiment_id") or "") == experiment_id
            and str(row.get("trial_id") or "") == trial_id
            and str(row.get("oos_fingerprint") or "") == oos_fingerprint
        ):
            return row
    return None


def assert_oos_available(
    root: Path,
    *,
    spec: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    """Fail closed if this exact experiment/trial already consumed its OOS window."""
    partitions = freeze_chronological_partitions(spec.get("chronological_partitions"))
    experiment_id = str(spec.get("experiment_id") or "").strip()
    trial_id = str(spec.get("trial_id") or "").strip()
    if not experiment_id or not trial_id:
        raise PartitionContractError(
            "OOS evaluation requires experiment_id and trial_id"
        )
    fingerprint = oos_window_fingerprint(partitions)
    existing = find_oos_consumption(
        root,
        experiment_id=experiment_id,
        trial_id=trial_id,
        oos_fingerprint=fingerprint,
    )
    if existing is not None:
        raise PartitionContractError(
            "untouched_oos already consumed for this exact experiment/trial "
            f"(receipt_identity={receipt_identity_key(experiment_id=experiment_id, trial_id=trial_id, oos_fingerprint=fingerprint)}; "
            f"consumed_at={existing.get('consumed_at')!r})"
        )
    return partitions


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
        "receipt_identity": receipt_identity_key(
            experiment_id=experiment_id,
            trial_id=trial_id,
            oos_fingerprint=fingerprint,
        ),
        "experiment_id": experiment_id,
        "trial_id": trial_id,
        "partition": PARTITION_UNTOUCHED_OOS,
        "untouched_oos_window": dict(partitions[PARTITION_UNTOUCHED_OOS]),
        "oos_fingerprint": fingerprint,
        "code_sha": code_sha,
        "runner_version": runner_version,
        "consumed_at": consumed_at,
        "scope": "exact_experiment_trial",
        "family_wide_lock": False,
    }
    if _present(evidence_path):
        receipt["evidence_path"] = str(evidence_path)
    if _present(runner_report_sha256):
        receipt["runner_report_sha256"] = str(runner_report_sha256)
    return receipt


def append_oos_receipt(root: Path, receipt: Mapping[str, Any]) -> Path:
    """Append a durable OOS consumption receipt. Fail closed on identity collision."""
    experiment_id = str(receipt["experiment_id"])
    trial_id = str(receipt["trial_id"])
    fingerprint = str(receipt["oos_fingerprint"])
    existing = find_oos_consumption(
        root,
        experiment_id=experiment_id,
        trial_id=trial_id,
        oos_fingerprint=fingerprint,
    )
    if existing is not None:
        raise PartitionContractError(
            "refusing to write OOS receipt: untouched_oos already consumed for "
            f"experiment_id={experiment_id!r} trial_id={trial_id!r}"
        )
    path = oos_ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(receipt), sort_keys=True) + "\n")
    return path


def write_oos_receipt_artifact(evidence_dir: Path, receipt: Mapping[str, Any]) -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / OOS_RECEIPT_FILENAME
    path.write_text(
        json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def partition_check_results(spec: Mapping[str, Any]) -> list[tuple[str, bool, str]]:
    """Return (name, passed, evidence) tuples for runner integrity checks."""
    errors = validate_spec_partitions(spec)
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
    frozen = freeze_chronological_partitions(spec.get("chronological_partitions"))
    return [
        (
            "chronological_partitions",
            True,
            "development→validation→untouched_oos ordered without overlap "
            f"(oos_fingerprint={oos_window_fingerprint(frozen)})",
        )
    ]
