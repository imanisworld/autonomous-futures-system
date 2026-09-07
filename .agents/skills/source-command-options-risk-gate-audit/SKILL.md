---
name: "source-command-options-risk-gate-audit"
description: "Migrated source command `options-risk-gate-audit`"
---

# source-command-options-risk-gate-audit

Use this skill when the user asks to run the migrated source command `options-risk-gate-audit`.

## Command Template

# /options-risk-gate-audit

Purpose:
Run a focused, read-only audit of the options subsystem risk gate.

Do not modify files.
Do not commit.
Do not push.
Do not change config.
Do not add execution.
Do not add broker submission.
Do not create live-order functionality.
Do not assume the system is advisory-only without verifying it.

Core rule:
No proof, no trust.
No proof, no merge.
No alert without risk proof.
No executable order path unless explicitly approved.

Required checks:
- current branch and working tree state
- changed files, if any
- options risk gate architecture
- whether the system is advisory-only, preview-only, or execution-capable
- whether any broker/order submission path exists
- LIVE_OPTIONS_TRADING_ENABLED handling
- options_manager/live_lock.py behavior
- max premium / max debit enforcement
- max contracts / sizing enforcement
- account risk cap enforcement
- per-trade risk calculation
- DTE limits
- spread width / bid-ask liquidity filters
- volume and open interest filters
- contract quality filters
- Greeks/IV assumptions, if used
- earnings/news/event filters, if present
- ticker/watchlist restrictions
- score/grade thresholds
- whether risk gate can downgrade, reject, or suppress alerts
- whether risk rejection reasons are human-readable
- whether rejected contracts are journaled/logged clearly
- whether dry_run_review, human_confirm, and order_ticket respect risk gate output
- whether PreparedOrderTicket remains non-executable
- whether executable is always False unless explicitly approved elsewhere
- whether broker and broker_order_id remain None
- whether options risk changes can affect futures runtime
- tests covering risk gate decisions
- tests passing

Output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

System Classification:
ADVISORY ONLY / PREVIEW ONLY / EXECUTION CAPABLE / UNSAFE / INCONCLUSIVE

Risk Gate Classification:
VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT

Why:
2–5 decisive reasons.

What I Verified:
- files reviewed
- risk gate logic checked
- contract filters checked
- sizing checked
- premium/debit limits checked
- liquidity filters checked
- rejection reasons checked
- advisory/preview/execution boundary checked
- live lock checked
- futures isolation checked
- tests checked

Problems Found:
Separate:
- blockers
- warnings
- minor cleanup

Required Fixes:
- must-fix before merge
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Important:
If the risk gate only scores or advises, classify it as advisory risk gating.
If the risk gate can prepare non-executable tickets, classify it as preview-only risk gating.
If any path can submit, place, route, or execute an options order, classify as execution-capable and audit live-lock enforcement.
If risk rejection reasons are missing, vague, or not journaled, classify as HOLD.
If filters exist but are not tested, classify as PROMISING BUT UNPROVEN, not VALIDATED.
