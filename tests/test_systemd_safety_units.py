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


def _service_bodies(installer: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    marker = "cat > /etc/systemd/system/"
    cursor = 0
    while True:
        start = installer.find(marker, cursor)
        if start < 0:
            break
        name_start = start + len(marker)
        name_end = installer.find(" << EOF", name_start)
        name = installer[name_start:name_end]
        body_start = name_end + len(" << EOF\n")
        body_end = installer.find("\nEOF", body_start)
        bodies[name] = installer[body_start:body_end]
        cursor = body_end + 4
    return bodies


def test_immutable_release_python_timers_cannot_write_bytecode():
    """#1056 fails a release that contains __pycache__. Every Python process this
    installer starts inside /root/autonomous-futures-system must be told not to
    write bytecode. Timer cadence stays on the existing schedules.
    """
    installer = (ROOT / "scripts/install_timers.sh").read_text()
    bodies = _service_bodies(installer)
    python_services = {
        name: body
        for name, body in bodies.items()
        if name.endswith(".service")
        and "$VENV" in body
        and "$REPO" in body
    }
    assert set(python_services) == {"calendar-sync.service", "feed-watchdog.service"}
    for name, body in python_services.items():
        assert "Environment=PYTHONDONTWRITEBYTECODE=1" in body, name
        assert body.index("Environment=PYTHONDONTWRITEBYTECODE=1") < body.index("ExecStart=")

    assert "OnCalendar=*-*-01 06:00:00 UTC" in bodies["calendar-sync.timer"]
    assert "OnBootSec=3min" in bodies["feed-watchdog.timer"]
    assert "OnUnitActiveSec=5min" in bodies["feed-watchdog.timer"]
    assert "ExecStart=$VENV $REPO/scripts/feed_watchdog.py" in python_services["feed-watchdog.service"]
    assert "sync_news_calendar.py --apply" in python_services["calendar-sync.service"]
