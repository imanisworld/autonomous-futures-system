from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path

import pytest

from ops.live_box_guard import (
    PROOF_CRITICAL_RUNTIME_OVERRIDES,
    WEBHOOK_SECRET_ENV_NAMES,
    live_box_drift_report,
)


@pytest.fixture(autouse=True)
def _isolate_proof_runtime_overrides(monkeypatch):
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"EXPECTED_PROOF_{name}", raising=False)
    monkeypatch.delenv("ENABLE_MANUAL_EXECUTION_CONTROLS", raising=False)
    for name in WEBHOOK_SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-primary")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "test-rotation")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text("trading_mode:\n  live_trading_enabled: false\n", encoding="utf-8")
    (repo / "logs").mkdir()
    subprocess.check_call(["git", "add", "risk_rules.yaml"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_live_box_drift_guard_verifies_pinned_repo(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    commit = _git(repo, "rev-parse", "HEAD")
    digest = hashlib.sha256((repo / "risk_rules.yaml").read_bytes()).hexdigest()
    log_dir = repo / "logs"

    monkeypatch.setenv("EXPECTED_LIVE_REPO_ROOT", str(repo))
    monkeypatch.setenv("EXPECTED_LIVE_BRANCH", "main")
    monkeypatch.setenv("EXPECTED_LIVE_COMMIT", commit)
    monkeypatch.setenv("EXPECTED_RISK_RULES_SHA256", digest)
    monkeypatch.setenv("EXPECTED_RUNTIME_JOURNAL_DIR", str(log_dir))
    monkeypatch.setenv("EXPECTED_RUNTIME_EVIDENCE_SOURCE", "active_box_journal_and_status")
    monkeypatch.setenv("RUNTIME_EVIDENCE_SOURCE", "active_box_journal_and_status")
    monkeypatch.setenv("WEBHOOK_SECRET", "primary-test-secret")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "rotation-test-secret")

    report = live_box_drift_report(repo_root=repo, log_dir=log_dir)

    assert report["ok"] is True
    assert report["status"] == "ok"
    assert report["mismatches"] == []
    assert report["missing_pins"] == []


def test_security_runtime_report_is_redacted_and_manual_is_inert(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "primary-never-print-me")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET_NEXT", "next-never-print-me")

    report = live_box_drift_report(
        repo_root=repo,
        log_dir=repo / "logs",
        manual_controls_enabled=False,
    )
    security = report["security_runtime"]

    assert security["manual_endpoint"]["effectively_inert"] is True
    assert security["manual_endpoint"]["evidence"] == "loaded_runtime_config"
    assert security["webhook_secret_rotation"]["rotation_ready"] is True
    rendered = repr(security)
    assert "primary-never-print-me" not in rendered
    assert "next-never-print-me" not in rendered


def test_security_runtime_fails_closed_when_manual_controls_are_enabled(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "primary")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "secondary")

    report = live_box_drift_report(
        repo_root=repo,
        log_dir=repo / "logs",
        manual_controls_enabled=True,
    )

    assert report["status"] == "error"
    assert report["security_runtime"]["ok"] is False
    assert report["security_runtime"]["manual_endpoint"]["effectively_inert"] is False


def test_security_runtime_warns_when_rotation_is_not_staged(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "only-primary")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET")
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET_NEXT", raising=False)

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")
    rotation = report["security_runtime"]["webhook_secret_rotation"]

    assert report["status"] == "warn"
    assert rotation["primary_configured"] is True
    assert rotation["rotation_ready"] is False
    assert rotation["configured_env_names"] == ["WEBHOOK_SECRET"]


def test_security_runtime_errors_without_primary_even_if_alias_exists(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.delenv("WEBHOOK_SECRET")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "alias-only")

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["status"] == "error"
    assert report["security_runtime"]["webhook_secret_rotation"]["primary_configured"] is False


def test_live_box_drift_guard_reports_mismatched_commit_and_config(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)

    monkeypatch.setenv("EXPECTED_LIVE_REPO_ROOT", str(repo))
    monkeypatch.setenv("EXPECTED_LIVE_BRANCH", "main")
    monkeypatch.setenv("EXPECTED_LIVE_COMMIT", "0" * 40)
    monkeypatch.setenv("EXPECTED_RISK_RULES_SHA256", "1" * 64)
    monkeypatch.setenv("EXPECTED_RUNTIME_JOURNAL_DIR", str(repo / "logs"))

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["ok"] is False
    assert report["status"] == "error"
    assert {"commit", "risk_rules_sha256"}.issubset(set(report["mismatches"]))


def test_live_box_drift_guard_warns_when_pins_are_absent(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    for name in (
        "EXPECTED_LIVE_REPO_ROOT",
        "EXPECTED_LIVE_BRANCH",
        "EXPECTED_LIVE_COMMIT",
        "EXPECTED_RISK_RULES_SHA256",
        "EXPECTED_RUNTIME_JOURNAL_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["ok"] is False
    assert report["status"] == "warn"
    assert {"branch", "commit", "risk_rules_sha256"}.issubset(set(report["missing_pins"]))


def test_live_box_guard_reports_active_unpinned_strategy_override(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("VWAP_ENTRY_MAX_DISTANCE_TICKS", "12")

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert report["ok"] is False
    assert "VWAP_ENTRY_MAX_DISTANCE_TICKS" in report["active_runtime_overrides"]
    assert "VWAP_ENTRY_MAX_DISTANCE_TICKS" in report["unpinned_runtime_overrides"]
    item = next(
        item for item in report["proof_critical_runtime_overrides"]
        if item["name"] == "VWAP_ENTRY_MAX_DISTANCE_TICKS"
    )
    assert item["observed"] == "12"
    assert item["pin"] == "EXPECTED_PROOF_VWAP_ENTRY_MAX_DISTANCE_TICKS"


def test_live_box_guard_accepts_matching_runtime_override_pin(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("MOMENTUM_ENTRY_REANCHOR", "true")
    monkeypatch.setenv("EXPECTED_PROOF_MOMENTUM_ENTRY_REANCHOR", "true")

    report = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")

    assert "MOMENTUM_ENTRY_REANCHOR" not in report["unpinned_runtime_overrides"]
    comparison = next(
        item for item in report["comparisons"]
        if item["name"] == "runtime_override:MOMENTUM_ENTRY_REANCHOR"
    )
    assert comparison["ok"] is True


def test_live_box_guard_can_pin_override_as_unset(monkeypatch, tmp_path):
    repo = _init_repo(tmp_path)
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", raising=False)
    monkeypatch.setenv("EXPECTED_PROOF_ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "<unset>")

    clean = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")
    comparison = next(
        item for item in clean["comparisons"]
        if item["name"] == "runtime_override:ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ"
    )
    assert comparison["ok"] is True

    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "16")
    drifted = live_box_drift_report(repo_root=repo, log_dir=repo / "logs")
    assert "runtime_override:ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ" in drifted["mismatches"]


def test_execution_env_reads_are_classified_for_proof_guard():
    """CI tripwire: a new execution-path env read needs an explicit proof decision."""
    root = Path(__file__).resolve().parents[1]
    source_paths = (
        root / "config/settings.py",
        root / "execution/tradovate_broker.py",
        root / "webhook/runner.py",
    )
    discovered: set[str] = set()
    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            is_getenv = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"
            )
            is_env_bool = (
                isinstance(node.func, ast.Name)
                and node.func.id == "_env_bool"
            )
            if is_getenv or is_env_bool:
                discovered.add(first.value)

    # These cannot alter futures setup eligibility, sizing, entry/fill behavior,
    # or exits. A new exemption should carry the same level of scrutiny.
    non_proof_runtime_env = {
        # Webhook intake / ops hardening (dedupe, maintenance 503, rate limit,
        # status auth, secret rotation): they gate ingest availability, never
        # setup eligibility, sizing, entry/fill behavior, or exits.
        "DEDUPE_TTL_SECONDS",
        "MAINTENANCE_FLAG_PATH",
        "MAINTENANCE_MODE",
        "STATUS_AUTH_TOKEN",
        "TRADINGVIEW_WEBHOOK_SECRET",
        "TRADINGVIEW_WEBHOOK_SECRET_NEXT",
        "WEBHOOK_RATE_LIMIT_PER_MINUTE",
        "DISCORD_HEARTBEAT_ENABLED",
        "DISCORD_NOTIFICATIONS_ENABLED",
        "DISCORD_WEBHOOK_URL",
        "ENABLE_MANUAL_EXECUTION_CONTROLS",
        "GEX_OBSERVE_ENABLED",
        "GEX_OBSERVE_MAX_DTE",
        "GEX_SHADOW_ANALYSIS_ENABLED",
        "LOG_DIR",
        "LOG_LEVEL",
        "OPTIONS_COMPANION_ENABLED",
            "OPTIONS_COMPANION_MODE",
            "OPTIONS_COMPANION_SQLITE_PATH",
            "OPTIONS_COMPANION_STRICT_SIGNA",
            "PUBLIC_ACCOUNT_ID",
            "PUBLIC_API_KEY",
            "PUBLIC_BASE_URL",
            "RANGE_OBSERVE_ENABLED",
            "SIGNA_API_ENABLED",
        "SIGNA_API_KEY",
        "SIGNA_BASE_URL",
        "SIGNA_TIMEOUT_SECONDS",
        "TRADOVATE_API_KEY_ID",
        "TRADOVATE_API_KEY_SECRET",
        "TRADOVATE_PASSWORD",
        "TRADOVATE_USERNAME",
    }
    classified = set(PROOF_CRITICAL_RUNTIME_OVERRIDES) | non_proof_runtime_env

    assert discovered <= classified, (
        "Execution-path environment reads need proof-critical classification: "
        f"{sorted(discovered - classified)}"
    )
