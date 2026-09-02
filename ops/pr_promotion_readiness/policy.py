"""Pure verdict logic: evidence + scope policy -> READY / HOLD / REJECT.

No I/O, no network, no clock. Every rule is deterministic and fail-closed:
anything missing or unknown is a HOLD, anything proven bad is a REJECT.
READY requires every required item to be present *and* passing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Optional

from .models import HOLD, READY, REJECT, PromotionEvidence, PromotionVerdict, ScopeFinding
from .regression import describe, scan_patches

_FAILED_CONCLUSIONS = {
    "FAILURE",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "ERROR",
    "STARTUP_FAILURE",
    "STALE",
}
_PASSED_CONCLUSIONS = {"SUCCESS"}

# Areas an advisory options change must never touch. Any match is a REJECT,
# regardless of scope profile.
FORBIDDEN_AREA_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^execution/", "broker submission / live execution"),
    (r"^options_manager/broker_boundary\.py$", "broker boundary"),
    (r"^options_manager/mock_broker_preview\.py$", "broker preview"),
    (r"^options_companion/", "credentialed companion lane"),
    (r"^deploy/", "deployment"),
    (r"^scripts/(atomic_release|deploy_lock)\.sh$", "deployment"),
    (r"^ops/(release_integrity|release_manifest|live_box_guard)\.py$", "deployment / live-box guard"),
    (r"^\.github/workflows/", "CI definition"),
    (r"^config/", "runtime config"),
    (r"^risk_rules\.yaml$", "risk policy"),
    (r"^risk/", "risk engine"),
    (r"^webhook/", "live webhook runtime"),
    (r"^main\.py$", "runtime entrypoint"),
    (r"(^|/)\.env($|\.)", "credentials"),
    (r"(credential|secret|api_key|apikey)", "credentials"),
)


@dataclass(frozen=True)
class ScopePolicy:
    name: str
    description: str
    allowed_patterns: tuple[str, ...]  # a changed file must match one of these
    forbidden_patterns: tuple[tuple[str, str], ...] = FORBIDDEN_AREA_PATTERNS
    required_checks: tuple[str, ...] = ("tests",)
    require_full_suite_evidence: bool = True
    require_targeted_evidence: bool = False
    require_approved_review: bool = False
    # Branches matching these patterns need an APPROVED review before READY,
    # even when require_approved_review is off: work authored by the auditing
    # agent itself must be reviewed by someone else (five-tool model).
    independent_review_branch_patterns: tuple[str, ...] = (r"^claude/",)
    hold_markers: tuple[str, ...] = ("HOLD", "DO NOT MERGE", "WIP")


SCOPE_POLICIES: dict[str, ScopePolicy] = {
    "options-advisory": ScopePolicy(
        name="options-advisory",
        description="Advisory options_manager code, its tests, and its docs only.",
        allowed_patterns=(
            r"^options_manager/",
            r"^tests/test_options_",
            r"^docs/options-",
        ),
    ),
    "ops-tooling": ScopePolicy(
        name="ops-tooling",
        description="Read-only ops tooling, its tests, docs, and its own records.",
        allowed_patterns=(
            r"^ops/pr_promotion_readiness/",
            r"^tests/test_pr_promotion_readiness",
            r"^docs/",
            r"^data/promotion_readiness/",
        ),
    ),
}


def classify_scope(files: tuple[str, ...] | list[str], policy: ScopePolicy) -> tuple[ScopeFinding, ...]:
    findings: list[ScopeFinding] = []
    for path in files:
        forbidden = next(
            ((pattern, area) for pattern, area in policy.forbidden_patterns if re.search(pattern, path)),
            None,
        )
        if forbidden is not None:
            findings.append(ScopeFinding(path=path, category="forbidden", rule=forbidden[1]))
            continue
        allowed = next((p for p in policy.allowed_patterns if re.search(p, path)), None)
        if allowed is None:
            findings.append(ScopeFinding(path=path, category="out_of_scope", rule=policy.name))
        else:
            findings.append(ScopeFinding(path=path, category="allowed", rule=allowed))
    return tuple(findings)


def _has_hold_marker(text: str, markers: tuple[str, ...]) -> bool:
    upper = text.upper()
    return any(marker.upper() in upper for marker in markers)


def evaluate_promotion_readiness(
    evidence: PromotionEvidence,
    policy: ScopePolicy,
    *,
    expected_head_sha: Optional[str] = None,
) -> PromotionVerdict:
    blockers: list[str] = []
    holds: list[str] = []

    # --- evidence integrity -------------------------------------------------
    for err in evidence.collection_errors:
        holds.append(f"evidence collection error: {err}")
    if not evidence.head_sha:
        holds.append("head SHA unknown")
    if expected_head_sha and evidence.head_sha and not evidence.head_sha.startswith(expected_head_sha):
        holds.append(f"stale SHA: head {evidence.head_sha[:12]} != expected {expected_head_sha[:12]}")

    # --- PR state -----------------------------------------------------------
    if evidence.state and evidence.state.upper() != "OPEN":
        holds.append(f"PR is {evidence.state}, not OPEN")
    if evidence.is_draft is None:
        holds.append("draft status unknown")
    elif evidence.is_draft:
        holds.append("PR is a draft")
    if _has_hold_marker(evidence.title, policy.hold_markers):
        holds.append(f"title carries a hold marker: {evidence.title!r}")
    for label in evidence.labels:
        if _has_hold_marker(label, policy.hold_markers):
            holds.append(f"label carries a hold marker: {label!r}")

    # --- base ---------------------------------------------------------------
    if evidence.base_ref != "main":
        holds.append(f"base branch is {evidence.base_ref or 'unknown'!r}, expected 'main'")
    if not evidence.base_sha:
        holds.append("current main SHA unknown")
    elif not evidence.merge_base_sha:
        holds.append("merge-base with main unknown")
    elif evidence.merge_base_sha != evidence.base_sha:
        behind = f" (behind by {evidence.behind_base_by})" if evidence.behind_base_by is not None else ""
        holds.append(
            f"not based on current main: merge-base {evidence.merge_base_sha[:12]} != main {evidence.base_sha[:12]}{behind}"
        )

    # --- mergeability -------------------------------------------------------
    mergeable = (evidence.mergeable or "").upper()
    if mergeable == "CONFLICTING":
        blockers.append("merge conflict with main")
    elif mergeable != "MERGEABLE":
        holds.append(f"mergeability unknown ({evidence.mergeable or 'absent'})")
    merge_state = (evidence.merge_state or "").upper()
    if merge_state != "CLEAN":
        holds.append(f"merge state is {evidence.merge_state or 'unknown'}, not CLEAN")

    # --- CI checks ----------------------------------------------------------
    if not evidence.checks:
        holds.append("no CI checks reported on head")
    seen = {c.name for c in evidence.checks}
    for required in policy.required_checks:
        if required not in seen:
            holds.append(f"required check {required!r} missing")
    for check in evidence.checks:
        conclusion = (check.conclusion or "").upper()
        if conclusion in _PASSED_CONCLUSIONS:
            continue
        if conclusion in _FAILED_CONCLUSIONS:
            blockers.append(f"check {check.name!r} {conclusion}")
        else:
            holds.append(f"check {check.name!r} not green ({conclusion or 'pending'})")

    # --- tests --------------------------------------------------------------
    full = [t for t in evidence.tests if t.kind == "full"]
    targeted = [t for t in evidence.tests if t.kind == "targeted"]
    for t in evidence.tests:
        if t.sha and evidence.head_sha and not evidence.head_sha.startswith(t.sha) and not t.sha.startswith(evidence.head_sha):
            holds.append(f"{t.kind} test evidence is for {t.sha[:12]}, not head {evidence.head_sha[:12]}")
            continue
        if t.passed is None or t.failed is None or t.errors is None:
            holds.append(f"{t.kind} test evidence from {t.source} has unknown counts")
            continue
        if t.failed > 0 or t.errors > 0:
            blockers.append(f"{t.kind} tests from {t.source}: {t.failed} failed, {t.errors} errors")
        elif t.passed <= 0:
            holds.append(f"{t.kind} test evidence from {t.source} ran zero tests")
    if policy.require_full_suite_evidence and not full:
        holds.append("full-suite test evidence missing for head")
    if policy.require_targeted_evidence and not targeted:
        holds.append("targeted test evidence missing for head")

    # --- review -------------------------------------------------------------
    decision = (evidence.review_decision or "").upper()
    if decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        holds.append(f"review decision is {decision}")
    if policy.require_approved_review and decision != "APPROVED":
        holds.append("approved review required by policy but absent")
    if decision != "APPROVED" and any(re.search(p, evidence.branch or "") for p in policy.independent_review_branch_patterns):
        holds.append(f"independent (approved) review required for self-authored branch {evidence.branch!r}")
    unresolved = [t for t in evidence.review_threads if not t.resolved]
    for thread in unresolved:
        holds.append(f"unresolved review comment on {thread.path or '(pr)'} by {thread.author}: {thread.excerpt[:80]!r}")

    # --- scope --------------------------------------------------------------
    scope_findings = classify_scope(evidence.changed_files, policy)
    if not evidence.changed_files:
        holds.append("changed-file list empty or unavailable")
    for finding in scope_findings:
        if finding.category == "forbidden":
            blockers.append(f"forbidden area touched: {finding.path} ({finding.rule})")
        elif finding.category == "out_of_scope":
            holds.append(f"outside scope {policy.name!r}: {finding.path}")

    # --- policy regressions (patch content) ------------------------------------
    regression_findings = scan_patches(evidence.patches)
    if evidence.changed_files and not evidence.patches:
        holds.append("patch content unavailable; policy-regression scan not performed")
    for finding, text in zip(regression_findings, describe(regression_findings)):
        if finding.severity == "reject":
            blockers.append(text)
        else:
            holds.append(text)

    if blockers:
        verdict = REJECT
    elif holds:
        verdict = HOLD
    else:
        verdict = READY
    reasons = tuple(blockers) + tuple(holds)
    if verdict == READY:
        reasons = (
            f"head {evidence.head_sha[:12]} based on current main {evidence.base_sha[:12]}",
            "mergeable and CLEAN",
            f"all {len(evidence.checks)} CI checks SUCCESS",
            "full-suite tests passed with zero failures: "
            + ", ".join(f"{t.passed} passed/{t.skipped or 0} skipped ({t.source})" for t in full),
            f"all {len(evidence.changed_files)} changed files inside scope {policy.name!r}",
            "no unresolved review comments or hold markers",
            f"policy-regression scan clean over {len(evidence.patches)} patch(es)",
        )
    return PromotionVerdict(
        verdict=verdict,
        reasons=reasons,
        blockers=tuple(blockers),
        holds=tuple(holds),
        scope_findings=scope_findings,
        scope_policy=policy.name,
        evidence=evidence,
        regression_findings=regression_findings,
    )


def policy_fingerprint() -> str:
    """sha256 over every scope profile and forbidden-area pattern. Recorded
    with each run so a silent change to scope/forbidden policy is visible
    as a fingerprint change between records."""
    payload = {name: asdict(policy) for name, policy in sorted(SCOPE_POLICIES.items())}
    payload["_forbidden"] = list(FORBIDDEN_AREA_PATTERNS)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
