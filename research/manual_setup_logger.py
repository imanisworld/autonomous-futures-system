"""Append-only, observe-only logger for manually identified strategy setups.

This module has no strategy detection, risk, broker, or execution imports.  It
records what an operator observed and what later happened to the *original*
bracket.  Rows are research evidence and cannot authorize a trade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


SCHEMA_VERSION = "manual_setup.v1"
EVIDENCE_FILENAME = "manual_setup_observations.jsonl"
CONTEXT_FILENAME = "strategy_context_observations.jsonl"
INSTRUMENTS = {"MNQ", "MES"}
DIRECTIONS = {"LONG", "SHORT"}
DECISIONS = {"TAKEN", "SKIPPED"}
SHADOW_RESULTS = {
    "STOP_FIRST",
    "T1_FIRST",
    "T2_FIRST",
    "NEITHER_BY_CUTOFF",
}


class ValidationError(ValueError):
    """The submitted observation is incomplete or internally inconsistent."""


class DuplicateRecordError(ValidationError):
    """An immutable record with the same identity already exists."""


def evidence_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / EVIDENCE_FILENAME


def _utc_iso(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValidationError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValidationError(f"{field} must be greater than zero")
    return result


def _optional_number(value: Any, field: str) -> float | None:
    return None if value is None else _number(value, field)


def _positive_integer(value: Any, field: str) -> int:
    number = _number(value, field, positive=True)
    if not number.is_integer():
        raise ValidationError(f"{field} must be a whole number")
    return int(number)


def _boolean(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValidationError(f"{field} must be boolean")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def setup_id_for(payload: dict[str, Any]) -> str:
    """Return the stable identity of one strategy signal.

    Price levels are deliberately excluded: corrections to manually transcribed
    levels must not create a second apparent signal in the research sample.
    """
    identity = {
        "strategy": _text(payload.get("strategy"), "strategy").lower(),
        "contract_version": _text(
            payload.get("contract_version"), "contract_version"
        ).lower(),
        "signal_timestamp": _utc_iso(
            payload.get("signal_timestamp"), "signal_timestamp"
        ),
        "instrument": _text(payload.get("instrument"), "instrument").upper(),
        "direction": _text(payload.get("direction"), "direction").upper(),
    }
    return "ms_" + _sha256(identity)[:24]


def _validate_bracket(raw: Any, direction: str) -> dict[str, float | None]:
    if not isinstance(raw, dict):
        raise ValidationError("original_bracket must be an object")
    bracket = {
        "entry": _number(raw.get("entry"), "original_bracket.entry", positive=True),
        "stop": _number(raw.get("stop"), "original_bracket.stop", positive=True),
        "t1": _number(raw.get("t1"), "original_bracket.t1", positive=True),
        "t2": _optional_number(raw.get("t2"), "original_bracket.t2"),
    }
    entry, stop, t1, t2 = (
        bracket["entry"],
        bracket["stop"],
        bracket["t1"],
        bracket["t2"],
    )
    if direction == "LONG":
        valid = stop < entry < t1 and (t2 is None or t2 >= t1)
    else:
        valid = stop > entry > t1 and (t2 is None or t2 <= t1)
    if not valid:
        raise ValidationError(
            "original bracket levels are inconsistent with the trade direction"
        )
    return bracket


def _missing_snapshot(
    name: str,
    *,
    source: str = "not_provided",
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "observed_at": None,
        "source": source,
        "data": None,
        "missing_reason": reason or f"{name}_not_provided",
    }


def _validate_snapshot(raw: Any, name: str, signal_ts: str) -> dict[str, Any]:
    if raw is None:
        return _missing_snapshot(name)
    if not isinstance(raw, dict):
        raise ValidationError(f"context.{name} must be an object")
    available = raw.get("available")
    if not isinstance(available, bool):
        raise ValidationError(f"context.{name}.available must be boolean")
    if not available:
        reason = _text(
            raw.get("missing_reason", f"{name}_unavailable"),
            f"context.{name}.missing_reason",
        )
        return {
            "available": False,
            "observed_at": None,
            "source": _text(
                raw.get("source", "operator"), f"context.{name}.source"
            ),
            "data": None,
            "missing_reason": reason,
        }
    observed_at = _utc_iso(raw.get("observed_at"), f"context.{name}.observed_at")
    source = _text(raw.get("source"), f"context.{name}.source")
    data = raw.get("data")
    if not isinstance(data, dict) or not data:
        raise ValidationError(
            f"context.{name}.data must be a non-empty object when available"
        )
    signal_dt = datetime.fromisoformat(signal_ts)
    observed_dt = datetime.fromisoformat(observed_at)
    return {
        "available": True,
        "observed_at": observed_at,
        "source": source,
        "data": data,
        "age_seconds_at_signal": (signal_dt - observed_dt).total_seconds(),
        "causal_at_signal": observed_dt <= signal_dt,
        # Exact equality is the only freshness assertion made here.  No
        # undocumented maximum-age threshold is invented.
        "exact_signal_timestamp": observed_at == signal_ts,
    }


def _context_from_payload(raw: Any, signal_ts: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValidationError("context must be an object")
    return {
        "zone": _validate_snapshot(raw.get("zone"), "zone", signal_ts),
        "vwap": _validate_snapshot(raw.get("vwap"), "vwap", signal_ts),
        "signa": _validate_snapshot(raw.get("signa"), "signa", signal_ts),
        "joined_observer": None,
    }


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open() as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    f"{path}:{line_number} contains invalid JSON"
                ) from exc
            if isinstance(row, dict):
                yield row


def join_exact_context(
    *,
    context_path: str | Path,
    instrument: str,
    signal_timestamp: str,
) -> dict[str, Any]:
    """Load exactly one observer row matching instrument and signal timestamp.

    Nearest-row joins are intentionally forbidden: without an authoritative
    maximum age, they could make stale context look contemporaneous.
    """
    path = Path(context_path)
    matches = []
    for row in _iter_jsonl(path):
        if str(row.get("instrument", "")).upper() != instrument:
            continue
        try:
            row_ts = _utc_iso(row.get("timestamp"), "observer.timestamp")
        except ValidationError:
            continue
        if row_ts == signal_timestamp:
            matches.append(row)
    if not matches:
        raise ValidationError(
            "no exact instrument/timestamp context observation was found"
        )
    if len(matches) != 1:
        raise ValidationError(
            "multiple exact context observations were found; resolve the duplicate first"
        )
    row = matches[0]
    zone_data = row.get("supply_demand_confluence")
    vwap_data = row.get("vwap")
    return {
        "zone": (
            {
                "available": True,
                "observed_at": signal_timestamp,
                "source": "strategy_context_observer",
                "data": zone_data,
                "age_seconds_at_signal": 0.0,
                "causal_at_signal": True,
                "exact_signal_timestamp": True,
            }
            if isinstance(zone_data, dict) and zone_data.get("available")
            else _missing_snapshot(
                "zone",
                source="strategy_context_observer",
                reason="observer_reported_zone_unavailable",
            )
        ),
        "vwap": (
            {
                "available": True,
                "observed_at": signal_timestamp,
                "source": "strategy_context_observer",
                "data": vwap_data,
                "age_seconds_at_signal": 0.0,
                "causal_at_signal": True,
                "exact_signal_timestamp": True,
            }
            if isinstance(vwap_data, dict) and vwap_data
            else _missing_snapshot(
                "vwap",
                source="strategy_context_observer",
                reason="observer_reported_vwap_unavailable",
            )
        ),
        # The existing observer does not record Signa.  Never derive it from
        # another field or make a second live API call during manual logging.
        "signa": _missing_snapshot(
            "signa",
            source="strategy_context_observer",
            reason="observer_does_not_record_signa",
        ),
        "joined_observer": {
            "path": str(path),
            "row_sha256": _sha256(row),
            "timestamp": signal_timestamp,
            "match": "exact_instrument_and_timestamp",
        },
    }


def _merge_context(
    supplied: dict[str, Any], joined: dict[str, Any] | None
) -> dict[str, Any]:
    if joined is None:
        return supplied
    merged = dict(joined)
    # Explicitly observed operator values may fill fields the observer lacks,
    # but may not overwrite data actually present in the joined row.
    for name in ("zone", "vwap", "signa"):
        if not merged[name]["available"] and supplied[name]["available"]:
            merged[name] = supplied[name]
    return merged


def _base_record(payload: dict[str, Any], *, context_path: str | Path | None) -> dict:
    strategy = _text(payload.get("strategy"), "strategy")
    contract_version = _text(payload.get("contract_version"), "contract_version")
    signal_ts = _utc_iso(payload.get("signal_timestamp"), "signal_timestamp")
    instrument = _text(payload.get("instrument"), "instrument").upper()
    direction = _text(payload.get("direction"), "direction").upper()
    if instrument not in INSTRUMENTS:
        raise ValidationError(f"instrument must be one of {sorted(INSTRUMENTS)}")
    if direction not in DIRECTIONS:
        raise ValidationError(f"direction must be one of {sorted(DIRECTIONS)}")
    decision = _text(payload.get("decision"), "decision").upper()
    if decision not in DECISIONS:
        raise ValidationError(f"decision must be one of {sorted(DECISIONS)}")
    skip_reason = payload.get("skip_reason")
    if decision == "SKIPPED":
        skip_reason = _text(skip_reason, "skip_reason")
    elif skip_reason not in (None, ""):
        raise ValidationError("skip_reason must be empty when decision is TAKEN")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValidationError("provenance must be an object")
    supplied_context = _context_from_payload(payload.get("context"), signal_ts)
    joined = (
        join_exact_context(
            context_path=context_path,
            instrument=instrument,
            signal_timestamp=signal_ts,
        )
        if context_path is not None
        else None
    )
    canonical_payload = {
        "strategy": strategy,
        "contract_version": contract_version,
        "signal_timestamp": signal_ts,
        "instrument": instrument,
        "direction": direction,
    }
    setup_id = setup_id_for(canonical_payload)
    provided_id = payload.get("setup_id")
    if provided_id is not None and provided_id != setup_id:
        raise ValidationError("provided setup_id does not match the stable computed id")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "manual_setup",
        "setup_id": setup_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "observation_only": True,
        "gate_authoritative": False,
        "execution_authorized": False,
        **canonical_payload,
        "original_bracket": _validate_bracket(
            payload.get("original_bracket"), direction
        ),
        "operator_decision": {
            "status": decision,
            "skip_reason": skip_reason or None,
        },
        "actual_execution": {
            "status": "PENDING" if decision == "TAKEN" else "NOT_TAKEN",
            "fill": None,
            "costs": None,
        },
        "context": _merge_context(supplied_context, joined),
        "shadow_outcome": {
            "status": "PENDING",
            "result": None,
            "original_bracket_preserved": True,
        },
        "provenance": {
            "source": _text(provenance.get("source"), "provenance.source"),
            "recorded_by": _text(
                provenance.get("recorded_by"), "provenance.recorded_by"
            ),
            "notes": provenance.get("notes"),
        },
    }


def _locked_append(path: Path, record: dict[str, Any], dedupe_key: tuple) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            for line in handle:
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (existing.get("kind"), existing.get("setup_id")) == dedupe_key:
                    raise DuplicateRecordError(
                        f"{dedupe_key[0]} already exists for {dedupe_key[1]}"
                    )
            handle.seek(0, 2)
            handle.write(_canonical_json(record) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_setup(
    payload: dict[str, Any],
    *,
    log_dir: str | Path,
    context_path: str | Path | None = None,
) -> dict[str, Any]:
    record = _base_record(payload, context_path=context_path)
    _locked_append(
        evidence_path(log_dir),
        record,
        (record["kind"], record["setup_id"]),
    )
    return record


def _find_setup(path: Path, setup_id: str) -> dict[str, Any]:
    matches = [
        row
        for row in _iter_jsonl(path)
        if row.get("kind") == "manual_setup" and row.get("setup_id") == setup_id
    ]
    if len(matches) != 1:
        raise ValidationError(
            f"expected exactly one manual_setup row for {setup_id}; found {len(matches)}"
        )
    return matches[0]


def _validate_execution(raw: Any, taken: bool) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("actual_execution must be an object")
    if not taken:
        if raw.get("fill") not in (None, {}):
            raise ValidationError("a skipped setup cannot have an actual fill")
        return {"status": "NOT_TAKEN", "fill": None, "costs": None}
    fill = raw.get("fill")
    costs = raw.get("costs")
    if not isinstance(fill, dict):
        raise ValidationError("actual_execution.fill is required for a taken setup")
    if not isinstance(costs, dict):
        raise ValidationError("actual_execution.costs is required for a taken setup")
    missing_costs = [
        name for name in ("commission", "fees", "slippage") if name not in costs
    ]
    if missing_costs:
        raise ValidationError(
            "actual_execution.costs must explicitly include "
            + ", ".join(missing_costs)
        )
    commission = _number(
        costs.get("commission"), "actual_execution.costs.commission"
    )
    fees = _number(costs.get("fees"), "actual_execution.costs.fees")
    slippage = _number(costs.get("slippage"), "actual_execution.costs.slippage")
    if min(commission, fees, slippage) < 0:
        raise ValidationError("actual execution cost components cannot be negative")
    return {
        "status": "FILLED",
        "fill": {
            "price": _number(fill.get("price"), "actual_execution.fill.price", positive=True),
            "contracts": _positive_integer(
                fill.get("contracts"),
                "actual_execution.fill.contracts",
            ),
            "filled_at": _utc_iso(
                fill.get("filled_at"), "actual_execution.fill.filled_at"
            ),
        },
        "costs": {
            "commission": commission,
            "fees": fees,
            "slippage": slippage,
            "total": commission + fees + slippage,
            "currency": _text(
                costs.get("currency", "USD"), "actual_execution.costs.currency"
            ).upper(),
        },
    }


def _validate_shadow(
    raw: Any, original_bracket: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationError("shadow_outcome must be an object")
    result = _text(raw.get("result"), "shadow_outcome.result").upper()
    if result not in SHADOW_RESULTS:
        raise ValidationError(
            f"shadow_outcome.result must be one of {sorted(SHADOW_RESULTS)}"
        )
    resolved_at = raw.get("resolved_at")
    resolved_at = _utc_iso(resolved_at, "shadow_outcome.resolved_at")
    t1_hit = _boolean(raw.get("t1_hit"), "shadow_outcome.t1_hit")
    t2_hit = _boolean(raw.get("t2_hit"), "shadow_outcome.t2_hit")
    stop_hit = _boolean(raw.get("stop_hit"), "shadow_outcome.stop_hit")
    if t2_hit and original_bracket.get("t2") is None:
        raise ValidationError("shadow_outcome.t2_hit cannot be true without a T2")
    if t2_hit and not t1_hit:
        raise ValidationError("shadow_outcome.t2_hit requires t1_hit on this bracket")
    if result == "STOP_FIRST" and (not stop_hit or t1_hit or t2_hit):
        raise ValidationError(
            "STOP_FIRST requires stop_hit=true before either target was hit"
        )
    if result == "T1_FIRST" and (not t1_hit or t2_hit):
        raise ValidationError(
            "T1_FIRST requires t1_hit=true and t2_hit=false"
        )
    if result == "T2_FIRST" and (not t1_hit or not t2_hit):
        raise ValidationError(
            "T2_FIRST requires both t1_hit=true and t2_hit=true"
        )
    if result == "NEITHER_BY_CUTOFF" and (t1_hit or t2_hit or stop_hit):
        raise ValidationError(
            "NEITHER_BY_CUTOFF requires all hit flags to be false"
        )
    return {
        "status": "RESOLVED",
        "result": result,
        "resolved_at": resolved_at,
        "exit_price": _optional_number(
            raw.get("exit_price"), "shadow_outcome.exit_price"
        ),
        "t1_hit": t1_hit,
        "t2_hit": t2_hit,
        "stop_hit": stop_hit,
        "notes": raw.get("notes"),
    }


def record_resolution(
    payload: dict[str, Any], *, log_dir: str | Path
) -> dict[str, Any]:
    setup_id = _text(payload.get("setup_id"), "setup_id")
    path = evidence_path(log_dir)
    setup = _find_setup(path, setup_id)
    taken = setup["operator_decision"]["status"] == "TAKEN"
    actual_execution = _validate_execution(payload.get("actual_execution"), taken)
    shadow_outcome = _validate_shadow(
        payload.get("shadow_outcome"), setup["original_bracket"]
    )
    signal_dt = datetime.fromisoformat(setup["signal_timestamp"])
    resolved_dt = datetime.fromisoformat(shadow_outcome["resolved_at"])
    if resolved_dt < signal_dt:
        raise ValidationError(
            "shadow_outcome.resolved_at cannot precede signal_timestamp"
        )
    if taken:
        filled_dt = datetime.fromisoformat(actual_execution["fill"]["filled_at"])
        if filled_dt < signal_dt:
            raise ValidationError(
                "actual_execution.fill.filled_at cannot precede signal_timestamp"
            )
        if filled_dt > resolved_dt:
            raise ValidationError(
                "actual_execution.fill.filled_at cannot follow "
                "shadow_outcome.resolved_at"
            )
    record = {
        "schema_version": SCHEMA_VERSION,
        "kind": "manual_setup_resolution",
        "setup_id": setup_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "observation_only": True,
        "gate_authoritative": False,
        "execution_authorized": False,
        "actual_execution": actual_execution,
        "shadow_outcome": {
            **shadow_outcome,
            "original_bracket": setup["original_bracket"],
            "original_bracket_sha256": _sha256(setup["original_bracket"]),
        },
        "provenance": {
            "source": _text(
                (payload.get("provenance") or {}).get("source"),
                "provenance.source",
            ),
            "recorded_by": _text(
                (payload.get("provenance") or {}).get("recorded_by"),
                "provenance.recorded_by",
            ),
        },
    }
    _locked_append(path, record, (record["kind"], setup_id))
    return record


def _load_object(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"could not read valid JSON object from {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("input JSON must contain one object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Observe-only manual strategy setup logger"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("record", help="append one manual setup")
    setup.add_argument("--input", required=True, help="setup JSON object")
    setup.add_argument("--log-dir", default="logs")
    setup.add_argument(
        "--context-log",
        help=(
            "strategy_context_observations.jsonl; requires an exact "
            "instrument/timestamp match"
        ),
    )
    resolution = sub.add_parser(
        "resolve", help="append actual fill/costs and original-bracket outcome"
    )
    resolution.add_argument("--input", required=True, help="resolution JSON object")
    resolution.add_argument("--log-dir", default="logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = _load_object(args.input)
        if args.command == "record":
            row = record_setup(
                payload, log_dir=args.log_dir, context_path=args.context_log
            )
        else:
            row = record_resolution(payload, log_dir=args.log_dir)
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(_canonical_json(row))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
