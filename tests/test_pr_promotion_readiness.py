"""ops.pr_promotion_readiness -- fail-closed verdict, read-only collection,
append-only record. No network: gh is replaced by a canned runner."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ops.pr_promotion_readiness import (
    HOLD,
    READY,
    REJECT,
    SCOPE_POLICIES,
    PromotionEvidence,
    append_promotion_record,
    build_promotion_record,
    classify_scope,
    collect_pr_evidence,
    evaluate_promotion_readiness,
    parse_pytest_summary,
)
from ops.pr_promotion_readiness import cli, evidence as evidence_mod
from ops.pr_promotion_readiness.actions import NoPromotionAction
from ops.pr_promotion_readiness.models import CheckResult, ReviewThread
from ops.pr_promotion_readiness.models import TestEvidence as _TestEvidence

POLICY = SCOPE_POLICIES["options-advisory"]
HEAD = "c1456fb755a6998f7528ee4b1f571c165e5835ac"
MAIN = "a2d97cf0000000000000000000000000000000000"


def _check(name="tests", conclusion="SUCCESS", url="https://github.com/o/r/actions/runs/1/job/100"):
    return CheckResult(name=name, conclusion=conclusion, workflow="CI", details_url=url)


def _full(passed=4507, failed=0, skipped=6, errors=0, sha=HEAD, kind="full"):
    return _TestEvidence(kind=kind, source="ci tests job 100", sha=sha, passed=passed, failed=failed, skipped=skipped, errors=errors)


def _ready_evidence(**overrides) -> PromotionEvidence:
    base = dict(
        pr_number=438,
        collected_at="2026-09-02T11:30:00+00:00",
        repo="o/r",
        url="https://github.com/o/r/pull/438",
        title="Add fail-closed advisory options contract shortlist",
        author="imani",
        state="OPEN",
        is_draft=False,
        labels=(),
        branch="chatgpt/options-contract-shortlist",
        head_sha=HEAD,
        base_ref="main",
        base_sha=MAIN,
        merge_base_sha=MAIN,
        behind_base_by=0,
        ahead_of_base_by=7,
        mergeable="MERGEABLE",
        merge_state="CLEAN",
        review_decision="",
        review_threads=(),
        changed_files=("options_manager/contracts/selector.py", "tests/test_options_contract_shortlist.py"),
        checks=(_check("tests"), _check("CodeQL", url="https://github.com/o/r/runs/5")),
        tests=(_full(),),
        collection_errors=(),
    )
    base.update(overrides)
    return PromotionEvidence(**base)


# --- policy: READY only when everything is present and passing --------------


def test_complete_passing_evidence_is_ready():
    verdict = evaluate_promotion_readiness(_ready_evidence(), POLICY)
    assert verdict.verdict == READY
    assert verdict.blockers == () and verdict.holds == ()
    assert verdict.exit_code == 0


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"head_sha": None}, "head SHA unknown"),
        ({"is_draft": True}, "draft"),
        ({"is_draft": None}, "draft status unknown"),
        ({"state": "MERGED"}, "not OPEN"),
        ({"title": "[HOLD] thing"}, "hold marker"),
        ({"labels": ("do not merge",)}, "hold marker"),
        ({"base_ref": "develop"}, "expected 'main'"),
        ({"base_sha": None}, "current main SHA unknown"),
        ({"merge_base_sha": None}, "merge-base with main unknown"),
        ({"merge_base_sha": "0" * 40, "behind_base_by": 3}, "not based on current main"),
        ({"mergeable": "UNKNOWN"}, "mergeability unknown"),
        ({"mergeable": ""}, "mergeability unknown"),
        ({"merge_state": "BLOCKED"}, "not CLEAN"),
        ({"checks": ()}, "no CI checks"),
        ({"checks": (_check("CodeQL"),)}, "required check 'tests' missing"),
        ({"checks": (_check("tests", "PENDING"),)}, "not green"),
        ({"checks": (_check("tests", ""),)}, "not green"),
        ({"tests": ()}, "full-suite test evidence missing"),
        ({"tests": (_full(passed=None),)}, "unknown counts"),
        ({"tests": (_full(passed=0),)}, "ran zero tests"),
        ({"tests": (_full(sha="deadbeef" + "0" * 32),)}, "not head"),
        ({"review_decision": "CHANGES_REQUESTED"}, "CHANGES_REQUESTED"),
        ({"review_decision": "REVIEW_REQUIRED"}, "REVIEW_REQUIRED"),
        ({"review_threads": (ReviewThread(path="a.py", author="x", excerpt="fix this", resolved=False),)}, "unresolved review comment"),
        ({"changed_files": ()}, "changed-file list empty"),
        ({"changed_files": ("alert_ranker/scanner.py",)}, "outside scope"),
        ({"collection_errors": ("pr view: boom",)}, "evidence collection error"),
    ],
)
def test_missing_or_unknown_evidence_holds(overrides, fragment):
    verdict = evaluate_promotion_readiness(_ready_evidence(**overrides), POLICY)
    assert verdict.verdict == HOLD, verdict.reasons
    assert any(fragment in r for r in verdict.holds), verdict.holds
    assert verdict.exit_code == 2


def test_stale_expected_head_holds():
    verdict = evaluate_promotion_readiness(_ready_evidence(), POLICY, expected_head_sha="16f2602")
    assert verdict.verdict == HOLD
    assert any("stale SHA" in r for r in verdict.holds)
    assert evaluate_promotion_readiness(_ready_evidence(), POLICY, expected_head_sha=HEAD[:7]).verdict == READY


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"mergeable": "CONFLICTING"}, "merge conflict"),
        ({"checks": (_check("tests", "FAILURE"),)}, "FAILURE"),
        ({"checks": (_check("tests"), _check("CodeQL", "CANCELLED"))}, "CANCELLED"),
        ({"tests": (_full(failed=1),)}, "1 failed"),
        ({"tests": (_full(errors=2),)}, "2 errors"),
        ({"changed_files": ("execution/tradovate_broker.py",)}, "forbidden area"),
        ({"changed_files": ("options_manager/broker_boundary.py",)}, "broker boundary"),
        ({"changed_files": ("deploy/systemd/x.service",)}, "deployment"),
        ({"changed_files": ("scripts/atomic_release.sh",)}, "deployment"),
        ({"changed_files": ("config/settings.py",)}, "runtime config"),
        ({"changed_files": ("risk_rules.yaml",)}, "risk policy"),
        ({"changed_files": (".env.example",)}, "credentials"),
        ({"changed_files": ("options_manager/secret_store.py",)}, "credentials"),
        ({"changed_files": ("webhook/runner.py",)}, "live webhook"),
        ({"changed_files": (".github/workflows/ci.yml",)}, "CI definition"),
    ],
)
def test_hard_failures_reject(overrides, fragment):
    verdict = evaluate_promotion_readiness(_ready_evidence(**overrides), POLICY)
    assert verdict.verdict == REJECT, verdict.reasons
    assert any(fragment in r for r in verdict.blockers), verdict.blockers
    assert verdict.exit_code == 3


def test_reject_outranks_hold_and_both_are_reported():
    verdict = evaluate_promotion_readiness(_ready_evidence(is_draft=True, mergeable="CONFLICTING"), POLICY)
    assert verdict.verdict == REJECT
    assert verdict.blockers and verdict.holds
    assert set(verdict.reasons) == set(verdict.blockers) | set(verdict.holds)


def test_targeted_evidence_optional_unless_policy_requires_it():
    targeted = _full(passed=77, skipped=0, kind="targeted")
    assert evaluate_promotion_readiness(_ready_evidence(tests=(_full(), targeted)), POLICY).verdict == READY
    strict = replace(POLICY, require_targeted_evidence=True)
    assert evaluate_promotion_readiness(_ready_evidence(), strict).verdict == HOLD
    assert evaluate_promotion_readiness(_ready_evidence(tests=(_full(), targeted)), strict).verdict == READY
    assert evaluate_promotion_readiness(_ready_evidence(tests=(_full(), replace(targeted, failed=1))), strict).verdict == REJECT


def test_approved_review_policy_holds_without_approval():
    strict = replace(POLICY, require_approved_review=True)
    assert evaluate_promotion_readiness(_ready_evidence(), strict).verdict == HOLD
    assert evaluate_promotion_readiness(_ready_evidence(review_decision="APPROVED"), strict).verdict == READY


def test_self_authored_branch_needs_independent_review():
    mine = _ready_evidence(branch="claude/contract-validator-finite-hardening")
    verdict = evaluate_promotion_readiness(mine, POLICY)
    assert verdict.verdict == HOLD
    assert any("independent (approved) review required" in h for h in verdict.holds)
    assert evaluate_promotion_readiness(replace(mine, review_decision="APPROVED"), POLICY).verdict == READY
    assert evaluate_promotion_readiness(_ready_evidence(branch="chatgpt/x"), POLICY).verdict == READY
    relaxed = replace(POLICY, independent_review_branch_patterns=())
    assert evaluate_promotion_readiness(mine, relaxed).verdict == READY


def test_scope_classification_checks_forbidden_before_allowed():
    findings = classify_scope(
        ("options_manager/contracts/selector.py", "options_manager/broker_boundary.py", "docs/other.md", "docs/options-x.md"),
        POLICY,
    )
    assert [f.category for f in findings] == ["allowed", "forbidden", "out_of_scope", "allowed"]


# --- pytest summary parsing ---------------------------------------------------


def test_parse_pytest_summary_reads_last_summary_line():
    log = "junk\n1 failed, 3 passed in 1.0s\n=== 4507 passed, 6 skipped, 1 warning in 54.37s ===\n"
    assert parse_pytest_summary(log) == {
        "passed": 4507, "failed": 0, "skipped": 6, "errors": 0, "xfailed": 0, "xpassed": 0, "warnings": 1, "deselected": 0,
    }
    assert parse_pytest_summary("2 failed, 10 passed, 1 error in 2s")["errors"] == 1
    assert parse_pytest_summary("no tests ran in 0.00s") is None
    assert parse_pytest_summary("") is None
    assert parse_pytest_summary("77 passed") is None  # no ' in ' => not a summary line


# --- evidence collection through a canned, read-only gh -----------------------


def _canned_runner(*, tests_conclusion="SUCCESS", log="4507 passed, 6 skipped in 54s", job_sha=HEAD, fail_compare=False):
    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        if args[:2] == ["repo", "view"]:
            return 0, json.dumps({"nameWithOwner": "o/r"}), ""
        if args[:2] == ["pr", "view"]:
            return 0, json.dumps({
                "number": 438, "url": "https://github.com/o/r/pull/438", "title": "T", "author": {"login": "imani"},
                "state": "OPEN", "isDraft": False, "labels": [], "headRefName": "b", "headRefOid": HEAD,
                "baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
                "files": [{"path": "options_manager/contracts/selector.py"}],
                "statusCheckRollup": [
                    {"name": "tests", "conclusion": tests_conclusion, "workflowName": "CI",
                     "detailsUrl": "https://github.com/o/r/actions/runs/1/job/100"},
                    {"name": "CodeQL", "conclusion": "SUCCESS", "detailsUrl": "https://github.com/o/r/runs/5"},
                ],
            }), ""
        if args[:1] == ["api"] and args[1].endswith("/branches/main"):
            return 0, json.dumps({"commit": {"sha": MAIN}}), ""
        if args[:1] == ["api"] and "/compare/" in args[1]:
            if fail_compare:
                return 1, "", "HTTP 500"
            return 0, json.dumps({"merge_base_commit": {"sha": MAIN}, "behind_by": 0, "ahead_by": 7}), ""
        if args[:2] == ["api", "graphql"]:
            return 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
                {"isResolved": True, "path": "x.py", "comments": {"nodes": [{"author": {"login": "r"}, "body": "ok"}]}},
            ]}}}}}), ""
        if args[:1] == ["api"] and "/actions/jobs/" in args[1]:
            return 0, json.dumps({"head_sha": job_sha}), ""
        if args[:2] == ["run", "view"]:
            return 0, log, ""
        raise AssertionError(f"unexpected gh call {args}")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_collect_evidence_is_read_only_and_complete():
    runner = _canned_runner()
    ev = collect_pr_evidence(438, runner=runner, now="2026-09-02T11:30:00+00:00")
    assert ev.head_sha == HEAD and ev.base_sha == MAIN and ev.merge_base_sha == MAIN
    assert ev.behind_base_by == 0 and ev.ahead_of_base_by == 7
    assert [c.name for c in ev.checks] == ["tests", "CodeQL"]
    assert ev.tests == (_TestEvidence(kind="full", source="ci tests job 100", sha=HEAD, passed=4507, failed=0, skipped=6, errors=0, command="pytest -q (CI)"),)
    assert ev.review_threads[0].resolved is True
    assert ev.collection_errors == ()
    for call in runner.calls:
        assert evidence_mod._is_read_only_gh_command(call), call
    assert evaluate_promotion_readiness(ev, POLICY).verdict == READY


def test_collect_evidence_records_failures_instead_of_guessing():
    ev = collect_pr_evidence(438, runner=_canned_runner(fail_compare=True))
    assert ev.merge_base_sha is None
    assert any("compare with base" in e for e in ev.collection_errors)
    assert evaluate_promotion_readiness(ev, POLICY).verdict == HOLD


def test_ci_log_without_summary_yields_no_test_evidence():
    ev = collect_pr_evidence(438, runner=_canned_runner(log="no tests ran in 0.00s"))
    assert ev.tests == ()
    verdict = evaluate_promotion_readiness(ev, POLICY)
    assert verdict.verdict == HOLD
    assert any("full-suite test evidence missing" in h for h in verdict.holds)


def test_failed_ci_is_not_mined_for_test_counts():
    ev = collect_pr_evidence(438, runner=_canned_runner(tests_conclusion="FAILURE"))
    assert ev.tests == ()
    assert evaluate_promotion_readiness(ev, POLICY).verdict == REJECT


def test_ci_counts_from_a_different_sha_hold():
    ev = collect_pr_evidence(438, runner=_canned_runner(job_sha="f" * 40))
    verdict = evaluate_promotion_readiness(ev, POLICY)
    assert verdict.verdict == HOLD
    assert any("not head" in h for h in verdict.holds)


@pytest.mark.parametrize(
    "args",
    [
        ["pr", "merge", "438"],
        ["pr", "close", "438"],
        ["pr", "comment", "438", "--body", "x"],
        ["pr", "review", "438", "--approve"],
        ["workflow", "run", "ci.yml"],
        ["api", "repos/o/r/pulls/438/merge", "-X", "PUT"],
        ["api", "repos/o/r/branches/main", "--method", "DELETE"],
        ["api", "graphql", "-f", "query=mutation { mergePullRequest }"],
        ["run", "rerun", "1"],
        [],
    ],
)
def test_mutating_gh_shapes_are_refused(args):
    assert not evidence_mod._is_read_only_gh_command(args)
    with pytest.raises(ValueError):
        evidence_mod.run_gh(args, runner=lambda a: (0, "{}", ""))


def test_package_capability_audit_is_clean():
    from ops.pr_promotion_readiness.post_session import audit_automation_capabilities

    root = Path(evidence_mod.__file__).parent
    audit = audit_automation_capabilities(root, previous_policy_fingerprint=None)
    assert audit.findings == (), audit.findings
    # only evidence.py may spawn a process, and only the gh CLI with an arg list
    # (post_session.py names the word in its audit rule; the AST audit above proves it never imports it)
    for name in ("policy.py", "record.py", "actions.py", "cli.py", "models.py", "session_evidence.py"):
        assert "subprocess" not in (root / name).read_text()
    assert 'subprocess.run(\n            ["gh", *args],' in (root / "evidence.py").read_text()


# --- record ------------------------------------------------------------------


def test_record_is_append_only_and_complete(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text('{"existing":true}\n')
    verdict = evaluate_promotion_readiness(_ready_evidence(), POLICY)
    record = build_promotion_record(verdict)
    append_promotion_record(path, record)
    append_promotion_record(path, build_promotion_record(evaluate_promotion_readiness(_ready_evidence(is_draft=True), POLICY)))
    lines = path.read_text().splitlines()
    assert lines[0] == '{"existing":true}'
    assert len(lines) == 3
    first, second = json.loads(lines[1]), json.loads(lines[2])
    for key in ("pr_number", "branch", "head_sha", "base_sha", "timestamp", "ci_checks", "tests", "mergeable",
                "scope_findings", "blockers", "holds", "verdict", "reasons"):
        assert key in first, key
    assert first["verdict"] == READY and second["verdict"] == HOLD
    assert first["action_taken"].startswith("none")


def test_record_refuses_non_file_path(tmp_path):
    with pytest.raises(ValueError):
        append_promotion_record(tmp_path, {"x": 1})


def test_no_promotion_action_never_acts():
    ready = evaluate_promotion_readiness(_ready_evidence(), POLICY)
    hold = evaluate_promotion_readiness(_ready_evidence(is_draft=True), POLICY)
    assert NoPromotionAction().perform(ready, human_approval="yes").startswith("no action")
    assert NoPromotionAction().perform(hold).startswith("no action: verdict is HOLD")


# --- cli ----------------------------------------------------------------------


def test_cli_exit_codes_and_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "collect_pr_evidence", lambda n, operator_tests=(): _ready_evidence(pr_number=n, is_draft=(n == 439)))
    record = tmp_path / "r.jsonl"
    code = cli.main(["--pr", "438", "--record", str(record)])
    assert code == 0
    assert "READY FOR PROMOTION" in capsys.readouterr().out
    code = cli.main(["--pr", "438", "--pr", "439", "--record", str(record), "--json"])
    assert code == 2
    out = capsys.readouterr().out
    assert '"verdict": "HOLD"' in out
    assert len(record.read_text().splitlines()) == 3


def test_operator_test_evidence_file_round_trip(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps([{"kind": "targeted", "source": "local", "sha": HEAD, "passed": 77, "failed": 0, "skipped": 0, "errors": 0, "command": "pytest tests/x"}, {"kind": "targeted", "passed": 5}]))
    loaded = evidence_mod.load_operator_test_evidence(str(path))
    assert loaded[0].passed == 77 and loaded[0].command == "pytest tests/x"
    assert loaded[1].failed is None  # unknown stays unknown
    verdict = evaluate_promotion_readiness(_ready_evidence(tests=(_full(), loaded[1])), POLICY)
    assert verdict.verdict == HOLD and any("unknown counts" in h for h in verdict.holds)
