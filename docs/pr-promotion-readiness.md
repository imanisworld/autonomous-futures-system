# PR promotion-readiness check (validation automation only)

`python -m ops.pr_promotion_readiness --pr 438 [--pr 439] [--scope options-advisory]`

Answers one question from live GitHub evidence, without changing anything:
is every required proof item present and passing right now? Output is one
of `READY FOR PROMOTION` (exit 0), `HOLD` (exit 2), `REJECT` (exit 3).
**Human approval remains required for the actual merge.** No proof, no
promotion.

## What it does, in order

1. Reads the PR through the existing `gh` CLI (allowlisted read-only shapes
   only): head SHA, base, draft, labels, mergeability, merge state, review
   decision, changed files, status checks.
2. Reads the current `main` tip and the merge-base from the GitHub compare
   API. Not based on current main = HOLD.
3. Reads unresolved review threads (GraphQL query, never a mutation).
4. For a green `tests` check, opens the existing CI job log and parses the
   pytest summary line; confirms the job ran on the PR head. That is the
   full-suite evidence. No second CI is created.
5. Optionally takes operator-run targeted results via `--test-evidence
   FILE` (JSON). Unknown counts stay unknown and HOLD.
6. Classifies every changed file: forbidden area = REJECT, outside the
   scope profile = HOLD.
7. Evaluates the pure policy (`policy.py`) and appends one JSON line to
   `data/promotion_readiness/promotion_records.jsonl`.

## Verdict rules

REJECT: merge conflict; any check failed/cancelled/timed out; any test
failure or error; any file in a forbidden area (`execution/`, broker
boundary/preview, `options_companion/`, `deploy/`, release/deploy scripts,
live-box guard, CI workflows, `config/`, `risk/`, `risk_rules.yaml`,
`webhook/`, `main.py`, `.env*`, credential-named paths).

HOLD: anything missing or unknown -- head SHA, main SHA, merge-base,
mergeability, checks, required `tests` check, full-suite counts, draft
status; PR not open; draft; `HOLD` / `DO NOT MERGE` / `WIP` in title or
labels; stale base; merge state not CLEAN; pending checks; test evidence
for a different SHA; `CHANGES_REQUESTED` / `REVIEW_REQUIRED`; unresolved
review comments; a `claude/*` branch without an APPROVED review
(self-authored work needs independent review); out-of-scope files;
`--expect-head` mismatch; any collection error.

READY FOR PROMOTION: none of the above.

## What it must never do

Merge, deploy, push, restart, trade, edit risk policy, bypass a failed
test, or infer missing evidence. `evidence.py` refuses every `gh` shape
that is not on its read-only allowlist; `tests/test_pr_promotion_readiness.py`
asserts the refusal and scans the package source for promotion verbs.

## Extension point

`actions.py` defines the `PromotionAction` protocol. A future
human-approved action consumes a READY verdict plus an explicit approval
it did not manufacture, as a separate object. The validation layer does
not change for that. Only `NoPromotionAction` exists today.

Scope profiles live in `policy.SCOPE_POLICIES`; `require_approved_review`
and `require_targeted_evidence` are off by default and can be switched on
per profile.
