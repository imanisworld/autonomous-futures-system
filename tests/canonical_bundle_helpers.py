"""Test helper: build a real PROMOTION_QUALITY canonical bundle via the runner."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops import evidence_identity
from ops import evidence_row as er
from ops import research_experiment_runner as runner

ROOT = Path(__file__).resolve().parents[1]
SETUP_TYPE = "demo_canonical_trade"
PARTITIONS = {
    "development": {"start": "2025-01-01T00:00:00Z", "end": "2025-07-01T00:00:00Z"},
    "validation": {"start": "2025-07-01T00:00:00Z", "end": "2025-10-01T00:00:00Z"},
    "untouched_oos": {"start": "2025-10-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"},
}


def assumptions(entry_fill_model: str = "ioc_limit") -> dict:
    return {
        "entry_fill_model": entry_fill_model,
        "same_bar_ambiguity_rule": "stop_first",
        "stop_handling": "fixed_stop",
        "target_handling": "fixed_limit",
        "slippage_assumption": "adverse_ticks=1",
        "commission": "usd_per_contract_per_side=0.74",
        "exchange_broker_fees": "usd_per_contract_per_side=0",
        "sizing_assumptions": "replay_risk_engine",
    }


def _rows(
    *,
    count: int,
    cancellations: int,
    instrument: str,
    contracts: int,
    start: str,
    model_id: str,
    dataset_hash: str,
) -> list[dict]:
    base = datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(days=1)
    rows = []
    for index in range(count):
        ts = (base + timedelta(hours=index)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        win = index % 4 != 3
        gross = 10.0 * contracts if win else -5.0 * contracts
        costs = 1.48 * contracts
        net = round(gross - costs, 2)
        rows.append(
            {
                "evidence_type": "trade_execution",
                "instrument": instrument,
                "strategy_identity": "example",
                "candidate_signal_id": f"cand-{index}",
                "signal_ts": ts,
                "decision_ts": ts,
                "earliest_legal_order_ts": ts,
                "intended_entry": 100.0,
                "direction": "LONG",
                "fill_state": "FILLED",
                "fill_price": 100.25,
                "fill_ts": ts,
                "stop": 99.0,
                "target": 103.0,
                "exit_price": 103.0 if win else 99.0,
                "exit_ts": ts,
                "exit_reason": "TARGET_HIT" if win else "STOP_HIT",
                "mae": -0.5,
                "mfe": 2.75,
                "gross_pnl": gross,
                "costs_fees": costs,
                "net_pnl": net,
                "r_multiple": round(net / (5.0 * contracts), 4),
                "contracts": contracts,
                "data_fingerprint": f"dataset_hash:{dataset_hash}",
                "execution_model_id": model_id,
            }
        )
    for offset in range(cancellations):
        index = count + offset
        ts = (base + timedelta(hours=index)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows.append(
            {
                "evidence_type": "trade_execution",
                "instrument": instrument,
                "strategy_identity": "example",
                "candidate_signal_id": f"cand-{index}",
                "signal_ts": ts,
                "decision_ts": ts,
                "earliest_legal_order_ts": ts,
                "intended_entry": 100.0,
                "direction": "LONG",
                "fill_state": "NO_FILL",
                "stop": 99.0,
                "target": 103.0,
                "no_fill_reason": "CANCELLED",
                "data_fingerprint": f"dataset_hash:{dataset_hash}",
                "execution_model_id": model_id,
            }
        )
    return rows


def make_promotion_bundle(
    root: Path,
    *,
    trial_id: str = "T-2026-10-07-canonical-promotion-01",
    instrument: str = "MNQ",
    contracts: int = 1,
    fills: int = 40,
    cancellations: int = 0,
    entry_fill_model: str = "ioc_limit",
    evaluation_partition: str = "untouched_oos",
) -> tuple[str, str]:
    """Create a git repo at ``root`` holding one canonical bundle.

    Returns ``(bundle_rel_path, code_sha)``. ``root`` may already hold files.
    """
    experiment_id = trial_id.replace("T-", "E-", 1)
    prereg = f"docs/prereg-{trial_id}.md"
    dataset_hash = "b" * 64
    for rel in ("docs/research-experiment-specs", "docs/research-evidence"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "docs/research-experiment-spec.schema.json").write_text(
        (ROOT / "docs/research-experiment-spec.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "docs/research-oos-consumption-ledger.jsonl").write_text("", encoding="utf-8")
    (root / prereg).write_text(f"# prereg {trial_id}\n", encoding="utf-8")
    population = "canonical promotion fixture"
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "event": "PLANNED",
                "recorded_at": "2026-10-07T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": prereg,
                "prereg_commit": None,
                "family_id": "canonical_promotion",
                "family_label": "canonical_promotion",
                "population": population,
                "variant_set": {"count": 1, "manifest": prereg},
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    frozen = assumptions(entry_fill_model)
    spec = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "trial_id": trial_id,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-07T00:00:00Z",
        "supersedes": None,
        "hypothesis": "canonical promotion fixture",
        "prereg_path": prereg,
        "baseline": {"commit_sha": "c" * 40, "label": "baseline"},
        "candidate": {"commit_sha": "c" * 40, "change": "demo", "label": "candidate"},
        "data": {
            "source": "fixture",
            "window": {"start": "2025-01-01", "end": "2026-01-01"},
            "dataset_id": "fixture://canonical-promotion",
            "dataset_hash": dataset_hash,
        },
        "population": population,
        "setup_type": SETUP_TYPE,
        "evidence_type": "trade_execution",
        "execution_assumptions": frozen,
        "timeframe": "5m",
        "changed_variables": [{"name": "x", "baseline_value": "a", "candidate_value": "b"}],
        "held_constant": ["population"],
        "execution": {
            "entry_logic": "fixture",
            "exit_logic": "fixture",
            "stop_logic": "fixture",
            "target_logic": "fixture",
            "sizing": "fixture",
            "friction": "frozen",
        },
        "required_metrics": ["population_size"],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{trial_id}/",
        "variant_manifest": None,
        "notes": "U5 fixture",
        "chronological_partitions": json.loads(json.dumps(PARTITIONS)),
        "evaluation_partition": evaluation_partition,
    }
    spec_path = root / "docs/research-experiment-specs" / f"{experiment_id}.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    write_synthetic_fi_suite(root)
    runner._git(root, "init")
    runner._git(root, "config", "user.email", "t@example.com")
    runner._git(root, "config", "user.name", "t")
    runner._git(root, "add", ".")
    runner._git(root, "commit", "-m", "seed")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")

    rows = _rows(
        count=fills,
        cancellations=cancellations,
        instrument=instrument,
        contracts=contracts,
        start=PARTITIONS[evaluation_partition]["start"],
        model_id=er.execution_model_id(frozen),
        dataset_hash=dataset_hash,
    )

    def adapter(ctx):
        return runner.ArmRawResult(
            arm=ctx.spec["_runner_arm"], commit_sha=head, members=[dict(r) for r in rows]
        )

    previous = runner.EXECUTION_ADAPTERS.get(SETUP_TYPE)
    runner.register_execution_adapter(SETUP_TYPE, adapter)
    try:
        report = runner.execute_experiment(root, spec_path)
    finally:
        if previous is None:
            runner.EXECUTION_ADAPTERS.pop(SETUP_TYPE, None)
        else:
            runner.EXECUTION_ADAPTERS[SETUP_TYPE] = previous
    assert report.status == "VALID", report.errors
    bundle_rel = f"docs/research-evidence/{trial_id}"
    status = evidence_identity.classify_evidence_bundle(root, root / bundle_rel)
    assert status.status == evidence_identity.PROMOTION_QUALITY, status.reasons
    return bundle_rel, head


def write_synthetic_fi_suite(root: Path) -> None:
    """One trivially passing committed test per required FI scenario (U10 fixtures)."""
    from ops.fault_injection_gate import REQUIRED_SCENARIOS

    suite = root / "tests/fault_injection"
    suite.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for scenario in REQUIRED_SCENARIOS:
        token = scenario.replace("FI-", "fi").lower()
        lines.append(f"def test_{token}_synthetic():\n    assert True\n")
    (suite / "test_synthetic_fi.py").write_text("\n\n".join(lines), encoding="utf-8")


def make_fi_manifest(root: Path, code_sha: str, *, mutate=None) -> str:
    """Write synthetic FI proof structurally bound to an exact fixture SHA."""
    from ops import fault_injection_gate as fi

    discovered = sorted(fi.discovered_scenarios_at(root, code_sha))
    manifest = {
        "schema_version": fi.SCHEMA_VERSION,
        "generator": fi.GENERATOR,
        "code_sha": code_sha,
        "suite_dir": fi.SUITE_DIR,
        "suite_fingerprint": fi.suite_fingerprint_at(root, code_sha),
        "pytest_exit_code": 0,
        "required_scenarios": list(fi.REQUIRED_SCENARIOS),
        "discovered_scenarios": discovered,
        "scenarios": {
            scenario: {
                "passed": 1, "failed": 0, "skipped": 0, "result": "PASS", "tests": []
            }
            for scenario in fi.REQUIRED_SCENARIOS
        },
        "overall": "PASS",
    }
    if mutate is not None:
        mutate(manifest)
    path = root / "fi_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return "fi_manifest.json"
