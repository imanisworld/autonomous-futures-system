"""Execution adapter for the frozen options 30m 2-1-2 continuation geometry ablation.

This adapter does not discover a population, optimize a target, or simulate
option P&L. It reads the existing coverage-outcomes evidence, selects the
pre-registered 59-member structural cohort using only symbol/date/family, and
maps the two already-recorded gate paths (nearest vs >=1R floor) into the
generic AFS Experiment Runner member shape.

The adapter is intentionally narrow. Any population, version, date, arm, or
dataset-hash drift fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ops.research_experiment_runner import ArmRawResult, ExperimentContext

SETUP_TYPE = "options_212c_target_geometry"
FAMILY = "STRAT_212_CONTINUATION"
DATE_FROM = "2026-09-09"
DATE_TO = "2026-09-15"
EXPECTED_POPULATION = 59
OUTCOME_VERSION = "out-v0.1"
REDUCER_VERSION = "ep-v0.1"
DATASET_ENV = "AFS_OPTIONS_212C_TARGET_GEOMETRY_DATASET"
EXPECTED_SYMBOLS = frozenset(
    {
        "AAPL",
        "AMZN",
        "BAC",
        "COIN",
        "GE",
        "GOOGL",
        "INTC",
        "IWM",
        "JPM",
        "MRK",
        "MSFT",
        "NFLX",
        "NVDA",
        "PLTR",
        "QQQ",
        "SPY",
        "TLT",
        "TSLA",
        "WMT",
        "XOM",
    }
)
ARM_GEOMETRY = {"baseline": "nearest", "candidate": "floor"}
ARM_BUCKET = {"baseline": "gate_bucket_nearest", "candidate": "gate_bucket_floor"}
ARM_COMMIT = {"baseline": "baseline", "candidate": "candidate"}


class AdapterPreconditionError(RuntimeError):
    """The registered experiment cannot be executed reproducibly."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_path(ctx: ExperimentContext) -> Path:
    override = os.environ.get(DATASET_ENV)
    if override:
        return Path(override).expanduser().resolve()

    dataset_id = str((ctx.spec.get("data") or {}).get("dataset_id") or "")
    if dataset_id.startswith("file://"):
        return Path(dataset_id[7:]).expanduser().resolve()

    candidate = (ctx.root / dataset_id).resolve()
    if dataset_id and candidate.is_file():
        return candidate

    raise AdapterPreconditionError(
        f"dataset not found; set {DATASET_ENV} to the frozen coverage outcomes JSON "
        f"(dataset_id={dataset_id!r})"
    )


def _validate_spec_contract(ctx: ExperimentContext) -> str:
    spec = ctx.spec
    arm = str(spec.get("_runner_arm") or "")
    if arm not in ARM_GEOMETRY:
        raise AdapterPreconditionError(f"unsupported runner arm {arm!r}")

    changed = spec.get("changed_variables") or []
    expected = {
        "name": "target_geometry_rule",
        "baseline_value": "nearest_v1",
        "candidate_value": "floor_ge1r",
    }
    if changed != [expected]:
        raise AdapterPreconditionError(
            "changed_variables drift: adapter requires only target_geometry_rule "
            "nearest_v1 -> floor_ge1r"
        )

    data = spec.get("data") or {}
    window = data.get("window") or {}
    if window.get("start") != DATE_FROM or window.get("end") != DATE_TO:
        raise AdapterPreconditionError(
            f"window drift: expected {DATE_FROM}..{DATE_TO}, got "
            f"{window.get('start')}..{window.get('end')}"
        )

    dataset_hash = data.get("dataset_hash")
    if not dataset_hash:
        raise AdapterPreconditionError(
            "dataset_hash is required by this adapter before an APPROVED run"
        )
    return arm


def _load_payload(path: Path, expected_hash: str) -> dict[str, Any]:
    if not path.is_file():
        raise AdapterPreconditionError(f"dataset file missing: {path}")
    actual = _sha256(path)
    if actual != expected_hash:
        raise AdapterPreconditionError(
            f"dataset SHA-256 mismatch: actual={actual} expected={expected_hash}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterPreconditionError(f"dataset unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("episodes"), list):
        raise AdapterPreconditionError("dataset must be an object with episodes[]")
    return payload


