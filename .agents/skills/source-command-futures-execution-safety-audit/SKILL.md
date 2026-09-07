---
name: "source-command-futures-execution-safety-audit"
description: "Migrated source command `futures-execution-safety-audit`"
---

# source-command-futures-execution-safety-audit

Use this skill when the user asks to run the migrated source command `futures-execution-safety-audit`.

## Command Template

# /futures-execution-safety-audit

Purpose:
Focused audit of execution safety only — the gates that stand between a risk-approved decision and a real order reaching the broker. Narrower than `/futures-full-audit`; use this when you specifically need to re-verify the execution chokepoints (e.g. after touching risk/risk_engine.py, webhook/runner.py, or execution/tradovate_broker.py).

Core rule: No proof, no run. Every item below must be confirmed by reading the actual code path, not recalled from a prior audit in this conversation.

Required files/checks:
- Live lock: config/settings.py `LiveTradingBlockedError`, confirm it still raises on `live_trading_enabled=true` from either risk_rules.yaml or the `LIVE_TRADING_ENABLED` env var
- Broker/account routing: `_make_broker` in webhook/runner.py, `BROKER` env handling, TradovateBroker account ID resolution
- Daily loss limit: risk_rules.yaml `daily_limits.max_daily_loss`, `RiskEngine._check_daily_loss_limit`
- Max trades/day: risk_rules.yaml `daily_limits.max_trades_per_day`, `RiskEngine._check_daily_trade_limit` — report the configured value even if very high; do not silently treat a high value as equivalent to unlimited without saying so
- Per-trade stop: `RiskEngine._check_bracket_completeness`, `order_rules.require_stop`
- Bracket order integrity: `order_rules.order_type: bracket`, entry+stop+target all required before an order is built
- Open position check: `RiskEngine._check_no_open_position`
- Working-order check: webhook/runner.py's recheck immediately before `broker.execute_bracket(order)` — confirm placement is still immediately before that call, confirm it is gated on `not isinstance(broker, PaperBroker)` (or equivalent), and confirm `working_order_recheck_enabled` from risk_rules.yaml `execution_safety` is respected
- Stale alert check: `RiskEngine._check_alert_freshness`, `execution_safety.max_alert_age_seconds` / `log_alert_age_only`
- Future timestamp check: `_check_alert_freshness`'s future-timestamp branch, confirm it is NOT gated by `log_alert_age_only`
- Missing timestamp behavior: confirm `reject_on_missing_alert_timestamp` default and that it actually rejects when true
- Fail-closed broker reads: confirm the working-order recheck's exception path rejects rather than assumes clear (`order_state_unreadable`, not silent pass)
- Journaled rejection reasons: confirm every suppression path (`RISK_REJECTED`, `ORDER_SUPPRESSED`, `BLOCKED_DATA_QUALITY`) writes a reason a human can read later
- No accidental live route: confirm PaperBroker is actually selected under the current config (`paper_mode`), and that no code path reaches `TradovateBroker.execute_bracket` without every guard above having run

Forbidden actions:
- Do not place trades.
- Do not arm live trading.
- Do not modify risk thresholds.
- Do not modify code.
- Do not commit or push.
- Do not mark an item SAFE without citing the specific file/line/check that proves it — no citation means UNVERIFIED, not SAFE.

Required output format:

VERDICT: SAFE FOR PAPER / SAFE FOR DEMO / HOLD / REJECT / LIVE BLOCKED
LIVE LOCK:
BROKER/ACCOUNT ROUTING:
DAILY LOSS LIMIT:
MAX TRADES/DAY:
PER-TRADE STOP:
BRACKET INTEGRITY:
OPEN POSITION CHECK:
WORKING-ORDER CHECK:
STALE ALERT CHECK:
FUTURE TIMESTAMP CHECK:
MISSING TIMESTAMP BEHAVIOR:
FAIL-CLOSED BROKER READS:
JOURNALED REJECTION REASONS:
LIVE ROUTE CHECK:
BLOCKERS:
SAFE NEXT STEP:

Safety gates:
- Any item marked UNVERIFIED caps the verdict at HOLD.
- Live lock failure, or any path that could reach `execute_bracket` on a live broker without every guard above active, is LIVE BLOCKED / REJECT.
- A fail-open broker-read (treats a failed order-list read as "no working orders") is a REJECT — this is a correctness regression of the working-order recheck, not a style nit.
- `working_order_recheck_enabled=false` is reportable but not automatically a REJECT — note it as a reduced-safety configuration and let the verdict reflect that (HOLD, not silently SAFE).

Safe next step:
If this audit is clean, the next step is normal paper/demo observation, not a live-readiness decision — this command never certifies live-readiness on its own. If anything is HOLD or REJECT, name the exact file and check that needs a human decision.
