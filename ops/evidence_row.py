"""Canonical evidence envelope and typed evidence rows for the Experiment Runner.

Research/evidence plumbing only. This module does not place orders, change
strategy/risk/broker behavior, or migrate historical evidence.

Evidence types are explicit. Non-trade experiments (for example options
coverage/geometry) use ``coverage`` and are not promotion-eligible. New specs
must declare ``evidence_type``; only frozen legacy options experiment IDs may
omit it.
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

# Frozen pre-U1 options experiment IDs may omit evidence_type and are treated as
# coverage. New specs must declare evidence_type explicitly.
LEGACY_COVERAGE_EXPERIMENT_IDS = frozenset(
    {
        "E-2026-09-25-options-212c-target-geometry-01",
        "E-2026-10-02-options-212c-floor-outcome-01",
        "E-2026-10-04-options-212c-floor-outcome-02",
        "E-2026-10-04-options-212c-floor-factor-01",
        "E-2026-10-04-options-212c-preentry-factors-01",
    }
)

FILL_STATE_FILLED = "FILLED"
FILL_STATE_NO_FILL = "NO_FILL"
KNOWN_FILL_STATES = frozenset({FILL_STATE_FILLED, FILL_STATE_NO_FILL})
KNOWN_DIRECTIONS = frozenset({"LONG", "SHORT"})

# Legacy funnel keys must not be used to score trade_execution evidence.
LEGACY_TRADE_SCORING_KEYS = frozenset({"entered", "completed", "result"})

# Outcome economics forbidden on NO_FILL rows.
NO_FILL_FORBIDDEN_FIELDS = (
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
    "promotion_eligible",
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
    "direction",
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

TRADE_LOOKALIKE_KEYS = frozenset(
    {
        "fill_state",
        "fill_price",
        "fill_ts",
        "earliest_legal_order_ts",
        "r_multiple",
        "gross_pnl",
        "net_pnl",
        "costs_fees",
    }
)

PNL_TOLERANCE = 0.01


class EvidenceContractError(ValueError):
    """Fail-closed evidence contract violation."""


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def resolve_evidence_type(spec: Mapping[str, Any]) -> str:
    """Return the declared evidence type.

    New specs must declare ``evidence_type``. Only frozen legacy options
    experiment IDs may omit it (treated as coverage).
    """
    raw = spec.get("evidence_type")
    experiment_id = str(spec.get("experiment_id") or "").strip()
    if raw is None or raw == "":
        if experiment_id in LEGACY_COVERAGE_EXPERIMENT_IDS:
            return EVIDENCE_TYPE_COVERAGE
        raise EvidenceContractError(
            "evidence_type is required; omit only for frozen legacy coverage "
            f"experiment IDs ({sorted(LEGACY_COVERAGE_EXPERIMENT_IDS)})"
        )
    text = str(raw).strip()
    if text not in KNOWN_EVIDENCE_TYPES:
        raise EvidenceContractError(
            f"unknown evidence_type {text!r}; expected one of {sorted(KNOWN_EVIDENCE_TYPES)}"
        )
    return text


def freeze_execution_assumptions(assumptions: Mapping[str, Any]) -> dict[str, Any]:
    """Return a frozen, deterministic execution-assumption bundle."""
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


def require_code_sha(code_sha: Any) -> str:
    text = str(code_sha or "").strip()
    if not text or text.lower() == "unknown":
        raise EvidenceContractError("code_sha must be a real commit SHA, not 'unknown'")
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text.lower()):
        raise EvidenceContractError(f"code_sha must be a 40-char hex SHA; got {code_sha!r}")
    return text.lower()


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


def prior_exposure_identity_from_spec(spec: Mapping[str, Any]) -> Optional[str]:
    """Return prior-exposure identity only from an explicit prior-exposure field."""
    for key in ("prior_exposure_identity", "prior_exposure", "prior_exposed"):
        value = spec.get(key)
        if _present(value):
            return str(value)
    return None


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
        "code_sha": require_code_sha(code_sha),
        "data_identity": data_identity_from_spec(spec),
        "runner_version": runner_version,
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "promotion_eligible": resolved_type == EVIDENCE_TYPE_TRADE_EXECUTION,
    }
    if resolved_type == EVIDENCE_TYPE_COVERAGE:
        envelope["promotion_eligible"] = False
    if _present(spec.get("prereg_path")):
        envelope["preregistration_identity"] = str(spec["prereg_path"])
    prior = prior_exposure_identity_from_spec(spec)
    if prior is not None:
        envelope["prior_exposure_identity"] = prior
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
    validate_common_envelope(
        envelope, require_execution_model=(resolved_type == EVIDENCE_TYPE_TRADE_EXECUTION)
    )
    return envelope


def validate_common_envelope(
    envelope: Mapping[str, Any],
    *,
    require_execution_model: bool = False,
) -> None:
    for key in COMMON_ENVELOPE_REQUIRED:
        if key not in envelope or not _present(envelope.get(key)) and envelope.get(key) is not False:
            if key == "promotion_eligible" and envelope.get(key) is False:
                continue
            if key not in envelope or (
                key != "promotion_eligible" and not _present(envelope.get(key))
            ):
                raise EvidenceContractError(f"evidence envelope missing required field: {key}")
    if "promotion_eligible" not in envelope or not isinstance(
        envelope.get("promotion_eligible"), bool
    ):
        raise EvidenceContractError("evidence envelope missing boolean promotion_eligible")
    if str(envelope["schema_version"]) != EVIDENCE_ROW_SCHEMA_VERSION:
        raise EvidenceContractError(
            f"unsupported evidence schema_version {envelope['schema_version']!r}"
        )
    evidence_type = str(envelope["evidence_type"])
    if evidence_type not in KNOWN_EVIDENCE_TYPES:
        raise EvidenceContractError(f"unknown evidence_type {evidence_type!r}")
    if evidence_type == EVIDENCE_TYPE_COVERAGE and envelope.get("promotion_eligible") is not False:
        raise EvidenceContractError("coverage evidence must set promotion_eligible=false")
    if require_execution_model and not _present(envelope.get("execution_model_id")):
        raise EvidenceContractError("evidence envelope missing execution_model_id")
    require_code_sha(envelope["code_sha"])
    parse_ts(envelope["generated_at"], field_name="generated_at")


def _looks_like_trade_row(row: Mapping[str, Any]) -> bool:
    return any(_present(row.get(key)) for key in TRADE_LOOKALIKE_KEYS)


def validate_trade_execution_row(
    row: Mapping[str, Any],
    *,
    expected_execution_model_id: Optional[str] = None,
    expected_data_fingerprint: Optional[str] = None,
) -> None:
    """Fail-closed validation for one promotion-quality futures trade row."""
    if not isinstance(row, Mapping):
        raise EvidenceContractError("trade_execution row must be an object")
    if str(row.get("evidence_type") or EVIDENCE_TYPE_TRADE_EXECUTION) != EVIDENCE_TYPE_TRADE_EXECUTION:
        raise EvidenceContractError(
            f"evidence-type/schema mismatch: expected {EVIDENCE_TYPE_TRADE_EXECUTION}"
        )

    legacy = sorted(key for key in LEGACY_TRADE_SCORING_KEYS if key in row)
    if legacy:
        raise EvidenceContractError(
            "trade_execution rejects legacy scoring keys: " + ", ".join(legacy)
        )

    for key in TRADE_IDENTITY_REQUIRED:
        _require_present(row, key)

    if expected_data_fingerprint is not None:
        if str(row["data_fingerprint"]) != str(expected_data_fingerprint):
            raise EvidenceContractError(
                "data_fingerprint must match the experiment/spec dataset identity"
            )

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

    entry = _require_finite(row["intended_entry"], field_name="intended_entry")
    stop = _require_finite(row["stop"], field_name="stop")
    target = _require_finite(row["target"], field_name="target")

    if fill_state == FILL_STATE_NO_FILL:
        for forbidden in NO_FILL_FORBIDDEN_FIELDS:
            if _present(row.get(forbidden)):
                raise EvidenceContractError(
                    f"NO_FILL row must not carry outcome field {forbidden}"
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

    direction = str(row["direction"]).strip().upper()
    if direction not in KNOWN_DIRECTIONS:
        raise EvidenceContractError(
            f"direction must be LONG or SHORT; got {row['direction']!r}"
        )
    if direction == "LONG" and not (stop < entry < target):
        raise EvidenceContractError(
            "LONG bracket requires stop < intended_entry < target"
        )
    if direction == "SHORT" and not (target < entry < stop):
        raise EvidenceContractError(
            "SHORT bracket requires target < intended_entry < stop"
        )

    for key in (
        "fill_price",
        "exit_price",
        "mae",
        "mfe",
        "gross_pnl",
        "costs_fees",
        "net_pnl",
        "r_multiple",
    ):
        _require_finite(row[key], field_name=key)

    costs = float(row["costs_fees"])
    if costs < 0:
        raise EvidenceContractError("costs_fees must be >= 0")
    gross = float(row["gross_pnl"])
    net = float(row["net_pnl"])
    if abs(net - (gross - costs)) > PNL_TOLERANCE:
        raise EvidenceContractError(
            "net_pnl must equal gross_pnl - costs_fees within $0.01"
        )
    if float(row["mfe"]) < 0:
        raise EvidenceContractError("mfe must be >= 0")
    if float(row["mae"]) > 0:
        raise EvidenceContractError("mae must be <= 0")

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
    expected_data_fingerprint: Optional[str] = None,
) -> list[str]:
    """Validate arm members for the declared evidence type. Returns error strings."""
    errors: list[str] = []
    if evidence_type == EVIDENCE_TYPE_COVERAGE:
        for index, row in enumerate(members):
            if isinstance(row, Mapping) and _looks_like_trade_row(row):
                errors.append(
                    f"member[{index}]: coverage evidence cannot masquerade as "
                    "trade_execution (trade-like fields present)"
                )
        return errors
    if evidence_type != EVIDENCE_TYPE_TRADE_EXECUTION:
        return [f"unknown evidence_type {evidence_type!r}"]
    if expected_execution_model_id is None or not str(expected_execution_model_id).strip():
        return ["missing execution model identity for trade_execution evidence"]
    if expected_data_fingerprint is None or not str(expected_data_fingerprint).strip():
        return ["missing dataset identity for trade_execution evidence"]
    if not members:
        return ["trade_execution arm produced no members"]
    for index, row in enumerate(members):
        try:
            validate_trade_execution_row(
                row,
                expected_execution_model_id=expected_execution_model_id,
                expected_data_fingerprint=expected_data_fingerprint,
            )
        except EvidenceContractError as exc:
            errors.append(f"member[{index}]: {exc}")
    return errors


def _safe_div(num: float, den: float) -> Optional[float]:
    if den == 0:
        return None
    return num / den


def compute_trade_execution_metrics(
    members: Sequence[Mapping[str, Any]],
    required: Sequence[str],
) -> dict[str, Any]:
    """Score trade_execution arms from canonical FILLED fields only.

    Uses ``r_multiple`` on FILLED rows. Legacy funnel keys
    (``entered`` / ``completed`` / ``result``) are rejected. NO_FILL rows never
    contribute to win/loss/expectancy scoring.
    """
    for index, row in enumerate(members):
        if not isinstance(row, Mapping):
            raise EvidenceContractError(f"member[{index}]: trade row must be an object")
        legacy = sorted(key for key in LEGACY_TRADE_SCORING_KEYS if key in row)
        if legacy:
            raise EvidenceContractError(
                f"member[{index}]: trade_execution rejects legacy scoring keys: "
                + ", ".join(legacy)
            )

    filled = [
        m
        for m in members
        if str(m.get("fill_state") or "").strip().upper() == FILL_STATE_FILLED
    ]
    no_fill = [
        m
        for m in members
        if str(m.get("fill_state") or "").strip().upper() == FILL_STATE_NO_FILL
    ]
    results = [float(m["r_multiple"]) for m in filled]
    winners = [r for r in results if r > 0]
    losers = [r for r in results if r < 0]
    mae_vals = [float(m["mae"]) for m in filled]
    mfe_vals = [float(m["mfe"]) for m in filled]
    target_hits = sum(
        1 for m in filled if str(m.get("exit_reason") or "").upper() in {"TARGET", "TARGET_HIT"}
    )
    stop_hits = sum(
        1 for m in filled if str(m.get("exit_reason") or "").upper() in {"STOP", "STOP_HIT"}
    )

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in results:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    metrics: dict[str, Any] = {
        "population_size": {"count": len(members)},
        "filled_trades": {"count": len(filled), "of": len(members)},
        "no_fill_count": {"count": len(no_fill), "of": len(members)},
        "completed_trades": {"count": len(filled), "of": len(members)},
        "win_rate": {
            "count": len(winners),
            "rate": _safe_div(len(winners), len(results)),
            "of": len(results),
            "unit": "r_multiple",
        },
        "loss_rate": {
            "count": len(losers),
            "rate": _safe_div(len(losers), len(results)),
            "of": len(results),
            "unit": "r_multiple",
        },
        "expectancy": {
            "value": (sum(results) / len(results)) if results else None,
            "of": len(results),
            "unit": "r_multiple",
        },
        "median_result": {
            "value": (sorted(results)[len(results) // 2] if results else None),
            "of": len(results),
            "unit": "r_multiple",
        },
        "total_result": {
            "value": sum(results) if results else 0.0,
            "of": len(results),
            "unit": "r_multiple",
        },
        "average_winner": {
            "value": (sum(winners) / len(winners)) if winners else None,
            "of": len(winners),
            "unit": "r_multiple",
        },
        "average_loser": {
            "value": (sum(losers) / len(losers)) if losers else None,
            "of": len(losers),
            "unit": "r_multiple",
        },
        "payoff_ratio": {
            "value": (
                abs((sum(winners) / len(winners)) / (sum(losers) / len(losers)))
                if winners and losers
                else None
            ),
            "winners": len(winners),
            "losers": len(losers),
        },
        "mae": {
            "mean": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
            "of": len(mae_vals),
        },
        "mfe": {
            "mean": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
            "of": len(mfe_vals),
        },
        "target_hit_rate": {
            "count": target_hits,
            "rate": _safe_div(target_hits, len(filled)),
            "of": len(filled),
        },
        "stop_hit_rate": {
            "count": stop_hits,
            "rate": _safe_div(stop_hits, len(filled)),
            "of": len(filled),
        },
        "drawdown": {"max_drawdown": max_dd, "of": len(results), "unit": "r_multiple"},
        "exposure": {
            "completed_trades": len(filled),
            "population": len(members),
            "rate": _safe_div(len(filled), len(members)),
        },
    }
    missing = [name for name in required if name not in metrics]
    metrics["_missing_required"] = missing
    return metrics
