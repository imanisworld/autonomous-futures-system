"""Plain data shapes shared by the evidence, policy, and record layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

READY = "READY FOR PROMOTION"
HOLD = "HOLD"
REJECT = "REJECT"

# Non-zero exit codes so any wrapper that treats "not ready" as failure fails closed.
EXIT_CODES = {READY: 0, HOLD: 2, REJECT: 3}


@dataclass(frozen=True)
class CheckResult:
    """One GitHub status check / check run as reported on the PR head."""

    name: str
    conclusion: str  # upper-cased GitHub conclusion or state; "" when absent
    workflow: str = ""
    details_url: str = ""


@dataclass(frozen=True)
class TestEvidence:
    """One pytest result set. Counts are None when unknown -- never guessed."""

    kind: str  # "full" | "targeted"
    source: str
    sha: Optional[str]
    passed: Optional[int]
    failed: Optional[int]
    skipped: Optional[int]
    errors: Optional[int]
    command: str = ""


@dataclass(frozen=True)
class ReviewThread:
    path: str
    author: str
    excerpt: str
    resolved: bool


@dataclass(frozen=True)
class ScopeFinding:
    path: str
    category: str  # "forbidden" | "out_of_scope" | "allowed"
    rule: str


@dataclass(frozen=True)
class PromotionEvidence:
    """Everything the verdict is computed from. Unknown = None / "" / ()."""

    pr_number: int
    collected_at: str
    repo: str = ""
    url: str = ""
    title: str = ""
    author: str = ""
    state: str = ""  # OPEN / CLOSED / MERGED / ""
    is_draft: Optional[bool] = None
    labels: tuple[str, ...] = ()
    branch: str = ""
    head_sha: Optional[str] = None
    base_ref: str = ""
    base_sha: Optional[str] = None  # current remote tip of the base branch
    merge_base_sha: Optional[str] = None
    behind_base_by: Optional[int] = None
    ahead_of_base_by: Optional[int] = None
    mergeable: str = ""  # MERGEABLE / CONFLICTING / UNKNOWN / ""
    merge_state: str = ""  # CLEAN / BLOCKED / BEHIND / UNSTABLE / DIRTY / DRAFT / UNKNOWN
    review_decision: str = ""  # APPROVED / CHANGES_REQUESTED / REVIEW_REQUIRED / ""
    review_threads: tuple[ReviewThread, ...] = ()
    changed_files: tuple[str, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    tests: tuple[TestEvidence, ...] = ()
    patches: tuple[tuple[str, str], ...] = ()  # (path, unified patch) per changed file
    collection_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromotionVerdict:
    verdict: str  # READY / HOLD / REJECT
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]  # REJECT-level
    holds: tuple[str, ...]  # HOLD-level
    scope_findings: tuple[ScopeFinding, ...]
    scope_policy: str
    evidence: PromotionEvidence
    regression_findings: tuple = ()

    @property
    def label(self) -> str:
        if self.verdict == REJECT and self.regression_findings:
            return "REJECT — POLICY REGRESSION"
        return self.verdict

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]
