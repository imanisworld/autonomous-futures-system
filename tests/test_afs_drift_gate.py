import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "afs-drift-gate.sh"

# Sources the gate (its main guard keeps `source` side-effect free) with a
# local shell standing in for ssh, so the whole comparison runs against a
# directory on disk instead of a real box.
HARNESS = """
set -euo pipefail
export AFS_BOX=fake-box
export AFS_BOX_ROOT="{box_root}"
export AFS_SHARED_DIR="{shared}"
export AFS_DRIFT_REF=HEAD
export AFS_DRIFT_PATHS="alert_ranker ops/project_check"
unset DISCORD_ROUTE_DEPLOYMENT DISCORD_WEBHOOK_URL
local_exec() {{ bash -c "$1"; }}
export REMOTE_EXEC=local_exec
cd "{repo}"
source "{script}" {args}
"""


def _run(repo, box_root, shared, body, args=""):
    script = HARNESS.format(
        script=SCRIPT, repo=repo, box_root=box_root, shared=shared, args=args
    ) + body
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60
    )


def _md5(text):
    return hashlib.md5(text.encode()).hexdigest()


def _make_repo(tmp):
    """A repo whose watched paths mirror the real alert_ranker/ops layout."""
    repo = Path(tmp) / "repo"
    (repo / "alert_ranker").mkdir(parents=True)
    (repo / "ops" / "project_check").mkdir(parents=True)
    (repo / "alert_ranker" / "app.py").write_text("main app\n")
    (repo / "alert_ranker" / "config.py").write_text("main config\n")
    (repo / "alert_ranker" / "lifecycle.py").write_text("main lifecycle\n")
    (repo / "alert_ranker" / "README.md").write_text("docs are not watched\n")
    (repo / "ops" / "project_check" / "session.py").write_text("main session\n")
    (repo / "untracked_area").mkdir()
    (repo / "untracked_area" / "other.py").write_text("outside the watch set\n")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "seed"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    return repo


def _make_box(tmp, repo):
    """Box copy that starts identical to the repo tree."""
    box = Path(tmp) / "box"
    box.mkdir()
    for rel in (
        "alert_ranker/app.py",
        "alert_ranker/config.py",
        "alert_ranker/lifecycle.py",
        "alert_ranker/README.md",
        "ops/project_check/session.py",
    ):
        dest = box / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((repo / rel).read_text())
    return box


def test_gate_is_host_agnostic_and_read_only():
    text = SCRIPT.read_text()
    assert 'BOX="${AFS_BOX:?' in text
    # Host identity comes from AFS_BOX, never the file. Asserted as "no IPv4
    # literal at all" rather than by naming the box's address, so this repo
    # stays free of the operational detail it is checking for.
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text.replace("127.0.0.1", ""))
    # A drift gate that can restart or promote is no longer a gate.
    assert "systemctl restart" not in text
    assert "git pull" not in text


def test_clean_box_reports_no_drift():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        box = _make_box(tmp, repo)
        result = _run(repo, box, Path(tmp) / "shared", "drift_report\n")
        assert result.returncode == 0, result.stderr
        assert "box matches HEAD" in result.stdout


def test_merged_but_unshipped_and_edited_files_are_classified():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        box = _make_box(tmp, repo)
        # Box is behind on app.py and has never received lifecycle.py --
        # exactly the two shapes the real alert showed.
        (box / "alert_ranker" / "app.py").write_text("stale app\n")
        (box / "alert_ranker" / "lifecycle.py").unlink()
        (box / "ops" / "project_check" / "hotfix.py").write_text("hand-added\n")
        result = _run(repo, box, Path(tmp) / "shared", "drift_report\n")

        assert result.returncode == 1
        assert "3 UNEXPECTED drift item(s)" in result.stdout
        main_md5, box_md5 = _md5("main app\n"), _md5("stale app\n")
        assert f"DIFFER alert_ranker/app.py {main_md5} {box_md5}" in result.stdout
        assert "MISSING alert_ranker/lifecycle.py" in result.stdout
        assert "EXTRA ops/project_check/hotfix.py" in result.stdout
        # Watched set is .py only, and only under the configured roots.
        assert "README.md" not in result.stdout
        assert "untracked_area" not in result.stdout


def test_drift_lines_are_sorted_by_path_not_by_status():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        box = _make_box(tmp, repo)
        (box / "alert_ranker" / "config.py").write_text("stale config\n")
        (box / "alert_ranker" / "app.py").write_text("stale app\n")
        (box / "alert_ranker" / "lifecycle.py").unlink()
        result = _run(repo, box, Path(tmp) / "shared", "drift_report\n")

        paths = [
            line.split()[1]
            for line in result.stdout.splitlines()
            if line.startswith(("DIFFER", "MISSING", "EXTRA"))
        ]
        assert paths == sorted(paths)
        assert paths[:3] == [
            "alert_ranker/app.py",
            "alert_ranker/config.py",
            "alert_ranker/lifecycle.py",
        ]


