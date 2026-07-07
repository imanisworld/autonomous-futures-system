import os
import subprocess
import tempfile
from pathlib import Path

LOCK_LIB = Path(__file__).resolve().parent.parent / "scripts" / "deploy_lock.sh"

HARNESS = """
set -euo pipefail
source "{lib}"
local_exec() {{ bash -c "$1"; }}
REMOTE_EXEC=local_exec
LOCK_DIR="{lock_dir}"
"""


def _run(script_body, lock_dir):
    script = HARNESS.format(lib=LOCK_LIB, lock_dir=lock_dir) + script_body
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30
    )


def test_first_acquire_succeeds_and_writes_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "abc123" "test-script" ""\n'
            'cat "$LOCK_DIR/meta.txt"\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "ref=abc123" in result.stdout
        assert "script=test-script" in result.stdout


def test_second_acquire_without_force_refuses_and_prints_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" ""\n',
            lock_dir,
        )
        assert result.returncode != 0
        assert "ref=first" in result.stdout
        assert "script=script-a" in result.stdout


def test_force_lock_breaks_existing_lock():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" "--force-lock"\n'
            'cat "$LOCK_DIR/meta.txt"\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "ref=second" in result.stdout


def test_release_allows_next_acquire():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'deploy_lock_release "$LOCK_DIR"\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" ""\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr


def test_trap_releases_lock_on_normal_exit():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        script_body = (
            "(\n"
            '  deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            "  trap 'deploy_lock_release \"$LOCK_DIR\"' EXIT\n"
            ")\n"
            'test ! -d "$LOCK_DIR"\n'
        )
        result = _run(script_body, lock_dir)
        assert result.returncode == 0, result.stderr


def test_trap_releases_lock_on_simulated_failure():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        script_body = (
            "set +e\n"
            "(\n"
            "  set -e\n"
            '  deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            "  trap 'deploy_lock_release \"$LOCK_DIR\"' EXIT\n"
            "  false\n"
            ")\n"
            'test ! -d "$LOCK_DIR"\n'
        )
        result = _run(script_body, lock_dir)
        assert result.returncode == 0, result.stderr


def test_atomic_release_script_sources_and_uses_deploy_lock():
    text = (
        Path(__file__).resolve().parent.parent / "scripts" / "atomic_release.sh"
    ).read_text()
    assert 'source "$ROOT/scripts/deploy_lock.sh"' in text
    assert text.count("deploy_lock_acquire") == 4  # build, verify, promote, rollback
    # build has a preliminary trap plus the combined worktree+lock trap = 5 total
    assert text.count("deploy_lock_release") == 5
    assert "FORCE_LOCK=" in text
