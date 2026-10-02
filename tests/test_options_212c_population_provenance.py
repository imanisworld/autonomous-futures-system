"""Provenance binding for the draft 2-1-2 target-geometry experiment.

These tests do not approve the spec and do not score either arm.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ops.research_experiment_adapters.options_212c_target_geometry import (
    population_manifest_sha256,
    verify_population_binding,
)
from ops.research_experiment_runner import discover_specs, run_validation

ROOT = Path(__file__).resolve().parents[1]
SPEC_REL = "docs/research-experiment-specs/E-2026-09-25-options-212c-target-geometry-01.json"
MANIFEST_REL = (
    "docs/research-population-manifests/"
    "T-2026-09-25-prereg-options-212c-target-geometry-2026-09-25-01.json"
)
TRIAL_MANIFEST_REL = (
    "docs/research-trial-manifests/"
    "T-2026-09-25-prereg-options-212c-target-geometry-2026-09-25-01.json"
)
FROZEN_SHA = "1963db73bccf0fd366eaaa077bb4e9582ed453ff220f1c5e789961096f3f113c"
FROZEN_SIZE = 35_024_516


def _spec() -> dict:
    return json.loads((ROOT / SPEC_REL).read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads((ROOT / MANIFEST_REL).read_text(encoding="utf-8"))


def test_verified_population_manifest_matches_its_canonical_hash() -> None:
    spec = _spec()
    manifest = _manifest()
    recomputed = population_manifest_sha256(manifest["canonical"])
    again = population_manifest_sha256(json.loads(json.dumps(manifest["canonical"])))

    assert spec["status"] == "DRAFT"
    assert spec["approved_by"] is None
    assert spec["approved_at"] is None
    assert manifest["manifest_sha256"] == recomputed == again
    assert manifest["canonical"]["population_count"] == 59
    assert len(manifest["canonical"]["episode_ids"]) == 59
    assert len(set(manifest["canonical"]["episode_ids"])) == 59
    assert spec["data"]["dataset_hash"] == FROZEN_SHA
    assert spec["data"]["dataset_size_bytes"] == FROZEN_SIZE
    assert spec["data"]["population_count"] == 59
    assert spec["data"]["population_manifest_sha256"] == recomputed
    assert spec["data"]["population_manifest"] == MANIFEST_REL
    assert spec["data"]["selection_implementation"].endswith("_select_population")
    assert spec["data"]["selection_version"] == manifest["canonical"]["selection"]["version"]
    assert spec["changed_variables"] == [
        {
            "name": "target_geometry_rule",
            "baseline_value": "nearest_v1",
            "candidate_value": "floor_ge1r",
        }
    ]

    trial = json.loads((ROOT / TRIAL_MANIFEST_REL).read_text(encoding="utf-8"))
    assert trial["results_sha256"] is None


def test_draft_spec_validates_and_stays_out_of_approved_discovery() -> None:
    report = run_validation(ROOT, ROOT / SPEC_REL, for_execution=False)
    assert report.status == "VALID"

    blocked = run_validation(ROOT, ROOT / SPEC_REL, for_execution=True)
    assert blocked.status == "BLOCKED"

    approved = discover_specs(ROOT)
    assert approved == []
    visible = discover_specs(ROOT, status=None)
    assert any(path.name == "E-2026-09-25-options-212c-target-geometry-01.json" for path in visible)


def test_frozen_file_reproduces_manifest_when_present() -> None:
    raw_path = os.environ.get(
        "AFS_OPTIONS_212C_TARGET_GEOMETRY_DATASET",
        "/tmp/outcomes_2026-09-09_2026-09-15.json",
    )
    path = Path(raw_path)
    if not path.is_file():
        pytest.skip("frozen outcomes file is not on this machine")

    spec = _spec()
    first = verify_population_binding(path, spec)
    second = verify_population_binding(path, spec)
    assert first["population_count"] == 59
    assert first["episode_ids"] == second["episode_ids"]
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["manifest_sha256"] == spec["data"]["population_manifest_sha256"]
    assert "activated" not in first
