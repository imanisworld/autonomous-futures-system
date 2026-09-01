---
name: "source-command-options-why-no-alert"
description: "Migrated source command `options-why-no-alert`"
---

# source-command-options-why-no-alert

Use this skill when the user asks to run the migrated source command `options-why-no-alert`.

## Command Template

# /options-why-no-alert

Purpose:
Run a read-only options why-no-alert audit when the options subsystem produces no alert, no dry-run review, no human-confirmed preview, or no prepared order ticket.

Do not modify files.
Do not commit.
Do not push.
Do not change config.
Do not add execution.
Do not add broker submission.
Do not create live-order functionality.
Do not assume the system is advisory-only without verifying it.

Core rule:
No proof, no alert.
No proof, no preview.
No proof, no ticket.
No executable options order path unless explicitly approved.

Required checks:
- current branch and working tree state
- changed files, if any
- options subsystem architecture
- whether the system is advisory-only, preview-only, or execution-capable
- whether any broker/order submission path exists
- LIVE_OPTIONS_TRADING_ENABLED handling
- options_manager/live_lock.py behavior
- options_manager/broker_boundary.py behavior, if present
- scanner input availability
- provider/data availability
- stale or missing provider data behavior
- ticker/watchlist eligibility
- market/session eligibility
- earnings/news/event filters, if present
- setup detection result
- score/grade threshold result
- GEX/flow/regime inputs, if used
- contract chain availability
- contract selection result
- DTE filter result
- max premium / max debit filter result
- spread width / bid-ask liquidity result
- volume and open interest filter result
- contract quality filter result
- risk gate result
- risk downgrade/reject/suppress reason
- whether rejected contracts are logged/journaled clearly
- whether dry_run_review was skipped, blocked, or produced no candidate
- whether human_confirm was unavailable, skipped, or blocked
- whether order_ticket was unavailable, skipped, blocked, or produced a non-executable ticket
- whether any prepared ticket remains non-executable
- whether executable remains False
- whether broker remains None
- whether broker_order_id remains None
- whether submitted remains False, if broker boundary schema exists
- whether futures execution paths are untouched
- whether options no-alert behavior can affect futures runtime
- relevant tests exist
- relevant tests pass

Output format:

Verdict:
VALID NO-ALERT / BLOCKED BY DATA / BLOCKED BY FILTERS / BLOCKED BY RISK / BLOCKED BY PREVIEW GATE / BLOCKED BY EXECUTION SAFETY / INCONCLUSIVE

System Classification:
ADVISORY ONLY / PREVIEW ONLY / EXECUTION CAPABLE / UNSAFE / INCONCLUSIVE

Why:
2–5 decisive reasons.

What I Verified:
- files reviewed
- scanner inputs checked
- provider data checked
- setup logic checked
- scoring checked
- contract filters checked
- risk gate checked
- rejection reasons checked
- dry-run review checked
- human-confirm preview checked
- order-ticket boundary checked
- broker-boundary behavior checked, if present
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
- must-fix before relying on alerts
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Important:
If no alert happened because filters or risk gates rejected the candidate with clear reasons, classify as VALID NO-ALERT or BLOCKED BY RISK/FILTERS.
If no alert happened because provider data was missing or stale without a clear reason, classify as BLOCKED BY DATA or INCONCLUSIVE.
If no alert happened because preview/order-ticket safety blocked escalation, classify as BLOCKED BY PREVIEW GATE or BLOCKED BY EXECUTION SAFETY.
If any path can submit, place, route, or execute an options order, classify as EXECUTION CAPABLE and stop the no-alert audit until live-lock enforcement is separately audited.
If evidence is missing at any stage, say exactly what evidence is missing instead of guessing.
