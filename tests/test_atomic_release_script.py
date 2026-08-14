import re
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
    assert "build) build_release" in text
    assert "verify) verify_release" in text
    assert "promote) promote_release" in text
    assert "rollback) rollback_release" in text
    assert 'AFS_RELEASES_DIR:-/root/afs-releases' in text
    assert 'AFS_CURRENT_LINK:-/root/autonomous-futures-system' in text


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


def test_candidate_can_verify_with_current_production_pins():
    text = SCRIPT.read_text()
    assert "AFS_VERIFY_POSTURE:-shadow_baseline" in text
    assert '== "preserve_current"' in text
    assert 'candidate_overrides=""' in text
    assert 'posture_label="current production pins"' in text


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


def test_promotion_and_rollback_use_atomic_symlink_replacement():
    text = SCRIPT.read_text()
    assert "mv -Tf '$CURRENT.next' '$CURRENT'" in text
    assert "current.previous" in text


def test_build_cleanup_trap_captures_paths_before_function_returns():
    text = SCRIPT.read_text()
    assert "trap \"git worktree remove -f '$work'" in text
