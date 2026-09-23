import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "afs-server-drift-gate.sh"


def _fixture(tmp: str, *, expected="abc123", manifest="abc123"):
    root = Path(tmp)
    live = root / "live"
    shared = root / "shared"
    live.mkdir()
    shared.mkdir()
    (live / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": manifest}, "fingerprint_sha256": "fp123"})
    )
    (shared / ".env").write_text(
        f"EXPECTED_LIVE_COMMIT={expected}\nEXPECTED_RELEASE_FINGERPRINT=fp123\n"
    )
    return root, live, shared


def _run_sourced(root, live, shared, body, *, info_main="0", repo_url=None):
    env = [
        f'export AFS_LIVE_ROOT="{live}"',
        f'export AFS_SHARED_DIR="{shared}"',
        f'export AFS_ENV_FILE="{shared / ".env"}"',
        f'export AFS_DRIFT_LOG="{root / "drift.log"}"',
        f'export AFS_DRIFT_MAIN_INFO={info_main}',
    ]
    if repo_url is not None:
        env.append(f'export AFS_REPO_URL="{repo_url}"')
    harness = "\n".join(env) + f'\nsource "{SCRIPT}"\n' + body
    return subprocess.run(["bash", "-c", harness], capture_output=True, text=True)


def test_server_gate_is_read_only_and_seed_is_refused():
    text = SCRIPT.read_text()
    assert "systemctl restart" not in text
    assert "atomic_release" not in text
    assert "git pull" not in text

    result = subprocess.run(["bash", str(SCRIPT), "--seed"], capture_output=True, text=True)
    assert result.returncode == 64
    assert "REFUSED" in result.stderr

def test_clean_release_uses_manifest_and_pins_not_main():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "release integrity: OK"; }\nrun_gate\n',
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "OK release-integrity: abc123" in result.stdout
        assert "ALARM" not in result.stdout


def test_manifest_integrity_failure_is_red_alarm():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "hash mismatch: webhook/app.py"; return 1; }\n'
            'post_red_alert(){ echo "RED_ALERT:$1"; }\nrun_gate\n',
        )
        assert result.returncode == 1
        assert "ALARM release-drift: release integrity FAILED" in result.stdout
        assert "RED_ALERT:" in result.stdout
        assert "hash mismatch: webhook/app.py" in result.stdout


def test_expected_commit_mismatch_is_red_alarm():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp, expected="wrong", manifest="abc123")
        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "release integrity: OK"; }\n'
            'post_red_alert(){ echo "RED_ALERT:$1:$2"; }\nrun_gate\n',
        )
        assert result.returncode == 1
        assert "pinned commit mismatch" in result.stdout
        assert "EXPECTED_LIVE_COMMIT=wrong manifest_commit=abc123" in result.stdout
        assert "RED_ALERT:" in result.stdout
        # Discord text: plain headline first; pin names and SHAs only as a small "-#" detail line
        assert "RED_ALERT:🚨 Server is running a different version than expected:" in result.stdout
        assert "-# EXPECTED_LIVE_COMMIT=wrong manifest_commit=abc123" in result.stdout
        assert "-# READ ONLY" in result.stdout

def test_main_ahead_is_informational_and_non_failing():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        repo = root / "repo"
        (repo / "webhook").mkdir(parents=True)
        (repo / "webhook" / "app.py").write_text("new main\n")
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)

        (live / "webhook").mkdir()
        (live / "webhook" / "app.py").write_text("deployed release\n")

        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "release integrity: OK"; }\nrun_gate\n',
            info_main="1",
            repo_url=repo,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "INFO main-vs-primary-release: 1 difference(s)" in result.stdout
        assert "DIFFER webhook/app.py" in result.stdout
        assert "ALARM" not in result.stdout
        assert "UNEXPECTED drift" not in result.stdout


def test_missing_manifest_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        (live / "release_manifest.json").unlink()
        result = _run_sourced(root, live, shared, 'post_red_alert(){ :; }\nrun_gate\n')
        assert result.returncode == 1
        assert "integrity proof unavailable" not in result.stdout  # Discord title only
        assert "live release tree/manifest unavailable" in result.stdout


def test_options_scanner_release_integrity_failure_is_red_alarm():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "release integrity: OK"; }\n'
            'options_scanner_release_check(){ echo "options hash mismatch"; return 1; }\n'
            'post_red_alert(){ echo "RED_ALERT:$1:$2"; }\nrun_gate\n',
        )
        assert result.returncode == 1
        assert "options-scanner release integrity FAILED" in result.stdout
        assert "options hash mismatch" in result.stdout
        assert "RED_ALERT:" in result.stdout


def test_options_scanner_release_integrity_success_is_reported():
    with tempfile.TemporaryDirectory() as tmp:
        root, live, shared = _fixture(tmp)
        result = _run_sourced(
            root,
            live,
            shared,
            'release_integrity_check(){ echo "release integrity: OK"; }\n'
            'options_scanner_release_check(){ echo "OK options-scanner release-integrity: deadbeefcafe (/release)"; }\n'
            'run_gate\n',
        )
        assert result.returncode == 0, result.stderr + result.stdout
        assert "OK options-scanner release-integrity: deadbeefcafe" in result.stdout
        assert "ALARM" not in result.stdout
