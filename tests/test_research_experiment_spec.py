"""Fail-closed governance checks for approved experiment specifications.

Repository governance only. These tests do not import runtime, broker, risk,
collector, strategy, or execution modules. They do not run experiments.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

import jsonschema
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_REL = "docs/research-experiment-spec.schema.json"
SPECS_DIR = ROOT / "docs" / "research-experiment-specs"
EXAMPLES_DIR = SPECS_DIR / "examples"
LEDGER_REL = "docs/research-trial-ledger.jsonl"
LEDGER = ROOT / LEDGER_REL

EXPERIMENT_RE = re.compile(r"^E-\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-\d{2}$")
TRIAL_RE = re.compile(r"^T-\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-\d{2}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

# Fields frozen after the first APPROVED appearance on origin/main.
FROZEN_AFTER_APPROVED = {
    "schema_version",
    "experiment_id",
    "trial_id",
    "hypothesis",
    "prereg_path",
    "baseline",
    "candidate",
    "data",
    "population",
    "setup_type",
    "timeframe",
    "changed_variables",
    "held_constant",
    "execution",
    "required_metrics",
    "acceptance_criteria",
    "rejection_criteria",
    "evidence_path",
    "variant_manifest",
}

LIVE_STATUSES = {"DRAFT", "APPROVED", "REVOKED", "SUPERSEDED"}
ALLOWED_STATUS_TRANSITIONS = {
    "APPROVED": {"APPROVED", "REVOKED", "SUPERSEDED"},
    "REVOKED": {"REVOKED"},
    "SUPERSEDED": {"SUPERSEDED"},
    "DRAFT": {"DRAFT", "APPROVED", "REVOKED", "SUPERSEDED"},
}


def _load_schema() -> dict:
    path = ROOT / SCHEMA_REL
    assert path.exists(), f"missing {SCHEMA_REL}"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def _iter_spec_files() -> list[Path]:
    if not SPECS_DIR.exists():
        return []
    return sorted(p for p in SPECS_DIR.rglob("*.json") if p.is_file())


def _is_example(path: Path) -> bool:
    try:
        path.relative_to(EXAMPLES_DIR)
        return True
    except ValueError:
        return False


def _ledger_first_rows() -> dict[str, dict]:
    assert LEDGER.exists(), f"missing {LEDGER_REL}"
    first: dict[str, dict] = {}
    for lineno, raw in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        assert raw.strip(), f"{LEDGER_REL}:{lineno}: blank lines are not allowed"
        row = json.loads(raw)
        first.setdefault(row["trial_id"], row)
    return first


def _validate_candidate_arm(spec: dict, label: str) -> None:
    candidate = spec["candidate"]
    sha = candidate.get("commit_sha")
    change = candidate.get("change")
    assert sha is not None or change is not None, (
        f"{label}: candidate must declare commit_sha and/or change"
    )
    if sha is not None:
        assert isinstance(sha, str) and SHA40_RE.fullmatch(sha), (
            f"{label}: candidate.commit_sha must be 40-hex"
        )


def _validate_live_linkage(spec: dict, label: str, first_rows: dict[str, dict]) -> None:
    trial_id = spec["trial_id"]
    assert trial_id in first_rows, f"{label}: trial_id {trial_id} missing from ledger"
    row = first_rows[trial_id]
    assert row["event"] in {"PLANNED", "ADOPTED"}, (
        f"{label}: linked trial first event must be PLANNED or ADOPTED, got {row['event']}"
    )
    assert spec["prereg_path"] == row["prereg_path"], (
        f"{label}: prereg_path must match ledger frozen prereg_path"
    )
    assert spec["population"] == row["population"], (
        f"{label}: population must match ledger frozen population"
    )
    prereg = ROOT / spec["prereg_path"]
    assert prereg.is_file(), f"{label}: prereg file missing: {spec['prereg_path']}"

    expected_evidence = f"docs/research-evidence/{trial_id}/"
    assert spec["evidence_path"] == expected_evidence, (
        f"{label}: evidence_path must be exactly {expected_evidence}"
    )

    variant = row["variant_set"]
    if variant["count"] > 1:
        assert spec.get("variant_manifest") == variant["manifest"], (
            f"{label}: multi-variant trial requires variant_manifest == ledger manifest"
        )
        assert (ROOT / variant["manifest"]).is_file(), (
            f"{label}: ledger variant manifest missing on disk"
        )


def test_experiment_spec_schema_is_draft_2020_12() -> None:
    schema = _load_schema()
    assert schema.get("$schema", "").endswith("draft/2020-12/schema")
    assert schema.get("properties", {}).get("schema_version", {}).get("const") == 1


def test_example_specs_validate_and_stay_in_examples_dir() -> None:
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    examples = list(EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.exists() else []
    assert examples, "at least one EXAMPLE fixture is required"

    for path in examples:
        spec = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(spec), key=lambda e: e.path)
        assert not errors, f"{path}: " + "; ".join(
            f"{'/'.join(str(p) for p in err.path)}: {err.message}" for err in errors
        )
        assert spec["status"] == "EXAMPLE", f"{path}: examples must have status EXAMPLE"
        assert EXPERIMENT_RE.fullmatch(spec["experiment_id"])
        assert TRIAL_RE.fullmatch(spec["trial_id"])
        assert path.stem == spec["experiment_id"], (
            f"{path}: filename stem must equal experiment_id"
        )
        _validate_candidate_arm(spec, str(path))
        assert spec["evidence_path"] == f"docs/research-evidence/{spec['trial_id']}/"


def test_live_specs_validate_schema_and_ledger_linkage() -> None:
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    first_rows = _ledger_first_rows()
    live_files = [p for p in _iter_spec_files() if not _is_example(p)]

    for path in live_files:
        rel = path.relative_to(ROOT).as_posix()
        assert path.parent == SPECS_DIR, (
            f"{rel}: live specs must sit directly under docs/research-experiment-specs/"
        )
        spec = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(spec), key=lambda e: e.path)
        assert not errors, f"{rel}: " + "; ".join(
            f"{'/'.join(str(p) for p in err.path)}: {err.message}" for err in errors
        )
        assert spec["status"] in LIVE_STATUSES, (
            f"{rel}: live specs cannot use status {spec['status']!r}"
        )
        assert path.stem == spec["experiment_id"], (
            f"{rel}: filename stem must equal experiment_id"
        )
        _validate_candidate_arm(spec, rel)
        _validate_live_linkage(spec, rel, first_rows)


def test_approved_specs_are_frozen_against_origin_main_when_present() -> None:
    import subprocess

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    if git("rev-parse", "--verify", "origin/main").returncode != 0:
        return

    for path in _iter_spec_files():
        if _is_example(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        probe = git("cat-file", "-e", f"origin/main:{rel}")
        if probe.returncode != 0:
            continue

        base = json.loads(git("show", f"origin/main:{rel}").stdout)
        current = json.loads(path.read_text(encoding="utf-8"))
        base_status = base.get("status")
        current_status = current.get("status")
        assert base_status in ALLOWED_STATUS_TRANSITIONS, f"{rel}: unknown base status"
        assert current_status in ALLOWED_STATUS_TRANSITIONS[base_status], (
            f"{rel}: illegal status transition {base_status} -> {current_status}"
        )

        if base_status == "APPROVED" or (
            base_status in {"REVOKED", "SUPERSEDED"} and current_status != "DRAFT"
        ):
            for key in sorted(FROZEN_AFTER_APPROVED):
                assert current.get(key) == base.get(key), (
                    f"{rel}: frozen field {key!r} changed after approval"
                )


def test_no_approved_specs_hide_inside_examples() -> None:
    if not EXAMPLES_DIR.exists():
        return
    for path in EXAMPLES_DIR.glob("*.json"):
        spec = json.loads(path.read_text(encoding="utf-8"))
        assert spec.get("status") == "EXAMPLE"
        assert "approved_at" not in spec or spec.get("approved_at") is None


def test_schema_rejects_approved_without_approver_and_empty_candidate() -> None:
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    example = json.loads(
        (EXAMPLES_DIR / "E-2026-09-25-demo-single-variable-01.json").read_text(encoding="utf-8")
    )

    missing_approval = dict(example)
    missing_approval["status"] = "APPROVED"
    missing_approval["approved_by"] = None
    missing_approval["approved_at"] = None
    assert list(validator.iter_errors(missing_approval)), (
        "APPROVED without approved_by/approved_at must fail schema validation"
    )

    empty_candidate = dict(example)
    empty_candidate["candidate"] = {
        "commit_sha": None,
        "change": None,
        "label": "broken",
    }
    assert list(validator.iter_errors(empty_candidate)), (
        "candidate with neither commit_sha nor change must fail schema validation"
    )
