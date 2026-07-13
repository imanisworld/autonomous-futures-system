import subprocess

import pytest

from ops.behavior_neutral_gate import (
    APP_PY_PATH,
    NEVER_SAFE_FILES,
    SAFE_OPERATIONAL_FILES,
    app_py_change_is_safe,
    check_behavior_neutral,
    evaluate_changed_files,
)


@pytest.fixture
def temp_git_repo(tmp_path):
    """A real, isolated git repo (not this one) so check_behavior_neutral's
    git-backed path -- diff/show over actual commits -- is exercised
    end-to-end rather than mocked."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def run(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)

    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    return repo, run


def test_explicit_operational_files_pass_with_no_content_review():
    result = evaluate_changed_files([
        "ops/evidence_report.py",
        "scripts/health_digest.py",
        "tests/test_atomic_release_script.py",
        "docs/some-notes.md",
        "research/mnq_structural_level_5m.py",
        ".gitignore",
    ])
    assert result.is_behavior_neutral
    assert result.blocking_reasons == []


def test_separate_stock_advisory_paper_lane_is_safe_for_futures_promotion():
    result = evaluate_changed_files([
        "stocks_advisory/paper_runner.py",
        "data/stocks_advisory_paper_proof/PROOF_MANIFEST.md",
    ])
    assert result.is_behavior_neutral


def test_arbitrary_new_file_under_scripts_or_ops_is_not_auto_safe():
    # Landing in scripts/ or ops/ is not enough -- must be explicitly listed.
    result = evaluate_changed_files(["scripts/brand_new_tool.py"])
    assert not result.is_behavior_neutral
    assert "scripts/brand_new_tool.py" in result.blocking_reasons[0]

    result = evaluate_changed_files(["ops/brand_new_module.py"])
    assert not result.is_behavior_neutral


def test_never_safe_files_are_blocked_even_though_listed_nowhere_else():
    for path in NEVER_SAFE_FILES:
        result = evaluate_changed_files([path])
        assert not result.is_behavior_neutral, path
        assert "never eligible" in result.blocking_reasons[0]


def test_never_safe_overrides_would_be_directory_safety():
    # The gate's own logic and the promotion script are never safe, even
    # though they live in directories that otherwise contain safe files.
    assert "ops/behavior_neutral_gate.py" in NEVER_SAFE_FILES
    assert "scripts/atomic_release.sh" in NEVER_SAFE_FILES
    result = evaluate_changed_files(["ops/behavior_neutral_gate.py"])
    assert not result.is_behavior_neutral
    result = evaluate_changed_files(["scripts/atomic_release.sh"])
    assert not result.is_behavior_neutral


def test_strategy_and_risk_paths_are_denied_by_default():
    for path in (
        "strategy/signal_engine.py",
        "risk_rules.yaml",
        "webhook/runner.py",
        "execution/tradovate_broker.py",
        "config/settings.py",
        "adaptive/execution_gate.py",
    ):
        result = evaluate_changed_files([path])
        assert not result.is_behavior_neutral, path


def test_app_py_change_inside_safe_helper_is_safe():
    baseline = (
        "import os\n"
        "\n"
        "def _shadow_feed_status():\n"
        "    return 'old'\n"
        "\n"
        "def receive_alert():\n"
        "    return 'unchanged'\n"
    )
    candidate = (
        "import os\n"
        "\n"
        "def _shadow_feed_status():\n"
        "    return 'new label text, totally different body'\n"
        "\n"
        "def receive_alert():\n"
        "    return 'unchanged'\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert ok
    assert reasons == []

    result = evaluate_changed_files([APP_PY_PATH], app_py_sources=(baseline, candidate))
    assert result.is_behavior_neutral


def test_app_py_change_inside_non_safe_function_is_blocked():
    baseline = "def receive_alert():\n    return process(payload)\n"
    candidate = "def receive_alert():\n    return process(payload, skip_risk_check=True)\n"
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any(APP_PY_PATH in r for r in reasons)


def test_app_py_new_top_level_function_is_not_auto_trusted():
    baseline = "def _dashboard_payload():\n    return {}\n"
    candidate = (
        "def _dashboard_payload():\n    return {}\n"
        "\n"
        "def _new_helper():\n    return 1\n"
    )
    ok, _reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok


def test_app_py_removed_function_is_blocked():
    baseline = "def _old_helper():\n    return 1\n"
    candidate = ""
    ok, _reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok


def test_app_py_decorator_only_change_on_safe_function_is_still_safe():
    # Decorator changes matter for route handlers, but _dashboard_payload is
    # a plain helper (no decorator either way) -- included to document that
    # the safe-function marker swallows decorators too, so a change to a
    # safe function's OWN decorator (if it ever had one) would not sneak
    # past as a "residual" diff.
    baseline = "def _dashboard_payload():\n    return {'a': 1}\n"
    candidate = "def _dashboard_payload():\n    return {'a': 2, 'b': 3}\n"
    ok, _reasons = app_py_change_is_safe(baseline, candidate)
    assert ok


def test_app_py_module_level_constant_change_is_blocked():
    baseline = (
        "ALLOWED_IPS = ['1.2.3.4']\n"
        "\n"
        "def _dashboard_payload():\n"
        "    return {}\n"
    )
    candidate = (
        "ALLOWED_IPS = ['1.2.3.4', '9.9.9.9']\n"
        "\n"
        "def _dashboard_payload():\n"
        "    return {}\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any(APP_PY_PATH in r for r in reasons)


def test_app_py_import_change_is_blocked():
    baseline = "import os\n\ndef _dashboard_payload():\n    return {}\n"
    candidate = "import os\nimport sys\n\ndef _dashboard_payload():\n    return {}\n"
    ok, _reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok


def test_app_py_decorator_change_on_non_safe_route_is_blocked():
    baseline = (
        "@app.get('/status/today')\n"
        "def status_today():\n"
        "    return {}\n"
    )
    candidate = (
        "@app.get('/webhook/alert')\n"
        "def status_today():\n"
        "    return {}\n"
    )
    ok, reasons = app_py_change_is_safe(baseline, candidate)
    assert not ok
    assert any(APP_PY_PATH in r for r in reasons)


def test_app_py_changed_without_sources_provided_fails_closed():
    result = evaluate_changed_files([APP_PY_PATH], app_py_sources=None)
    assert not result.is_behavior_neutral


def test_no_operational_file_secretly_overlaps_never_safe():
    assert not (SAFE_OPERATIONAL_FILES & NEVER_SAFE_FILES)


def test_check_behavior_neutral_git_backed_safe_diff(temp_git_repo):
    repo, run = temp_git_repo
    (repo / "ops").mkdir()
    (repo / "ops" / "evidence_report.py").write_text("VALUE = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "baseline")
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "ops" / "evidence_report.py").write_text("VALUE = 2\n")
    run("add", ".")
    run("commit", "-q", "-m", "operational tweak")
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    result = check_behavior_neutral(str(repo), baseline_sha, candidate_sha)
    assert result.is_behavior_neutral
    assert result.blocking_reasons == []


def test_check_behavior_neutral_git_backed_unsafe_diff(temp_git_repo):
    repo, run = temp_git_repo
    (repo / "strategy").mkdir()
    (repo / "strategy" / "signal_engine.py").write_text("THRESHOLD = 1\n")
    run("add", ".")
    run("commit", "-q", "-m", "baseline")
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "strategy" / "signal_engine.py").write_text("THRESHOLD = 999\n")
    run("add", ".")
    run("commit", "-q", "-m", "strategy tweak")
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    result = check_behavior_neutral(str(repo), baseline_sha, candidate_sha)
    assert not result.is_behavior_neutral
    assert "strategy/signal_engine.py" in result.blocking_reasons[0]


def test_check_behavior_neutral_git_backed_app_py_diff(temp_git_repo):
    repo, run = temp_git_repo
    (repo / "webhook").mkdir()
    (repo / "webhook" / "app.py").write_text(
        "def _dashboard_payload():\n    return {'a': 1}\n"
    )
    run("add", ".")
    run("commit", "-q", "-m", "baseline")
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / "webhook" / "app.py").write_text(
        "def _dashboard_payload():\n    return {'a': 2}\n"
    )
    run("add", ".")
    run("commit", "-q", "-m", "safe dashboard tweak")
    candidate_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    result = check_behavior_neutral(str(repo), baseline_sha, candidate_sha)
    assert result.is_behavior_neutral


def test_check_behavior_neutral_bad_sha_fails_closed(temp_git_repo):
    repo, run = temp_git_repo
    (repo / "README.md").write_text("hello\n")
    run("add", ".")
    run("commit", "-q", "-m", "baseline")

    result = check_behavior_neutral(str(repo), "deadbeef0000", "cafef00d0000")
    assert not result.is_behavior_neutral
    assert result.blocking_reasons
