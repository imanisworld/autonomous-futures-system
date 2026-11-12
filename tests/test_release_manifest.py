from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ops.release_integrity import FINGERPRINT_PIN_ENV, verify_release
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


def test_staged_release_pins_tracked_data_helpers_and_rejects_new_code(
    tmp_path, monkeypatch
):
    """Clean checkout models atomic_release's tar source, without local .env.

    Data evidence remains outside the source hash, but executable helpers
    actually shipped under data/ must be hashed and audited for new code.
    """
    repo = _repo(tmp_path)
    data = repo / "data" / "options_proof"
    data.mkdir(parents=True)
    (data / "build_manifest.py").write_text("VERSION = 1\n", encoding="utf-8")
    (data / "settings.yaml").write_text("threshold: 2\n", encoding="utf-8")
    (data / "evidence.json").write_text('{"result": "sample"}\n', encoding="utf-8")
    subprocess.check_call(["git", "add", "data"], cwd=repo)
    subprocess.check_call(["git", "commit", "-m", "add tracked data helpers"], cwd=repo)

    clean = tmp_path / "clean-checkout"
    subprocess.check_call(["git", "clone", "-q", str(repo), str(clean)])
    manifest = build_release_manifest(clean)
    assert manifest["repo"]["dirty"] is False
    assert "data/options_proof/build_manifest.py" in manifest["source_files"]
    assert "data/options_proof/settings.yaml" in manifest["source_files"]
    assert "data/options_proof/evidence.json" not in manifest["source_files"]

    # Stage the tracked contents of the clean checkout. A source checkout can
    # have ignored local secrets; the release verifier must never rely on them.
    staged = tmp_path / "staged-release"
    staged.mkdir()
    for rel in _git(clean, "ls-files", "-z").split("\0"):
        if not rel:
            continue
        dest = staged / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clean / rel, dest)
    (staged / "release_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, manifest["fingerprint_sha256"])
    passed = verify_release(repo_root=staged)
    assert passed["ok"] is True, passed["problems"]

    # New, untracked executable code must not hide inside the excluded
    # evidence directory; byte-for-byte mutation of tracked code must fail.
    rogue = staged / "data" / "options_proof" / "rogue.py"
    rogue.write_text("ROGUE = True\n", encoding="utf-8")
    blocked = verify_release(repo_root=staged)
    assert blocked["status"] == "FAIL"
    assert "data/options_proof/rogue.py" in blocked["extra_runtime_files"]
    rogue.unlink()

    helper = staged / "data" / "options_proof" / "build_manifest.py"
    helper.write_text("VERSION = 2\n", encoding="utf-8")
    tampered = verify_release(repo_root=staged)
    assert tampered["status"] == "FAIL"
    assert "data/options_proof/build_manifest.py" in tampered["mismatched"]
