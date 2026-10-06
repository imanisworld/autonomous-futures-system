"""Typed evidence contract for the Experiment Runner (U1)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ops import evidence_row as er
from ops import research_experiment_runner as runner
from ops.research_experiment_adapters import register_builtin_adapters

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "ops" / "evidence_row.py"
ADAPTER_PATH = ROOT / "ops" / "research_experiment_adapters" / "options_212c_target_geometry.py"

_FORBIDDEN = {
    "execution.tradovate_broker",
    "execution.paper_broker",
    "execution.broker",
    "risk.risk_engine",
}


@pytest.fixture(autouse=True)
def _clear_adapters():
    runner.clear_execution_adapters()
    yield
    runner.clear_execution_adapters()


def _assumptions(**overrides):
    base = {
        "entry_fill_model": "next_bar_open",
        "same_bar_ambiguity_rule": "stop_before_target",
        "stop_handling": "touch_inclusive",
        "target_handling": "touch_inclusive",
        "slippage_assumption": "1_tick_adverse",
        "commission": "MNQ_round_turn_2.50_frozen",
        "exchange_broker_fees": "included_in_commission_bundle",
        "sizing_assumptions": "1_contract",
    }
    base.update(overrides)
    return base


def _trade_row(**overrides):
    model_id = er.execution_model_id(_assumptions())
    row = {
        "evidence_type": er.EVIDENCE_TYPE_TRADE_EXECUTION,
        "instrument": "MNQ",
        "strategy_identity": "orb_reclaim@v1",
        "candidate_signal_id": "cand-1",
        "signal_ts": "2026-05-23T14:30:00+00:00",
        "decision_ts": "2026-05-23T14:30:00+00:00",
        "earliest_legal_order_ts": "2026-05-23T14:35:00+00:00",
        "intended_entry": 24310.25,
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
        "session": "new_york",
        "regime": "TRENDING",
        "data_fingerprint": "dataset_hash:abc",
        "execution_model_id": model_id,
    }
    row.update(overrides)
    return row


def test_module_has_no_broker_or_risk_imports():
    tree = ast.parse(EVIDENCE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not (imported & _FORBIDDEN)


def test_options_adapter_still_has_no_broker_authority():
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    assert not (imported & _FORBIDDEN)
    assert "Tradovate" not in ADAPTER_PATH.read_text(encoding="utf-8")
    assert "execute_bracket" not in ADAPTER_PATH.read_text(encoding="utf-8")


def test_valid_trade_execution_row_passes():
    er.validate_trade_execution_row(_trade_row())


def test_fill_exactly_at_earliest_legal_order_ts_is_allowed():
    er.validate_trade_execution_row(
        _trade_row(
            earliest_legal_order_ts="2026-05-23T14:35:00+00:00",
            fill_ts="2026-05-23T14:35:00+00:00",
        )
    )


def test_fill_before_earliest_legal_order_ts_fails_closed():
    with pytest.raises(er.EvidenceContractError, match="causal timing violation"):
        er.validate_trade_execution_row(
            _trade_row(
                earliest_legal_order_ts="2026-05-23T14:35:00+00:00",
                fill_ts="2026-05-23T14:34:59+00:00",
            )
        )


def test_missing_required_trade_field_fails_closed():
    row = _trade_row()
    del row["earliest_legal_order_ts"]
    with pytest.raises(er.EvidenceContractError, match="earliest_legal_order_ts"):
        er.validate_trade_execution_row(row)


def test_missing_execution_model_identity_fails_closed():
    errors = er.validate_arm_evidence(
        [_trade_row()],
        evidence_type=er.EVIDENCE_TYPE_TRADE_EXECUTION,
        expected_execution_model_id=None,
    )
    assert errors and "execution model identity" in errors[0]


def test_missing_data_fingerprint_fails_closed():
    row = _trade_row()
    del row["data_fingerprint"]
    with pytest.raises(er.EvidenceContractError, match="data_fingerprint"):
        er.validate_trade_execution_row(row)


def test_no_fill_row_without_fabricated_fill_fields():
    row = _trade_row(
        fill_state=er.FILL_STATE_NO_FILL,
        reject_reason="ENTRY_NOT_FILLED",
    )
    for key in (
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
    ):
        row.pop(key, None)
    er.validate_trade_execution_row(row)


def test_no_fill_with_fabricated_fill_price_fails():
    row = _trade_row(
        fill_state=er.FILL_STATE_NO_FILL,
        reject_reason="ENTRY_NOT_FILLED",
        fill_price=24310.5,
        fill_ts=None,
    )
    row.pop("fill_ts", None)
    with pytest.raises(er.EvidenceContractError, match="must not fabricate fill_price"):
        er.validate_trade_execution_row(row)


def test_execution_model_id_includes_commission_and_slippage():
    a = _assumptions()
    b = _assumptions(commission="MNQ_round_turn_3.00_frozen")
    c = _assumptions(slippage_assumption="2_tick_adverse")
    assert er.execution_model_id(a) != er.execution_model_id(b)
    assert er.execution_model_id(a) != er.execution_model_id(c)
    assert er.execution_model_id(a) == er.execution_model_id(dict(a))


def test_changing_frozen_assumption_changes_execution_model_id():
    before = er.execution_model_id(_assumptions())
    after = er.execution_model_id(_assumptions(entry_fill_model="decision_close_plus_1"))
    assert before != after


def test_non_finite_pnl_fails_closed():
    with pytest.raises(er.EvidenceContractError, match="net_pnl"):
        er.validate_trade_execution_row(_trade_row(net_pnl=float("nan")))


def test_common_envelope_requires_trial_and_data_identity():
    with pytest.raises(er.EvidenceContractError, match="trial_id"):
        er.build_common_envelope(
            spec={
                "experiment_id": "E-2026-10-06-demo-01",
                "setup_type": "demo",
                "data": {"dataset_id": "x", "source": "s", "window": {"start": "a", "end": "b"}},
            },
            code_sha="a" * 40,
            runner_version="1.1.0",
        )
    with pytest.raises(er.EvidenceContractError, match="data identity"):
        er.build_common_envelope(
            spec={
                "experiment_id": "E-2026-10-06-demo-01",
                "trial_id": "T-2026-10-06-demo-01",
                "setup_type": "demo",
                "data": {},
            },
            code_sha="a" * 40,
            runner_version="1.1.0",
        )


def test_coverage_default_skips_trade_fields():
    assert er.resolve_evidence_type({}) == er.EVIDENCE_TYPE_COVERAGE
    assert er.validate_arm_evidence(
        [{"setup_detected": True, "entered": False, "completed": False, "result": None}],
        evidence_type=er.EVIDENCE_TYPE_COVERAGE,
    ) == []


def test_runner_trade_execution_fixture_validates(tmp_path: Path):
    root = tmp_path / "repo"
    _seed_trade_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    model_id = er.execution_model_id(_assumptions())

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"],
            commit_sha=head,
            members=[_trade_row(execution_model_id=model_id, candidate_signal_id="c-1")],
        )

    runner.clear_execution_adapters()
    runner.register_execution_adapter("demo_trade", adapter)
    report = runner.execute_experiment(
        root,
        root / "docs/research-experiment-specs/E-2026-10-06-trade-demo-01.json",
        write_evidence=True,
        evidence_dir=tmp_path / "out",
    )
    assert report.status == "VALID"
    assert (tmp_path / "out" / "evidence_envelope.json").is_file()
    envelope = (tmp_path / "out" / "evidence_envelope.json").read_text(encoding="utf-8")
    assert "trade_execution" in envelope
    assert model_id in envelope


def test_runner_missing_trade_field_is_invalid_experiment(tmp_path: Path):
    root = tmp_path / "repo"
    _seed_trade_repo(root)
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    model_id = er.execution_model_id(_assumptions())

    def adapter(ctx: runner.ExperimentContext) -> runner.ArmRawResult:
        bad = _trade_row(execution_model_id=model_id)
        del bad["data_fingerprint"]
        return runner.ArmRawResult(arm=ctx.spec["_runner_arm"], commit_sha=head, members=[bad])

    runner.clear_execution_adapters()
    runner.register_execution_adapter("demo_trade", adapter)
    report = runner.execute_experiment(
        root,
        root / "docs/research-experiment-specs/E-2026-10-06-trade-demo-01.json",
        write_evidence=False,
    )
    assert report.status == "INVALID"
    assert report.result == "INVALID EXPERIMENT"
    assert any(c.name == "evidence_contract" and not c.passed for c in report.integrity_checks)


def test_existing_options_approved_spec_still_schema_valid():
    schema = runner.load_schema(ROOT)
    path = (
        ROOT
        / "docs/research-experiment-specs/E-2026-09-25-options-212c-target-geometry-01.json"
    )
    spec = runner.load_spec(path)
    assert runner.schema_validate(spec, schema) == []
    assert er.resolve_evidence_type(spec) == er.EVIDENCE_TYPE_COVERAGE


def test_builtin_options_adapter_registration_unchanged():
    runner.clear_execution_adapters()
    register_builtin_adapters()
    assert "options_212c_target_geometry" in runner.EXECUTION_ADAPTERS


def _seed_trade_repo(root: Path) -> None:
    import json

    (root / "docs/research-experiment-specs").mkdir(parents=True)
    (root / "docs/research-evidence").mkdir(parents=True)
    (root / "docs/research-experiment-spec.schema.json").write_text(
        (ROOT / "docs/research-experiment-spec.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    trial_id = "T-2026-10-06-trade-demo-01"
    population = "demo population"
    (root / "docs/prereg-trade-demo-2026-10-06.md").write_text("# prereg\n", encoding="utf-8")
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "event": "PLANNED",
                "recorded_at": "2026-10-06T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": "docs/prereg-trade-demo-2026-10-06.md",
                "prereg_commit": None,
                "family_id": "trade_demo",
                "family_label": "trade_demo",
                "population": population,
                "variant_set": {"count": 1, "manifest": "docs/prereg-trade-demo-2026-10-06.md"},
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assumptions = _assumptions()
    fake = "cccccccccccccccccccccccccccccccccccccccc"
    spec = {
        "schema_version": 1,
        "experiment_id": "E-2026-10-06-trade-demo-01",
        "trial_id": trial_id,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-06T00:00:00Z",
        "supersedes": None,
        "hypothesis": "demo trade_execution contract",
        "prereg_path": "docs/prereg-trade-demo-2026-10-06.md",
        "baseline": {"commit_sha": fake, "label": "baseline"},
        "candidate": {"commit_sha": fake, "change": "demo", "label": "candidate"},
        "data": {
            "source": "fixture",
            "window": {"start": "2026-01-01", "end": "2026-01-02"},
            "dataset_id": "fixture://trade-demo",
            "dataset_hash": "a" * 64,
        },
        "population": population,
        "setup_type": "demo_trade",
        "evidence_type": "trade_execution",
        "execution_assumptions": assumptions,
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
            "friction": "frozen",
        },
        "required_metrics": ["population_size"],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{trial_id}/",
        "variant_manifest": None,
        "notes": "synthetic trade_execution contract fixture",
    }
    spec_path = root / "docs/research-experiment-specs/E-2026-10-06-trade-demo-01.json"
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
