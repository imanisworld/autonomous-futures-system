"""Gated PR promotion-readiness check -- validation automation only.

This package answers one question about a pull request, from live GitHub
evidence, without changing anything: is every required proof item present
and passing right now? It produces exactly one of:

    READY FOR PROMOTION   every required proof item present and passing
    HOLD                  something is missing, unknown, stale, or unreviewed
    REJECT                a hard block: failed CI/tests, merge conflict, or a
                          change inside a forbidden area

It never merges, deploys, pushes, restarts, trades, edits risk policy,
bypasses a failed test, or infers missing evidence. "No proof, no
promotion." Human approval remains required for the actual merge; the
validation layer here is deliberately separate from any future
human-approved promotion action (see ``actions.py``).

Layers (kept separate so an approval action can be added later without
rewriting validation):

    evidence.py   read-only collection via the ``gh`` CLI (allowlisted shapes)
    policy.py     pure, deterministic verdict from evidence + scope policy
    record.py     append-only JSONL promotion record
    cli.py        ``python -m ops.pr_promotion_readiness --pr N``

This is not a replacement for ``ops/project_check/promotion.py`` (the
strategy-evidence proof gate) and not a second CI: it reads the existing
GitHub checks and the existing CI job log.
"""

from .evidence import collect_pr_evidence, parse_pytest_summary
from .models import HOLD, READY, REJECT, PromotionEvidence, PromotionVerdict, TestEvidence
from .policy import SCOPE_POLICIES, ScopePolicy, classify_scope, evaluate_promotion_readiness
from .record import append_promotion_record, build_promotion_record

__all__ = [
    "HOLD",
    "READY",
    "REJECT",
    "SCOPE_POLICIES",
    "PromotionEvidence",
    "PromotionVerdict",
    "ScopePolicy",
    "TestEvidence",
    "append_promotion_record",
    "build_promotion_record",
    "classify_scope",
    "collect_pr_evidence",
    "evaluate_promotion_readiness",
    "parse_pytest_summary",
]
