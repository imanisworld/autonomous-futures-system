"""U11: release refuses a missing / mismatched exact-SHA CI proof."""
from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from ops import release_ci_proof as rp

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _run(name="tests", conclusion="success", sha=SHA, app="github-actions", run_id=1, completed="2026-10-07T00:00:00Z"):
    return {"id": run_id, "name": name, "head_sha": sha, "status": "completed", "conclusion": conclusion,
            "app": app, "completed_at": completed}


def _proof(runs, sha=SHA, repo=rp.REPO):
    return {
        "schema_version": rp.SCHEMA_VERSION,
        "generator": rp.GENERATOR,
        "repo": repo,
        "sha": sha,
        "check_runs": runs,
    }


def _required_runs(*, tests_conclusion="success", tests_status="completed"):
    runs = []
    for index, name in enumerate(rp.REQUIRED_CHECKS, 1):
        conclusion = tests_conclusion if name == "tests" else "success"
        status = tests_status if name == "tests" else "completed"
        runs.append(_run(name=name, conclusion=conclusion, run_id=index, completed=f"2026-10-07T00:0{index}:00Z") | {"status": status})
    return runs


def test_valid_proof_verifies():
    assert rp.verify_proof(_proof(_required_runs()), sha=SHA) == []


@pytest.mark.parametrize(
    "proof,needle",
    [
        (None, "missing"),
        ({"tests": "passed"}, "missing"),
        (_proof([_run()], sha="b" * 40), "is for"),
        (_proof([_run()], repo="someone/else"), "repo"),
        (_proof([]), "absent"),
        (_proof([_run(conclusion="failure")]), "completed/failure"),
        (_proof([_run(app="third-party")]), "absent"),
        (_proof([_run(sha="c" * 40)]), "ran on"),
    ],
)
def test_bad_proofs_are_refused(proof, needle):
    assert any(needle in p for p in rp.verify_proof(proof, sha=SHA))


def test_latest_run_decides_by_run_id_and_in_progress_rerun_blocks():
    others = [run for run in _required_runs() if run["name"] != "tests"]
    old_ok = _run(run_id=10, completed="2026-10-07T02:00:00Z")
    new_running = _run(run_id=11, conclusion=None, completed=None)
    new_running["status"] = "in_progress"
    assert any(
        "in_progress/None" in problem
        for problem in rp.verify_proof(_proof(others + [old_ok, new_running]), sha=SHA)
    )
    rerun_ok = _run(run_id=12, completed="2026-10-07T03:00:00Z")
    assert rp.verify_proof(_proof(others + [old_ok, new_running, rerun_ok]), sha=SHA) == []


def test_moving_ref_is_refused():
    assert rp.verify_proof(_proof([_run()], sha="main"), sha="main")
    with pytest.raises(rp.CIProofError):
        rp.fetch_proof("main")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_paginates_without_credentials():
    pages = {
        1: {
            "total_count": 3,
            "check_runs": [
                {**_run(name="Analyze (python)", run_id=2), "app": {"slug": "github-actions"}},
                {**_run(name="Analyze (actions)", run_id=3), "app": {"slug": "github-actions"}},
            ],
        },
        2: {
            "total_count": 3,
            "check_runs": [{**_run(run_id=1), "app": {"slug": "github-actions"}}],
        },
    }
    seen = []

    def opener(request, timeout):
        seen.append(request)
        page = int(request.full_url.rsplit("page=", 1)[1])
        return _Response(json.dumps(pages[page]).encode())

    proof = rp.fetch_proof(SHA, opener=opener)
    assert {r["name"] for r in proof["check_runs"]} == set(rp.REQUIRED_CHECKS)
    assert all("Authorization" not in r.headers for r in seen)
    assert all(r.headers.get("User-agent") for r in seen)
    assert rp.verify_proof(proof, sha=SHA) == []


def test_fetch_failure_fails_closed():
    def opener(request, timeout):
        raise OSError("network down")

    with pytest.raises(rp.CIProofError, match="could not fetch"):
        rp.fetch_proof(SHA, opener=opener)


def test_verify_cli(tmp_path):
    path = tmp_path / "proof.json"
    path.write_text(json.dumps(_proof(_required_runs())))
    assert rp.main(["verify", "--sha", SHA, "--proof", str(path)]) == 0
    assert rp.main(["verify", "--sha", "b" * 40, "--proof", str(path)]) == 1
    assert rp.main(["verify", "--sha", SHA, "--proof", str(tmp_path / "missing.json")]) == 1


def test_live_verification_rejects_current_github_failure():
    saved = _proof(_required_runs())

    def opener(request, timeout):
        runs = []
        for run in _required_runs():
            raw = dict(run)
            raw["app"] = {"slug": "github-actions"}
            if raw["name"] == "tests":
                raw["conclusion"] = "failure"
            runs.append(raw)
        return _Response(json.dumps({"total_count": len(runs), "check_runs": runs}).encode())

    problems = rp.verify_live_proof(saved, sha=SHA, opener=opener)
    assert any("live GitHub CI" in problem and "completed/failure" in problem for problem in problems)


def test_live_verification_requires_saved_latest_run_ids_to_match():
    saved = _proof(_required_runs())

    def opener(request, timeout):
        runs = []
        for run in _required_runs():
            raw = dict(run)
            raw["id"] = int(raw["id"]) + 100
            raw["app"] = {"slug": "github-actions"}
            runs.append(raw)
        return _Response(json.dumps({"total_count": len(runs), "check_runs": runs}).encode())

    problems = rp.verify_live_proof(saved, sha=SHA, opener=opener)
    assert problems == [
        "release CI proof is stale relative to current GitHub required-check runs; refetch it"
    ]



# ─── Release tooling (temp/test only, never a real box) ─────────────────────


def test_build_refuses_without_ci_proof_before_touching_anything():
    env = {k: v for k, v in os.environ.items() if k != "RELEASE_CI_PROOF"}
    env["AFS_BOX"] = "unused"
    proc = subprocess.run(
        ["bash", "scripts/atomic_release.sh", "build", SHA],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 65
    assert "RELEASE_CI_PROOF" in proc.stderr


def test_build_refuses_a_missing_proof_file(tmp_path):
    env = dict(os.environ, AFS_BOX="unused", RELEASE_CI_PROOF=str(tmp_path / "nope.json"))
    proc = subprocess.run(
        ["bash", "scripts/atomic_release.sh", "build", SHA],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 65


def test_release_installs_only_the_lock_and_verifies_it():
    text = (ROOT / "scripts/atomic_release.sh").read_text(encoding="utf-8")
    assert "install -q --no-deps --requirement '$RELEASES/$sha/requirements.lock'" in text
    assert "--requirement '$RELEASES/$sha/requirements.txt'" not in text
    assert ".venv/bin/pip' check" in text
    assert "-m ops.dependency_lock check-freeze" in text
    assert 'python3 -m ops.release_ci_proof verify-live --sha "$sha"' in text
    assert "python3 -m ops.dependency_lock check-requirements" in text
    assert "python3 -m ops.dependency_lock check-python" in text
    assert "PYTHONPATH=\'$RELEASES/$sha\' python3 -m ops.dependency_lock check-python" in text
    assert "git merge-base --is-ancestor \"$sha\" origin/main" in text
    assert 'archive="$(mktemp "/tmp/afs-release-${short}.XXXX")"' in text
    build = text.split("build_release() {", 1)[1].split("verify_release() {", 1)[0]
    assert build.index("verify-live") < build.index("deploy_lock_acquire")
