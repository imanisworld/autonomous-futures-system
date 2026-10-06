"""Canonical evidence envelope and typed evidence rows for the Experiment Runner.

Research/evidence plumbing only. This module does not place orders, change
strategy/risk/broker behavior, or migrate historical evidence.

Evidence types are explicit. Non-trade experiments (for example options
coverage/geometry) use ``coverage`` and are not required to supply trade fills,
stops, targets, MAE/MFE, or P&L.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

EVIDENCE_ROW_SCHEMA_VERSION = "1.0.0"

EVIDENCE_TYPE_COVERAGE = "coverage"
EVIDENCE_TYPE_TRADE_EXECUTION = "trade_execution"
KNOWN_EVIDENCE_TYPES = frozenset({EVIDENCE_TYPE_COVERAGE, EVIDENCE_TYPE_TRADE_EXECUTION})

FILL_STATE_FILLED = "FILLED"
FILL_STATE_NO_FILL = "NO_FILL"
KNOWN_FILL_STATES = frozenset({FILL_STATE_FILLED, FILL_STATE_NO_FILL})

# Frozen assumption keys that participate in execution_model_id.
EXECUTION_ASSUMPTION_KEYS = (
    "entry_fill_model",
    "same_bar_ambiguity_rule",
    "stop_handling",
    "target_handling",
    "slippage_assumption",
    "commission",
    "exchange_broker_fees",
    "sizing_assumptions",
)

COMMON_ENVELOPE_REQUIRED = (
    "schema_version",
    "experiment_id",
    "trial_id",
    "setup_type",
    "evidence_type",
    "code_sha",
    "data_identity",
    "runner_version",
    "generated_at",
)

TRADE_IDENTITY_REQUIRED = (
    "instrument",
    "strategy_identity",
    "candidate_signal_id",
    "signal_ts",
    "decision_ts",
    "earliest_legal_order_ts",
    "intended_entry",
    "fill_state",
    "stop",
    "target",
    "data_fingerprint",
    "execution_model_id",
)

TRADE_FILLED_REQUIRED = (
    "fill_price",
    "fill_ts",
    "exit_price",
    "exit_ts",
    "exit_reason",
    "mae",
    "mfe",
    "gross_pnl",
    "costs_fees",
    "net_pnl",
    "r_multiple",
)

TRADE_NUMERIC_FIELDS = frozenset(
    {
        "intended_entry",
        "fill_price",
        "stop",
        "target",
        "exit_price",
        "mae",
        "mfe",
        "gross_pnl",
        "costs_fees",
        "net_pnl",
        "r_multiple",
    }
)


class EvidenceContractError(ValueError):
    """Fail-closed evidence contract violation."""


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def resolve_evidence_type(spec: Mapping[str, Any]) -> str:
    """Return the declared evidence type, defaulting to coverage for compatibility."""
    raw = spec.get("evidence_type")
    if raw is None or raw == "":
        return EVIDENCE_TYPE_COVERAGE
    text = str(raw).strip()
    if text not in KNOWN_EVIDENCE_TYPES:
        raise EvidenceContractError(
            f"unknown evidence_type {text!r}; expected one of {sorted(KNOWN_EVIDENCE_TYPES)}"
        )
    return text


def freeze_execution_assumptions(assumptions: Mapping[str, Any]) -> dict[str, Any]:
    """Return a frozen, deterministic execution-assumption bundle.

    Missing required keys fail closed. Extra keys are rejected so identity stays
    stable and explicit.
    """
    if not isinstance(assumptions, Mapping):
        raise EvidenceContractError("execution_assumptions must be an object")
    unknown = sorted(set(assumptions) - set(EXECUTION_ASSUMPTION_KEYS))
    if unknown:
        raise EvidenceContractError(
            f"execution_assumptions has unsupported keys: {unknown}"
        )
    frozen: dict[str, Any] = {}
    missing: list[str] = []
    for key in EXECUTION_ASSUMPTION_KEYS:
        value = assumptions.get(key)
        if not _present(value):
            missing.append(key)
            continue
        frozen[key] = value
    if missing:
        raise EvidenceContractError(
            "execution_assumptions missing required keys: " + ", ".join(missing)
        )
    return frozen


def execution_model_id(assumptions: Mapping[str, Any]) -> str:
    """Stable identity for a frozen execution/cost assumption bundle."""
    frozen = freeze_execution_assumptions(assumptions)
    digest = hashlib.sha256(_canonical_json(frozen).encode("utf-8")).hexdigest()
    return f"em-{digest[:32]}"


def parse_ts(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError(f"{field_name} must be a non-empty ISO-8601 timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceContractError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise EvidenceContractError(f"{field_name} must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


def _require_finite(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceContractError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise EvidenceContractError(f"{field_name} must be finite (got {value!r})")
    return number


def _require_present(row: Mapping[str, Any], key: str) -> Any:
    if key not in row or not _present(row.get(key)):
        raise EvidenceContractError(f"missing required field: {key}")
    return row[key]


def data_identity_from_spec(spec: Mapping[str, Any]) -> str:
    data = spec.get("data") if isinstance(spec.get("data"), Mapping) else {}
    dataset_hash = data.get("dataset_hash")
    if _present(dataset_hash):
        return f"dataset_hash:{dataset_hash}"
    dataset_id = data.get("dataset_id")
    if _present(dataset_id):
        window = data.get("window") if isinstance(data.get("window"), Mapping) else {}
        start = window.get("start")
        end = window.get("end")
        source = data.get("source")
        return f"dataset_id:{dataset_id}|source:{source}|window:{start}..{end}"
    raise EvidenceContractError("missing data identity (dataset_hash or dataset_id)")


def build_common_envelope(
    *,
    spec: Mapping[str, Any],
    code_sha: str,
    runner_version: str,
    evidence_type: Optional[str] = None,
    execution_model_id_value: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Build the common identity envelope for a runner evidence bundle."""
    resolved_type = evidence_type or resolve_evidence_type(spec)
    envelope: dict[str, Any] = {
        "schema_version": EVIDENCE_ROW_SCHEMA_VERSION,
        "experiment_id": _require_present(spec, "experiment_id"),
        "trial_id": _require_present(spec, "trial_id"),
        "setup_type": _require_present(spec, "setup_type"),
        "evidence_type": resolved_type,
        "code_sha": code_sha,
        "data_identity": data_identity_from_spec(spec),
        "runner_version": runner_version,
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if _present(spec.get("prereg_path")):
        envelope["preregistration_identity"] = str(spec["prereg_path"])
    if _present(spec.get("population")):
        envelope["prior_exposure_identity"] = str(spec["population"])
    strategy = spec.get("strategy_identity") or spec.get("setup_type")
    if _present(strategy):
        envelope["strategy_identity"] = str(strategy)
    if _present(execution_model_id_value):
        envelope["execution_model_id"] = str(execution_model_id_value)
    elif resolved_type == EVIDENCE_TYPE_TRADE_EXECUTION:
        assumptions = spec.get("execution_assumptions")
        if not isinstance(assumptions, Mapping):
            raise EvidenceContractError(
                "trade_execution requires execution_assumptions on the experiment spec"
            )
        envelope["execution_model_id"] = execution_model_id(assumptions)
        envelope["execution_assumptions"] = freeze_execution_assumptions(assumptions)
    validate_common_envelope(envelope, require_execution_model=(resolved_type == EVIDENCE_TYPE_TRADE_EXECUTION))
    return envelope


def validate_common_envelope(
    envelope: Mapping[str, Any],
    *,
    require_execution_model: bool = False,
) -> None:
    for key in COMMON_ENVELOPE_REQUIRED:
        if key not in envelope or not _present(envelope.get(key)):
            raise EvidenceContractError(f"evidence envelope missing required field: {key}")
    if str(envelope["schema_version"]) != EVIDENCE_ROW_SCHEMA_VERSION:
        raise EvidenceContractError(
            f"unsupported evidence schema_version {envelope['schema_version']!r}"
        )
    if str(envelope["evidence_type"]) not in KNOWN_EVIDENCE_TYPES:
        raise EvidenceContractError(
            f"unknown evidence_type {envelope['evidence_type']!r}"
        )
    if require_execution_model and not _present(envelope.get("execution_model_id")):
        raise EvidenceContractError("evidence envelope missing execution_model_id")
    parse_ts(envelope["generated_at"], field_name="generated_at")


def validate_trade_execution_row(
    row: Mapping[str, Any],
    *,
    expected_execution_model_id: Optional[str] = None,
) -> None:
    """Fail-closed validation for one promotion-quality futures trade row."""
    if not isinstance(row, Mapping):
        raise EvidenceContractError("trade_execution row must be an object")
    if str(row.get("evidence_type") or EVIDENCE_TYPE_TRADE_EXECUTION) != EVIDENCE_TYPE_TRADE_EXECUTION:
        raise EvidenceContractError(
            f"evidence-type/schema mismatch: expected {EVIDENCE_TYPE_TRADE_EXECUTION}"
        )

    for key in TRADE_IDENTITY_REQUIRED:
        _require_present(row, key)

    fill_state = str(row["fill_state"]).strip().upper()
    if fill_state not in KNOWN_FILL_STATES:
        raise EvidenceContractError(
            f"fill_state must be one of {sorted(KNOWN_FILL_STATES)}; got {row['fill_state']!r}"
        )

    if expected_execution_model_id is not None:
        if str(row["execution_model_id"]) != str(expected_execution_model_id):
            raise EvidenceContractError(
                "execution_model_id does not match the frozen experiment assumptions"
            )

    signal_ts = parse_ts(row["signal_ts"], field_name="signal_ts")
    decision_ts = parse_ts(row["decision_ts"], field_name="decision_ts")
    earliest = parse_ts(row["earliest_legal_order_ts"], field_name="earliest_legal_order_ts")
    if decision_ts < signal_ts:
        raise EvidenceContractError("decision_ts must be >= signal_ts")
    if earliest < decision_ts:
        raise EvidenceContractError("earliest_legal_order_ts must be >= decision_ts")

    for key in ("intended_entry", "stop", "target"):
        _require_finite(row[key], field_name=key)

    if fill_state == FILL_STATE_NO_FILL:
        for forbidden in ("fill_price", "fill_ts"):
            if _present(row.get(forbidden)):
                raise EvidenceContractError(
                    f"NO_FILL row must not fabricate {forbidden}"
                )
        if not (
            _present(row.get("reject_reason"))
            or _present(row.get("skip_reason"))
            or _present(row.get("no_fill_reason"))
        ):
            raise EvidenceContractError(
                "NO_FILL row requires reject_reason, skip_reason, or no_fill_reason"
            )
        return

    # FILLED
    for key in TRADE_FILLED_REQUIRED:
        _require_present(row, key)
    for key in TRADE_FILLED_REQUIRED:
        if key in TRADE_NUMERIC_FIELDS or key in {
            "fill_price",
            "exit_price",
            "mae",
            "mfe",
            "gross_pnl",
            "costs_fees",
            "net_pnl",
            "r_multiple",
        }:
            if key.endswith("_ts") or key == "exit_reason":
                continue
            if key in {
                "fill_price",
                "exit_price",
                "mae",
                "mfe",
                "gross_pnl",
                "costs_fees",
                "net_pnl",
                "r_multiple",
            }:
                _require_finite(row[key], field_name=key)

    fill_ts = parse_ts(row["fill_ts"], field_name="fill_ts")
    exit_ts = parse_ts(row["exit_ts"], field_name="exit_ts")
    if fill_ts < earliest:
        raise EvidenceContractError(
            "causal timing violation: fill_ts must be >= earliest_legal_order_ts "
            f"(fill_ts={row['fill_ts']!r}, earliest_legal_order_ts={row['earliest_legal_order_ts']!r})"
        )
    if exit_ts < fill_ts:
        raise EvidenceContractError("exit_ts must be >= fill_ts")


def validate_arm_evidence(
    members: Sequence[Mapping[str, Any]],
    *,
    evidence_type: str,
    expected_execution_model_id: Optional[str] = None,
) -> list[str]:
    """Validate arm members for the declared evidence type. Returns error strings."""
    errors: list[str] = []
    if evidence_type == EVIDENCE_TYPE_COVERAGE:
        return errors
    if evidence_type != EVIDENCE_TYPE_TRADE_EXECUTION:
        return [f"unknown evidence_type {evidence_type!r}"]
    if expected_execution_model_id is None or not str(expected_execution_model_id).strip():
        return ["missing execution model identity for trade_execution evidence"]
    if not members:
        return ["trade_execution arm produced no members"]
    for index, row in enumerate(members):
        try:
            validate_trade_execution_row(
                row, expected_execution_model_id=expected_execution_model_id
            )
        except EvidenceContractError as exc:
            errors.append(f"member[{index}]: {exc}")
    return errors
