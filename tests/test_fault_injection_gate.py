"""U10: exact-SHA fault-injection proof as a promotion prerequisite."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from ops import fault_injection_gate as fi
from ops import research_experiment_runner as runner
from tests.canonical_bundle_helpers import (
    make_fi_manifest,
    make_promotion_bundle,
    write_synthetic_fi_suite,
)

ROOT = Path(__file__).resolve().parents[1]


# ─── Inventory (CI tripwire) ────────────────────────────────────────────────


def _discovered_scenarios() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / fi.SUITE_DIR).glob("test_*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scenario = fi.scenario_for_test(node.name)
                if scenario:
                    found.add(scenario)
    return found


def test_required_inventory_equals_the_defined_fi_suite():
    """A new FI scenario must be added to REQUIRED_SCENARIOS deliberately."""
    assert _discovered_scenarios() == set(fi.REQUIRED_SCENARIOS)


def test_undefined_scenarios_are_reported_not_invented():
    assert {"FI-12", "FI-14", "AUTH_TOKEN_LOSS", "ROLL_MISMATCH"} <= set(fi.UNDEFINED_SCENARIOS)
    assert not set(fi.UNDEFINED_SCENARIOS) & set(fi.REQUIRED_SCENARIOS)
    assert not set(fi.UNDEFINED_SCENARIOS) & _discovered_scenarios()


@pytest.mark.parametrize(
    "name,scenario",
    [
        ("test_fi1_ambiguous_submit_is_not_booked_flat[timeout]", "FI-1"),
        ("test_fi5a_nan_open", "FI-5"),
        ("test_fi10_torn_trade_row_is_not_read_as_flat", "FI-10"),
        ("test_jwc1_x", "JW"),
        ("test_jw1_lost_trade_row", "JW"),
        ("test_jwt_token_case", "JW"),
        ("test_lw_ledger", "LW"),
        ("test_lwm_memory_case", "LW"),
        ("test_something_else", None),
    ],
)
def test_scenario_mapping(name, scenario):
    assert fi.scenario_for_test(name) == scenario


def test_skipped_or_xfailed_scenario_is_not_proof():
    results = fi.scenario_results([("test_fi1_a", "passed"), ("test_fi1_b", "skipped"),
                                   ("test_fi2_a", "skipped"), ("test_fi3_a", "failed")])
    assert results["FI-1"]["result"] == "NOT_PROVEN"
    assert results["FI-2"]["result"] == "NOT_PROVEN"
    assert results["FI-3"]["result"] == "FAIL"


# ─── Generator on a real (temporary) git checkout ──────────────────────────


def _repo(tmp_path: Path, *, extra: str = "") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    write_synthetic_fi_suite(repo)
    if extra:
        (repo / "tests/fault_injection/test_extra.py").write_text(extra, encoding="utf-8")
    runner._git(repo, "init")
    runner._git(repo, "config", "user.email", "t@example.com")
    runner._git(repo, "config", "user.name", "t")
    runner._git(repo, "add", ".")
    runner._git(repo, "commit", "-m", "seed")
    return repo


def test_inventory_discovery_sees_class_based_scenarios(tmp_path):
    repo = _repo(tmp_path)
    extra = repo / "tests/fault_injection/test_class_scenario.py"
    extra.write_text(
        "class TestNewScenario:\n    def test_fi12_nested(self):\n        assert True\n",
        encoding="utf-8",
    )
    runner._git(repo, "add", ".")
    runner._git(repo, "commit", "-m", "add class-based FI-12")
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    assert "FI-12" in fi.discovered_scenarios_at(repo, head)
    with pytest.raises(fi.FaultInjectionGateError, match="scenario inventory drift"):
        fi.generate_manifest(repo, python=sys.executable)

def test_generated_manifest_binds_sha_and_suite(tmp_path):
    repo = _repo(tmp_path)
    manifest = fi.generate_manifest(repo, python=sys.executable)
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    assert manifest["overall"] == "PASS"
    assert manifest["code_sha"] == head
    assert manifest["suite_fingerprint"] == fi.suite_fingerprint_at(repo, head)
    assert set(manifest["scenarios"]) == set(fi.REQUIRED_SCENARIOS)
    assert set(manifest["discovered_scenarios"]) == set(fi.REQUIRED_SCENARIOS)
    assert fi.verify_manifest(repo, manifest, code_sha=head) == []


def test_failing_fi_test_fails_the_manifest(tmp_path):
    repo = _repo(tmp_path, extra="def test_fi3_regression():\n    assert False\n")
    manifest = fi.generate_manifest(repo, python=sys.executable)
    assert manifest["overall"] == "FAIL"
    assert manifest["scenarios"]["FI-3"]["result"] == "FAIL"
    head = manifest["code_sha"]
    assert any("FI-3" in b for b in fi.verify_manifest(repo, manifest, code_sha=head))


def test_xfailed_known_defect_is_not_proof(tmp_path):
    extra = (
        "import pytest\n\n@pytest.mark.xfail(strict=True, raises=AssertionError, reason='KNOWN DEFECT FI-4')\n"
        "def test_fi4_known_defect():\n    assert False\n"
    )
    repo = _repo(tmp_path, extra=extra)
    manifest = fi.generate_manifest(repo, python=sys.executable)
    assert manifest["scenarios"]["FI-4"]["result"] == "NOT_PROVEN"
    assert fi.verify_manifest(repo, manifest, code_sha=manifest["code_sha"])


def test_generator_refuses_a_dirty_checkout(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests/fault_injection/test_synthetic_fi.py").write_text("x = 1\n")
    with pytest.raises(fi.FaultInjectionGateError, match="not clean"):
        fi.generate_manifest(repo, python=sys.executable)


def test_generator_refuses_untracked_checkout_contamination(tmp_path):
    repo = _repo(tmp_path)
    (repo / "tests/fault_injection/test_untracked.py").write_text(
        "def test_fi1_untracked_override():\n    assert True\n", encoding="utf-8"
    )
    with pytest.raises(fi.FaultInjectionGateError, match="not clean"):
        fi.generate_manifest(repo, python=sys.executable)


def test_generator_refuses_scenario_inventory_drift(tmp_path):
    repo = _repo(tmp_path)
    extra = repo / "tests/fault_injection/test_new_scenario.py"
    extra.write_text("def test_fi12_new():\n    assert True\n", encoding="utf-8")
    runner._git(repo, "add", ".")
    runner._git(repo, "commit", "-m", "add undefined FI-12")
    with pytest.raises(fi.FaultInjectionGateError, match="scenario inventory drift"):
        fi.generate_manifest(repo, python=sys.executable)


# ─── Verifier refusals ─────────────────────────────────────────────────────


@pytest.fixture
def good(tmp_path):
    repo = _repo(tmp_path)
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    make_fi_manifest(repo, head)
    return repo, head, json.loads((repo / "fi_manifest.json").read_text())


def test_good_manifest_verifies(good):
    repo, head, manifest = good
    assert fi.verify_manifest(repo, manifest, code_sha=head) == []


def test_missing_or_foreign_manifest_is_refused(good):
    repo, head, _ = good
    assert fi.verify_manifest(repo, None, code_sha=head)
    green_pytest = {"pytest": "8063 passed", "exit_code": 0}
    blockers = fi.verify_manifest(repo, green_pytest, code_sha=head)
    assert any("not produced by the FI gate generator" in b for b in blockers)


def test_stale_manifest_for_another_sha_is_refused(good):
    repo, _, manifest = good
    (repo / "README").write_text("x")
    runner._git(repo, "add", ".")
    runner._git(repo, "commit", "-m", "next")
    new_head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    assert any("stale" in b for b in fi.verify_manifest(repo, manifest, code_sha=new_head))


def test_manifest_claiming_a_sha_with_a_different_suite_is_refused(good):
    repo, _, manifest = good
    (repo / "tests/fault_injection/test_synthetic_fi.py").write_text(
        (repo / "tests/fault_injection/test_synthetic_fi.py").read_text() + "\n# changed\n"
    )
    runner._git(repo, "commit", "-am", "suite changed")
    new_head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    manifest = dict(manifest, code_sha=new_head)
    assert any("does not match the FI suite" in b for b in fi.verify_manifest(repo, manifest, code_sha=new_head))


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda m: m["scenarios"].pop("FI-7"), "FI-7 has no result"),
        (lambda m: m["scenarios"]["LW"].update(result="FAIL", failed=1), "LW is 'FAIL'"),
        (lambda m: m["scenarios"]["JW"].update(skipped=1), "JW"),
        (lambda m: m.update(pytest_exit_code=1), "did not exit cleanly"),
    ],
)
def test_any_required_scenario_not_passing_blocks(good, mutate, needle):
    repo, head, manifest = good
    mutate(manifest)
    assert any(needle in b for b in fi.verify_manifest(repo, manifest, code_sha=head))


def test_forged_pass_manifest_cannot_hide_a_failing_exact_sha_suite(tmp_path):
    repo = _repo(tmp_path, extra="def test_fi3_regression():\n    assert False\n")
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    make_fi_manifest(repo, head)
    manifest = json.loads((repo / "fi_manifest.json").read_text())
    blockers = fi.verify_manifest(repo, manifest, code_sha=head)
    assert any("mechanical exact-SHA FI suite rerun did not exit cleanly" in b for b in blockers)
    assert any("mechanical exact-SHA fault-injection scenario FI-3" in b for b in blockers)


def test_generate_cli_refuses_overwriting_tracked_file(tmp_path):
    repo = _repo(tmp_path)
    victim = repo / "tests/fault_injection/test_synthetic_fi.py"
    before = victim.read_bytes()
    assert fi.main(["generate", "--repo-root", str(repo), "--out", str(victim)]) == 2
    assert victim.read_bytes() == before


def test_generate_cli_refuses_output_outside_repo(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.json"
    assert fi.main(["generate", "--repo-root", str(repo), "--out", str(outside)]) == 2
    assert not outside.exists()

def test_unknown_code_sha_blocks(good):
    repo, _, manifest = good
    assert any("exact code SHA" in b for b in fi.verify_manifest(repo, manifest, code_sha=None))


def test_exact_sha_runner_ignores_inherited_pytest_addopts(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k definitely_not_a_real_fi_test")
    code, cases = fi.run_suite_at(repo, head, python=sys.executable)
    assert code == 0
    executed = {name.split("[", 1)[0] for name, _ in cases if fi.scenario_for_test(name)}
    assert executed == fi.committed_fi_tests_at(repo, head)


def test_exact_sha_runner_pins_pytest_config_to_extracted_tree(tmp_path):
    repo = _repo(tmp_path)
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -k definitely_not_a_real_fi_test\n", encoding="utf-8")
    runner._git(repo, "add", "pytest.ini")
    runner._git(repo, "commit", "-m", "hostile pytest config")
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    code, cases = fi.run_suite_at(repo, head, python=sys.executable)
    assert code == 0
    executed = {name.split("[", 1)[0] for name, _ in cases if fi.scenario_for_test(name)}
    assert executed == fi.committed_fi_tests_at(repo, head)


def test_exact_sha_runner_rejects_export_ignored_fi_test(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".gitattributes").write_text(
        "tests/fault_injection/test_synthetic_fi.py export-ignore\n",
        encoding="utf-8",
    )
    runner._git(repo, "add", ".gitattributes")
    runner._git(repo, "commit", "-m", "hide FI suite from archive")
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(fi.FaultInjectionGateError, match="archive FI suite mismatch"):
        fi.run_suite_at(repo, head, python=sys.executable)


def test_exact_sha_runner_has_bounded_timeout(tmp_path):
    repo = _repo(
        tmp_path,
        extra="import time\n\ndef test_fi3_hang():\n    time.sleep(5)\n    assert True\n",
    )
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(fi.FaultInjectionGateError, match="exceeded 1s timeout"):
        fi.run_suite_at(repo, head, python=sys.executable, timeout_seconds=1)


@pytest.mark.parametrize("bad", [True, 1.0, -1, "1"])
def test_manifest_counts_require_nonnegative_exact_integers(good, bad):
    repo, head, manifest = good
    manifest["scenarios"]["FI-1"]["passed"] = bad
    blockers = fi.verify_manifest(repo, manifest, code_sha=head)
    assert any("FI-1 has invalid non-integer counts" in b for b in blockers)


def test_manifest_count_detail_mismatch_blocks(good):
    repo, head, manifest = good
    manifest["scenarios"]["FI-1"]["passed"] = 2
    blockers = fi.verify_manifest(repo, manifest, code_sha=head)
    assert any("FI-1 count/test detail mismatch" in b for b in blockers)


def test_manifest_pytest_exit_code_rejects_bool(good):
    repo, head, manifest = good
    manifest["pytest_exit_code"] = False
    blockers = fi.verify_manifest(repo, manifest, code_sha=head)
    assert any("did not exit cleanly" in b for b in blockers)


# ─── Promotion integration ─────────────────────────────────────────────────


def _promotion(tmp_path, monkeypatch, fault_injection):
    from ops.project_check.promotion import build_promotion_report

    for name, value in (("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32"), ("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16"),
                        ("ENTRY_FILL_MODEL", "ioc_limit"), ("MAX_CONTRACTS_HARD_CAP", "1")):
        monkeypatch.setenv(name, value)
    bundle, code_sha = make_promotion_bundle(tmp_path)
    packet = {
        "canonical_evidence": {"bundles": [bundle]},
        "identity_parity": {
            "candidate_identity_parity": True, "direction_parity": True,
            "entry_stop_target_parity": True, "timeframe_parity": True,
            "lookahead_or_partial_bar_dependency": False,
        },
        "runtime_parity": {"replay_live_logic_confirmed": True},
        "execution_context_claimed": {
            "instrument": "MNQ", "entry_fill_model": "ioc_limit", "entry_tolerance_ticks": 32,
            "contract_qty": 1, "commission_slippage_assumptions": "frozen",
        },
        "stated_classification": "PROMISING BUT UNPROVEN",
    }
    if fault_injection is not None:
        packet["fault_injection"] = fault_injection(tmp_path, code_sha)
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(packet))
    return build_promotion_report(strategy="example", repo_root=tmp_path, evidence_path=path)


def test_promotion_passes_only_with_exact_sha_fi_proof(tmp_path, monkeypatch):
    report = _promotion(tmp_path, monkeypatch, lambda root, sha: {"manifest": make_fi_manifest(root, sha)})
    assert report["gate_pass"] is True, report["classification"]["blockers"]
    assert report["fault_injection"]["verified"] is True


def test_promotion_without_fi_proof_is_blocked(tmp_path, monkeypatch):
    report = _promotion(tmp_path, monkeypatch, None)
    assert report["gate_pass"] is False
    assert any(b.startswith("fault-injection proof") for b in report["classification"]["blockers"])
    assert report["classification"]["effective_classification"] == "BLOCKED_BY_HARD_CAP"


def test_promotion_with_stale_fi_proof_is_blocked(tmp_path, monkeypatch):
    report = _promotion(
        tmp_path, monkeypatch,
        lambda root, sha: {"manifest": make_fi_manifest(root, sha, mutate=lambda m: m.update(code_sha="e" * 40))},
    )
    assert report["gate_pass"] is False
    assert any("stale" in b for b in report["classification"]["blockers"])


def test_promotion_refuses_manifest_outside_repo(tmp_path, monkeypatch):
    def outside(root, sha):
        rel = make_fi_manifest(root, sha)
        external = root.parent / f"{root.name}-outside-fi.json"
        external.write_text((root / rel).read_text(encoding="utf-8"), encoding="utf-8")
        return {"manifest": str(external)}

    report = _promotion(tmp_path, monkeypatch, outside)
    assert report["gate_pass"] is False
    assert any("inside the repository root" in b for b in report["classification"]["blockers"])
