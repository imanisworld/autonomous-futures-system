from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ops.release_manifest import build_release_manifest


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.check_call(["git", "init", "-b", "main"], cwd=repo)
    subprocess.check_call(["git", "config", "user.email", "test@example.com"], cwd=repo)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=repo)
    (repo / "risk_rules.yaml").write_text(
        """
trading_mode:
  live_trading_enabled: false
  paper_mode: true
instruments:
  allowed: [MES, MNQ]
daily_limits:
  max_trades_per_day: 3
  max_consecutive_losses: 2
  max_daily_loss: 150
strategy:
  enabled_concepts: [orb_breakout]
  disabled_concepts_per_instrument: {MES: [vwap_reclaim]}
""".lstrip(),
        encoding="utf-8",
    )
    (repo / "app.py").write_text("print('safe')\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (repo / ".env").write_text("WEBHOOK_SECRET=never-print\n", encoding="utf-8")
    subprocess.check_call(
        ["git", "add", "risk_rules.yaml", "app.py", ".gitignore"], cwd=repo
    )
    subprocess.check_call(["git", "commit", "-m", "init"], cwd=repo)
    return repo


def test_manifest_is_deterministic_and_secret_free(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    monkeypatch.setenv("WEBHOOK_SECRET", "runtime-secret-never-print")
    when = datetime(2026, 6, 30, 20, 0, tzinfo=timezone.utc)

    first = build_release_manifest(repo, generated_at=when)
    second = build_release_manifest(repo, generated_at=when)

    assert first == second
    assert first["repo"]["commit"] == _git(repo, "rev-parse", "HEAD")
    assert first["repo"]["dirty"] is False
    assert first["source_file_count"] == 3
    assert ".env" not in first["source_files"]
    rendered = json.dumps(first)
    assert "runtime-secret-never-print" not in rendered
    assert "never-print" not in rendered


def test_manifest_records_safe_config_and_proof_override(monkeypatch, tmp_path):
    repo = _repo(tmp_path)
    monkeypatch.setenv("MOMENTUM_ENTRY_REANCHOR", "false")
    monkeypatch.setenv("EXPECTED_PROOF_MOMENTUM_ENTRY_REANCHOR", "false")

    manifest = build_release_manifest(repo)

    assert manifest["config"]["trading_mode"] == {
        "live_trading_enabled": False,
        "paper_mode": True,
    }
    assert manifest["config"]["enabled_concepts"] == ["orb_breakout"]
    assert manifest["config"]["daily_limits"]["max_trades_per_day"] == 3
    override = manifest["proof_critical_runtime_overrides"][
        "MOMENTUM_ENTRY_REANCHOR"
    ]
    assert override == {
        "observed": "false",
        "expected": "false",
        "pinned": True,
        "matches": True,
    }


def test_manifest_fingerprint_changes_with_tracked_source(tmp_path):
    repo = _repo(tmp_path)
    before = build_release_manifest(repo)
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    after = build_release_manifest(repo)

    assert after["repo"]["dirty"] is True
    assert before["fingerprint_sha256"] != after["fingerprint_sha256"]
    assert before["source_files"]["app.py"] != after["source_files"]["app.py"]
