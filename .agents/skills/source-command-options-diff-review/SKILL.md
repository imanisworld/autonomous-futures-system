---
name: "source-command-options-diff-review"
description: "Migrated source command `options-diff-review`"
---

# source-command-options-diff-review

Use this skill when the user asks to run the migrated source command `options-diff-review`.

## Command Template

# /options-diff-review

Purpose:
Run a read-only options-system diff review before commit, PR, or merge. Scoped to the options subsystem (options_companion/, options_manager/, alert_ranker/, sources/gex_*, sources/signa_*, and any options-specific risk/journal code) — not the futures execution path. See `/futures-diff-review` for that.

Core rule: No proof, no merge. Advisory-only unless explicitly approved otherwise — any diff that adds a broker/order/execution path where none existed is a hard stop, not a judgment call.

Required files/checks:
- Full list of changed files
- Whether advisory-only status is preserved: confirm no code path in the diff submits, modifies, or cancels a real broker order for options
- Whether any broker/order/execution path was added (new imports of a broker client, new HTTP calls to a trading endpoint, new "place order"/"submit"/"execute" function, anything that could route a decision to a live account)
- Whether scanner scoring logic changed (GEX/Signa/flow scoring, contract ranking, confidence/grade computation)
- Whether risk gate logic changed (max premium, DTE, liquidity, spread width, position sizing, account risk, stop/invalidation logic)
- Whether contract quality filters changed (liquidity/OI/volume/spread thresholds, expiration filters)
- Whether alerting behavior changed (what triggers an alert, what gets suppressed, notification routing)
- Whether journal/log behavior changed (what gets recorded, whether rejection reasons remain human-readable)
- Whether provider/data assumptions changed (data source, symbol mapping, staleness handling, chain provider swapped or reconfigured)
- Whether tests were added or updated for the changed behavior
- Whether tests actually pass (real test run, not assumed)
- Whether the change affects options only, futures only, or shared code — a change touching shared code (e.g. journal_logger.py, broker_interface.py) requires the same scrutiny as `/futures-diff-review` would apply, not a lighter options-only pass

Forbidden actions:
- Do not modify files.
- Do not commit.
- Do not push.
- Do not change config.
- Do not add execution.
- Do not add broker submission.
- Do not create live-order functionality.
- Do not approve a diff that adds any execution/order-submission path — that is an automatic REJECT regardless of code quality, unless the user has explicitly and separately authorized moving the options system off advisory-only.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Why:
2-5 decisive reasons

What I Verified:
- files reviewed
- logic checked
- safety gates checked
- advisory/execution path checked
- tests checked

Problems Found:
Separate blockers from minor cleanup.

Required Fixes:
- must-fix before merge
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- Any new broker/order/execution path is an automatic REJECT.
- Any file changed without its diff actually read blocks APPROVE (HOLD).
- A scoring/risk-gate/contract-filter change without a stated reason blocks APPROVE (HOLD) — same discipline as a futures risk-threshold change.
- Missing, unrun, or failing tests for changed behavior blocks APPROVE.
- A change touching shared (non-options-specific) code is never AUDIT ONLY — it must be evaluated with the same weight as a futures-side change.

Safe next step:
State the smallest safe action — usually "run the test suite and re-review," "safe to commit locally," or "needs a human decision on the flagged scoring/risk change." Never "push," "merge," or "enable execution" from this command.
