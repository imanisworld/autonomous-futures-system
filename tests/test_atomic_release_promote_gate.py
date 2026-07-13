"""Executable tests for atomic_release.sh's _promote_gate_check -- not just
string assertions on the script text. Sources the real script with a mocked
`remote()` (runs locally instead of over ssh) against a fake $SHARED/.env and
a real, disposable git repo, and exercises the actual bash control flow.
"""
import subprocess
import textwrap
from pathlib import Path

import pytest

SCRIPT = "scripts/atomic_release.sh"
# The real repo's atomic_release.sh -- always sourced from here. `cwd` for
# the subprocess is a separate, disposable temp git repo (see fixture below)
# so `git rev-parse --show-toplevel` inside the sourced script resolves to
# the fake repo, not this one, without ever touching this repo's own state.
REAL_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_gate_check(repo, env_lines, sha):
    """Source atomic_release.sh in a subshell (cwd = the disposable `repo`)
    with `remote()` overridden to run locally, a fake $SHARED/.env, and call
    _promote_gate_check "$sha". Returns (returncode, stdout, stderr).
    """
    shared = repo / "shared"
    shared.mkdir(exist_ok=True)
    (shared / ".env").write_text("\n".join(env_lines) + "\n")

    # atomic_release.sh does `source "$ROOT/scripts/deploy_lock.sh"` where
    # $ROOT = `git rev-parse --show-toplevel` (the disposable repo, since
    # that's cwd) -- give it a real copy so sourcing succeeds.
    fake_scripts_dir = repo / "scripts"
    fake_scripts_dir.mkdir(exist_ok=True)
    real_deploy_lock = REAL_REPO_ROOT / "scripts" / "deploy_lock.sh"
    (fake_scripts_dir / "deploy_lock.sh").write_text(real_deploy_lock.read_text())

    script = textwrap.dedent(f"""
        set -euo pipefail
        export AFS_BOX=unused-in-tests
        export AFS_SHARED_DIR="{shared}"
        export AFS_RELEASES_DIR="{repo}/releases"
        export AFS_CURRENT_LINK="{repo}/current"
        # ops.behavior_neutral_gate lives in the real repo, not the fake
        # one _promote_gate_check's $ROOT points at -- put it on the path.
        export PYTHONPATH="{REAL_REPO_ROOT}"
        source "{REAL_REPO_ROOT}/{SCRIPT}"
        remote() {{ eval "$1"; }}
        _promote_gate_check "{sha}"
    """)
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.fixture
def git_repo_with_two_commits(tmp_path):
    """A real, disposable git repo (never this repo) with a baseline commit
    touching an operational-safe file and a second commit touching a
    strategy file -- gives us one behavior-neutral sha pair and one not,
    fully offline (a self-pointing `origin` remote so `git fetch origin`
    inside the gate check succeeds without network access)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("remote", "add", "origin", str(repo))

    (repo / "ops").mkdir()
    (repo / "ops" / "evidence_report.py").write_text("VALUE = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "baseline")
    live_sha = run("rev-parse", "HEAD")

    (repo / "ops" / "evidence_report.py").write_text("VALUE = 2\n")
    run("add", ".")
    run("commit", "-q", "-m", "safe operational tweak")
    safe_candidate_sha = run("rev-parse", "HEAD")

    run("checkout", "-q", live_sha)
    (repo / "strategy").mkdir()
    (repo / "strategy" / "signal_engine.py").write_text("THRESHOLD = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "unsafe strategy tweak")
    unsafe_candidate_sha = run("rev-parse", "HEAD")
    run("checkout", "-q", live_sha)  # leave the worktree on the baseline commit

    return repo, live_sha, safe_candidate_sha, unsafe_candidate_sha


BASELINE_PINS = [
    "SCHEDULE_MODE=always_on_shadow",
    "EXPECTED_PROOF_SCHEDULE_MODE=always_on_shadow",
    "HTF_DIRECTION_MODE=off",
    "EXPECTED_PROOF_HTF_DIRECTION_MODE=off",
    "EXIT_MODE=static",
    "EXPECTED_PROOF_EXIT_MODE=static",
]

CURRENT_PINS = [
    "SCHEDULE_MODE=current",
    "EXPECTED_PROOF_SCHEDULE_MODE=current",
    "HTF_DIRECTION_MODE=off",
    "EXPECTED_PROOF_HTF_DIRECTION_MODE=off",
    "EXIT_MODE=runner_shadow",
    "EXPECTED_PROOF_EXIT_MODE=runner_shadow",
]


def test_baseline_posture_succeeds_without_behavior_check(git_repo_with_two_commits):
    repo, live_sha, _safe, unsafe_candidate = git_repo_with_two_commits
    env_lines = BASELINE_PINS + [f"EXPECTED_LIVE_COMMIT={live_sha}"]
    code, out, err = _run_gate_check(repo, env_lines, unsafe_candidate)
    assert code == 0, err
    # Path 1 never runs the behavior-neutral check -- any release may
    # promote from the reset baseline, including one that touches strategy/.
    assert "behavior-neutral" not in out


def test_current_posture_with_safe_diff_succeeds(git_repo_with_two_commits):
    repo, live_sha, safe_candidate, _unsafe = git_repo_with_two_commits
    env_lines = CURRENT_PINS + [f"EXPECTED_LIVE_COMMIT={live_sha}"]
    code, out, err = _run_gate_check(repo, env_lines, safe_candidate)
    assert code == 0, err
    assert "behavior-neutral check passed" in out


def test_current_posture_with_unsafe_diff_fails(git_repo_with_two_commits):
    repo, live_sha, _safe, unsafe_candidate = git_repo_with_two_commits
    env_lines = CURRENT_PINS + [f"EXPECTED_LIVE_COMMIT={live_sha}"]
    code, out, err = _run_gate_check(repo, env_lines, unsafe_candidate)
    assert code == 1
    assert "not behavior-neutral" in err


def test_neither_posture_matches_fails(git_repo_with_two_commits):
    repo, live_sha, safe_candidate, _unsafe = git_repo_with_two_commits
    drifted_pins = [
        "SCHEDULE_MODE=always_on_paper",
        "EXPECTED_PROOF_SCHEDULE_MODE=always_on_paper",
        "HTF_DIRECTION_MODE=off",
        "EXPECTED_PROOF_HTF_DIRECTION_MODE=off",
        "EXIT_MODE=runner_shadow",
        "EXPECTED_PROOF_EXIT_MODE=runner_shadow",
        f"EXPECTED_LIVE_COMMIT={live_sha}",
    ]
    code, out, err = _run_gate_check(repo, drifted_pins, safe_candidate)
    assert code == 1
    assert "matches neither the reset baseline" in err


def test_missing_expected_live_commit_fails(git_repo_with_two_commits):
    repo, _live_sha, safe_candidate, _unsafe = git_repo_with_two_commits
    # CURRENT_PINS with no EXPECTED_LIVE_COMMIT line at all.
    code, out, err = _run_gate_check(repo, CURRENT_PINS, safe_candidate)
    assert code == 1
    assert "cannot determine the currently-live commit" in err


def test_invalid_live_commit_fails_closed(git_repo_with_two_commits):
    repo, _live_sha, safe_candidate, _unsafe = git_repo_with_two_commits
    env_lines = CURRENT_PINS + ["EXPECTED_LIVE_COMMIT=0000000000000000000000000000000000dead"]
    code, out, err = _run_gate_check(repo, env_lines, safe_candidate)
    assert code == 1
    assert "not behavior-neutral" in err or "could not diff" in err


def test_gate_check_never_mutates_env_or_current_link(git_repo_with_two_commits):
    repo, live_sha, _safe, unsafe_candidate = git_repo_with_two_commits
    env_lines = CURRENT_PINS + [f"EXPECTED_LIVE_COMMIT={live_sha}"]
    shared = repo / "shared"
    shared.mkdir(exist_ok=True)
    (shared / ".env").write_text("\n".join(env_lines) + "\n")
    before = (shared / ".env").read_text()
    current_link = repo / "current"

    code, _out, _err = _run_gate_check(repo, env_lines, unsafe_candidate)
    assert code == 1
    after = (shared / ".env").read_text()
    assert before == after
    assert not current_link.exists()
