"""U4: mandatory evidence identity gate + consumed-trial partition lock."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from ops import evidence_identity as gate
from ops import evidence_row as er
from ops import experiment_partitions as partitions
from ops import research_experiment_runner as runner

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "E-2026-10-07-identity-gate-01"
TRIAL_ID = "T-2026-10-07-identity-gate-01"
PREREG = "docs/prereg-identity-gate-2026-10-07.md"
DATASET_HASH = "a" * 64
ASSUMPTIONS = {
    "entry_fill_model": "market",
    "same_bar_ambiguity_rule": "stop_first",
    "stop_handling": "fixed_stop",
    "target_handling": "fixed_limit",
    "slippage_assumption": "adverse_ticks=1",
    "commission": "usd_per_contract_per_side=0.74",
    "exchange_broker_fees": "usd_per_contract_per_side=0",
    "sizing_assumptions": "replay_risk_engine",
}
PARTS = {
    "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    "validation": {"start": "2026-06-01T00:00:00Z", "end": "2026-07-01T00:00:00Z"},
    "untouched_oos": {"start": "2026-07-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
}
SIGNAL_BY_PARTITION = {
    "development": "2026-05-15T14:30:00Z",
    "validation": "2026-06-15T14:30:00Z",
    "untouched_oos": "2026-07-15T14:30:00Z",
}


@pytest.fixture(autouse=True)
def _clear():
    runner.clear_execution_adapters()
    yield
    runner.clear_execution_adapters()


def _trade_row(signal_ts: str) -> dict:
    return {
        "evidence_type": "trade_execution",
        "instrument": "MNQ",
        "strategy_identity": "orb_reclaim",
        "candidate_signal_id": f"cand-{signal_ts}",
        "signal_ts": signal_ts,
        "decision_ts": signal_ts,
        "earliest_legal_order_ts": signal_ts,
        "intended_entry": 100.0,
        "direction": "LONG",
        "fill_state": "FILLED",
        "fill_price": 100.25,
        "fill_ts": signal_ts,
        "stop": 99.0,
        "target": 103.0,
        "exit_price": 103.0,
        "exit_ts": signal_ts,
        "exit_reason": "TARGET_HIT",
        "mae": -0.5,
        "mfe": 2.75,
        "gross_pnl": 5.5,
        "costs_fees": 1.48,
        "net_pnl": 4.02,
        "r_multiple": 1.6,
        "data_fingerprint": f"dataset_hash:{DATASET_HASH}",
        "execution_model_id": er.execution_model_id(ASSUMPTIONS),
    }


def _register(head: str, signal_ts: str, evidence_type: str = "trade_execution") -> None:
    def adapter(ctx):
        member = (
            _trade_row(signal_ts)
            if evidence_type == "trade_execution"
            else {"signal_ts": signal_ts, "setup_detected": True}
        )
        return runner.ArmRawResult(arm=ctx.spec["_runner_arm"], commit_sha=head, members=[member])

    runner.register_execution_adapter("demo_trade", adapter)


def _seed(
    root: Path,
    *,
    evaluation_partition: str | None = "development",
    with_partitions: bool = True,
    evidence_type: str = "trade_execution",
    dataset_hash: str | None = DATASET_HASH,
    prior_exposed: str | None = "none",
) -> Path:
    (root / "docs/research-experiment-specs").mkdir(parents=True)
    (root / "docs/research-evidence").mkdir(parents=True)
    (root / "docs/research-experiment-spec.schema.json").write_text(
        (ROOT / "docs/research-experiment-spec.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "docs/research-oos-consumption-ledger.jsonl").write_text("", encoding="utf-8")
    (root / PREREG).write_text(f"# prereg {TRIAL_ID}\n", encoding="utf-8")
    population = "identity gate population"
    ledger_row = {
        "trial_id": TRIAL_ID,
        "event": "PLANNED",
        "recorded_at": "2026-10-07T00:00:00Z",
        "recorded_by": "test",
        "prereg_path": PREREG,
        "prereg_commit": None,
        "family_id": "identity_gate",
        "family_label": "identity_gate",
        "population": population,
        "variant_set": {"count": 1, "manifest": PREREG},
        "attempts_in_family_before": 0,
        "pre_ledger_attempts": "UNKNOWN",
    }
    if prior_exposed is not None:
        ledger_row["prior_exposed"] = prior_exposed
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(ledger_row) + "\n", encoding="utf-8"
    )
    data = {
        "source": "fixture",
        "window": {"start": "2026-05-01", "end": "2026-08-01"},
        "dataset_id": "fixture://identity-gate",
    }
    if dataset_hash is not None:
        data["dataset_hash"] = dataset_hash
    spec = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "trial_id": TRIAL_ID,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-07T00:00:00Z",
        "supersedes": None,
        "hypothesis": "identity gate fixture",
        "prereg_path": PREREG,
        "baseline": {"commit_sha": "c" * 40, "label": "baseline"},
        "candidate": {"commit_sha": "c" * 40, "change": "demo", "label": "candidate"},
        "data": data,
        "population": population,
        "setup_type": "demo_trade",
        "evidence_type": evidence_type,
        "timeframe": "5m",
        "changed_variables": [{"name": "x", "baseline_value": "a", "candidate_value": "b"}],
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
        "evidence_path": f"docs/research-evidence/{TRIAL_ID}/",
        "variant_manifest": None,
        "notes": "U4 fixture",
    }
    if evidence_type == "trade_execution":
        spec["execution_assumptions"] = dict(ASSUMPTIONS)
    if with_partitions:
        spec["chronological_partitions"] = json.loads(json.dumps(PARTS))
    if evaluation_partition is not None:
        spec["evaluation_partition"] = evaluation_partition
    spec_path = root / "docs/research-experiment-specs" / f"{EXPERIMENT_ID}.json"
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
    _register(head, SIGNAL_BY_PARTITION.get(evaluation_partition or "development"), evidence_type)
    return spec_path


def _run(spec_path: Path, **kwargs):
    root = spec_path.parents[2]
    report = runner.execute_experiment(root, spec_path, **kwargs)
    return root, report


def _bundle(root: Path) -> Path:
    return root / "docs/research-evidence" / TRIAL_ID


def _rewrite_spec(spec_path: Path, mutate) -> None:
    spec = json.loads(spec_path.read_text())
    mutate(spec)
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


# ─── PROMOTION_QUALITY path ─────────────────────────────────────────────────


def test_canonical_runner_bundle_is_promotion_quality(tmp_path):
    root, report = _run(_seed(tmp_path / "repo"))
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.PROMOTION_QUALITY, result.reasons
    assert result.identity["trial_id"] == TRIAL_ID
    assert result.identity["evaluation_partition"] == "development"
    assert result.identity["execution_model_id"] == er.execution_model_id(ASSUMPTIONS)
    assert result.identity["trial_prior_exposed"] == "none"
    assert result.identity["data_identity"] == f"dataset_hash:{DATASET_HASH}"
    assert result.identity["strategy_identity"] == "orb@v1"
    manifest = json.loads((_bundle(root) / "bundle_manifest.json").read_text())
    assert set(gate.REQUIRED_BUNDLE_FILES) <= set(manifest["files"])


def test_untouched_oos_bundle_is_promotion_quality_and_receipt_bound(tmp_path):
    root, report = _run(_seed(tmp_path / "repo", evaluation_partition="untouched_oos"))
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.PROMOTION_QUALITY, result.reasons
    # The ledger receipt must equal the bundle's receipt.
    ledger = root / partitions.OOS_LEDGER_REL
    row = json.loads(ledger.read_text().splitlines()[0])
    row["code_sha"] = "f" * 40
    ledger.write_text(json.dumps(row, sort_keys=True) + "\n")
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert any("contradicts the OOS consumption ledger" in r for r in result.reasons)


def test_bundle_cli_reports_status(tmp_path, capsys):
    root, _ = _run(_seed(tmp_path / "repo"))
    code = gate.main([str(_bundle(root)), "--repo-root", str(root)])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == gate.PROMOTION_QUALITY


# ─── INVALID: claims promotion eligibility without identity ─────────────────


def test_edited_bundle_file_is_invalid(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    raw = _bundle(root) / "candidate_raw.json"
    payload = json.loads(raw.read_text())
    payload["members"][0]["net_pnl"] = 999.0
    raw.write_text(json.dumps(payload))
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert "bundle file edited after writing: candidate_raw.json" in result.reasons


def test_strategy_identity_is_bound_to_canonical_trade_rows(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    bundle = _bundle(root)
    envelope_path = bundle / "evidence_envelope.json"
    envelope = json.loads(envelope_path.read_text())
    assert envelope["strategy_identity"] == "orb@v1"

    # Re-label the strategy and repair the envelope digest in the manifest.
    # Byte identity alone must not make the false strategy attribution valid.
    envelope["strategy_identity"] = "made-up-strategy"
    envelope_path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n")
    manifest_path = bundle / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["evidence_envelope.json"] = gate._sha256(envelope_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    result = gate.classify_evidence_bundle(root, bundle)
    assert result.status == gate.INVALID
    assert any("strategy_identity contradicts" in reason for reason in result.reasons)


def test_missing_bundle_manifest_is_invalid(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    (_bundle(root) / "bundle_manifest.json").unlink()
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert "missing bundle_manifest.json" in result.reasons


@pytest.mark.parametrize(
    "key",
    ["preregistration_identity", "strategy_identity", "evaluation_partition", "trial_prior_exposed"],
)
def test_missing_identity_field_is_invalid(tmp_path, key):
    root, _ = _run(_seed(tmp_path / "repo"))
    envelope_path = _bundle(root) / "evidence_envelope.json"
    envelope = json.loads(envelope_path.read_text())
    envelope.pop(key)
    envelope_path.write_text(json.dumps(envelope))
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert any(key in reason for reason in result.reasons), result.reasons


def test_unregistered_trial_is_invalid(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    (root / "docs/research-trial-ledger.jsonl").write_text("")
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert "trial_id is not registered in the trial ledger" in result.reasons


def test_prior_exposure_must_match_trial_ledger(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    ledger = root / "docs/research-trial-ledger.jsonl"
    row = json.loads(ledger.read_text())
    row["prior_exposed"] = "partial"
    ledger.write_text(json.dumps(row) + "\n")
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert any("prior_exposed" in r for r in result.reasons)


def test_trial_without_recorded_prior_exposure_needs_no_envelope_value(tmp_path):
    root, report = _run(_seed(tmp_path / "repo", prior_exposed=None))
    assert report.status == "VALID", report.errors
    envelope = json.loads((_bundle(root) / "evidence_envelope.json").read_text())
    assert "trial_prior_exposed" not in envelope
    assert gate.classify_evidence_bundle(root, _bundle(root)).status == gate.PROMOTION_QUALITY


def test_bundle_outside_registered_folder_is_invalid(tmp_path):
    spec_path = _seed(tmp_path / "repo")
    root = spec_path.parents[2]
    elsewhere = root / "scratch/evidence" / TRIAL_ID
    report = runner.execute_experiment(root, spec_path, evidence_dir=elsewhere)
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, elsewhere)
    assert result.status == gate.INVALID
    assert any("docs/research-evidence/<trial_id>/" in r for r in result.reasons)


def test_approved_spec_drift_after_run_is_invalid(tmp_path):
    spec_path = _seed(tmp_path / "repo")
    root, _ = _run(spec_path)
    _rewrite_spec(spec_path, lambda s: s.__setitem__("hypothesis", "rewritten later"))
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert "bundled spec differs from the repository's approved spec" in result.reasons


def test_unpinned_dataset_identity_is_invalid(tmp_path):
    spec_path = _seed(tmp_path / "repo", dataset_hash=None)
    root = spec_path.parents[2]
    head = json.loads(spec_path.read_text())["baseline"]["commit_sha"]
    runner.clear_execution_adapters()

    def adapter(ctx):
        row = _trade_row(SIGNAL_BY_PARTITION["development"])
        row["data_fingerprint"] = er.data_identity_from_spec(ctx.spec)
        return runner.ArmRawResult(arm=ctx.spec["_runner_arm"], commit_sha=head, members=[row])

    runner.register_execution_adapter("demo_trade", adapter)
    report = runner.execute_experiment(root, spec_path)
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert "data_identity must be a pinned dataset_hash fingerprint" in result.reasons


def test_legacy_spec_without_partitions_cannot_be_promotion_quality(tmp_path):
    root, report = _run(_seed(tmp_path / "repo", with_partitions=False, evaluation_partition=None))
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID
    assert any("chronological_partitions" in r for r in result.reasons)


def test_receipt_on_non_oos_bundle_is_invalid(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo"))
    bundle = _bundle(root)
    (bundle / partitions.OOS_RECEIPT_FILENAME).write_text("{}\n")
    manifest_path = bundle / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][partitions.OOS_RECEIPT_FILENAME] = gate._sha256(
        bundle / partitions.OOS_RECEIPT_FILENAME
    )
    manifest_path.write_text(json.dumps(manifest))
    result = gate.classify_evidence_bundle(root, bundle)
    assert result.status == gate.INVALID
    assert "non-OOS evidence must not carry an OOS consumption receipt" in result.reasons


# ─── REFERENCE_ONLY: never silently promotion-quality ───────────────────────


def test_coverage_bundle_is_reference_only(tmp_path):
    root, report = _run(_seed(tmp_path / "repo", evidence_type="coverage"))
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.REFERENCE_ONLY
    assert not result.promotion_quality


def test_legacy_bundle_without_envelope_is_reference_only(tmp_path):
    legacy = tmp_path / "docs/research-evidence/T-legacy"
    legacy.mkdir(parents=True)
    (legacy / "results.json").write_text('{"trial_id": "T-legacy"}')
    result = gate.classify_evidence_bundle(tmp_path, legacy)
    assert result.status == gate.REFERENCE_ONLY
    assert "legacy" in result.reasons[0]


def test_envelope_flipped_to_promotion_eligible_without_identity_is_invalid(tmp_path):
    root, _ = _run(_seed(tmp_path / "repo", evidence_type="coverage"))
    envelope_path = _bundle(root) / "evidence_envelope.json"
    envelope = json.loads(envelope_path.read_text())
    envelope["promotion_eligible"] = True
    envelope_path.write_text(json.dumps(envelope))
    result = gate.classify_evidence_bundle(root, _bundle(root))
    assert result.status == gate.INVALID


# ─── Consumed-trial partition lock (U2 carry-over minors 1 and 2) ───────────


def _consume_oos(tmp_path: Path) -> Path:
    spec_path = _seed(tmp_path / "repo", evaluation_partition="untouched_oos")
    _, report = _run(spec_path)
    assert report.status == "VALID", report.errors
    return spec_path


def _rerun_development(spec_path: Path, mutate) -> runner.RunnerReport:
    def apply(spec):
        spec["evaluation_partition"] = "development"
        mutate(spec)

    _rewrite_spec(spec_path, apply)
    head = json.loads(spec_path.read_text())["baseline"]["commit_sha"]
    runner.clear_execution_adapters()
    _register(head, SIGNAL_BY_PARTITION["development"])
    return runner.execute_experiment(spec_path.parents[2], spec_path)


def test_consumed_trial_cannot_be_rescored_without_partitions(tmp_path):
    spec_path = _consume_oos(tmp_path)

    def drop(spec):
        spec.pop("chronological_partitions")
        spec.pop("evaluation_partition")

    report = _rerun_development(spec_path, drop)
    assert report.status == "INVALID"
    checks = {c.name: c.passed for c in report.integrity_checks}
    assert checks["consumed_trial_partitions"] is False
    assert "recorded chronological_partitions" in report.errors[0]


def test_consumed_trial_cannot_redraw_windows_over_consumed_oos_dates(tmp_path):
    spec_path = _consume_oos(tmp_path)

    def shift(spec):
        spec["chronological_partitions"] = {
            "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
            "validation": {"start": "2026-06-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
            "untouched_oos": {"start": "2026-08-01T00:00:00Z", "end": "2026-09-01T00:00:00Z"},
        }

    report = _rerun_development(spec_path, shift)
    assert report.status == "INVALID"
    assert "redrawn window" in report.errors[0]


def test_consumed_trial_rerun_on_recorded_partitions_is_allowed(tmp_path):
    spec_path = _consume_oos(tmp_path)
    report = _rerun_development(spec_path, lambda spec: None)
    assert report.status == "VALID", report.errors


def test_equivalent_oos_window_string_is_not_a_redraw(tmp_path):
    spec_path = _consume_oos(tmp_path)

    def reformat(spec):
        spec["chronological_partitions"]["untouched_oos"] = {
            "start": "2026-07-01T00:00:00+00:00",
            "end": "2026-07-31T20:00:00-04:00",
        }

    report = _rerun_development(spec_path, reformat)
    assert report.status == "VALID", report.errors


def test_consumed_trial_oos_second_look_still_refused(tmp_path):
    spec_path = _consume_oos(tmp_path)
    _, report = _run(spec_path)
    assert report.status == "INVALID"


def test_unconsumed_trial_is_unaffected_by_lock(tmp_path):
    spec_path = _seed(tmp_path / "repo")

    def drop(spec):
        spec.pop("chronological_partitions")
        spec.pop("evaluation_partition")

    _rewrite_spec(spec_path, drop)
    _, report = _run(spec_path)
    assert report.status == "VALID", report.errors


def test_corrupt_oos_ledger_fails_closed(tmp_path):
    spec_path = _seed(tmp_path / "repo")
    (spec_path.parents[2] / partitions.OOS_LEDGER_REL).write_text("{not json\n")
    _, report = _run(spec_path)
    assert report.status == "INVALID"


# ─── U3 → U4 integration: futures replay evidence is promotion-quality ──────


def test_futures_replay_bundle_classifies_promotion_quality(tmp_path, monkeypatch, config):
    from ops.research_experiment_adapters import futures_replay as fr
    from tests import test_futures_replay_adapter as u3

    spec_path = u3._seed(tmp_path / "repo")
    head = json.loads(spec_path.read_text())["baseline"]["commit_sha"]
    monkeypatch.delenv("WEBULL_FUTURES_MIRROR_ENABLED", raising=False)
    monkeypatch.setattr(fr, "CODE_SHA_PROVIDER", lambda: head)
    monkeypatch.setattr(fr, "BASE_CONFIG_PROVIDER", lambda: config)
    runner.register_execution_adapter(fr.SETUP_TYPE, fr.run_futures_replay)
    root = spec_path.parents[2]
    report = runner.execute_experiment(root, spec_path)
    assert report.status == "VALID", report.errors
    result = gate.classify_evidence_bundle(root, root / "docs/research-evidence" / u3.TRIAL_ID)
    assert result.status == gate.PROMOTION_QUALITY, result.reasons


# ─── Repository scan (CI) ───────────────────────────────────────────────────


def _git_ls(*patterns: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", *patterns],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def test_repository_canonical_bundles_live_in_registered_folder_and_are_not_invalid():
    """Any tracked canonical envelope / bundle manifest anywhere in the repo must
    sit in docs/research-evidence/<trial_id>/ and must not be INVALID. Evidence
    outside the registered folder cannot escape register-before-count."""
    tracked = _git_ls(f"*{gate.ENVELOPE_FILENAME}", f"*{gate.BUNDLE_MANIFEST_FILENAME}")
    first_rows = gate.ledger_first_rows(ROOT)
    for rel in tracked:
        path = PurePosixPath(rel)
        assert len(path.parts) == 4 and "/".join(path.parts[:2]) == gate.EVIDENCE_ROOT_REL, (
            f"{rel}: canonical evidence must live at {gate.EVIDENCE_ROOT_REL}/<trial_id>/"
        )
        result = gate.classify_evidence_bundle(ROOT, ROOT / path.parent, first_rows=first_rows)
        assert result.status != gate.INVALID, f"{rel}: {result.reasons}"
