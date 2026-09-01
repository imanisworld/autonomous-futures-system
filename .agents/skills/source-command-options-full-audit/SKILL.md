---
name: "source-command-options-full-audit"
description: "Migrated source command `options-full-audit`"
---

# source-command-options-full-audit

Use this skill when the user asks to run the migrated source command `options-full-audit`.

## Command Template

# /options-full-audit

Purpose:
Run a whole-system read-only audit of the options subsystem.

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
Advisory/preview-only unless explicitly proven otherwise.

Required checks:
- current branch and working tree state
- changed files, if any
- options subsystem architecture
- advisory-only vs preview-only vs executable status
- whether any broker/order submission path exists
- whether any live-options path exists
- LIVE_OPTIONS_TRADING_ENABLED handling
- options_manager/live_lock.py behavior
- dry_run_review behavior
- human_confirm behavior
- order_ticket behavior
- whether PreparedOrderTicket is permanently non-executable
- whether broker and broker_order_id remain None
- scanner inputs
- provider/data assumptions
- scoring logic
- risk gate logic
- contract quality filters
- alerting behavior
- journal/log behavior
- config loading
- tests present
- tests passing
- whether futures execution paths are untouched
- whether options changes can affect futures runtime

Output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

System Classification:
ADVISORY ONLY / PREVIEW ONLY / EXECUTION CAPABLE / UNSAFE / INCONCLUSIVE

Why:
2-5 decisive reasons.

What I Verified:
- files reviewed
- architecture checked
- advisory/preview/execution boundary checked
- live lock checked
- risk gates checked
- scoring checked
- contract filters checked
- journal/logging checked
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
If order prep modules exist, do not treat their existence alone as execution.
Verify whether any object can actually be submitted to a broker.
If executable is always False, broker is None, broker_order_id is None, and no submit/place/order API exists, classify as PREVIEW ONLY or ADVISORY/PREVIEW ONLY, not execution-capable.
If any broker submission path exists, classify as EXECUTION CAPABLE and audit live-lock enforcement.
