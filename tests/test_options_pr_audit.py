"""
tests/test_options_pr_audit.py

scripts/options_pr_audit.py tests. Proves the read-only options-PR audit
tool classifies lane membership correctly, finds sensitive-path and
forbidden-identifier hits only in *added* diff lines, never shells out to
git for anything but read-only subcommands, never writes a file unless
--out is passed, degrades CI status gracefully when `gh` is unavailable,
and never imports a broker/execution/network module itself.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import scripts.options_pr_audit as audit_module
from scripts.options_pr_audit import (
    GitRepo,
    PrAuditReport,
    assess_deploy_needed,
    assess_runtime_impact,
    broker_execution_config_touch_status,
    build_report,
    build_verdict_candidate,
    check_ci_status,
    classify_lane,
    find_forbidden_identifier_hits,
    find_sensitive_path_hits,
    main,
    parse_args,
    render_report,
    run_pytest,
)

_FORBIDDEN_IMPORT_FRAGMENTS = (
    "execution",
    "webhook",
    "alert_ranker",
    "options_companion",
    "risk_engine",
    "requests",
    "httpx",
    "robin_stocks",
    "ib_insync",
    "ibapi",
)

_FORBIDDEN_ORDER_ACTION_IDENTIFIERS = (
    "place_order(",
    "submit_order(",
    "cancel_order(",
    "replace_order(",
    "execute_order(",
    "live_order(",
)


def _module_source() -> str:
    return Path(audit_module.__file__).read_text()


def _imported_modules(module) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


class _FakeGitRepo(GitRepo):
    """A GitRepo whose read methods return fixed, injected values instead
    of shelling out -- lets every function under test run against a
    fully deterministic, fake diff."""

    def __init__(
        self,
        *,
        branch: str = "claude/options-pr-audit-test",
        head: str = "head-sha-1234567",
        base: str = "base-sha-abcdefg",
        changed: list[str] | None = None,
        stat: str = " 2 files changed, 10 insertions(+)",
        added_lines_by_path: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(cwd=None, runner=lambda args: "")
        self._branch = branch
        self._head = head
        self._base = base
        self._changed = changed or []
        self._stat = stat
        self._added_lines_by_path = added_lines_by_path or {}

    def current_branch(self) -> str:
        return self._branch

    def head_sha(self) -> str:
        return self._head

    def merge_base(self, base_ref: str) -> str:
        return self._base

    def changed_files(self, base_sha: str, head_sha: str) -> list[str]:
        return self._changed

    def diff_stat(self, base_sha: str, head_sha: str) -> str:
        return self._stat

    def added_lines(self, base_sha: str, head_sha: str, path: str) -> list[str]:
        return self._added_lines_by_path.get(path, [])


# --- 1. lane classification ------------------------------------------------------------------------


def test_classify_lane_all_options_files():
    files = ["options_manager/adapters/foo.py", "tests/test_options_foo.py"]
    assert classify_lane(files) == "OPTIONS"


def test_classify_lane_self_paths_count_as_options():
    files = ["scripts/options_pr_audit.py", "tests/test_options_pr_audit.py"]
    assert classify_lane(files) == "OPTIONS"


def test_classify_lane_mixed_reports_outside_files():
    files = ["options_manager/adapters/foo.py", "execution/order_router.py"]
    lane = classify_lane(files)
    assert "MIXED" in lane
    assert "execution/order_router.py" in lane


def test_classify_lane_empty_diff():
    assert classify_lane([]) == "EMPTY_DIFF"


# --- 2. sensitive path hits ------------------------------------------------------------------------


def test_sensitive_path_hits_detects_config_and_workflow_files():
    files = ["options_manager/config.py", ".github/workflows/ci.yml", "options_manager/adapters/foo.py"]
    hits = find_sensitive_path_hits(files)
    hit_files = {f for f, _ in hits}
    assert "options_manager/config.py" in hit_files
    assert ".github/workflows/ci.yml" in hit_files
    assert "options_manager/adapters/foo.py" not in hit_files


def test_sensitive_path_hits_detects_credential_shaped_paths():
    hits = find_sensitive_path_hits(["ops/secret_store.py", ".env.production"])
    hint_values = {hint for _, hint in hits}
    assert "secret" in hint_values
    assert ".env" in hint_values


def test_sensitive_path_hits_empty_for_clean_diff():
    assert find_sensitive_path_hits(["options_manager/validation/base.py"]) == []


# --- 3. forbidden identifier hits (added lines only, self-path excluded) --------------------------


def test_forbidden_identifier_hit_found_in_added_line():
    repo = _FakeGitRepo(
        changed=["options_manager/adapters/rogue.py"],
        added_lines_by_path={
            "options_manager/adapters/rogue.py": ["def helper():", "    submit_order(ticket)"]
        },
    )
    hits = find_forbidden_identifier_hits(repo, "base", "head", repo.changed_files("base", "head"))
    assert len(hits) == 1
    assert hits[0][0] == "options_manager/adapters/rogue.py"
    assert hits[0][1] == "submit_order"


def test_forbidden_identifier_hit_skips_self_paths():
    repo = _FakeGitRepo(
        changed=["scripts/options_pr_audit.py"],
        added_lines_by_path={"scripts/options_pr_audit.py": ['    "submit_order",']},
    )
    hits = find_forbidden_identifier_hits(repo, "base", "head", repo.changed_files("base", "head"))
    assert hits == []


def test_forbidden_identifier_hits_empty_for_clean_added_lines():
    repo = _FakeGitRepo(
        changed=["options_manager/adapters/clean.py"],
        added_lines_by_path={"options_manager/adapters/clean.py": ["def normalize_quote(raw):", "    return raw"]},
    )
    hits = find_forbidden_identifier_hits(repo, "base", "head", repo.changed_files("base", "head"))
    assert hits == []


def test_forbidden_identifier_hit_detects_live_flag_flip():
    repo = _FakeGitRepo(
        changed=["options_manager/config.py"],
        added_lines_by_path={"options_manager/config.py": ["LIVE_OPTIONS_TRADING_ENABLED = True"]},
    )
    hits = find_forbidden_identifier_hits(repo, "base", "head", repo.changed_files("base", "head"))
    assert any("LIVE_OPTIONS_TRADING_ENABLED" in h[1] for h in hits)


# --- 4. broker/execution/config touch status --------------------------------------------------------


def test_touch_status_flags_execution_and_config():
    status = broker_execution_config_touch_status(
        ["execution/order_router.py", "options_manager/config.py", ".github/workflows/ci.yml", "atomic_release.sh"]
    )
    assert status["touches_execution"] is True
    assert status["touches_config"] is True
    assert status["touches_ci"] is True
    assert status["touches_deploy"] is True


def test_touch_status_all_false_for_adapter_only_diff():
    status = broker_execution_config_touch_status(["options_manager/adapters/polygon_historical.py"])
    assert not any(status.values())


# --- 5. runtime impact / deploy needed heuristics ---------------------------------------------------


def test_runtime_impact_flags_scanner_touch():
    assert "possible" in assess_runtime_impact(["options_manager/scanner/watchlist.py"])


def test_runtime_impact_none_expected_for_adapter_validation_only():
    impact = assess_runtime_impact(
        ["options_manager/adapters/polygon_historical.py", "options_manager/validation/base.py", "tests/test_x.py"]
    )
    assert "none expected" in impact


def test_deploy_needed_no_for_adapter_only_diff():
    status = broker_execution_config_touch_status(["options_manager/adapters/foo.py"])
    assert assess_deploy_needed(status) == "no"


def test_deploy_needed_flags_ci_touch():
    status = broker_execution_config_touch_status([".github/workflows/ci.yml"])
    assert "reviewer must confirm" in assess_deploy_needed(status)


# --- 6. verdict candidate -----------------------------------------------------------------------------


def test_verdict_candidate_clean():
    verdict = build_verdict_candidate("OPTIONS", [], [], tests_failed=False, tests_ran=True)
    assert verdict.startswith("CLEAN CANDIDATE")
    assert "tests passed" in verdict


def test_verdict_candidate_blocks_on_forbidden_identifier():
    verdict = build_verdict_candidate(
        "OPTIONS", [], [("f.py", "submit_order", "submit_order(x)")], tests_failed=False, tests_ran=True
    )
    assert verdict.startswith("BLOCK CANDIDATE")


def test_verdict_candidate_blocks_on_credential_path():
    verdict = build_verdict_candidate("OPTIONS", [("secrets.py", "secret")], [], tests_failed=False, tests_ran=False)
    assert verdict.startswith("BLOCK CANDIDATE")


def test_verdict_candidate_holds_on_test_failure():
    verdict = build_verdict_candidate("OPTIONS", [], [], tests_failed=True, tests_ran=True)
    assert verdict.startswith("HOLD CANDIDATE")


def test_verdict_candidate_holds_outside_lane():
    verdict = build_verdict_candidate("MIXED (outside options lane: execution/x.py)", [], [], tests_failed=False, tests_ran=True)
    assert verdict.startswith("HOLD CANDIDATE")


def test_verdict_candidate_review_on_sensitive_path():
    verdict = build_verdict_candidate(
        "OPTIONS", [("options_manager/config.py", "options_manager/config.py")], [], tests_failed=False, tests_ran=True
    )
    assert verdict.startswith("REVIEW CANDIDATE")


# --- 7. CI status degrades gracefully -----------------------------------------------------------------


def test_ci_status_not_requested_without_pr():
    assert check_ci_status(None) == "not requested (no --pr given)"


def test_ci_status_unavailable_when_gh_missing(monkeypatch):
    def _raise(*args, **kwargs):
        raise OSError("gh not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert "unavailable" in check_ci_status(216)


def test_ci_status_unavailable_on_timeout(monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=20)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert "unavailable" in check_ci_status(216)


# --- 8. run_pytest command construction (subprocess mocked, no real test run) --------------------------


def test_run_pytest_builds_expected_command_and_parses_summary(monkeypatch):
    class _Result:
        stdout = "1 passed in 0.01s\n"
        stderr = ""

    def _fake_run(cmd, cwd=None, capture_output=True, text=True, timeout=600):
        assert cmd[-2:] == ["tests/test_x.py", "-q"] or "tests/test_x.py" in cmd
        return _Result()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    cmd_str, summary = run_pytest(["tests/test_x.py"])
    assert "tests/test_x.py" in cmd_str
    assert "1 passed" in summary


# --- 9. build_report end-to-end against a fully fake repo (no real subprocess calls) -------------------


def test_build_report_end_to_end_clean_diff():
    repo = _FakeGitRepo(
        changed=["options_manager/adapters/polygon_historical.py", "tests/test_options_polygon_historical.py"],
        added_lines_by_path={
            "options_manager/adapters/polygon_historical.py": ["def fetch_stock_aggregates(...):", "    return []"],
            "tests/test_options_polygon_historical.py": ["def test_x(): pass"],
        },
    )
    report = build_report(repo, "origin/main", pr_number=None, skip_tests=True)
    assert isinstance(report, PrAuditReport)
    assert report.lane == "OPTIONS"
    assert report.base_sha == "base-sha-abcdefg"
    assert report.head_sha == "head-sha-1234567"
    assert report.sensitive_path_hits == []
    assert report.forbidden_identifier_hits == []
    assert report.test_result_summary == "skipped (--skip-tests passed)"
    assert report.ci_status == "not requested (no --pr given)"
    assert report.verdict_candidate.startswith("CLEAN CANDIDATE")
    assert "does not authorize merge" in report.stop_condition


def test_build_report_flags_mixed_lane_and_forbidden_identifier():
    repo = _FakeGitRepo(
        changed=["execution/order_router.py"],
        added_lines_by_path={"execution/order_router.py": ["    submit_order(ticket)"]},
    )
    report = build_report(repo, "origin/main", pr_number=None, skip_tests=True)
    assert "MIXED" in report.lane
    assert len(report.forbidden_identifier_hits) == 1
    assert report.verdict_candidate.startswith("BLOCK CANDIDATE")


# --- 10. render_report includes every required output field -------------------------------------------


def test_render_report_includes_all_required_fields():
    repo = _FakeGitRepo(changed=["options_manager/validation/base.py"])
    report = build_report(repo, "origin/main", pr_number=None, skip_tests=True)
    text = render_report(report)
    for required in (
        "Lane:",
        "Current branch:",
        "Base SHA:",
        "Head SHA:",
        "Changed files",
        "Diff stat:",
        "Sensitive path hits",
        "Forbidden identifier hits",
        "Broker/execution/config touch status:",
        "Test commands run:",
        "Test result summary:",
        "CI status:",
        "Runtime impact:",
        "Deploy needed:",
        "Verdict candidate:",
        "Stop condition:",
    ):
        assert required in text, f"missing {required!r} in rendered report"


# --- 11. --out is the only write this tool ever performs ----------------------------------------------


def test_main_without_out_does_not_write_any_file(tmp_path, monkeypatch, capsys):
    fake_report = build_report(
        _FakeGitRepo(changed=["options_manager/validation/base.py"]), "origin/main", skip_tests=True
    )
    monkeypatch.setattr(audit_module, "build_report", lambda *a, **k: fake_report)
    monkeypatch.chdir(tmp_path)
    main(["--base", "origin/main"])
    assert list(tmp_path.iterdir()) == []
    captured = capsys.readouterr()
    assert "Verdict candidate:" in captured.out


def test_main_with_out_writes_exactly_one_file(tmp_path, monkeypatch):
    fake_report = build_report(
        _FakeGitRepo(changed=["options_manager/validation/base.py"]), "origin/main", skip_tests=True
    )
    monkeypatch.setattr(audit_module, "build_report", lambda *a, **k: fake_report)
    out_path = tmp_path / "report.md"
    main(["--base", "origin/main", "--out", str(out_path)])
    assert out_path.exists()
    assert list(tmp_path.iterdir()) == [out_path]
    assert "Verdict candidate:" in out_path.read_text()


def test_parse_args_defaults():
    args = parse_args([])
    assert args.base == "origin/main"
    assert args.pr is None
    assert args.skip_tests is False
    assert args.out is None


# --- 12. the tool itself never imports a broker/execution/network module (except subprocess) ----------


def test_module_has_no_forbidden_imports():
    imported = _imported_modules(audit_module)
    for name in imported:
        for forbidden in _FORBIDDEN_IMPORT_FRAGMENTS:
            assert forbidden not in name, f"must not import {name!r}"


def test_module_has_no_order_action_verbs_as_callable_identifiers():
    source = _module_source()
    for forbidden in _FORBIDDEN_ORDER_ACTION_IDENTIFIERS:
        assert forbidden not in source, f"must not contain {forbidden!r}"


def test_module_never_calls_git_push_merge_or_commit():
    source = _module_source()
    for forbidden in ('"push"', "'push'", '"merge"', "'merge'", '"commit"', "'commit'", '"reset"', "'reset'"):
        assert forbidden not in source, f"must not contain a git write subcommand: {forbidden!r}"


def test_module_only_writes_a_file_when_out_is_passed():
    source = _module_source()
    write_calls = [line for line in source.splitlines() if ".write_text(" in line]
    assert len(write_calls) == 1
    assert "args.out" in _module_source()