def test_seed_accepts_current_drift_and_silences_it():
    with tempfile.TemporaryDirectory() as tmp:
        repo, shared = _make_repo(tmp), Path(tmp) / "shared"
        box = _make_box(tmp, repo)
        (box / "alert_ranker" / "app.py").write_text("box-owned app\n")

        seeded = _run(repo, box, shared, "drift_report\n", args="--seed")
        assert seeded.returncode == 0, seeded.stderr
        assert "seeded 1 accepted drift item(s)" in seeded.stdout

        after = _run(repo, box, shared, "drift_report\n")
        assert after.returncode == 0, after.stderr
        assert "box matches HEAD" in after.stdout


def test_reedit_of_a_seeded_file_alerts_again():
    with tempfile.TemporaryDirectory() as tmp:
        repo, shared = _make_repo(tmp), Path(tmp) / "shared"
        box = _make_box(tmp, repo)
        (box / "alert_ranker" / "app.py").write_text("box-owned app\n")
        _run(repo, box, shared, "drift_report\n", args="--seed")

        # Seeding pins both hashes, so a later edit is not covered by the
        # earlier acceptance.
        (box / "alert_ranker" / "app.py").write_text("box-owned app, edited again\n")
        result = _run(repo, box, shared, "drift_report\n")
        assert result.returncode == 1
        assert "1 UNEXPECTED drift item(s)" in result.stdout
        assert "DIFFER alert_ranker/app.py" in result.stdout


def test_seeding_does_not_hide_drift_that_appears_later():
    with tempfile.TemporaryDirectory() as tmp:
        repo, shared = _make_repo(tmp), Path(tmp) / "shared"
        box = _make_box(tmp, repo)
        (box / "alert_ranker" / "app.py").write_text("box-owned app\n")
        _run(repo, box, shared, "drift_report\n", args="--seed")

        (box / "alert_ranker" / "config.py").write_text("newly stale\n")
        result = _run(repo, box, shared, "drift_report\n")
        assert result.returncode == 1
        assert "1 UNEXPECTED drift item(s)" in result.stdout
        assert "DIFFER alert_ranker/config.py" in result.stdout
        assert "alert_ranker/app.py" not in result.stdout


def test_seed_subtraction_survives_mixed_statuses():
    """Display order is by path; the seed set difference needs whole-line
    ordering. Getting those two confused makes `comm` drop or leak items only
    once several statuses are in play, so exercise all three together."""
    with tempfile.TemporaryDirectory() as tmp:
        repo, shared = _make_repo(tmp), Path(tmp) / "shared"
        box = _make_box(tmp, repo)
        (box / "alert_ranker" / "app.py").write_text("box-owned app\n")
        (box / "alert_ranker" / "lifecycle.py").unlink()
        (box / "ops" / "project_check" / "hotfix.py").write_text("hand-added\n")

        seeded = _run(repo, box, shared, "drift_report\n", args="--seed")
        assert seeded.returncode == 0, seeded.stderr
        assert "seeded 3 accepted drift item(s)" in seeded.stdout

        after = _run(repo, box, shared, "drift_report\n")
        assert after.returncode == 0, after.stderr + after.stdout
        assert "not in sorted order" not in after.stderr
        assert "box matches HEAD" in after.stdout

        # One new item on top of a mixed seed set: only that item alerts.
        (box / "alert_ranker" / "config.py").write_text("newly stale\n")
        result = _run(repo, box, shared, "drift_report\n")
        assert result.returncode == 1
        assert "1 UNEXPECTED drift item(s)" in result.stdout
        assert "DIFFER alert_ranker/config.py" in result.stdout
        assert "not in sorted order" not in result.stderr


def test_empty_watch_set_refuses_instead_of_reporting_clean():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        box = _make_box(tmp, repo)
        script = HARNESS.format(
            script=SCRIPT, repo=repo, box_root=box, shared=Path(tmp) / "shared", args=""
        ).replace(
            'export AFS_DRIFT_PATHS="alert_ranker ops/project_check"',
            'export AFS_DRIFT_PATHS="does_not_exist"',
        )
        result = subprocess.run(
            ["bash", "-c", script + "drift_report\n"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 2
        assert "refusing to report a vacuously clean box" in result.stderr


def test_unreachable_box_refuses_instead_of_faking_a_full_drift():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        result = _run(repo, Path(tmp) / "no-such-box", Path(tmp) / "shared", "drift_report\n")
        # Hashing an unreadable box yields nothing, which would read as "every
        # watched file is missing from the deploy" -- a false alarm that is
        # indistinguishable from a genuinely empty box.
        assert result.returncode == 2
        assert "refusing to report drift against an unreachable box" in result.stderr
        assert "UNEXPECTED" not in result.stdout


def test_absent_watch_root_on_a_reachable_box_is_real_drift():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _make_repo(tmp)
        box = _make_box(tmp, repo)
        # ops/project_check never shipped: the box is readable, the root is not
        # there. That is merged-but-unshipped, not an outage.
        subprocess.run(["rm", "-rf", str(box / "ops")], check=True)
        result = _run(repo, box, Path(tmp) / "shared", "drift_report\n")
        assert result.returncode == 1
        assert "1 UNEXPECTED drift item(s)" in result.stdout
        assert "MISSING ops/project_check/session.py" in result.stdout
