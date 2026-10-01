from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ops.research_experiment_runner import ExperimentContext
from ops.research_experiment_adapters.options_212c_target_geometry import (
    AdapterPreconditionError,
    EXPECTED_POPULATION,
    EXPECTED_SYMBOLS,
    canonical_population_body,
    episode_identity,
    population_manifest_sha256,
    run_options_212c_target_geometry,
    verify_population_binding,
    _select_population,
)


def _view(geometry: str) -> dict:
    return {
        "view": "first_sight",
        "geometry": geometry,
        "outcome": "UNRESOLVED_AT_CLOSE",
        "target_1_r": 1.0 if geometry == "floor" else 0.5,
        "mfe_r": 1.25,
        "mae_r": -0.4,
        "close_r": 0.2,
        "flags": [],
    }


def _rows() -> list[dict]:
    symbols = sorted(EXPECTED_SYMBOLS)
    rows = []
    for i in range(EXPECTED_POPULATION):
        symbol = symbols[i % len(symbols)]
        day = 9 + (i % 5)
        baseline_bucket = "WOULD_OTHERWISE_QUALIFY" if i == 0 else "TARGET_GEOMETRY_REJECTED"
        candidate_bucket = "WOULD_OTHERWISE_QUALIFY" if i < 5 else "MARKET_ALIGNMENT_REJECTED"
        hour = 14 + (i // len(symbols))
        minute = 30 if i % 2 else 0
        sight_minute = minute + 17
        rows.append(
            {
                "outcome_id": "OPTIONS_COVERAGE_OUTCOMES",
                "outcome_version": "out-v0.1",
                "reducer_version": "ep-v0.1",
                "symbol": symbol,
                "session_date": f"2026-09-{day:02d}",
                "family": "STRAT_212_CONTINUATION",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "first_bar_start": f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00+00:00",
                "first_sight_at": f"2026-09-{day:02d}T{hour:02d}:{sight_minute:02d}:57+00:00",
                "first_sight_price": 100.0 + i,
                "first_sight_after_close": False,
                "alignment_ok": candidate_bucket != "MARKET_ALIGNMENT_REJECTED",
                "alignment_failures": "" if candidate_bucket != "MARKET_ALIGNMENT_REJECTED" else "spy",
                "gate_bucket_nearest": baseline_bucket,
                "gate_bucket_floor": candidate_bucket,
                "views": [_view("nearest"), _view("floor")],
            }
        )
    return rows


def _write_dataset(path: Path, rows: list[dict]) -> str:
    payload = {
        "summary": {
            "date_from": "2026-09-09",
            "date_to": "2026-09-15",
            "provider_errors": {},
            "sessions_missing": [],
        },
        "episodes": rows,
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ctx(tmp_path: Path, *, arm: str, dataset_hash: str) -> ExperimentContext:
    spec = {
        "_runner_arm": arm,
        "setup_type": "options_212c_target_geometry",
        "baseline": {"commit_sha": "a" * 40},
        "candidate": {"commit_sha": "b" * 40},
        "data": {
            "source": "fixture",
            "window": {"start": "2026-09-09", "end": "2026-09-15"},
            "dataset_id": "dataset.json",
            "dataset_hash": dataset_hash,
        },
        "changed_variables": [
            {
                "name": "target_geometry_rule",
                "baseline_value": "nearest_v1",
                "candidate_value": "floor_ge1r",
            }
        ],
    }
    return ExperimentContext(
        root=tmp_path,
        spec=spec,
        spec_path=tmp_path / "spec.json",
        spec_hash="c" * 64,
    )


def test_adapter_maps_same_59_members_and_only_changes_geometry(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())

    baseline = run_options_212c_target_geometry(_ctx(tmp_path, arm="baseline", dataset_hash=digest))
    candidate = run_options_212c_target_geometry(_ctx(tmp_path, arm="candidate", dataset_hash=digest))

    assert len(baseline.members) == EXPECTED_POPULATION
    assert len(candidate.members) == EXPECTED_POPULATION
    assert [m["episode_id"] for m in baseline.members] == [
        m["episode_id"] for m in candidate.members
    ]
    assert sum(1 for m in baseline.members if m["activated"]) == 1
    assert sum(1 for m in candidate.members if m["activated"]) == 5
    assert all(not m["entered"] and not m["completed"] and m["result"] is None for m in baseline.members)
    assert baseline.raw["geometry"] == "nearest"
    assert candidate.raw["geometry"] == "floor"
    assert baseline.commit_sha == "a" * 40
    assert candidate.commit_sha == "b" * 40


def test_adapter_fails_closed_on_population_drift(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows()[:-1])

    with pytest.raises(AdapterPreconditionError, match="population drift"):
        run_options_212c_target_geometry(_ctx(tmp_path, arm="baseline", dataset_hash=digest))


def test_adapter_fails_closed_on_dataset_hash_mismatch(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    _write_dataset(dataset, _rows())

    with pytest.raises(AdapterPreconditionError, match="SHA-256 mismatch"):
        run_options_212c_target_geometry(
            _ctx(tmp_path, arm="baseline", dataset_hash="0" * 64)
        )


def test_adapter_requires_dataset_hash_before_run(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    _write_dataset(dataset, _rows())

    with pytest.raises(AdapterPreconditionError, match="dataset_hash is required"):
        run_options_212c_target_geometry(
            _ctx(tmp_path, arm="baseline", dataset_hash="")
        )


def _binding_spec(dataset: Path, digest: str, **overrides: object) -> dict:
    data = {
        "dataset_hash": digest,
        "dataset_path": str(dataset),
        "dataset_size_bytes": dataset.stat().st_size,
        "population_count": EXPECTED_POPULATION,
        "window": {"start": "2026-09-09", "end": "2026-09-15"},
    }
    data.update(overrides)
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    rows = _select_population(payload)
    body = canonical_population_body(
        source_path=str(data["dataset_path"]),
        source_size_bytes=int(data["dataset_size_bytes"]),
        source_sha256=str(data["dataset_hash"]),
        episode_ids=[episode_identity(row) for row in rows],
    )
    data["population_manifest_sha256"] = population_manifest_sha256(body)
    return {"data": data}


def test_binding_rejects_mismatched_source_size(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())
    spec = _binding_spec(dataset, digest, dataset_size_bytes=dataset.stat().st_size - 1)

    with pytest.raises(AdapterPreconditionError, match="dataset size mismatch"):
        verify_population_binding(dataset, spec)


def test_binding_rejects_mismatched_population_hash(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())
    spec = _binding_spec(dataset, digest)
    spec["data"]["population_manifest_sha256"] = "ab" * 32

    with pytest.raises(AdapterPreconditionError, match="population manifest SHA-256 mismatch"):
        verify_population_binding(dataset, spec)


def test_binding_rejects_mismatched_population_count(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())
    spec = _binding_spec(dataset, digest, population_count=EXPECTED_POPULATION - 1)

    with pytest.raises(AdapterPreconditionError, match="population count mismatch"):
        verify_population_binding(dataset, spec)


def test_binding_accepts_matching_manifest_without_scoring(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())
    spec = _binding_spec(dataset, digest)
    first = verify_population_binding(dataset, spec)
    second = verify_population_binding(dataset, spec)

    assert first["population_count"] == EXPECTED_POPULATION
    assert first["episode_ids"] == second["episode_ids"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["manifest_sha256"] == spec["data"]["population_manifest_sha256"]
    assert "activated" not in first


def test_adapter_fails_closed_on_changed_variable_drift(tmp_path: Path):
    dataset = tmp_path / "dataset.json"
    digest = _write_dataset(dataset, _rows())
    ctx = _ctx(tmp_path, arm="baseline", dataset_hash=digest)
    ctx.spec["changed_variables"][0]["candidate_value"] = "invented_rule"

    with pytest.raises(AdapterPreconditionError, match="changed_variables drift"):
        run_options_212c_target_geometry(ctx)
