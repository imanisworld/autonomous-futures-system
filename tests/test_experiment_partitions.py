"""U2 chronological partitions and once-only OOS consumption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops import evidence_row as er
from ops import experiment_partitions as partitions
from ops import research_experiment_runner as runner

ROOT = Path(__file__).resolve().parents[1]


def _valid_partitions(**overrides):
    base = {
        "development": {"start": "2024-01-01", "end": "2024-12-31"},
        "validation": {"start": "2025-01-01", "end": "2025-06-30"},
        "untouched_oos": {"start": "2025-07-01", "end": "2025-12-31"},
    }
    base.update(overrides)
    return base


def test_valid_chronological_partitions_freeze():
    frozen = partitions.freeze_chronological_partitions(_valid_partitions())
    assert set(frozen) == {
        "development",
        "validation",
        "untouched_oos",
    }
    assert frozen["development"]["end"] == "2024-12-31"


def test_overlap_development_validation_rejected():
    with pytest.raises(partitions.PartitionContractError, match="development must end before validation"):
        partitions.freeze_chronological_partitions(
            _valid_partitions(
                development={"start": "2024-01-01", "end": "2025-03-01"},
                validation={"start": "2025-01-01", "end": "2025-06-30"},
            )
        )


def test_overlap_validation_oos_rejected():
    with pytest.raises(partitions.PartitionContractError, match="validation must end before untouched_oos"):
        partitions.freeze_chronological_partitions(
            _valid_partitions(
                validation={"start": "2025-01-01", "end": "2025-08-01"},
                untouched_oos={"start": "2025-07-01", "end": "2025-12-31"},
            )
        )


def test_reversed_window_rejected():
    with pytest.raises(partitions.PartitionContractError, match="requires end > start"):
        partitions.freeze_chronological_partitions(
            _valid_partitions(
                development={"start": "2024-12-31", "end": "2024-01-01"},
            )
        )


def test_missing_boundaries_rejected():
    with pytest.raises(partitions.PartitionContractError, match="missing start or end"):
        partitions.freeze_chronological_partitions(
            {
                "development": {"start": "2024-01-01"},
                "validation": {"start": "2025-01-01", "end": "2025-06-30"},
                "untouched_oos": {"start": "2025-07-01", "end": "2025-12-31"},
            }
        )
    with pytest.raises(partitions.PartitionContractError, match="missing required partitions"):
        partitions.freeze_chronological_partitions(
            {
                "development": {"start": "2024-01-01", "end": "2024-12-31"},
                "validation": {"start": "2025-01-01", "end": "2025-06-30"},
            }
        )


def test_evaluation_partition_without_partitions_fails():
    errors = partitions.validate_spec_partitions(
        {"evaluation_partition": "untouched_oos"}
    )
    assert errors and "requires chronological_partitions" in errors[0]


def test_legacy_spec_without_partitions_ok():
    assert partitions.validate_spec_partitions({}) == []
    name, passed, evidence = partitions.partition_check_results({})[0]
    assert name == "chronological_partitions"
    assert passed is True
    assert "omitted" in evidence


def test_oos_receipt_once_only(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    spec = {
        "experiment_id": "E-2026-10-06-partition-demo-01",
        "trial_id": "T-2026-10-06-partition-demo-01",
        "chronological_partitions": _valid_partitions(),
    }
    frozen = partitions.assert_oos_available(root, spec=spec)
    receipt = partitions.build_oos_receipt(
        spec=spec,
        partitions=frozen,
        code_sha="a" * 40,
        runner_version="1.2.0",
        consumed_at="2026-10-06T18:00:00Z",
    )
    partitions.append_oos_receipt(root, receipt)
    with pytest.raises(partitions.PartitionContractError, match="already consumed"):
        partitions.assert_oos_available(root, spec=spec)


def test_distinct_trial_not_blocked_by_other_receipt(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    parts = _valid_partitions()
    first = {
        "experiment_id": "E-2026-10-06-partition-demo-01",
        "trial_id": "T-2026-10-06-partition-demo-01",
        "chronological_partitions": parts,
    }
    second = {
        "experiment_id": "E-2026-10-06-partition-demo-02",
        "trial_id": "T-2026-10-06-partition-demo-02",
        "chronological_partitions": parts,
    }
    frozen = partitions.freeze_chronological_partitions(parts)
    partitions.append_oos_receipt(
        root,
        partitions.build_oos_receipt(
            spec=first,
            partitions=frozen,
            code_sha="a" * 40,
            runner_version="1.2.0",
            consumed_at="2026-10-06T18:00:00Z",
        ),
    )
    # Distinct experiment/trial with the same calendar OOS window remains available.
    partitions.assert_oos_available(root, spec=second)


def _seed_partition_repo(
    root: Path,
    *,
    experiment_id: str = "E-2026-10-06-partition-demo-01",
    trial_id: str = "T-2026-10-06-partition-demo-01",
    with_partitions: bool = True,
    bad_partitions: dict | None = None,
) -> Path:
    (root / "docs/research-experiment-specs").mkdir(parents=True)
    (root / "docs/research-evidence").mkdir(parents=True)
    (root / "docs/research-experiment-spec.schema.json").write_text(
        (ROOT / "docs/research-experiment-spec.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    population = "partition demo population"
    (root / "docs/prereg-partition-demo-2026-10-06.md").write_text("# prereg\n", encoding="utf-8")
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "event": "PLANNED",
                "recorded_at": "2026-10-06T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": "docs/prereg-partition-demo-2026-10-06.md",
                "prereg_commit": None,
                "family_id": "partition_demo",
                "family_label": "partition_demo",
                "population": population,
                "variant_set": {
                    "count": 1,
                    "manifest": "docs/prereg-partition-demo-2026-10-06.md",
                },
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fake = "cccccccccccccccccccccccccccccccccccccccc"
    spec = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "trial_id": trial_id,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-06T00:00:00Z",
        "supersedes": None,
        "hypothesis": "partition contract demo",
        "prereg_path": "docs/prereg-partition-demo-2026-10-06.md",
        "baseline": {"commit_sha": fake, "label": "baseline"},
        "candidate": {"commit_sha": fake, "change": "demo", "label": "candidate"},
        "data": {
            "source": "fixture",
            "window": {"start": "2024-01-01", "end": "2025-12-31"},
            "dataset_id": "fixture://partition-demo",
            "dataset_hash": "a" * 64,
        },
        "population": population,
        "setup_type": "demo_partition",
        "evidence_type": "coverage",
        "timeframe": "5m",
        "changed_variables": [
            {"name": "x", "baseline_value": "a", "candidate_value": "b"}
        ],
        "held_constant": ["population"],
        "execution": {
            "entry_logic": "demo",
            "exit_logic": "demo",
            "stop_logic": "demo",
            "target_logic": "demo",
            "sizing": "1",
            "friction": "none",
        },
        "required_metrics": ["population_size"],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{trial_id}/",
        "variant_manifest": None,
        "notes": "U2 partition fixture",
    }
    if with_partitions:
        spec["chronological_partitions"] = bad_partitions or _valid_partitions()
    spec_path = root / "docs/research-experiment-specs" / f"{experiment_id}.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    runner._git(root, "init")
    runner._git(root, "config", "user.email", "t@example.com")
    runner._git(root, "config", "user.name", "t")
    runner._git(root, "add", ".")
    runner._git(root, "commit", "-m", "seed")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return spec_path


def _coverage_member():
    return {
        "instrument": "MNQ",
        "setup_detected": True,
        "evaluated": True,
        "activated": True,
        "entered": False,
        "completed": False,
        "result": None,
        "setup_type": "demo_partition",
    }


@pytest.fixture(autouse=True)
def _clear_adapters():
    runner.clear_execution_adapters()
    yield
    runner.clear_execution_adapters()


def test_runner_valid_oos_consumes_receipt_once(tmp_path: Path):
    root = tmp_path / "repo"
    spec_path = _seed_partition_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_coverage_member()],
        )

    runner.register_execution_adapter("demo_partition", adapter)
    first = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=True,
        evidence_dir=tmp_path / "out1",
        evaluation_partition="untouched_oos",
    )
    assert first.status == "VALID"
    assert (tmp_path / "out1" / "oos_consumption_receipt.json").is_file()
    assert partitions.oos_ledger_path(root).is_file()

    second = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=True,
        evidence_dir=tmp_path / "out2",
        evaluation_partition="untouched_oos",
    )
    assert second.status == "INVALID"
    assert second.result == "INVALID EXPERIMENT"
    assert any("already consumed" in e for e in second.errors)


def test_invalid_oos_run_does_not_consume_receipt(tmp_path: Path):
    root = tmp_path / "repo"
    spec_path = _seed_partition_repo(
        root,
        bad_partitions=_valid_partitions(
            development={"start": "2024-01-01", "end": "2025-03-01"},
        ),
    )
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_coverage_member()],
        )

    runner.register_execution_adapter("demo_partition", adapter)
    # Schema may still validate windows as strings; runner partition check fails.
    report = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=True,
        evidence_dir=tmp_path / "bad",
        evaluation_partition="untouched_oos",
    )
    assert report.status == "INVALID"
    assert not partitions.oos_ledger_path(root).exists()
    assert not (tmp_path / "bad" / "oos_consumption_receipt.json").exists()


def test_failed_metrics_oos_does_not_consume(tmp_path: Path):
    root = tmp_path / "repo"
    spec_path = _seed_partition_repo(root)
    # Force missing required metric after arms run.
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec["required_metrics"] = ["population_size", "not_a_real_metric"]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_coverage_member()],
        )

    runner.register_execution_adapter("demo_partition", adapter)
    report = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=False,
        evaluation_partition="untouched_oos",
    )
    assert report.status == "INVALID"
    assert not partitions.oos_ledger_path(root).exists()


def test_distinct_approved_trial_not_blocked(tmp_path: Path):
    root = tmp_path / "repo"
    first_path = _seed_partition_repo(root)
    # Second trial in same root: add ledger row + spec.
    second_id = "E-2026-10-06-partition-demo-02"
    second_trial = "T-2026-10-06-partition-demo-02"
    population = "partition demo population"
    ledger = (root / "docs/research-trial-ledger.jsonl").read_text(encoding="utf-8")
    ledger += (
        json.dumps(
            {
                "trial_id": second_trial,
                "event": "PLANNED",
                "recorded_at": "2026-10-06T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": "docs/prereg-partition-demo-2026-10-06.md",
                "prereg_commit": None,
                "family_id": "partition_demo",
                "family_label": "partition_demo",
                "population": population,
                "variant_set": {
                    "count": 1,
                    "manifest": "docs/prereg-partition-demo-2026-10-06.md",
                },
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n"
    )
    (root / "docs/research-trial-ledger.jsonl").write_text(ledger, encoding="utf-8")
    first_spec = json.loads(first_path.read_text(encoding="utf-8"))
    second = dict(first_spec)
    second["experiment_id"] = second_id
    second["trial_id"] = second_trial
    second["evidence_path"] = f"docs/research-evidence/{second_trial}/"
    second_path = root / "docs/research-experiment-specs" / f"{second_id}.json"
    second_path.write_text(json.dumps(second, indent=2) + "\n", encoding="utf-8")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_coverage_member()],
        )

    runner.register_execution_adapter("demo_partition", adapter)
    first = runner.execute_experiment(
        root,
        first_path,
        write_evidence=False,
        evaluation_partition="untouched_oos",
    )
    assert first.status == "VALID"
    other = runner.execute_experiment(
        root,
        second_path,
        write_evidence=False,
        evaluation_partition="untouched_oos",
    )
    assert other.status == "VALID"


def test_legacy_options_spec_still_schema_valid_without_partitions():
    schema = runner.load_schema(ROOT)
    path = (
        ROOT
        / "docs/research-experiment-specs/E-2026-09-25-options-212c-target-geometry-01.json"
    )
    spec = runner.load_spec(path)
    assert runner.schema_validate(spec, schema) == []
    assert partitions.validate_spec_partitions(spec) == []
    assert er.resolve_evidence_type(spec) == er.EVIDENCE_TYPE_COVERAGE


def test_u1_trade_evidence_unchanged_with_partitions_absent(tmp_path: Path):
    """Smoke: trade_execution path still works when partitions omitted."""
    root = tmp_path / "repo"
    (root / "docs/research-experiment-specs").mkdir(parents=True)
    (root / "docs/research-evidence").mkdir(parents=True)
    (root / "docs/research-experiment-spec.schema.json").write_text(
        (ROOT / "docs/research-experiment-spec.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    trial_id = "T-2026-10-06-trade-u2-01"
    population = "demo"
    (root / "docs/prereg-trade.md").write_text("# prereg\n", encoding="utf-8")
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "event": "PLANNED",
                "recorded_at": "2026-10-06T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": "docs/prereg-trade.md",
                "prereg_commit": None,
                "family_id": "trade",
                "family_label": "trade",
                "population": population,
                "variant_set": {"count": 1, "manifest": "docs/prereg-trade.md"},
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assumptions = {
        "entry_fill_model": "next_bar_open",
        "same_bar_ambiguity_rule": "stop_before_target",
        "stop_handling": "touch_inclusive",
        "target_handling": "touch_inclusive",
        "slippage_assumption": "1_tick_adverse",
        "commission": "MNQ_round_turn_2.50_frozen",
        "exchange_broker_fees": "included_in_commission_bundle",
        "sizing_assumptions": "1_contract",
    }
    fake = "cccccccccccccccccccccccccccccccccccccccc"
    dataset_hash = "b" * 64
    fingerprint = f"dataset_hash:{dataset_hash}"
    model_id = er.execution_model_id(assumptions)
    spec = {
        "schema_version": 1,
        "experiment_id": "E-2026-10-06-trade-u2-01",
        "trial_id": trial_id,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-06T00:00:00Z",
        "supersedes": None,
        "hypothesis": "u1 still works",
        "prereg_path": "docs/prereg-trade.md",
        "baseline": {"commit_sha": fake, "label": "b"},
        "candidate": {"commit_sha": fake, "change": "c", "label": "c"},
        "data": {
            "source": "fixture",
            "window": {"start": "2026-01-01", "end": "2026-01-02"},
            "dataset_id": "fixture://trade",
            "dataset_hash": dataset_hash,
        },
        "population": population,
        "setup_type": "demo_trade_u2",
        "evidence_type": "trade_execution",
        "execution_assumptions": assumptions,
        "timeframe": "5m",
        "changed_variables": [{"name": "x", "baseline_value": "a", "candidate_value": "b"}],
        "held_constant": ["population"],
        "execution": {
            "entry_logic": "d",
            "exit_logic": "d",
            "stop_logic": "d",
            "target_logic": "d",
            "sizing": "1",
            "friction": "f",
        },
        "required_metrics": ["population_size", "win_rate"],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{trial_id}/",
        "variant_manifest": None,
        "notes": "u1 smoke",
    }
    spec_path = root / "docs/research-experiment-specs/E-2026-10-06-trade-u2-01.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    runner._git(root, "init")
    runner._git(root, "config", "user.email", "t@example.com")
    runner._git(root, "config", "user.name", "t")
    runner._git(root, "add", ".")
    runner._git(root, "commit", "-m", "seed")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    row = {
        "evidence_type": er.EVIDENCE_TYPE_TRADE_EXECUTION,
        "instrument": "MNQ",
        "strategy_identity": "orb@v1",
        "candidate_signal_id": "c1",
        "signal_ts": "2026-05-23T14:30:00+00:00",
        "decision_ts": "2026-05-23T14:30:00+00:00",
        "earliest_legal_order_ts": "2026-05-23T14:35:00+00:00",
        "intended_entry": 24310.25,
        "direction": "LONG",
        "fill_state": er.FILL_STATE_FILLED,
        "fill_price": 24310.50,
        "fill_ts": "2026-05-23T14:35:00+00:00",
        "stop": 24300.25,
        "target": 24333.25,
        "exit_price": 24333.25,
        "exit_ts": "2026-05-23T14:50:00+00:00",
        "exit_reason": "TARGET_HIT",
        "mae": -2.0,
        "mfe": 23.0,
        "gross_pnl": 46.0,
        "costs_fees": 2.5,
        "net_pnl": 43.5,
        "r_multiple": 2.3,
        "data_fingerprint": fingerprint,
        "execution_model_id": model_id,
    }

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"], commit_sha=head, members=[row]
        )

    runner.register_execution_adapter("demo_trade_u2", adapter)
    report = runner.execute_experiment(
        root, spec_path, write_evidence=False, evaluation_partition=None
    )
    assert report.status == "VALID"
    assert report.baseline_metrics["win_rate"]["count"] == 1


def test_development_rerun_does_not_write_oos_receipt(tmp_path: Path):
    root = tmp_path / "repo"
    spec_path = _seed_partition_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_coverage_member()],
        )

    runner.register_execution_adapter("demo_partition", adapter)
    first = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=False,
        evaluation_partition="development",
    )
    second = runner.execute_experiment(
        root,
        spec_path,
        write_evidence=False,
        evaluation_partition="development",
    )
    assert first.status == "VALID"
    assert second.status == "VALID"
    assert not partitions.oos_ledger_path(root).exists()
