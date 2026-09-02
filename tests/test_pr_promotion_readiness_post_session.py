"""Post-session promotion workflow: session gate, fresh evidence, capability
audit, merged-by-human gating, append-only records, idempotent reruns."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops.pr_promotion_readiness import HOLD, READY, REJECT
from ops.pr_promotion_readiness import evidence as evidence_mod
from ops.pr_promotion_readiness.policy import policy_fingerprint
from ops.pr_promotion_readiness.post_session import (
    ADAPTER_STEP,
    PRSpec,
    audit_automation_capabilities,
    confirm_merged_into_main,
    main,
    render_status_block,
    run_post_session_workflow,
)
from ops.pr_promotion_readiness.record import read_records
from ops.pr_promotion_readiness.session_evidence import SESSION_INCOMPLETE, verify_session_evidence

HEAD438 = "c1456fb755a6998f7528ee4b1f571c165e5835ac"
HEAD439 = "16f2602a2905cdd703f156af5d4bf339585c61e8"
HEAD440 = "6c8061200000000000000000000000000000000000"[:40]
MAIN = "a2d97cf5ce53305b5e0324379a5f10be74d8a37d"
AFTER = datetime(2026, 9, 2, 15, 0, tzinfo=timezone.utc)  # 11:00 ET
BEFORE = datetime(2026, 9, 2, 13, 50, tzinfo=timezone.utc)  # 09:50 ET

COMPLETE_SESSION = """# session 2026-09-02
## 09:26 ET packet
retrieved_at: 2026-09-02T13:26:10+00:00
locked ticker: XYZ
## 09:46 ET ORB update
retrieved_at: 2026-09-02T13:46:05+00:00
## 10:03 ET verdict
retrieved_at: 2026-09-02T14:03:20+00:00
Verdict: WAIT — NO ACTIONABLE SETUP
"""


def _session(tmp_path, text=COMPLETE_SESSION, name="morning_packet_2026-09-02_SESSION.md"):
    path = tmp_path / name
    path.write_text(text)
    return path


# --- session evidence gate ----------------------------------------------------


def test_session_gate(tmp_path):
    path = _session(tmp_path)
    ok = verify_session_evidence(path, now=AFTER)
    assert ok.complete and ok.sections_present == ("09:26", "09:46", "10:03") and ok.sha256
    early = verify_session_evidence(path, now=BEFORE)
    assert not early.complete and any("not reached" in r for r in early.reasons)
    missing = verify_session_evidence(tmp_path / "nope.md", now=AFTER)
    assert not missing.complete and not missing.exists
    partial = verify_session_evidence(_session(tmp_path, COMPLETE_SESSION.split("## 10:03")[0], "p_2026-09-02.md"), now=AFTER)
    assert "10:03 ET section missing" in partial.reasons
    no_ts = verify_session_evidence(_session(tmp_path, COMPLETE_SESSION.replace("retrieved_at: 2026-09-02T13:46:05+00:00\n", ""), "t_2026-09-02.md"), now=AFTER)
    assert "09:46 ET section has no retrieval timestamp" in no_ts.reasons
    changed = verify_session_evidence(path, now=AFTER, previous_sha256="0" * 64)
    assert changed.frozen is False and any("changed since last recorded fingerprint" in r for r in changed.reasons)
    frozen = verify_session_evidence(path, now=AFTER, previous_sha256=ok.sha256)
    assert frozen.frozen is True and frozen.complete


# --- canned read-only gh --------------------------------------------------------


def _pr_payload(number, head, branch, files, *, draft=False, title="T", state="OPEN", merge_commit=None, job=100):
    return {
        "number": number, "url": f"https://github.com/o/r/pull/{number}", "title": title, "author": {"login": "imani"},
        "state": state, "isDraft": draft, "labels": [], "headRefName": branch, "headRefOid": head, "baseRefName": "main",
        "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "", "files": [{"path": f} for f in files],
        "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS", "workflowName": "CI",
                               "detailsUrl": f"https://github.com/o/r/actions/runs/1/job/{job}"}],
        "mergeCommit": ({"oid": merge_commit} if merge_commit else None), "mergedAt": None,
    }


def _runner(*, main_sha=MAIN, merge_base=None, pr438_state="OPEN", merge_commit_438=None, main_contains=True, fail=None, head438=HEAD438):
    calls: list[list[str]] = []
    prs = {
        438: _pr_payload(438, head438, "chatgpt/options-contract-shortlist", ["options_manager/contracts/selector.py"], state=pr438_state, merge_commit=merge_commit_438, job=100),
        439: _pr_payload(439, HEAD439, "claude/contract-validator-finite-hardening", ["options_manager/contracts/contract_validator.py"], draft=True, title="[HOLD] x", job=200),
        440: _pr_payload(440, HEAD440, "claude/options-promotion-readiness", ["ops/pr_promotion_readiness/policy.py", "tests/test_pr_promotion_readiness.py"], draft=True, title="[HOLD] y", job=300),
    }
    jobs = {"100": head438, "200": HEAD439, "300": HEAD440}

    def runner(args):
        calls.append(list(args))
        if fail and fail(args):
            return 1, "", "HTTP 502"
        if args[:2] == ["repo", "view"]:
            return 0, json.dumps({"nameWithOwner": "o/r"}), ""
        if args[:2] == ["pr", "view"]:
            return 0, json.dumps(prs[int(args[2])]), ""
        if args[0] == "api" and args[1].endswith("/branches/main"):
            return 0, json.dumps({"commit": {"sha": main_sha}}), ""
        if args[0] == "api" and "/pulls/" in args[1] and args[1].endswith("/files"):
            return 0, json.dumps([{"filename": "options_manager/contracts/selector.py", "patch": "@@ -1 +1 @@\n+x = 1\n"}]), ""
        if args[0] == "api" and "/compare/" in args[1]:
            base, _, head = args[1].rsplit("/", 1)[1].partition("...")
            if head == "main":
                return 0, json.dumps({"status": "ahead" if main_contains else "diverged", "behind_by": 0 if main_contains else 1}), ""
            return 0, json.dumps({"merge_base_commit": {"sha": merge_base or main_sha}, "behind_by": 0, "ahead_by": 1}), ""
        if args[:2] == ["api", "graphql"]:
            return 0, json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}}), ""
        if args[0] == "api" and "/actions/jobs/" in args[1]:
            return 0, json.dumps({"head_sha": jobs[args[1].rsplit("/", 1)[1]]}), ""
        if args[:2] == ["run", "view"]:
            return 0, "4559 passed, 6 skipped in 70s", ""
        raise AssertionError(args)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


SPECS = (PRSpec(438, "options-advisory"), PRSpec(439, "options-advisory"), PRSpec(440, "ops-tooling"))


def _run(tmp_path, runner, *, now=AFTER, session_text=COMPLETE_SESSION, record=None):
    session = _session(tmp_path, session_text)
    return run_post_session_workflow(session_file=session, pr_specs=SPECS, record_path=record or tmp_path / "rec.jsonl", runner=runner, now=now)


def test_incomplete_session_holds_everything_and_touches_no_pr(tmp_path):
    runner = _runner()
    result = _run(tmp_path, runner, now=BEFORE)
    assert not result.session.complete
    assert all(v is None for v in result.verdicts.values())
    assert result.verdict_notes[438] == (SESSION_INCOMPLETE,)
    assert not any(c[:2] == ["pr", "view"] for c in runner.calls)  # no GitHub calls before the gate
    block = render_status_block(result)
    assert block.startswith("SESSION: HOLD") and "#438: HOLD — SESSION EVIDENCE INCOMPLETE" in block
    records = read_records(tmp_path / "rec.jsonl")
    assert len(records) == 1 and records[0]["record_type"] == "post_session_workflow"
    assert records[0]["verdicts"] == {"438": SESSION_INCOMPLETE, "439": SESSION_INCOMPLETE, "440": SESSION_INCOMPLETE}


def test_complete_session_evaluates_fresh_and_gates_next_step_on_merge(tmp_path):
    runner = _runner()
    result = _run(tmp_path, runner)
    assert result.session.complete
    assert result.verdicts[438].verdict == READY
    assert result.verdicts[439].verdict == HOLD
    assert result.verdicts[440].verdict == HOLD  # draft + hold marker + claude/* branch
    assert result.capability_audit is not None and result.capability_audit.clean
    assert result.next_eligible_step.startswith("none — #438 is OPEN")
    block = render_status_block(result)
    assert "#438: READY — HUMAN MERGE APPROVAL REQUIRED" in block
    assert f"head {HEAD438}  main {MAIN}" in block
    assert "#440: HOLD" in block and "capability audit clean" in block
    assert "HUMAN ACTION REQUIRED: #438 READY — HUMAN MERGE APPROVAL REQUIRED" in block
    for call in runner.calls:
        assert evidence_mod._is_read_only_gh_command(call), call
    records = read_records(tmp_path / "rec.jsonl")
    assert [r["record_type"] for r in records] == ["pr_readiness"] * 3 + ["post_session_workflow"]
    assert records[-1]["next_eligible_step"] == result.next_eligible_step
    assert records[-1]["action_taken"].startswith("none")


def test_adapter_step_only_after_human_merge_confirmed_on_fresh_main(tmp_path):
    merged = _runner(pr438_state="MERGED", merge_commit_438="abc123" + "0" * 34, main_sha="f" * 40)
    result = _run(tmp_path, merged)
    assert result.merge_438.confirmed
    assert result.next_eligible_step.startswith(ADAPTER_STEP)
    not_in_main = _runner(pr438_state="MERGED", merge_commit_438="abc123" + "0" * 34, main_contains=False)
    result = _run(tmp_path, not_in_main, record=tmp_path / "r2.jsonl")
    assert not result.merge_438.confirmed and result.next_eligible_step.startswith("none")


def test_previous_ready_is_discarded_when_head_or_main_changes(tmp_path):
    record = tmp_path / "rec.jsonl"
    _run(tmp_path, _runner(), record=record)
    new_head = "1" * 40
    result = _run(tmp_path, _runner(head438=new_head), record=record)
    assert any("previous READY" in n and "discarded" in n for n in result.verdict_notes[438])
    assert result.verdicts[438].evidence.head_sha == new_head
    result = _run(tmp_path, _runner(main_sha="2" * 40, merge_base=MAIN), record=record)
    assert result.verdicts[438].verdict == HOLD  # merge-base no longer equals fresh main
    assert any("not based on current main" in h for h in result.verdicts[438].holds)


def test_rerun_is_idempotent_append_only_and_detects_session_change(tmp_path):
    record = tmp_path / "rec.jsonl"
    first = _run(tmp_path, _runner(), record=record)
    second = _run(tmp_path, _runner(), record=record)
    assert second.session.frozen is True and second.session.complete
    assert len(read_records(record)) == 8
    session = tmp_path / "morning_packet_2026-09-02_SESSION.md"
    session.write_text(COMPLETE_SESSION + "\nbackfilled line\n")
    third = run_post_session_workflow(session_file=session, pr_specs=SPECS, record_path=record, runner=_runner(), now=AFTER)
    assert not third.session.complete and third.session.frozen is False
    assert all(v is None for v in third.verdicts.values())
    assert session.read_text().endswith("backfilled line\n")  # never rewritten by the tool


def test_github_failure_fails_closed(tmp_path):
    result = _run(tmp_path, _runner(fail=lambda a: "/compare/" in a[1] if a[0] == "api" else False))
    assert result.verdicts[438].verdict == HOLD
    assert any("evidence collection error" in h for h in result.verdicts[438].holds)
    assert result.next_eligible_step.startswith("none — cannot confirm #438 merge state") or "not merged" in result.next_eligible_step


def test_capability_audit_flags_real_capabilities(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "evidence.py").write_text('import subprocess\nsubprocess.run(["gh", "pr", "view"])\n')
    clean = audit_automation_capabilities(pkg, previous_policy_fingerprint=None)
    assert clean.clean
    (pkg / "bad.py").write_text(
        "import subprocess, shutil\nfrom execution import broker_interface\n"
        'subprocess.run(["git", "push"], shell=True)\nimport os\nos.system("systemctl restart x")\n'
    )
    bad = audit_automation_capabilities(pkg, previous_policy_fingerprint=None)
    joined = " | ".join(bad.findings)
    for expected in ("bad.py imports subprocess", "imports shutil", "imports execution", "not the gh CLI", "shell=", "calls os.system"):
        assert expected in joined, joined
    drift = audit_automation_capabilities(pkg, previous_policy_fingerprint="0" * 64)
    assert any("policy fingerprint changed" in f for f in drift.findings)
    assert policy_fingerprint() == policy_fingerprint()


def test_policy_drift_rejects_automation_pr(tmp_path):
    record = tmp_path / "rec.jsonl"
    record.write_text(json.dumps({"record_type": "pr_readiness", "pr_number": 1, "policy_fingerprint": "0" * 64}) + "\n")
    result = _run(tmp_path, _runner(), record=record)
    assert result.verdicts[440].verdict == REJECT
    assert any("policy fingerprint changed" in b for b in result.verdicts[440].blockers)
    assert result.verdicts[438].verdict == READY  # drift is attributed to the automation PR only


def test_confirm_merge_reads_only(tmp_path):
    runner = _runner(pr438_state="MERGED", merge_commit_438="a" * 40)
    conf = confirm_merged_into_main(438, runner=runner)
    assert conf.confirmed and conf.merge_commit == "a" * 40
    for call in runner.calls:
        assert evidence_mod._is_read_only_gh_command(call)


def test_prspec_parse():
    spec = PRSpec.parse("438:options-advisory:c1456fb:/tmp/t.json")
    assert (spec.number, spec.scope, spec.expect_head, spec.test_evidence_path) == (438, "options-advisory", "c1456fb", "/tmp/t.json")
    assert PRSpec.parse("440").scope == "options-advisory"
    with pytest.raises(ValueError):
        PRSpec.parse("1:no-such-scope")


def test_cli_exit_codes(tmp_path, monkeypatch, capsys):
    import ops.pr_promotion_readiness.post_session as ps

    session = _session(tmp_path)
    record = tmp_path / "rec.jsonl"
    monkeypatch.setattr(ps, "run_post_session_workflow", lambda **kw: _run(tmp_path, _runner(), record=record))
    assert main(["--session-file", str(session), "--pr", "438", "--pr", "439:options-advisory", "--pr", "440:ops-tooling", "--record", str(record)]) == 2
    assert "SESSION: COMPLETE" in capsys.readouterr().out
    monkeypatch.setattr(ps, "run_post_session_workflow", lambda **kw: _run(tmp_path, _runner(), now=BEFORE, record=record))
    assert main(["--session-file", str(session), "--pr", "438", "--record", str(record)]) == 2
