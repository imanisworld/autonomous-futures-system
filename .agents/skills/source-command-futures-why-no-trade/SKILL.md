---
name: "source-command-futures-why-no-trade"
description: "Migrated source command `futures-why-no-trade`"
---

# source-command-futures-why-no-trade

Use this skill when the user asks to run the migrated source command `futures-why-no-trade`.

## Command Template

# /futures-why-no-trade

Purpose:
Diagnose why the system did not trade, or missed an expected trade, for a specific bar, alert, or time window. This is a single-incident diagnostic, not a general system audit (see `/futures-full-audit` for that) and not a hindsight-driven rule-change tool.

Core rule: No proof, no run. A classification without a cited log line, journal entry, or code path is not a diagnosis — it's a guess, and must be reported as INCONCLUSIVE instead.

Required inputs:
- Symbol
- Approximate time / bar / session in question
- Expected setup or reason a trade was expected, if known
- Available evidence: journal entries, webhook/request logs, status output, or a chart screenshot for the window in question

Required files/checks (walk in this order; stop at the first stage that explains the outcome):
- Webhook received? — was an alert logged for this instrument/time at all
- Payload valid? — did it pass Pydantic validation and the OHLC/price-range checks in webhook/payload.py
- Bar deduped? — did webhook/dedupe.py treat this as a duplicate of an already-processed alert
- Timeframe matched? — did the payload's timeframe match `expected_timeframe_minutes`, or was it CONFIG_BLOCKED / TIMEFRAME_MISMATCH
- Data quality passed? — `_check_payload_quality`: contradictory OHLC, or stale bar past `max_staleness_seconds`
- Session allowed? — was this session in `allowed_sessions` / within `session_hours_et` / past `session_cutoffs`
- Decision engine produced a candidate? — did `DecisionEngine.evaluate` return TRADE with a setup, or NO_TRADE with a reason
- Risk engine rejected? — if TRADE, which specific `RiskEngine.validate()` check fired (`failed_rule`), including the alert-freshness gate (`alert_timestamp_missing` / `alert_timestamp_future` / `stale_alert`)
- Schedule gate suppressed? — `adaptive.execution_gate.order_placement_allowed` / `SHADOW_NO_ORDER`
- Working-order recheck suppressed? — `ORDER_SUPPRESSED` with `gate_reason` of `working_order_conflict` or `order_state_unreadable`
- Broker call reached? — did `broker.execute_bracket` actually get invoked
- Order placed? — fill result and order IDs if applicable
- Journal entry present? — is there a record for this decision at all, and does it match what actually happened
- Discord/status output clear? — was a human-readable notification/status entry produced
- Chart state vs. system state mismatch? — does what the chart shows disagree with what the system's own recorded market state was at that timestamp (data problem, not a system bug, if so)

Classification (pick exactly one):
- SYSTEM BUG
- CORRECT SKIP
- DATA FAILURE
- WEBHOOK FAILURE
- RISK BLOCK
- SCHEDULE/EXECUTION-SAFETY BLOCK
- STRATEGY GAP
- USER EXPECTATION MISMATCH
- INCONCLUSIVE

Forbidden actions:
- Do not place trades.
- Do not modify strategy, risk, or execution-safety code as part of a diagnosis.
- Do not change rules or thresholds based on one incident.
- Do not call it a bug without a cited log/journal/code reference.
- Do not assume a fill would have happened absent evidence.
- Do not let hindsight ("the chart clearly shows X") override what the system actually saw at that timestamp — report the disconnect, don't resolve it in the chart's favor by default.

Required output format:

VERDICT: EXPLAINED / INCONCLUSIVE / SYSTEM GAP / BUG SUSPECTED
SYMBOL:
TIME WINDOW:
WEBHOOK RECEIVED:
PAYLOAD VALID:
DEDUPE STATUS:
TIMEFRAME MATCH:
DATA QUALITY:
SESSION:
DECISION ENGINE:
RISK ENGINE:
SCHEDULE GATE:
WORKING-ORDER RECHECK:
BROKER CALL:
ORDER PLACED:
JOURNAL ENTRY:
NOTIFICATION OUTPUT:
CHART VS SYSTEM MISMATCH:
ROOT CAUSE:
BLOCKERS:
SAFE NEXT STEP:

Safety gates:
- No log/journal entry found for the window at all caps the verdict at INCONCLUSIVE — absence of evidence blocks a confident classification.
- A classification of SYSTEM BUG or BUG SUSPECTED requires a specific cited code path or log line; without one, downgrade to INCONCLUSIVE.
- CORRECT SKIP requires citing the specific gate/check that fired, not just "risk probably blocked it."

Safe next step:
If SYSTEM BUG or BUG SUSPECTED, the next step is `/futures-diff-review`-style scrutiny of the relevant code, not an immediate fix — file it for a deliberate change with tests, per the same "no proof, no run" discipline used everywhere else in this command set.
