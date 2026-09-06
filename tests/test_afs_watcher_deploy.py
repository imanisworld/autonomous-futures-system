"""
tests/test_afs_watcher_deploy.py

Regression coverage for the reboot-survival artifacts in ops/afs_watcher/.
The watcher itself runs on the box, outside this repo's Python package (it
imports its own local watcher_memory_guard.py, not ops.watcher_memory_guard),
so these are syntax/shape checks plus a real (non-root, non-systemd) exercise
of bootstrap_tmp_state.sh's file-copy behavior — not a systemd integration
test, which needs the actual box.
"""
from __future__ import annotations

import py_compile
import subprocess
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def test_deploy_directory_has_expected_files():
    expected = {
        "watcher.py",
        "watcher_memory_guard.py",
        "run_ro.sh",
        "supervisor.sh",
        "bootstrap_tmp_state.sh",
        "afs-watcher.service",
        "install_afs_watcher_service.sh",
        "README.md",
    }
    present = {p.name for p in DEPLOY_DIR.iterdir()}
    assert expected <= present


@pytest.mark.parametrize("name", ["watcher.py", "watcher_memory_guard.py"])
def test_python_sources_compile(tmp_path, name):
    py_compile.compile(str(DEPLOY_DIR / name), cfile=str(tmp_path / "out.pyc"), doraise=True)


@pytest.mark.parametrize(
    "name",
    ["run_ro.sh", "supervisor.sh", "bootstrap_tmp_state.sh", "install_afs_watcher_service.sh"],
)
def test_shell_scripts_pass_bash_syntax_check(name):
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY_DIR / name)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_systemd_unit_reuses_supervisor_and_restarts_on_boot():
    unit = (DEPLOY_DIR / "afs-watcher.service").read_text()
    assert "Restart=always" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "ExecStart=/bin/bash __AFS_WATCHER_SRC__/supervisor.sh" in unit
    assert "ExecStartPre=/bin/bash __AFS_WATCHER_SRC__/bootstrap_tmp_state.sh" in unit
    # No second watcher process type — the unit only ever launches supervisor.sh.
    assert "watcher.py" not in unit
    assert "tmux" not in unit


def test_install_script_refuses_to_run_as_non_root_and_does_not_start_service():
    script = (DEPLOY_DIR / "install_afs_watcher_service.sh").read_text()
    assert "EUID -ne 0" in script
    assert "systemctl enable" in script
    assert "systemctl start" not in script.split("cat <<MSG")[0]


def test_bootstrap_script_copies_watcher_files_into_runtime_state_dir(tmp_path, monkeypatch):
    runtime_state = tmp_path / "runtime"
    monkeypatch.setenv("AFS_WATCHER_TMP_STATE", str(runtime_state))
    result = subprocess.run(
        ["bash", str(DEPLOY_DIR / "bootstrap_tmp_state.sh")],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "AFS_WATCHER_TMP_STATE": str(runtime_state)},
    )
    assert result.returncode == 0, result.stderr
    for name in ("watcher.py", "watcher_memory_guard.py", "run_ro.sh"):
        copied = runtime_state / name
        assert copied.is_file()
        assert copied.read_bytes() == (DEPLOY_DIR / name).read_bytes()
    # run_ro.sh must be executable by the systemd unit's shell invocation.
    assert (runtime_state / "run_ro.sh").stat().st_mode & 0o700 == 0o700


def test_bootstrap_is_idempotent(tmp_path, monkeypatch):
    runtime_state = tmp_path / "runtime"
    env = {**__import__("os").environ, "AFS_WATCHER_TMP_STATE": str(runtime_state)}
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(DEPLOY_DIR / "bootstrap_tmp_state.sh")],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, result.stderr
    assert (runtime_state / "watcher.py").is_file()
