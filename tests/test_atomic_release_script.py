import os
import json
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


def test_deploy_memory_guard_refuses_critical_watcher_state(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"memory_guard": {"level": "CRITICAL"}}))
    env = os.environ.copy()
    env.update({"AFS_BOX": "unused", "AFS_WATCHER_STATE_FILE": str(state)})
    command = f'''source "{SCRIPT.resolve()}"
remote() {{ eval "$1"; }}
deploy_memory_guard_check
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "memory state is CRITICAL, unreadable, or stale" in result.stderr


def _guard_check(state_path, extra_env=None):
    env = os.environ.copy()
    env.update({"AFS_BOX": "unused", "AFS_WATCHER_STATE_FILE": str(state_path)})
    env.update(extra_env or {})
    command = f'''source "{SCRIPT.resolve()}"
remote() {{ eval "$1"; }}
deploy_memory_guard_check
'''
    return subprocess.run(["bash", "-c", command], env=env, capture_output=True, text=True)


def _fresh_state(level="HEALTHY", age_minutes=1):
    from datetime import datetime, timedelta, timezone
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=age_minutes)).isoformat()
    return {"last_tick_utc": stamp, "memory_guard": {"level": level, "reading": {"observed_utc": stamp}}}


def test_deploy_memory_guard_refuses_stale_or_malformed_state(tmp_path):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(_fresh_state(age_minutes=45)))
    result = _guard_check(stale)
    assert result.returncode == 1 and "stale" in result.stderr
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{")
    assert _guard_check(malformed).returncode == 1
    not_object = tmp_path / "list.json"
    not_object.write_text("[]")
    assert _guard_check(not_object).returncode == 1


def test_deploy_memory_guard_allows_fresh_healthy_state_and_honours_window(tmp_path):
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps(_fresh_state(age_minutes=1)))
    assert _guard_check(fresh).returncode == 0
    older = tmp_path / "older.json"
    older.write_text(json.dumps(_fresh_state(age_minutes=40)))
    assert _guard_check(older).returncode == 1
    assert _guard_check(older, {"AFS_WATCHER_STALE_MINUTES": "60"}).returncode == 0


def test_deploy_memory_guard_allows_missing_or_healthy_state(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            "AFS_BOX": "unused",
            "AFS_WATCHER_STATE_FILE": str(tmp_path / "missing.json"),
        }
    )
    command = f'''source "{SCRIPT.resolve()}"
remote() {{ eval "$1"; }}
deploy_memory_guard_check
'''
    result = subprocess.run(
        ["bash", "-c", command],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


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


def test_build_cleanup_trap_captures_paths_before_function_returns():
    text = SCRIPT.read_text()
    assert "trap \"git worktree remove -f '$work'" in text
