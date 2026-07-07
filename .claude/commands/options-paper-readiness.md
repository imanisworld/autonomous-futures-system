# /options-paper-readiness

Purpose:
Run a read-only options paper-readiness audit before relying on options scanner alerts, dry-run reviews, human-confirmed previews, or prepared order tickets.

Do not modify files.
Do not commit.
Do not push.
Do not change config.
Do not add execution.
Do not add broker submission.
Do not create live-order functionality.
Do not assume the system is advisory-only without verifying it.

Core rule:
No proof, no run.
No proof, no alert trust.
No executable options order path unless explicitly approved.
Paper/advisory/preview-only unless proven otherwise.

Required checks:
- current branch and working tree state
- changed files, if any
- options subsystem architecture
- whether the system is advisory-only, preview-only, or execution-capable
- whether any broker/order submission path exists
- LIVE_OPTIONS_TRADING_ENABLED handling
- options_manager/live_lock.py behavior
- config loads successfully
- scanner inputs are known and available
- provider/data assumptions are visible
- stale/missing provider data behavior
- watchlist/ticker scope is clear
- contract selection rules are clear
- contract quality filters are active
- max premium / max debit limits are active
- max contracts / sizing limits are active
- account risk cap is active
- DTE limits are active
- spread width / liquidity filters are active
- volume and open interest filters are active
- score/grade thresholds are active
- earnings/news/event filters, if present
- risk gate can reject or downgrade unsafe contracts
- risk rejection reasons are human-readable
- rejected contracts are logged/journaled clearly
- alert output is explainable
- alert output shows why a contract passed
- dry_run_review is read-only
- human_confirm is preview-only
- order_ticket produces non-executable tickets only
- PreparedOrderTicket.executable remains False
- PreparedOrderTicket.broker remains None
- PreparedOrderTicket.broker_order_id remains None
- futures execution paths are untouched
- options paper/preview behavior cannot affect futures runtime
- relevant tests exist
- relevant tests pass

Output format:

Verdict:
READY FOR PAPER / READY FOR PREVIEW / HOLD / REJECT / AUDIT ONLY

System Classification:
ADVISORY ONLY / PREVIEW ONLY / EXECUTION CAPABLE / UNSAFE / INCONCLUSIVE

Why:
2–5 decisive reasons.

What I Verified:
- files reviewed
- config checked
- scanner inputs checked
- provider assumptions checked
- risk gate checked
- contract filters checked
- alert behavior checked
- journal/log behavior checked
- dry-run review checked
- human-confirm preview checked
- order-ticket boundary checked
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
If the scanner can only produce advisory alerts, classify as READY FOR PAPER only if alerts are explainable and risk-gated.
If the system can produce non-executable prepared tickets, classify as READY FOR PREVIEW only if executable is always False, broker is None, broker_order_id is None, and no submit/place/order API exists.
If any path can submit, place, route, or execute an options order, classify as EXECUTION CAPABLE and do not approve paper/preview readiness without live-lock proof.
If provider data can be missing or stale without a clear rejection reason, classify as HOLD.
If risk rejections are not human-readable or not logged, classify as HOLD.
