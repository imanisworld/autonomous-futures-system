"""Regression contracts for systemd safety helpers."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_wide_stop_fallback_unit_uses_module_execution_from_repo_root():
    unit = (ROOT / "deploy/systemd/afs-wide-stop-demo-eod-fallback.service").read_text()
    assert "WorkingDirectory=/root/autonomous-futures-system" in unit
    assert (
        "ExecStart=/root/autonomous-futures-system/.venv/bin/python "
        "-m scripts.wide_stop_demo_eod_fallback"
    ) in unit


def test_wide_stop_fallback_imports_without_pythonpath_from_repo_root():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import scripts.wide_stop_demo_eod_fallback"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_feed_watchdog_installer_uses_shared_environment_file():
    installer = (ROOT / "scripts/install_timers.sh").read_text()
    feed_block = installer.split("# ─── 4. Feed watchdog", 1)[1]
    assert "EnvironmentFile=/root/afs-shared/.env" in feed_block
    assert "EnvironmentFile=$REPO/.env" not in feed_block
