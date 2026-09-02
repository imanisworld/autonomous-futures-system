"""``python -m ops.pr_promotion_readiness --pr 438 [--pr 439] [--scope options-advisory]``

Exit code: 0 READY FOR PROMOTION, 2 HOLD, 3 REJECT (highest across PRs).
Writes one append-only record per PR. Performs no mutation of any kind.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .actions import NoPromotionAction
from .evidence import collect_pr_evidence, load_operator_test_evidence
from .models import PromotionVerdict
from .policy import SCOPE_POLICIES, evaluate_promotion_readiness
from .record import DEFAULT_RECORD_PATH, append_promotion_record, build_promotion_record


def render(verdict: PromotionVerdict) -> str:
    ev = verdict.evidence
    lines = [
        f"PR #{ev.pr_number}  {ev.title}",
        f"  branch      {ev.branch}",
        f"  head        {ev.head_sha or 'unknown'}",
        f"  base        {ev.base_ref or '?'} @ {ev.base_sha or 'unknown'}  merge-base {ev.merge_base_sha or 'unknown'}",
        f"  mergeable   {ev.mergeable or 'unknown'} / {ev.merge_state or 'unknown'}  draft={ev.is_draft}  review={ev.review_decision or 'none'}",
        "  checks      " + (", ".join(f"{c.name}={c.conclusion or 'pending'}" for c in ev.checks) or "none"),
        "  tests       " + (
            "; ".join(
                f"{t.kind}: {t.passed} passed / {t.failed} failed / {t.skipped} skipped / {t.errors} errors ({t.source})"
                for t in ev.tests
            )
            or "none"
        ),
        f"  scope       {verdict.scope_policy}: "
        + ", ".join(f"{f.path} [{f.category}]" for f in verdict.scope_findings),
    ]
    if ev.collection_errors:
        lines.append("  collection  " + "; ".join(ev.collection_errors))
    lines.append(f"  VERDICT     {verdict.verdict}")
    for reason in verdict.reasons:
        lines.append(f"    - {reason}")
    lines.append("  action      " + NoPromotionAction().perform(verdict))
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ops.pr_promotion_readiness", description=__doc__)
    parser.add_argument("--pr", type=int, action="append", required=True, help="PR number (repeatable)")
    parser.add_argument("--scope", default="options-advisory", choices=sorted(SCOPE_POLICIES))
    parser.add_argument("--expect-head", default=None, help="HOLD unless the live head SHA starts with this")
    parser.add_argument("--test-evidence", default=None, help="JSON file of operator-run test results (see evidence.load_operator_test_evidence)")
    parser.add_argument("--record", default=str(DEFAULT_RECORD_PATH), help="append-only JSONL record path")
    parser.add_argument("--json", action="store_true", help="print the record JSON instead of text")
    args = parser.parse_args(argv)

    policy = SCOPE_POLICIES[args.scope]
    operator_tests = load_operator_test_evidence(args.test_evidence) if args.test_evidence else ()
    worst = 0
    for pr_number in args.pr:
        evidence = collect_pr_evidence(pr_number, operator_tests=operator_tests)
        verdict = evaluate_promotion_readiness(evidence, policy, expected_head_sha=args.expect_head)
        record = build_promotion_record(verdict)
        append_promotion_record(Path(args.record), record)
        if args.json:
            print(json.dumps(record, indent=2, sort_keys=True))
        else:
            print(render(verdict))
            print()
        worst = max(worst, verdict.exit_code)
    return worst


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
