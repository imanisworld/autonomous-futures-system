import os
import re
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/atomic_release.sh")

# The release tool must carry no box address. Asserted as "no IPv4 literal at
# all" rather than by naming the box, so this check does not itself put the
# address in the repo -- and so it also catches a *different* host being
# hardcoded later, which naming one address never would.
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def test_atomic_release_tool_is_host_agnostic_and_three_phase():
    text = SCRIPT.read_text()
    assert 'BOX="${AFS_BOX:?' in text
    # Loopback is the candidate's own health-check target, not a box identity.
    assert not _IPV4.search(text.replace("127.0.0.1", ""))
    assert "build)" in text
    assert "build_release" in text
    assert "verify)" in text
    assert "verify_release" in text
    assert "promote)" in text
    assert "promote_release" in text
    assert "rollback)" in text
    assert "rollback_release" in text
    assert 'AFS_RELEASES_DIR:-/root/afs-releases' in text
    assert 'AFS_CURRENT_LINK:-/root/autonomous-futures-system' in text


def test_atomic_release_script_parses_with_bash():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_atomic_release_script_is_executable_in_git():
    mode = subprocess.run(
        ["git", "ls-files", "-s", str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[0]
    assert mode == "100755"


def test_box_build_uses_explicit_python313_interpreter():
    text = SCRIPT.read_text()
    build = text.split("build_release() {", 1)[1].split("verify_release() {", 1)[0]

    assert 'box_python="${AFS_BOX_PYTHON:-python3.13}"' in build
    assert 'command -v "\\$box_python"' in build
    assert "box_python_version=" in build
    assert "!= 3.13" in build
    assert "expected 3.13" in build
    assert (
        "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='$RELEASES/$sha' "
        '"\\$box_python" -m ops.dependency_lock check-python'
    ) in build
    assert '"\\$box_python" -m venv \'$RELEASES/$sha/.venv\'' in build
    assert "python3 -m venv '$RELEASES/$sha/.venv'" not in build


def test_half_built_release_cannot_verify_or_promote():
    text = SCRIPT.read_text()
    build = text.split("build_release() {", 1)[1].split("verify_release() {", 1)[0]
    verify = text.split("verify_release() {", 1)[1].split(
        "# Decides whether", 1
    )[0]
    promote = text.split("promote_release() {", 1)[1].split("rollback_release() {", 1)[0]

    marker = "$SHARED/release-complete/$sha"
    assert marker in build and marker in verify and marker in promote

    # Completion is published only after box-side dependency and integrity
    # checks have succeeded. A failed build may leave a directory, but no
    # completion proof.
    marker_publish = build.index("mv -f '$SHARED/release-complete/$sha.tmp'")
    assert marker_publish > build.index("/pip' check")
    assert marker_publish > build.index("-m ops.dependency_lock check-freeze")
    assert marker_publish > build.index("-m ops.release_integrity --repo-root '$RELEASES/$sha'")

    # Verify refuses before it starts a candidate, and promote refuses before
    # it mutates .env / symlinks / systemd.
    verify_gate = verify.index("test -f '$SHARED/release-complete/$sha'")
    assert verify_gate < verify.index("systemd-run --unit='$unit'")

    promote_gate = promote.index("test -f '$SHARED/release-complete/$sha'")
    assert promote_gate < promote.index("sed -i '/^EXPECTED_RELEASE_FINGERPRINT=")
    assert promote_gate < promote.index("ln -s '$RELEASES/$sha' '$CURRENT.next'")

    # Marker is bound to the release manifest fingerprint, not mere existence.
    assert 'test "\\$complete_fp" = "\\$release_fp"' in verify
    assert 'test "\\$complete_fp" = "\\$fp"' in promote


def test_release_actions_reject_moving_refs_and_require_exact_sha():
    repo_root = SCRIPT.parent.parent.resolve()
    env = os.environ.copy()
    env["AFS_BOX"] = "unused"

    guard = subprocess.run(
        [
            "bash",
            "-c",
            f'''source "{SCRIPT.resolve()}"
_require_exact_sha "{'a' * 40}"
if _require_exact_sha "origin/main"; then exit 9; fi
if _require_exact_sha "{'A' * 40}"; then exit 10; fi
if _require_exact_sha "{'a' * 39}"; then exit 11; fi
''',
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert guard.returncode == 0, guard.stderr

    cli = subprocess.run(
        ["bash", str(SCRIPT), "build", "origin/main"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert cli.returncode == 64
    assert "exact 40-character lowercase commit SHA" in cli.stderr


def test_candidate_is_forced_observe_only_before_promotion():
    text = SCRIPT.read_text()
    assert "--setenv=SCHEDULE_MODE=always_on_shadow" in text
    assert "--setenv=EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow" in text
    assert "--setenv=HTF_DIRECTION_MODE=off" in text
    assert "--setenv=EXPECTED_PROOF_HTF_DIRECTION_MODE=off" in text
    assert "--setenv=EXIT_MODE=static" in text
    assert "--setenv=EXPECTED_PROOF_EXIT_MODE=static" in text
    assert "s.bind((\\\"127.0.0.1\\\", 0))" in text
    assert "--port '$port'" in text
    # EnvironmentFile overrides Environment= in systemd, so BROKER must be
    # removed from the candidate env file itself and replaced there with paper.
    assert "grep -Ev '^(EXPECTED_RELEASE_FINGERPRINT|BROKER)='" in text
    assert "BROKER=paper" in text
    assert "cleanup_candidate()" in text
    assert "systemctl stop '$unit' >/dev/null 2>&1 || true" in text
    assert "trap cleanup_candidate EXIT" in text


def test_candidate_can_verify_with_current_production_pins():
    text = SCRIPT.read_text()
    assert "AFS_VERIFY_POSTURE:-shadow_baseline" in text
    assert '== "preserve_current"' in text
    assert 'candidate_overrides=""' in text
    assert 'posture_label="current strategy/exit pins with paper-isolated broker"' in text


def test_promote_supports_baseline_and_operational_postures():
    text = SCRIPT.read_text()
    # Path 1: unchanged reset-baseline gate, still checked before promoting.
    assert "SCHEDULE_MODE=always_on_shadow'" in text
    assert "EXIT_MODE=static'" in text
    # Path 2: the approved operational posture is checked as an alternative,
    # never silently -- it always requires the behavior-neutral diff check.
    assert "SCHEDULE_MODE=current'" in text
    assert "EXIT_MODE=runner_shadow'" in text
    assert "ops.behavior_neutral_gate" in text
    assert "EXPECTED_LIVE_COMMIT" in text
    assert "promotion refused" in text


def test_promote_refuses_when_neither_posture_matches():
    text = SCRIPT.read_text()
    assert "matches neither the reset baseline" in text


def test_promotion_enforces_release_integrity_on_service_start():
    text = SCRIPT.read_text()
    assert "'Environment=RELEASE_INTEGRITY_ENFORCED=true'" in text


def test_promote_and_rollback_rearm_readonly_watcher_after_release_verification():
    text = SCRIPT.read_text()
    promote = text.split("promote_release() {", 1)[1].split("rollback_release() {", 1)[0]
    rollback = text.split("rollback_release() {", 1)[1].split(
        "# Guarded so tests can", 1
    )[0]

    for block in (promote, rollback):
        assert "systemctl cat afs-watcher.service" in block
        assert "watcher_src='$CURRENT/ops/afs_watcher'" in block
        assert "systemctl show afs-watcher.service -p WorkingDirectory --value" in block
        assert "watcher_memory_guard.py run_ro.sh supervisor.sh bootstrap_tmp_state.sh" in block
        assert 'if test -f "\$watcher_src/bounded_log_pipe.py"; then' in block
        assert 'cp -f "\$watcher_src/bounded_log_pipe.py" "\$watcher_dest/bounded_log_pipe.py"' in block
        assert 'cmp -s "\$watcher_src/bounded_log_pipe.py" "\$watcher_dest/bounded_log_pipe.py"' in block
        assert "cmp -s" in block
        assert "watcher_triage.py" in block
        assert "systemctl restart afs-watcher.service" in block
        assert "systemctl is-active afs-watcher.service" in block
        integrity_at = block.index("-m ops.release_integrity --repo-root '$CURRENT'")
        sync_at = block.index("watcher_src='$CURRENT/ops/afs_watcher'")
        restart_at = block.index("systemctl restart afs-watcher.service")
        assert integrity_at < sync_at < restart_at


def test_rollback_restores_previous_release_proof_pins_and_verifies_integrity():
    text = SCRIPT.read_text()
    assert "prev_fp=" in text
    assert "prev_commit=" in text
    assert "prev_risk=" in text
    assert "EXPECTED_RELEASE_FINGERPRINT=%s" in text
    assert "EXPECTED_LIVE_COMMIT=%s" in text
    assert "EXPECTED_RISK_RULES_SHA256=%s" in text
    # One post-activation integrity check for promote and one for rollback.
    assert text.count("-m ops.release_integrity --repo-root '$CURRENT'") == 2


def test_promotion_and_rollback_use_atomic_symlink_replacement():
    text = SCRIPT.read_text()
    assert "mv -Tf '$CURRENT.next' '$CURRENT'" in text
    assert "current.previous" in text


def _render_remote_command(action: str) -> tuple[int, str]:
    """Render one action's remote command with the ssh transport stubbed.

    Returns (argument count, the command as a single string). The count is the
    point: the command must reach ``remote`` — and therefore ssh — as ONE
    argument, so the string the box executes is the string that was written.
    """
    repo_root = SCRIPT.parent.parent.resolve()
    env = os.environ.copy()
    env["AFS_BOX"] = "unused"
    # promote now has a local exact-SHA CI/ancestry preflight before any box
    # contact. This helper is testing remote quoting only, so stub that local
    # preflight while leaving the remote command under test unchanged.
    env["RELEASE_CI_PROOF"] = str(SCRIPT.resolve())
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'''source "{SCRIPT.resolve()}"
REF={"a" * 40}
git() {{ return 0; }}
python3() {{ return 0; }}
deploy_lock_acquire() {{ DEPLOY_LOCK_OWNER=stub; return 0; }}
deploy_lock_release() {{ return 0; }}
_promote_gate_check() {{ return 0; }}
remote() {{ echo "ARGC=$#" >&2; printf '%s' "$1"; }}
{action}_release
''',
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    argc = int(re.search(r"ARGC=(\d+)", proc.stderr).group(1))
    return argc, proc.stdout


def test_remote_commands_reach_ssh_as_a_single_argument():
    """Bare `"` inside a remote block silently breaks it into several words.

    promote and rollback each built their command with unescaped double quotes
    around the activation check, which terminated the outer quoting: the string
    left the script as 6 arguments, ssh rejoined them with single spaces, and
    every double-quoted value in that region arrived on the box UNQUOTED. It
    reassembled into a valid script by luck, with no error to notice.
    """
    for action in ("promote", "rollback"):
        argc, rendered = _render_remote_command(action)
        assert argc == 1, f"{action} remote command split into {argc} arguments"
        # The activation check must reach the box with its quoting intact.
        assert 'test "$actual_cwd" = "$expected_cwd"' in rendered
        assert 'readlink -f "/proc/$pid/cwd"' in rendered
        # And what the box receives must be a valid shell script.
        subprocess.run(["bash", "-n"], input=rendered, text=True, check=True)


def test_build_cleanup_trap_captures_paths_before_function_returns():
    text = SCRIPT.read_text()
    assert "trap \"git worktree remove -f '$work'" in text


def test_promote_appends_durable_release_history_after_verification():
    """Releases promoted here must land in the durable history file.

    $RELEASES is pruned to a rolling window, so it is not a history. Only
    afs-deploy.sh --release appended release_history.txt, which meant a release
    promoted through this script could run and then have its directory evicted
    with no on-box record of the SHA that was live.
    """
    text = SCRIPT.read_text()
    promote = text.split("promote_release() {", 1)[1].split("rollback_release() {", 1)[0]

    assert "release_history.txt" in promote
    # Recorded only after activation and the post-activation integrity check
    # pass, so the file lists releases that came up, never attempts.
    assert promote.index("release_history.txt") > promote.index(
        "-m ops.release_integrity --repo-root '$CURRENT'"
    )
    # Same rules as the afs-deploy.sh append: deduped, atomic, append-only.
    assert "grep -q '^$sha" in promote
    assert "$hist.tmp." in promote
    assert "mv -f" in promote
    # The record carries sha, UTC timestamp and release dir, like the other path.
    assert "'%s %s %s\\n' '$sha'" in promote
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in promote
    # Nothing in the durable-history update block may delete or rewrite rows.
    history_tail = promote.split("release_history.txt", 1)[1]
    history_block = history_tail.split(
        "# The watcher runs from a persistent shared source directory", 1
    )[0]
    assert "rm -f" not in history_block


def test_watcher_sync_does_not_touch_persistent_secret_or_backup_files():
    text = SCRIPT.read_text()
    promote = text.split("promote_release() {", 1)[1].split("rollback_release() {", 1)[0]
    rollback = text.split("rollback_release() {", 1)[1].split(
        "# Guarded so tests can", 1
    )[0]
    for block in (promote, rollback):
        assert ".triage_key" not in block
        assert "watcher.py.bak" not in block
        assert "rm -rf" not in block


def test_release_ops_modules_never_write_bytecode_into_a_release():
    """ops.release_integrity refuses any __pycache__ in a release (#1056).

    Every run of the release's own ops modules must disable bytecode writes, or
    the check creates the files it then refuses: locally in the build worktree
    (and its tarball), and on the box as root, where chmod a-w does not stop
    the write -- including against the LIVE release after promote/rollback have
    already restarted the service.
    """
    text = SCRIPT.read_text()
    build = text.split("build_release() {", 1)[1].split("verify_release() {", 1)[0]
    export_at = build.index("export PYTHONDONTWRITEBYTECODE=1")
    assert export_at < build.index("python3 -m ops.release_manifest")
    assert export_at < build.index("python3 -m ops.release_integrity --repo-root . --manifest")
    assert export_at < build.index('tar czf "$archive"')

    remote_calls = re.findall(
        r"(\S+) \\\\\n\s+(\S+) PYTHONPATH='\$\w+(?:/\$sha)?' '[^']+/\.venv/bin/python' \\\n"
        r"\s+-m ops\.release_integrity",
        text,
    )
    assert [no_pyc for _, no_pyc in remote_calls] == ["PYTHONDONTWRITEBYTECODE=1"] * 4
    assert text.count("-m ops.release_integrity --repo-root '$") == 4

    for action in ("promote", "rollback"):
        argc, rendered = _render_remote_command(action)
        assert argc == 1
        assert "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH='" in rendered
        subprocess.run(["bash", "-n"], input=rendered, text=True, check=True)


def test_every_release_integrity_check_is_given_the_fingerprint_pin():
    """#1056: without EXPECTED_RELEASE_FINGERPRINT the check reports UNPINNED
    and exits 1, which stops build/verify and fails promote/rollback after the
    service has already restarted on the new release."""
    text = SCRIPT.read_text()
    build = text.split("build_release() {", 1)[1].split("verify_release() {", 1)[0]
    local = build.split("python3 -m ops.release_manifest", 1)[1].split('tar czf "$archive"', 1)[0]
    assert 'EXPECTED_RELEASE_FINGERPRINT="$(python3 -c' in local
    assert '["fingerprint_sha256"]' in local

    pins = re.findall(
        r"EXPECTED_RELEASE_FINGERPRINT=(\S+) \\\\\n\s+PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=",
        text,
    )
    # build (box), verify, promote, rollback -- each with the fingerprint that
    # step already holds; promote/rollback use the value written to .env.
    assert pins == [r'\"\$built_fp\"', "'$fingerprint'", r'\"\$fp\"', r'\"\$prev_fp\"']

    for action in ("promote", "rollback"):
        _, rendered = _render_remote_command(action)
        var = "fp" if action == "promote" else "prev_fp"
        assert f'EXPECTED_RELEASE_FINGERPRINT="${var}" \\\n' in rendered
        assert rendered.index(f"{var}=$(") < rendered.index(f'EXPECTED_RELEASE_FINGERPRINT="${var}"')
