# /futures-diff-review

Purpose:
Review the current repo diff or latest local commit for safety before commit, deploy, or push.

Core rule: No proof, no run. Any changed file whose actual diff was not read in full blocks approval.

Required files/checks:
- `git status --short` output
- `git diff` (unstaged/staged) and/or `git show <commit>` (for an already-committed change) — read the full text of every hunk, not just the file list
- Complete list of files changed
- Live lock untouched: config/settings.py's `LiveTradingBlockedError` check, LIVE_TRADING_ENABLED handling, and any per-call live guards in execution/tradovate_broker.py
- Broker routing untouched: execution/broker_interface.py, execution/tradovate_broker.py, execution/paper_broker.py's `_make_broker`/routing logic in webhook/runner.py — unless the diff explicitly and intentionally changes routing
- strategy/ untouched
- Options-side files untouched: options_companion/, alert_ranker/, risk/options_risk_engine.py
- execution/paper_broker.py fill-simulation logic (slippage, pessimistic_both_hit, fill resolution) untouched unless explicitly expected
- risk_rules.yaml thresholds unchanged, or if changed, each old→new value called out explicitly with the reason
- Tests added or updated for any new/changed behavior, and actually run (not just present in the diff)
- Test results reviewed directly (real pytest output), not assumed from a commit message or prior summary
- Fail-closed behavior intact wherever the diff touches a broker read, missing-data path, or timestamp/staleness check — an exception or missing value must reject, never silently proceed

Forbidden actions:
- Do not edit any file.
- Do not stage or unstage files.
- Do not commit.
- Do not push.
- Do not amend.
- Do not run anything beyond a read-only test run (no live network calls, no order submission, no broker writes).
- Do not approve based on the commit message or a prior summary alone — the actual diff text must be read.
- Do not treat an unreviewed file as safe by default.

Required output format:

VERDICT: APPROVE COMMIT / APPROVE PAPER-DEPLOY / HOLD / REJECT
FILES CHANGED:
LIVE LOCK:
BROKER ROUTING:
STRATEGY:
OPTIONS:
PAPERBROKER FILL SIM:
RISK THRESHOLDS:
TESTS:
FAIL-CLOSED BEHAVIOR:
BLOCKERS:
SAFE NEXT STEP:

Safety gates:
- Any file changed without its diff actually read blocks APPROVE (HOLD).
- Any touch to the live lock or live-trading guard blocks APPROVE outright.
- Any touch to an options-side file blocks APPROVE outright.
- Any risk_rules.yaml threshold change without an explicit, stated reason blocks APPROVE (HOLD), even if the change looks safe.
- Missing, unrun, or failing tests for changed behavior blocks APPROVE.
- A diff that silently widens what a check accepts (loosens a rejection condition) is treated as a risk-relevant change requiring the same scrutiny as a threshold change.

Safe next step:
State the smallest safe action — usually "run the test suite and re-review" or "safe to commit locally" or "needs a human decision on the flagged threshold change." Never "push" or "deploy live" from this command.
