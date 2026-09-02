"""Post-session promotion workflow: automatic verification, human merge.

Runs only after the morning forward-proof session file is complete,
timestamped, frozen, and the 10:03 ET stage time has passed. Then, for
each PR, re-collects *fresh* GitHub evidence through the existing
read-only path, evaluates the existing policy, appends to the existing
record, audits this automation's own capabilities (for the PR that ships
it), and gates the next implementation step on a *merged-by-human*
confirmation read from fresh main -- never on a READY verdict.

Reruns are idempotent: they only append records. Nothing here merges,
pushes, deploys, restarts, trades, edits policy, or touches the session
file.

``python -m ops.pr_promotion_readiness.post_session --session-file F --pr 438:options-advisory --pr 439:options-advisory --pr 440:ops-tooling``
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .evidence import Runner, _gh_json, _is_read_only_gh_command, collect_pr_evidence, load_operator_test_evidence
from .models import HOLD, READY, REJECT, PromotionVerdict, TestEvidence
from .policy import SCOPE_POLICIES, ScopePolicy, evaluate_promotion_readiness, policy_fingerprint
from .record import DEFAULT_RECORD_PATH, append_promotion_record, build_promotion_record, read_records
from .session_evidence import SESSION_INCOMPLETE, SessionEvidenceStatus, verify_session_evidence

ADAPTER_STEP = "read-only Robinhood contract adapter (normalize real chain output into ContractCandidate)"
AUTOMATION_PR_MARKER = "ops/pr_promotion_readiness/"

# Modules the automation must never import (capability boundary).
_FORBIDDEN_IMPORT_ROOTS = {
    "shutil", "socket", "http", "urllib", "requests", "httpx", "ftplib", "smtplib",
    "execution", "options_companion", "webhook", "risk", "config", "alert_ranker", "journal",
    "notifications", "sources", "strategy", "adaptive", "replay",
}
_MUTATING_GH_SHAPES: tuple[list[str], ...] = (
    ["pr", "merge", "1"],
    ["pr", "merge", "1", "--squash"],
    ["pr", "close", "1"],
    ["pr", "ready", "1"],
    ["pr", "edit", "1", "--title", "x"],
    ["pr", "review", "1", "--approve"],
    ["pr", "comment", "1", "--body", "x"],
    ["api", "repos/o/r/pulls/1/merge", "-X", "PUT"],
    ["api", "repos/o/r/git/refs/heads/main", "--method", "PATCH"],
    ["api", "graphql", "-f", "query=mutation { mergePullRequest(input:{}) { clientMutationId } }"],
    ["workflow", "run", "ci.yml"],
    ["run", "rerun", "1"],
    ["release", "create", "v1"],
)


@dataclass(frozen=True)
class PRSpec:
    number: int
    scope: str
    expect_head: Optional[str] = None
    test_evidence_path: Optional[str] = None

    @classmethod
    def parse(cls, text: str) -> "PRSpec":
        parts = text.split(":")
        number = int(parts[0])
        scope = parts[1] if len(parts) > 1 and parts[1] else "options-advisory"
        expect = parts[2] if len(parts) > 2 and parts[2] else None
        path = parts[3] if len(parts) > 3 and parts[3] else None
        if scope not in SCOPE_POLICIES:
            raise ValueError(f"unknown scope {scope!r}")
        return cls(number=number, scope=scope, expect_head=expect, test_evidence_path=path)


@dataclass(frozen=True)
class MergeConfirmation:
    pr_number: int
    state: str
    merge_commit: Optional[str]
    main_sha: Optional[str]
    main_contains_merge: Optional[bool]
    errors: tuple[str, ...]

    @property
    def confirmed(self) -> bool:
        return self.state == "MERGED" and self.main_contains_merge is True


@dataclass(frozen=True)
class CapabilityAudit:
    package_root: str
    findings: tuple[str, ...]
    policy_fingerprint: str
    previous_policy_fingerprint: Optional[str]

    @property
    def clean(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class PostSessionResult:
    run_at: str
    session: SessionEvidenceStatus
    verdicts: dict[int, Optional[PromotionVerdict]]
    verdict_notes: dict[int, tuple[str, ...]]
    merge_438: Optional[MergeConfirmation]
    merge_439: Optional[MergeConfirmation]
    capability_audit: Optional[CapabilityAudit]
    next_eligible_step: str
    human_actions: tuple[str, ...]
    record_path: str


# --- capability audit ---------------------------------------------------------


def audit_automation_capabilities(package_root: Path, *, previous_policy_fingerprint: Optional[str]) -> CapabilityAudit:
    """Static + runtime proof that this package cannot merge, push, deploy,
    restart, mutate main, trade, or change risk policy -- and that its
    scope/forbidden policy has not silently changed."""
    findings: list[str] = []
    for path in sorted(Path(package_root).glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(f"{path.name}: unparseable ({exc})")
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    findings.append(f"{path.name} imports {name}")
                if root == "subprocess" and path.name != "evidence.py":
                    findings.append(f"{path.name} imports subprocess (only evidence.py may)")
            if isinstance(node, ast.Call):
                func = node.func
                dotted = ""
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    dotted = f"{func.value.id}.{func.attr}"
                elif isinstance(func, ast.Name):
                    dotted = func.id
                if dotted in {"os.system", "os.execv", "os.execvp", "os.remove", "os.unlink", "os.rename", "os.replace", "shutil.rmtree", "exec", "eval"}:
                    findings.append(f"{path.name} calls {dotted}")
                if dotted == "subprocess.run":
                    first = node.args[0] if node.args else None
                    ok = (
                        isinstance(first, ast.List)
                        and first.elts
                        and isinstance(first.elts[0], ast.Constant)
                        and first.elts[0].value == "gh"
                    )
                    if not ok:
                        findings.append(f"{path.name} runs a subprocess that is not the gh CLI")
                    if any(kw.arg == "shell" for kw in node.keywords):
                        findings.append(f"{path.name} passes shell= to subprocess.run")
    for shape in _MUTATING_GH_SHAPES:
        if _is_read_only_gh_command(shape):
            findings.append(f"gh allowlist accepts a mutating shape: {' '.join(shape[:3])}")
    fingerprint = policy_fingerprint()
    if previous_policy_fingerprint and previous_policy_fingerprint != fingerprint:
        findings.append(
            f"scope/forbidden policy fingerprint changed since last record ({previous_policy_fingerprint[:12]} -> {fingerprint[:12]}); requires human acknowledgement"
        )
    return CapabilityAudit(
        package_root=str(package_root), findings=tuple(findings), policy_fingerprint=fingerprint,
        previous_policy_fingerprint=previous_policy_fingerprint,
    )


# --- merged-by-human confirmation ----------------------------------------------


def confirm_merged_into_main(pr_number: int, *, runner: Optional[Runner] = None) -> MergeConfirmation:
    errors: list[str] = []
    repo_json, err = _gh_json(["repo", "view", "--json", "nameWithOwner"], runner=runner)
    repo = str((repo_json or {}).get("nameWithOwner", "")) if isinstance(repo_json, dict) else ""
    if err:
        errors.append(f"repo: {err}")
    pr, err = _gh_json(["pr", "view", str(pr_number), "--json", "state,mergeCommit,mergedAt,baseRefName"], runner=runner)
    if err or not isinstance(pr, dict):
        errors.append(f"pr view: {err or 'no data'}")
        return MergeConfirmation(pr_number, "", None, None, None, tuple(errors))
    state = str(pr.get("state") or "")
    merge_commit = ((pr.get("mergeCommit") or {}).get("oid")) or None
    main_sha: Optional[str] = None
    contains: Optional[bool] = None
    if repo:
        branch_json, err = _gh_json(["api", f"repos/{repo}/branches/main"], runner=runner)
        if err:
            errors.append(f"main tip: {err}")
        else:
            main_sha = ((branch_json or {}).get("commit") or {}).get("sha") or None
        if state == "MERGED" and merge_commit:
            cmp_json, err = _gh_json(["api", f"repos/{repo}/compare/{merge_commit}...main"], runner=runner)
            if err:
                errors.append(f"compare merge commit with main: {err}")
            else:
                status = str((cmp_json or {}).get("status") or "")
                contains = status in {"identical", "ahead"}
    return MergeConfirmation(pr_number, state, merge_commit, main_sha, contains, tuple(errors))


# --- workflow ----------------------------------------------------------------


def _last_record_for(records: list[dict[str, Any]], pr_number: int) -> Optional[dict[str, Any]]:
    for item in reversed(records):
        if item.get("record_type", "pr_readiness") == "pr_readiness" and item.get("pr_number") == pr_number:
            return item
    return None


def _last_session_fingerprint(records: list[dict[str, Any]], session_path: str) -> Optional[str]:
    for item in reversed(records):
        if item.get("record_type") == "post_session_workflow" and item.get("session_file") == session_path:
            return item.get("session_sha256")
    return None


def _last_policy_fingerprint(records: list[dict[str, Any]]) -> Optional[str]:
    for item in reversed(records):
        fp = item.get("policy_fingerprint")
        if fp:
            return str(fp)
    return None


def run_post_session_workflow(
    *,
    session_file: Path,
    pr_specs: Sequence[PRSpec],
    record_path: Path = DEFAULT_RECORD_PATH,
    package_root: Optional[Path] = None,
    runner: Optional[Runner] = None,
    now: Optional[datetime] = None,
) -> PostSessionResult:
    current = now or datetime.now(timezone.utc)
    run_at = current.isoformat(timespec="seconds")
    record_path = Path(record_path)
    records = read_records(record_path)
    session = verify_session_evidence(
        Path(session_file), now=current, previous_sha256=_last_session_fingerprint(records, str(session_file))
    )

    verdicts: dict[int, Optional[PromotionVerdict]] = {}
    notes: dict[int, tuple[str, ...]] = {}
    merge_438: Optional[MergeConfirmation] = None
    merge_439: Optional[MergeConfirmation] = None
    audit: Optional[CapabilityAudit] = None
    next_step = "none"
    human: list[str] = []

    if not session.complete:
        for spec in pr_specs:
            verdicts[spec.number] = None
            notes[spec.number] = (SESSION_INCOMPLETE,)
        human.append("complete/freeze the morning session evidence; nothing is evaluated until then")
        next_step = f"none — {SESSION_INCOMPLETE}"
    else:
        previous_policy = _last_policy_fingerprint(records)
        for spec in pr_specs:
            policy: ScopePolicy = SCOPE_POLICIES[spec.scope]
            operator_tests: tuple[TestEvidence, ...] = ()
            note_list: list[str] = []
            if spec.test_evidence_path:
                try:
                    operator_tests = load_operator_test_evidence(spec.test_evidence_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    note_list.append(f"operator test evidence unreadable: {exc}")
            evidence = collect_pr_evidence(spec.number, runner=runner, now=run_at, operator_tests=operator_tests)
            verdict = evaluate_promotion_readiness(evidence, policy, expected_head_sha=spec.expect_head)
            previous = _last_record_for(records, spec.number)
            if previous and previous.get("verdict") == READY:
                if previous.get("head_sha") != evidence.head_sha or previous.get("base_sha") != evidence.base_sha:
                    note_list.append(
                        f"previous READY at head {str(previous.get('head_sha'))[:12]} / main {str(previous.get('base_sha'))[:12]} discarded: head or main changed"
                    )
            is_automation_pr = any(f.startswith(AUTOMATION_PR_MARKER) for f in evidence.changed_files)
            if is_automation_pr:
                audit = audit_automation_capabilities(package_root or Path(__file__).parent, previous_policy_fingerprint=previous_policy)
                if not audit.clean:
                    verdict = PromotionVerdict(
                        verdict=REJECT,
                        reasons=tuple(f"capability audit: {f}" for f in audit.findings) + verdict.reasons,
                        blockers=tuple(f"capability audit: {f}" for f in audit.findings) + verdict.blockers,
                        holds=verdict.holds,
                        scope_findings=verdict.scope_findings,
                        scope_policy=verdict.scope_policy,
                        evidence=verdict.evidence,
                    )
                else:
                    note_list.append("capability audit clean: no merge/push/deploy/restart/trade/risk-policy capability; gh allowlist rejects every mutating shape")
            record = build_promotion_record(verdict)
            record["workflow"] = "post_session"
            record["workflow_run_at"] = run_at
            record["session_file"] = str(session_file)
            record["session_sha256"] = session.sha256
            record["notes"] = note_list
            append_promotion_record(record_path, record)
            verdicts[spec.number] = verdict
            notes[spec.number] = tuple(note_list)
            if verdict.verdict == READY:
                human.append(f"#{spec.number} READY — HUMAN MERGE APPROVAL REQUIRED (head {evidence.head_sha[:12]})")

        merge_438 = confirm_merged_into_main(438, runner=runner)
        merge_439 = confirm_merged_into_main(439, runner=runner)
        if merge_438.confirmed:
            next_step = f"{ADAPTER_STEP} — eligible: #438 merged by human as {merge_438.merge_commit[:12]} and fresh main {merge_438.main_sha[:12]} contains it"
        elif merge_438.errors:
            next_step = f"none — cannot confirm #438 merge state ({'; '.join(merge_438.errors)})"
        else:
            next_step = f"none — #438 is {merge_438.state or 'unknown'}, not merged; adapter work does not start on READY alone"
        if merge_439.errors:
            human.append(f"cannot confirm #439 merge state ({'; '.join(merge_439.errors)})")
        if not any(a.startswith("#438 READY") for a in human) and (verdicts.get(438) is not None) and not merge_438.confirmed:
            human.append("#438 is not READY: resolve the listed blockers/holds before any merge")
        v439 = verdicts.get(439)
        if v439 is not None and v439.verdict != READY and not (merge_439 and merge_439.confirmed):
            human.append("#439 needs independent review + undraft + hold marker removed before it can be READY")
        v440 = verdicts.get(440)
        if v440 is not None and v440.verdict != READY:
            human.append("#440 (this automation) needs independent review before it can be READY")

    summary = {
        "record_version": 1,
        "record_type": "post_session_workflow",
        "tool": "ops.pr_promotion_readiness.post_session",
        "timestamp": run_at,
        "session_file": str(session_file),
        "session_sha256": session.sha256,
        "session_status": asdict(session),
        "policy_fingerprint": policy_fingerprint(),
        "verdicts": {str(n): (v.verdict if v else SESSION_INCOMPLETE) for n, v in verdicts.items()},
        "heads": {str(n): (v.evidence.head_sha if v else None) for n, v in verdicts.items()},
        "main_sha": (merge_438.main_sha if merge_438 else None),
        "merge_438": asdict(merge_438) if merge_438 else None,
        "merge_439": asdict(merge_439) if merge_439 else None,
        "capability_audit": asdict(audit) if audit else None,
        "next_eligible_step": next_step,
        "human_actions": human,
        "validators_finite_hardened_on_main": bool(merge_439 and merge_439.confirmed),
        "action_taken": "none (validation only; every merge requires human approval)",
    }
    append_promotion_record(record_path, summary)
    return PostSessionResult(
        run_at=run_at, session=session, verdicts=verdicts, verdict_notes=notes, merge_438=merge_438, merge_439=merge_439,
        capability_audit=audit, next_eligible_step=next_step, human_actions=tuple(human), record_path=str(record_path),
    )


def render_status_block(result: PostSessionResult) -> str:
    lines = [f"SESSION: {'COMPLETE' if result.session.complete else 'HOLD'}"]
    if not result.session.complete:
        lines.append(f"  {SESSION_INCOMPLETE}: " + "; ".join(result.session.reasons))
    else:
        lines.append(f"  file {result.session.path} sha256 {result.session.sha256[:12]} sections {','.join(result.session.sections_present)} frozen={result.session.frozen if result.session.frozen is not None else 'first-record'}")
    main_sha = result.merge_438.main_sha if result.merge_438 else None
    for number in sorted(result.verdicts):
        verdict = result.verdicts[number]
        if verdict is None:
            lines.append(f"#{number}: HOLD — SESSION EVIDENCE INCOMPLETE")
            continue
        ev = verdict.evidence
        label = "READY" if verdict.verdict == READY else verdict.verdict
        suffix = " — HUMAN MERGE APPROVAL REQUIRED" if verdict.verdict == READY else ""
        lines.append(f"#{number}: {label}{suffix}  head {ev.head_sha or 'unknown'}  main {ev.base_sha or main_sha or 'unknown'}")
        decisive = verdict.blockers or verdict.holds
        for reason in decisive[:6]:
            lines.append(f"    - {reason}")
        for note in result.verdict_notes.get(number, ()):
            lines.append(f"    · {note}")
    lines.append(f"NEXT ELIGIBLE STEP: {result.next_eligible_step}")
    lines.append("HUMAN ACTION REQUIRED: " + ("; ".join(result.human_actions) if result.human_actions else "none"))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ops.pr_promotion_readiness.post_session", description=__doc__)
    parser.add_argument("--session-file", required=True)
    parser.add_argument("--pr", action="append", required=True, help="NUMBER[:scope[:expect_head[:test_evidence.json]]] (repeatable)")
    parser.add_argument("--record", default=str(DEFAULT_RECORD_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    specs = [PRSpec.parse(item) for item in args.pr]
    result = run_post_session_workflow(session_file=Path(args.session_file), pr_specs=specs, record_path=Path(args.record))
    if args.json:
        print(json.dumps(read_records(Path(args.record))[-1], indent=2, sort_keys=True))
    else:
        print(render_status_block(result))
    if not result.session.complete:
        return 2
    codes = [v.exit_code for v in result.verdicts.values() if v is not None]
    return max(codes) if codes else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
