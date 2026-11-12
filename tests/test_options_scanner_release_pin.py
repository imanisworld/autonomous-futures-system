"""Options scanner release pin must never inherit the futures release pin."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ops.release_integrity import (
    ENFORCE_ENV,
    FINGERPRINT_PIN_ENV,
    SCANNER_FINGERPRINT_PIN_ENV,
    enforce_release_integrity,
    manifest_fingerprint,
    verify_release,
)


def _simple_release(root: Path) -> str:
    source = root / "app.py"
    source.write_text("APP = True\n", encoding="utf-8")
    manifest = {
        "repo": {"branch": "main", "commit": "a" * 40},
        "source_files": {"app.py": hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    fingerprint = manifest_fingerprint(manifest)
    manifest["fingerprint_sha256"] = fingerprint
    (root / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return fingerprint


def test_scanner_pin_is_required_and_isolated_from_futures_pin(tmp_path, monkeypatch):
    scanner_fp = _simple_release(tmp_path)
    monkeypatch.setenv(ENFORCE_ENV, "true")
    # Even a matching futures pin cannot satisfy the scanner release gate.
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, scanner_fp)
    monkeypatch.delenv(SCANNER_FINGERPRINT_PIN_ENV, raising=False)
    missing = verify_release(
        repo_root=tmp_path, fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV
    )
    assert missing["status"] == "UNPINNED"
    with pytest.raises(SystemExit, match=SCANNER_FINGERPRINT_PIN_ENV):
        enforce_release_integrity(
            repo_root=tmp_path, fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV
        )

    monkeypatch.setenv(SCANNER_FINGERPRINT_PIN_ENV, "f" * 64)
    wrong = verify_release(
        repo_root=tmp_path, fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV
    )
    assert wrong["status"] == "FAIL"
    assert any(SCANNER_FINGERPRINT_PIN_ENV in p for p in wrong["problems"])

    # A matching scanner pin accepts the audited file even if the futures
    # fingerprint is for a completely different release.
    monkeypatch.setenv(SCANNER_FINGERPRINT_PIN_ENV, scanner_fp)
    monkeypatch.setenv(FINGERPRINT_PIN_ENV, "0" * 64)
    correct = enforce_release_integrity(
        repo_root=tmp_path, fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV
    )
    assert correct is not None and correct["status"] == "OK"

    # Content drift must continue to fail despite the correct scanner pin.
    (tmp_path / "app.py").write_text("APP = False\n", encoding="utf-8")
    drifted = verify_release(
        repo_root=tmp_path, fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV
    )
    assert drifted["status"] == "FAIL"
    assert drifted["mismatched"] == ["app.py"]


def test_scanner_checks_integrity_before_local_scanner_imports():
    source = (Path(__file__).resolve().parents[1] / "alert_ranker/app.py").read_text()
    gate = "enforce_release_integrity(fingerprint_pin_env=SCANNER_FINGERPRINT_PIN_ENV)"
    assert gate in source
    assert source.index(gate) < source.index("from .access_gate import")


def test_scanner_import_fails_closed_without_independent_pin():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            ENFORCE_ENV: "true",
            FINGERPRINT_PIN_ENV: "f" * 64,
            SCANNER_FINGERPRINT_PIN_ENV: "",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    outcome = subprocess.run(
        [sys.executable, "-B", "-c", "import alert_ranker.app"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert outcome.returncode != 0
    assert "RELEASE INTEGRITY FAILURE" in outcome.stderr
