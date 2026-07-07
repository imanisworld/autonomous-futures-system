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


def test_first_acquire_succeeds_and_writes_owned_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "abc123" "test-script" ""\n'
            'cat "$LOCK_DIR/meta.txt"\n'
            'echo "OWNER_VAR=$DEPLOY_LOCK_OWNER"\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "ref=abc123" in result.stdout
        assert "script=test-script" in result.stdout
        assert "owner=" in result.stdout
        assert "OWNER_VAR=" in result.stdout
        owner_var = next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("OWNER_VAR=")
        )
        assert owner_var, "DEPLOY_LOCK_OWNER should be non-empty after acquire"
        assert f"owner={owner_var}" in result.stdout


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


def test_force_lock_breaks_existing_lock_with_a_new_owner_token():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'FIRST_OWNER="$DEPLOY_LOCK_OWNER"\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" "--force-lock"\n'
            'echo "FIRST_OWNER=$FIRST_OWNER"\n'
            'echo "SECOND_OWNER=$DEPLOY_LOCK_OWNER"\n'
            'cat "$LOCK_DIR/meta.txt"\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "ref=second" in result.stdout
        first_owner = next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("FIRST_OWNER=")
        )
        second_owner = next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("SECOND_OWNER=")
        )
        assert first_owner != second_owner, "force-break must mint a fresh owner token"
        assert f"owner={second_owner}" in result.stdout


def test_release_allows_next_acquire():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'deploy_lock_release "$LOCK_DIR" "$DEPLOY_LOCK_OWNER"\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" ""\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr


def test_stale_owner_release_after_force_break_is_a_noop_and_true_owner_can_still_release():
    # Regression for the ownership bug: A acquires, B force-breaks and
    # acquires, A calls release with its now-stale token -- the lock must
    # survive and still belong to B. B's own release must then remove it.
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        script_body = (
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'A_OWNER="$DEPLOY_LOCK_OWNER"\n'
            'deploy_lock_acquire "$LOCK_DIR" "second" "script-b" "--force-lock" >/dev/null\n'
            'B_OWNER="$DEPLOY_LOCK_OWNER"\n'
            'deploy_lock_release "$LOCK_DIR" "$A_OWNER"\n'
            'test -d "$LOCK_DIR" && echo LOCK_SURVIVED_A_RELEASE\n'
            'grep -q "owner=$B_OWNER" "$LOCK_DIR/meta.txt" && echo STILL_B_OWNED\n'
            'deploy_lock_release "$LOCK_DIR" "$B_OWNER"\n'
            'test ! -d "$LOCK_DIR" && echo B_RELEASED_ITS_OWN_LOCK\n'
        )
        result = _run(script_body, lock_dir)
        assert result.returncode == 0, result.stderr
        assert "LOCK_SURVIVED_A_RELEASE" in result.stdout
        assert "STILL_B_OWNED" in result.stdout
        assert "B_RELEASED_ITS_OWN_LOCK" in result.stdout


def test_release_with_no_token_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        result = _run(
            'deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            'deploy_lock_release "$LOCK_DIR" ""\n'
            'test -d "$LOCK_DIR" && echo STILL_PRESENT\n',
            lock_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "STILL_PRESENT" in result.stdout


def test_trap_releases_lock_on_normal_exit():
    with tempfile.TemporaryDirectory() as tmp:
        lock_dir = os.path.join(tmp, "deploy.lock")
        script_body = (
            "(\n"
            '  deploy_lock_acquire "$LOCK_DIR" "first" "script-a" "" >/dev/null\n'
            "  trap 'deploy_lock_release \"$LOCK_DIR\" \"$DEPLOY_LOCK_OWNER\"' EXIT\n"
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
            "  trap 'deploy_lock_release \"$LOCK_DIR\" \"$DEPLOY_LOCK_OWNER\"' EXIT\n"
            "  false\n"
            ")\n"
            'test ! -d "$LOCK_DIR"\n'
        )
        result = _run(script_body, lock_dir)
        assert result.returncode == 0, result.stderr


def test_atomic_release_script_sources_and_uses_ownership_aware_deploy_lock():
    text = (
        Path(__file__).resolve().parent.parent / "scripts" / "atomic_release.sh"
    ).read_text()
    assert 'source "$ROOT/scripts/deploy_lock.sh"' in text
    assert text.count("deploy_lock_acquire") == 4  # build, verify, promote, rollback
    # build has a preliminary trap plus the combined worktree+lock trap = 5 total
    assert text.count("deploy_lock_release") == 5
    assert "DEPLOY_LOCK_OWNER" in text
    assert "FORCE_LOCK=" in text
    # every release call site must pass the owner token, not just the lock dir
    for line in text.splitlines():
        if "deploy_lock_release '$LOCK_DIR'" in line and "DEPLOY_LOCK_OWNER" not in line:
            raise AssertionError(f"release call missing owner token: {line}")