def _select_population(payload: dict[str, Any]) -> list[dict[str, Any]]:
    summary = payload.get("summary") or {}
    if summary.get("date_from") != DATE_FROM or summary.get("date_to") != DATE_TO:
        raise AdapterPreconditionError(
            f"aggregate range drift: {summary.get('date_from')}..{summary.get('date_to')}"
        )
    if summary.get("provider_errors"):
        raise AdapterPreconditionError("aggregate carries provider_errors")
    missing = summary.get("sessions_missing") or []
    if missing:
        raise AdapterPreconditionError(f"aggregate has missing sessions: {missing}")

    selected: list[dict[str, Any]] = []
    for row in payload["episodes"]:
        if not isinstance(row, dict):
            raise AdapterPreconditionError("episode row is not an object")
        if row.get("family") != FAMILY:
            continue
        if row.get("symbol") not in EXPECTED_SYMBOLS:
            continue
        session = str(row.get("session_date") or "")
        if not (DATE_FROM <= session <= DATE_TO):
            continue
        selected.append(row)

    if len(selected) != EXPECTED_POPULATION:
        raise AdapterPreconditionError(
            f"population drift: selected {len(selected)} {FAMILY} episodes; "
            f"expected {EXPECTED_POPULATION}"
        )

    identities = [
        (
            row.get("symbol"),
            row.get("session_date"),
            row.get("direction"),
            row.get("first_bar_start"),
            row.get("family"),
        )
        for row in selected
    ]
    if len(set(identities)) != EXPECTED_POPULATION:
        raise AdapterPreconditionError("population contains duplicate episode identities")

    for row in selected:
        if row.get("outcome_version") != OUTCOME_VERSION:
            raise AdapterPreconditionError(
                f"outcome version drift: {row.get('outcome_version')!r}"
            )
        if row.get("reducer_version") != REDUCER_VERSION:
            raise AdapterPreconditionError(
                f"reducer version drift: {row.get('reducer_version')!r}"
            )
        if row.get("gate_bucket_nearest") is None or row.get("gate_bucket_floor") is None:
            raise AdapterPreconditionError("episode missing geometry gate bucket")
        views = row.get("views")
        if not isinstance(views, list):
            raise AdapterPreconditionError("episode missing views[]")
        have = {
            (view.get("view"), view.get("geometry"))
            for view in views
            if isinstance(view, dict)
        }
        if ("first_sight", "nearest") not in have or ("first_sight", "floor") not in have:
            raise AdapterPreconditionError(
                "episode missing first_sight nearest/floor path views"
            )

    selected.sort(
        key=lambda row: (
            str(row.get("session_date")),
            str(row.get("symbol")),
            str(row.get("first_bar_start")),
            str(row.get("direction")),
        )
    )
    return selected


def _view(row: dict[str, Any], geometry: str) -> dict[str, Any]:
    for item in row.get("views") or []:
        if (
            isinstance(item, dict)
            and item.get("view") == "first_sight"
            and item.get("geometry") == geometry
        ):
            return item
    raise AdapterPreconditionError(
        f"first_sight/{geometry} view missing for {row.get('symbol')} "
        f"{row.get('session_date')} {row.get('first_bar_start')}"
    )


def _time_bucket(value: Any) -> str:
    try:
        return datetime.fromisoformat(str(value)).strftime("%H:%M")
    except ValueError:
        return "UNKNOWN"


def _member(
    row: dict[str, Any],
    *,
    arm: str,
    setup_type: str,
) -> dict[str, Any]:
    geometry = ARM_GEOMETRY[arm]
    bucket = str(row[ARM_BUCKET[arm]])
    activated = bucket == "WOULD_OTHERWISE_QUALIFY"
    path = _view(row, geometry)

    # This is a coverage-gate experiment, not a synthetic option trade.
    # Preserve path observations in raw evidence but intentionally do not mark
    # entries/completions or manufacture realized P&L.
    return {
        "instrument": row.get("symbol"),
        "timestamp": row.get("first_sight_at") or row.get("first_bar_start"),
        "setup_detected": True,
        "evaluated": True,
        "rejection_reason": None if activated else bucket,
        "activated": activated,
        "entered": False,
        "completed": False,
        "entry_price": row.get("first_sight_price"),
        "exit_price": None,
        "exit_reason": None,
        "result": None,
        "mae": None,
        "mfe": None,
        "hold_time_seconds": None,
        "timeframe": "30m",
        "time_of_day": _time_bucket(row.get("first_bar_start")),
        "regime": "UNKNOWN",
        "setup_type": setup_type,
        "episode_id": "|".join(
            str(row.get(key) or "")
            for key in ("symbol", "session_date", "direction", "first_bar_start", "family")
        ),
        "geometry": geometry,
        "gate_bucket": bucket,
        "alignment_ok": row.get("alignment_ok"),
        "alignment_failures": row.get("alignment_failures"),
        "first_sight_after_close": row.get("first_sight_after_close"),
        "path_observation": {
            "outcome": path.get("outcome"),
            "target_1_r": path.get("target_1_r"),
            "mfe_r": path.get("mfe_r"),
            "mae_r": path.get("mae_r"),
            "close_r": path.get("close_r"),
            "flags": path.get("flags") or [],
        },
    }


def run_options_212c_target_geometry(ctx: ExperimentContext) -> ArmRawResult:
    """Return one normalized arm over the exact frozen 59-episode population."""
    arm = _validate_spec_contract(ctx)
    dataset_hash = str(ctx.spec["data"]["dataset_hash"])
    path = _dataset_path(ctx)
    payload = _load_payload(path, dataset_hash)
    rows = _select_population(payload)

    members = [
        _member(row, arm=arm, setup_type=str(ctx.spec.get("setup_type") or SETUP_TYPE))
        for row in rows
    ]
    commit_block = ctx.spec[ARM_COMMIT[arm]]
    commit_sha = commit_block.get("commit_sha")

    return ArmRawResult(
        arm=arm,
        commit_sha=commit_sha,
        members=members,
        raw={
            "dataset_sha256": dataset_hash,
            "dataset_file": path.name,
            "population_count": len(rows),
            "family": FAMILY,
            "date_from": DATE_FROM,
            "date_to": DATE_TO,
            "symbols": sorted(EXPECTED_SYMBOLS),
            "geometry": ARM_GEOMETRY[arm],
            "coverage_only": True,
            "no_option_pnl_claim": True,
        },
        warnings=[
            "Coverage-only target-geometry ablation: no option P&L, expectancy, "
            "or production-readiness claim is produced."
        ],
    )
