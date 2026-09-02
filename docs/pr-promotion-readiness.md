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

## Post-session workflow

`python -m ops.pr_promotion_readiness.post_session --session-file F --pr 438:options-advisory:c1456fb:data/promotion_readiness/targeted_tests_pr438.json --pr 439:options-advisory --pr 440:ops-tooling`

Runs only after the morning session file is complete (`## 09:26 ET`,
`## 09:46 ET`, `## 10:03 ET` sections, each with a retrieval timestamp),
the 10:03 ET stage time has passed, and the file's sha256 matches the last
recorded fingerprint (frozen). Otherwise every PR is
`HOLD — SESSION EVIDENCE INCOMPLETE` and GitHub is not consulted.

Then, per PR: fresh evidence through the same read-only path, the same
policy, one appended record. A previous READY is discarded whenever the
head or main SHA differs. For the PR that ships this automation it also
runs a capability audit (AST: no forbidden imports, no non-gh subprocess,
no shell=, no os.system; runtime: the gh allowlist rejects every mutating
shape; policy fingerprint unchanged since the last record). The next
implementation step (the read-only Robinhood contract adapter) becomes
eligible only when #438 is MERGED and fresh main contains its merge
commit -- never on READY alone. Output is one status block:
SESSION / #438 / #439 / #440 / NEXT ELIGIBLE STEP / HUMAN ACTION REQUIRED.

## Policy-regression scan

Every readiness run fetches each changed file's patch (read-only) and
scans added lines in source files for the Phase 1 charter regressions:
a second Strat classifier, Signa promoted to a gate, proxy/inferred GEX,
a hard position-count cap, a numeric aggregate-risk default, missing
risk or contract treated as pass, broker submission / auto entry-exit,
automatic averaging, and a `MIN_*` lowered or `MAX_*` raised. Any hit is
`REJECT — POLICY REGRESSION` with category, file, and line; it is never
auto-cleared. Test files are scanned only for removed fail-closed
assertions, which HOLD for human review. Missing patch content HOLDs
rather than assuming clean. The scanner's own file is excluded (it must
spell the patterns); the capability audit covers it by AST.
