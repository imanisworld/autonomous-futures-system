---
name: "source-command-futures-full-audit"
description: "Migrated source command `futures-full-audit`"
---

# source-command-futures-full-audit

Use this skill when the user asks to run the migrated source command `futures-full-audit`.

## Command Template

# /futures-full-audit

Purpose:
Full current-state audit of the futures system — a comprehensive, point-in-time snapshot of config, code, execution paths, and test health. Broader than `/futures-execution-safety-audit`; use this for a general system health check, not a focused pre-commit review (see `/futures-diff-review` for that).

Core rule: No proof, no run. Any area not directly inspected in this run must be reported as unverified, not assumed clean from memory or a prior audit.

Required files/checks:
- Current mode: `trading_mode.paper_mode` / `trading_mode.live_trading_enabled` from risk_rules.yaml and the runtime env
- Broker path: `BROKER` env var, `_make_broker` routing in webhook/runner.py
- Paper/demo/live routing: confirm which broker class actually gets instantiated for the current config
- Live locks: config/settings.py `LiveTradingBlockedError`, per-call guards in execution/tradovate_broker.py (execute_bracket, replace_stop, flatten_position)
- Risk engine: risk/risk_engine.py — full ordered check list in `RiskEngine.validate()`, confirm no check was silently removed or reordered in a way that weakens it
- Webhook path: webhook/app.py and webhook/runner.py — payload intake, dedupe, data-quality gate (`_check_payload_quality`), schedule-mode gate, working-order recheck, execute_bracket call site
- State builder: webhook/state_builder.py — payload → MarketState → TradeSetup field lineage, especially `entry_time`
- PaperBroker: execution/paper_broker.py — fill simulation, single-position guard
- TradovateBroker: execution/tradovate_broker.py — auth, contract/rollover resolution, order submission guards
- Live preflight: execution/live_preflight.py — arm/disarm state machine, daily reset, drift guard
- Journal: journal/journal_logger.py — decision/outcome logging present and wired into the pipeline
- Data quality: risk_rules.yaml `data_quality` block, `_check_payload_quality`
- Alert freshness: risk_rules.yaml `execution_safety` block, risk/risk_engine.py `_check_alert_freshness`
- Working-order recheck: webhook/runner.py — gate placement immediately before `execute_bracket`, PaperBroker exclusion
- Why-no-trade visibility: confirm rejection/suppression reasons are journaled and human-readable (see `/futures-why-no-trade` for a single-incident deep dive)
- Tests: full suite run, pass/fail counts, any skips explained
- Uncommitted changes: `git status --short`

Forbidden actions:
- Do not place trades.
- Do not arm live trading.
- Do not modify config, risk thresholds, or code.
- Do not edit files.
- Do not commit or push.
- Do not skip a checklist area — if something can't be verified (e.g. no live broker connection available), report it as UNVERIFIED, not PASS.

Required output format:

VERDICT: APPROVE / HOLD / REJECT / PAPER ONLY / AUDIT_ONLY
MODE:
BROKER PATH:
ROUTING:
LIVE LOCKS:
RISK ENGINE:
WEBHOOK PATH:
STATE BUILDER:
PAPERBROKER:
TRADOVATEBROKER:
LIVE PREFLIGHT:
JOURNAL:
DATA QUALITY:
ALERT FRESHNESS:
WORKING-ORDER RECHECK:
WHY-NO-TRADE VISIBILITY:
TESTS:
UNCOMMITTED CHANGES:
BLOCKERS:
SAFE NEXT STEP:

Safety gates:
- Live trading enabled by default, or reachable without explicit multi-layer authorization, is a hard REJECT.
- Any checklist area reported UNVERIFIED caps the verdict at HOLD or AUDIT_ONLY — never APPROVE.
- Uncommitted changes touching the live lock, broker routing, or risk thresholds cap the verdict at HOLD.
- Failing tests cap the verdict at REJECT.
- A clean paper-only system with all gates intact and all checks verified may be marked PAPER ONLY, not APPROVE — APPROVE is reserved for a state where you have also confirmed live-path readiness is explicitly out of scope for that verdict tier (this command never asserts live-readiness).

Safe next step:
Point to the next narrower command — `/futures-execution-safety-audit` for a focused re-check, `/futures-diff-review` if uncommitted changes exist, or `/futures-why-no-trade` if a specific missed-trade question triggered this audit.
