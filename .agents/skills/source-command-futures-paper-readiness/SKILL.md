---
name: "source-command-futures-paper-readiness"
description: "Migrated source command `futures-paper-readiness`"
---

# source-command-futures-paper-readiness

Use this skill when the user asks to run the migrated source command `futures-paper-readiness`.

## Command Template

# /futures-paper-readiness

Purpose:
Determine whether the current repo state is safe to run in paper/demo observation mode. This is a go/no-go check for starting or continuing a paper run — it does not certify anything about live trading, which is explicitly out of scope for this command.

Core rule: No proof, no run. Every item must be confirmed against the current code/config state, not assumed from a prior session's audit.

Required files/checks:
- Live trading disabled: risk_rules.yaml `trading_mode.live_trading_enabled: false`, `LIVE_TRADING_ENABLED` env unset or false
- Paper/demo mode visible: `trading_mode.paper_mode` value, which broker `_make_broker` would actually select right now
- No live execution route: confirm no code path in the current diff/state allows `TradovateBroker.execute_bracket` to run without every existing live guard
- Risk config loaded: `load_config()` succeeds against the current risk_rules.yaml without raising
- Daily loss limit present: `daily_limits.max_daily_loss` is set to a real (non-null, non-zero-unless-intentional) value
- Stop/target required: `order_rules.require_stop`, `require_target`, bracket-completeness check active
- Journal path available: journal/journal_logger.py writes to a real, writable log directory
- Error handling: a bad/malformed webhook payload is rejected (HTTP 400/422), not silently dropped or crashing the process
- Alert-age log-only behavior: confirm `execution_safety.log_alert_age_only` and `max_alert_age_seconds` are in the state you intend for this paper run — report the actual values, don't assume log-only
- Working-order recheck does not break paper: confirm the recheck is skipped for PaperBroker (no attempted Tradovate order-list call in the paper path) — a paper run must never fail due to a broker read it doesn't need
- Tests passing: full test suite run, real pass/fail counts
- Status endpoints if available: `/status/today`, `/status/broker-account` or equivalent respond and reflect the current mode accurately

Forbidden actions:
- Do not place trades.
- Do not arm live trading.
- Do not modify config or code.
- Do not commit or push.
- Do not treat "it worked last time" as proof — re-verify against the current state.

Required output format:

VERDICT: READY FOR PAPER / READY FOR DEMO / HOLD / REJECT
LIVE TRADING DISABLED:
MODE:
LIVE ROUTE:
RISK CONFIG:
DAILY LOSS LIMIT:
STOP/TARGET REQUIRED:
JOURNAL PATH:
ERROR HANDLING:
ALERT-AGE BEHAVIOR:
WORKING-ORDER RECHECK IMPACT:
TESTS:
STATUS ENDPOINTS:
BLOCKERS:
SAFE NEXT STEP:

Safety gates:
- Live trading not confirmed disabled is an automatic REJECT, full stop.
- Missing daily loss limit, missing stop/target requirement, or missing journal path is a REJECT — these are the minimum bar for even paper trading to be worth trusting.
- Failing tests is a REJECT.
- If the working-order recheck would attempt a broker call in the paper path, that is a REJECT — it means the PaperBroker exclusion regressed.
- "READY FOR DEMO" requires everything "READY FOR PAPER" requires, plus confirmation that Tradovate demo auth/account routing actually works — do not upgrade PAPER readiness to DEMO readiness without that separate check.

Safe next step:
If READY, the next step is to start or continue observation and let `/futures-why-no-trade` and `/futures-execution-safety-audit` be the tools used during that run — this command does not itself start anything. If HOLD/REJECT, name the smallest fix needed to re-run this check.
