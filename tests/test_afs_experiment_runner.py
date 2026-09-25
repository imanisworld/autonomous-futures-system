"""Fail-closed tests for the AFS Experiment Runner.

Does not invent live experiments. Uses the EXAMPLE fixture and temporary
synthetic specs only.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ops import research_experiment_runner as runner

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "docs"
    / "research-experiment-specs"
    / "examples"
    / "E-2026-09-25-demo-single-variable-01.json"
)


@pytest.fixture(autouse=True)
def _clear_adapters():
    runner.clear_execution_adapters()
    yield
    runner.clear_execution_adapters()


def _member(**overrides):
    base = {
        "instrument": "MNQ",
        "timestamp": "2026-01-02T15:00:00Z",
        "setup_detected": True,
        "evaluated": True,
        "rejection_reason": None,
        "activated": True,
        "entered": True,
        "completed": True,
        "entry_price": 100.0,
        "exit_price": 101.0,
        "exit_reason": "target",
        "result": 1.0,
        "mae": -0.2,
        "mfe": 1.2,
        "hold_time_seconds": 300,
        "timeframe": "5m",
        "time_of_day": "10",
        "regime": "UNKNOWN",
        "setup_type": "demo_setup",
    }
    base.update(overrides)
    return base


def test_example_fixture_exists():
    assert EXAMPLE.is_file()


def test_validate_example_structurally_valid_but_not_executable():
    report = runner.run_validation(ROOT, EXAMPLE, for_execution=False)
    assert report.status == "VALID"
    assert report.experiment_id == "E-2026-09-25-demo-single-variable-01"

    blocked = runner.run_validation(ROOT, EXAMPLE, for_execution=True)
    assert blocked.status == "BLOCKED"
    assert blocked.result == "BLOCKED"
    assert any(c.name == "approved_status" and not c.passed for c in blocked.integrity_checks)


def test_discover_approved_is_empty_without_live_specs():
    found = runner.discover_specs(ROOT, status="APPROVED", include_examples=False)
    assert found == []


def test_discover_examples_when_requested():
    found = runner.discover_specs(ROOT, status="EXAMPLE", include_examples=True)
    assert EXAMPLE in found


def test_execute_example_is_blocked_and_writes_nothing(tmp_path: Path):
    evidence = tmp_path / "evidence"
    report = runner.execute_experiment(
        ROOT,
        EXAMPLE,
        write_evidence=True,
        evidence_dir=evidence,
    )
    assert report.status == "BLOCKED"
    assert not evidence.exists()


def test_execute_approved_without_adapter_is_blocked(tmp_path: Path):
    root = tmp_path / "repo"
    _seed_mini_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec_path = root / "docs" / "research-experiment-specs" / "E-2026-09-25-mini-01.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    report = runner.execute_experiment(root, spec_path, write_evidence=False)
    assert report.status == "BLOCKED"
    assert any(c.name == "execution_adapter" and not c.passed for c in report.integrity_checks)


def test_execute_with_adapter_writes_evidence_and_metrics(tmp_path: Path):
    root = tmp_path / "repo"
    _seed_mini_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec_path = root / "docs" / "research-experiment-specs" / "E-2026-09-25-mini-01.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        arm = ctx.spec["_runner_arm"]
        members = [
            _member(result=1.0 if arm == "baseline" else 1.5),
            _member(result=-0.5, exit_reason="stop"),
        ]
        return runner.ArmRawResult(arm=arm, commit_sha=head, members=members, raw={"arm": arm})

    runner.register_execution_adapter("demo_setup", adapter)
    evidence = tmp_path / "out"
    report = runner.execute_experiment(
        root, spec_path, write_evidence=True, evidence_dir=evidence
    )
    assert report.status == "VALID"
    assert report.baseline_metrics is not None
    assert report.candidate_metrics is not None
    assert report.delta is not None
    assert (evidence / "runner_report.json").is_file()
    assert (evidence / "baseline_raw.json").is_file()
    assert (evidence / "candidate_raw.json").is_file()
    assert report.result == "INCONCLUSIVE"  # never auto-promotes


def test_metrics_include_counts_beside_rates():
    members = [
        _member(result=2.0),
        _member(result=-1.0, exit_reason="stop"),
        _member(activated=False, entered=False, completed=False, result=None),
    ]
    metrics = runner.compute_metrics(
        members,
        ["win_rate", "expectancy", "completed_trades", "activation_count"],
    )
    assert metrics["win_rate"]["count"] == 1
    assert metrics["win_rate"]["of"] == 2
    assert metrics["completed_trades"]["count"] == 2
    assert metrics["activation_count"]["count"] == 2
    assert metrics["activation_count"]["of"] == 3


def test_schema_invalid_spec_is_invalid(tmp_path: Path):
    root = tmp_path / "repo"
    _seed_mini_repo(root)
    bad = root / "docs" / "research-experiment-specs" / "E-2026-09-25-mini-01.json"
    payload = json.loads(bad.read_text(encoding="utf-8"))
    del payload["hypothesis"]
    bad.write_text(json.dumps(payload), encoding="utf-8")
    report = runner.run_validation(root, bad, for_execution=False)
    assert report.status == "INVALID"
    assert report.result == "INVALID EXPERIMENT"


def _seed_mini_repo(root: Path) -> None:
    """Minimal repo-shaped tree for linkage + execution tests."""
    (root / "docs" / "research-experiment-specs").mkdir(parents=True)
    (root / "docs" / "research-evidence").mkdir(parents=True)
    shutil.copy(ROOT / "docs" / "research-experiment-spec.schema.json", root / "docs")
    prereg = root / "docs" / "prereg-mini.md"
    prereg.write_text("# mini\ntrial_id: T-2026-09-25-prereg-mini-01\n", encoding="utf-8")

    trial_id = "T-2026-09-25-prereg-mini-01"
    population = "MNQ; mini fixture population"
    ledger = {
        "trial_id": trial_id,
        "event": "PLANNED",
        "recorded_at": "2026-09-25T00:00:00Z",
        "recorded_by": "test",
        "prereg_path": "docs/prereg-mini.md",
        "prereg_commit": None,
        "family_id": "mini_family",
        "family_label": "mini",
        "population": population,
        "variant_set": {"count": 1, "manifest": "docs/prereg-mini.md"},
        "prior_exposed": "none",
        "attempts_in_family_before": 0,
        "pre_ledger_attempts": "UNKNOWN",
    }
    (root / "docs" / "research-trial-ledger.jsonl").write_text(
        json.dumps(ledger) + "\n", encoding="utf-8"
    )

    # Resolve HEAD for SHAs after git init.
    runner._git(root, "init")
    runner._git(root, "config", "user.email", "test@example.com")
    runner._git(root, "config", "user.name", "test")
    # Placeholder SHAs replaced by tests that need resolvable commits.
    fake = "cccccccccccccccccccccccccccccccccccccccc"
    spec = {
        "schema_version": 1,
        "experiment_id": "E-2026-09-25-mini-01",
        "trial_id": trial_id,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-09-25T12:00:00Z",
        "supersedes": null_safe(),
        "hypothesis": "mini hypothesis",
        "prereg_path": "docs/prereg-mini.md",
        "baseline": {"commit_sha": fake, "label": "base"},
        "candidate": {"commit_sha": fake, "change": "one variable", "label": "cand"},
        "data": {
            "source": "fixture",
            "window": {"start": "2026-01-01", "end": "2026-01-31"},
            "dataset_id": "demo://mini",
            "dataset_hash": None,
        },
        "population": population,
        "setup_type": "demo_setup",
        "timeframe": "5m",
        "changed_variables": [
            {"name": "entry_delay_bars", "baseline_value": 0, "candidate_value": 1}
        ],
        "held_constant": ["population", "friction"],
        "execution": {
            "entry_logic": "e",
            "exit_logic": "x",
            "stop_logic": "s",
            "target_logic": "t",
            "sizing": "1",
            "friction": "none",
        },
        "required_metrics": [
            "population_size",
            "completed_trades",
            "win_rate",
            "expectancy",
            "total_result",
        ],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{trial_id}/",
        "variant_manifest": None,
        "notes": "synthetic test only",
    }
    spec_path = root / "docs" / "research-experiment-specs" / "E-2026-09-25-mini-01.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    runner._git(root, "add", ".")
    runner._git(root, "commit", "-m", "seed")


def null_safe():
    return None
