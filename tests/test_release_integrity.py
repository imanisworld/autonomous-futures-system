"""Tests for ops/release_integrity.py — the fail-closed startup drift gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ops.release_integrity import (
    ENFORCE_ENV,
    FINGERPRINT_PIN_ENV,
    enforce_release_integrity,
    manifest_fingerprint,
    verify_release,
)


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sha(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_release(root: Path) -> Path:
    """Lay out a tiny fake release tree + matching manifest."""
    files = {
        "webhook/runner.py": "RUNNER = 1\n",
        "strategy/signal_engine.py": "ENGINE = 1\n",
        "risk_rules.yaml": "max_daily_loss: 500\n",
    }
    for rel, content in files.items():
        _write(root, rel, content)
    manifest = {
        "schema_version": 1,
        "generated_at": "2026-07-01T00:00:00+00:00",
        "repo": {"branch": "main", "commit": "a" * 40, "dirty": False, "dirty_paths": []},
        "risk_rules_sha256": _sha(files["risk_rules.yaml"]),
        "source_files": {rel: _sha(content) for rel, content in files.items()},
        "source_file_count": len(files),
        "config": {},
        "proof_critical_runtime_overrides": {},
    }
    manifest["fingerprint_sha256"] = manifest_fingerprint(manifest)
    manifest_path = root / "release_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_clean_release_verifies_ok(tmp_path):
    _make_release(tmp_path)
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is True
    assert report["files_checked"] == 3
    assert report["fingerprint_ok"] is True
    assert report["release_commit"] == "a" * 40
    assert report["problems"] == []


def test_modified_file_fails(tmp_path):
    _make_release(tmp_path)
    _write(tmp_path, "webhook/runner.py", "RUNNER = 2  # drifted\n")
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert report["mismatched"] == ["webhook/runner.py"]


def test_missing_file_fails(tmp_path):
    _make_release(tmp_path)
    (tmp_path / "strategy/signal_engine.py").unlink()
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert report["missing"] == ["strategy/signal_engine.py"]


def test_extra_runtime_module_fails(tmp_path):
    """A first-party module not in the manifest is drift (hand-copied hotfix
    or undeployed leftover) even when every listed file matches."""
    _make_release(tmp_path)
    _write(tmp_path, "webhook/hotfix_copy.py", "PATCHED = True\n")
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert report["extra_runtime_files"] == ["webhook/hotfix_copy.py"]


def test_extra_scan_ignores_pycache_and_non_runtime(tmp_path):
    _make_release(tmp_path)
    _write(tmp_path, "webhook/__pycache__/junk.py", "x = 1\n")
    _write(tmp_path, "docs/notes.py", "x = 1\n")
    _write(tmp_path, "webhook/app.py.pre-routing", "old = 1\n")
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is True


def test_manifest_tamper_detected(tmp_path):
    manifest_path = _make_release(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_files"].pop("webhook/runner.py")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert report["fingerprint_ok"] is False


def test_missing_manifest_fails(tmp_path):
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert report["manifest_present"] is False


def test_fingerprint_pin_mismatch_fails(tmp_path, monkeypatch):
    _make_release(tmp_path)
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, "f" * 64)
    report = verify_release(repo_root=tmp_path)
    assert report["ok"] is False
    assert any(FINGERPRINT_PIN_ENV in p for p in report["problems"])


def test_fingerprint_pin_match_ok(tmp_path, monkeypatch):
    manifest_path = _make_release(tmp_path)
    fingerprint = json.loads(manifest_path.read_text(encoding="utf-8"))["fingerprint_sha256"]
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, fingerprint)
    assert verify_release(repo_root=tmp_path)["ok"] is True


def test_enforce_noop_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv(ENFORCE_ENV, raising=False)
    # Broken tree (no manifest at all) — still must not raise when not enforced.
    assert enforce_release_integrity(repo_root=tmp_path) is None


def test_enforce_passes_clean_tree(tmp_path, monkeypatch):
    _make_release(tmp_path)
    monkeypatch.setenv(ENFORCE_ENV, "true")
    report = enforce_release_integrity(repo_root=tmp_path)
    assert report is not None and report["ok"] is True


def test_enforce_refuses_startup_on_drift(tmp_path, monkeypatch):
    _make_release(tmp_path)
    _write(tmp_path, "risk_rules.yaml", "max_daily_loss: 999999\n")
    monkeypatch.setenv(ENFORCE_ENV, "true")
    with pytest.raises(SystemExit, match="RELEASE INTEGRITY FAILURE"):
        enforce_release_integrity(repo_root=tmp_path)


def test_enforce_refuses_startup_without_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv(ENFORCE_ENV, "true")
    with pytest.raises(SystemExit, match="RELEASE INTEGRITY FAILURE"):
        enforce_release_integrity(repo_root=tmp_path)


def test_round_trip_with_release_manifest_builder(tmp_path):
    """A manifest built by ops.release_manifest verifies clean against the
    same tree it was built from (mismatched/missing empty, fingerprint ok)."""
    from ops.release_manifest import build_release_manifest

    repo_root = Path(__file__).resolve().parents[1]
    manifest = build_release_manifest(repo_root)
    out = tmp_path / "release_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    report = verify_release(repo_root=repo_root, manifest_path=out)
    assert report["mismatched"] == []
    assert report["missing"] == []
    assert report["fingerprint_ok"] is True
    assert report["files_checked"] == manifest["source_file_count"]
