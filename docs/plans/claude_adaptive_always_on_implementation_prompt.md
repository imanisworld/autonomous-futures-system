# Claude Implementation Prompt: Adaptive Opportunity Tracking and Always-On Shadow Schedule

You are working in:

`/Users/djb.a.e/MAINVSCODE/autonomous-futures-system`

Date: June 5, 2026

## Objective

Implement an evidence-first, read-only shadow layer that measures missed regular-system
opportunities across every futures session. Prepare an always-on paper schedule behind an
explicit feature flag, but do not enable it by default and do not change live execution.

Do not implement or activate the experimental range-fade/chop strategy. Current evidence
does not support giving it a separate trade allowance.

Before editing, inspect the repository, existing uncommitted changes, tests, and the research
document:

`docs/experiments/range_fade_backtest.md`

Work with existing changes. Do not revert or overwrite them.

## Current Conclusions

### Regular MNQ System

The expanded regular-system replay remained positive across chronological splits:

| Split | Resolved Trades | Win Rate | Expectancy | Profit Factor |
|---|---:|---:|---:|---:|
| Development | 253 | 75.5% | $105.42 | 6.49 |
| Validation | 74 | 70.3% | $143.95 | 4.95 |
| Holdout | 86 | 82.6% | $202.68 | 10.02 |

These totals are simulated, include position sizing, exclude unresolved trades, and do not
represent one-contract cash profit.

### Always-On Schedule Experiment

Using all available MNQ 15-minute bars:

| Schedule | Resolved | Win Rate | P&L | Expectancy | Profit Factor | Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| Current schedule | 621 | 80.0% | $92,275.10 | $148.59 | 7.03 | $618.00 |
| Always on | 776 | 78.0% | $117,211.10 | $151.05 | 6.40 | $528.00 |

At a stress cost of $5 round-trip per contract, always-on remained ahead by approximately
$20,331.

Always-on session results:

| Session | Resolved | Win Rate | Expectancy | Profit Factor |
|---|---:|---:|---:|---:|
| Asian | 199 | 83.4% | $145.35 | 7.20 |
| London | 126 | 77.0% | $136.30 | 5.00 |
| New York | 415 | 77.3% | $160.99 | 7.10 |
| Session gap | 31 | 58.1% | $122.75 | 3.29 |
| Off-hours | 5 | 60.0% | $99.56 | 4.89 |

Limitations:

- TradingView delivered approximately eleven months, not two years.
- Overnight ORB signals may reference the prior New York ORB.
- Off-hours sample size is insufficient.
- MES exports lack equivalent historical VWAP, EMA, and NY ORB context.
- Always-on has not been observed through live TradingView alert delivery.

### Range/Chop Strategy

Do not integrate it into the decision or execution paths.

- MNQ New York range: 36 trades, $3.03 expectancy, PF 1.108 before commissions.
- MNQ Asian range: interesting but almost no validation/holdout opportunities.
- MES and London variants were generally negative or insufficient.
- Do not create a separate range trade budget.

## Required Safety Invariants

These must remain shared across all sessions and any future strategy lane:

- Live trading remains disabled.
- Paper execution remains the default.
- One open position maximum.
- Shared maximum daily loss.
- Shared maximum drawdown.
- Shared consecutive-loss and circuit-breaker behavior.
- Shared total daily trade capacity.
- News restrictions override schedule expansion.
- No automatic config edits from adaptive analysis.
- No adaptive component may place trades or relax rules.
- No changes to the public port-80 deployment.
- No direct activation on the live VPS.

## Existing Architecture To Respect

Inbound and market state:

- `tradingview/risksentinel_context.pine`
- `webhook/payload.py`
- `webhook/state_builder.py`
- `webhook/runner.py`
- `webhook/app.py`

Decision and gates:

- `strategy/signal_engine.py`
- `strategy/regime_classifier.py`
- `strategy/confluence_scorer.py`
- `strategy/gex_gate.py`
- `strategy/signa_gate.py`

Risk and configuration:

- `risk/risk_engine.py`
- `risk_rules.yaml`
- `config/settings.py`

Replay and evidence:

- `replay/replay_engine.py`
- `replay/candle_loader.py`
- `replay/replay_report.py`
- `scripts/csv_to_replay.py`
- `scripts/run_replay_batch.py`

Adaptive committee:

- `adaptive/committee.py`
- `adaptive/journal_reader.py`
- `adaptive/payload_auditor.py`
- `adaptive/risk_steward.py`
- `adaptive/strategy_analyst.py`
- `adaptive/ops_monitor.py`

Journal and dashboard:

- `journal/journal_logger.py`
- `webhook/app.py`

## Phase 1: Audit Before Implementation

Document findings before changing behavior:

1. Enumerate every session restriction in both `DecisionEngine` and `RiskEngine`.
2. Identify TradingView alert conditions that may prevent bars from reaching the backend
   outside current schedules.
3. Verify how NY ORB values behave overnight and whether overnight strategies should use
   prior NY ORB, London ORB, session-specific structure, or no ORB.
4. Verify session classification boundaries, CME maintenance break handling, and calendar-day
   versus CME-session-day resets.
5. Verify replay has no higher-timeframe lookahead.
6. Identify all unresolved-trade handling and cost-model limitations.
7. Verify whether current schedule and always-on comparisons use identical non-session rules.

Do not proceed if the audit discovers lookahead, incomplete alert delivery, or a material
comparison mismatch. Fix evidence correctness first and add regression tests.

## Phase 2: Shadow Opportunity Tracker

Implement a read-only counterfactual tracker. Its purpose is to answer:

> If a valid regular-system setup was blocked only by a schedule/session gate, what would
> have happened afterward?

Create structured contracts similar to:

### OpportunityCandidate

- `candidate_id`
- `source_bar_id`
- `detected_at`
- `instrument`
- `session`
- `timeframe`
- `strategy`
- `direction`
- `entry`
- `stop`
- `target`
- `failed_gates`
- `risk_failed_rule`
- `market_condition`
- trend, VWAP, volume, ORB, HTF, confluence, and regime snapshots
- `status`
- `expires_at`

### OpportunityOutcome

- `candidate_id`
- `resolved_at`
- `result`
- `exit_reason`
- hypothetical P&L ticks and dollars
- contracts used for normalization
- MFE and MAE
- bars to resolution
- estimated commissions/slippage

Rules:

- Track only candidates with a valid direction and bracket.
- Distinguish `SETUP_BLOCKED`, `RISK_REJECTED`, and `NO_SETUP`.
- Never claim one gate caused a missed trade when multiple independent gates failed.
- Separate schedule-only blocks from strategy-quality and risk blocks.
- Use causal future bars and pessimistic same-bar resolution.
- Never submit an order.
- Never mutate config.
- Persist enough information for reproducible analysis.

Prefer established repository storage patterns. If choosing JSONL versus SQLite, document the
decision and migration/retention implications.

## Phase 3: Always-On Paper Feature Flag

Add an explicit schedule mode with conservative defaults:

- `current`: preserve existing behavior exactly.
- `always_on_shadow`: evaluate all supported sessions but never submit orders.
- `always_on_paper`: permit paper orders only.

Requirements:

- Default remains `current`.
- Live execution must reject `always_on_paper`.
- Do not silently remove existing session definitions.
- Keep news, loss, drawdown, open-position, and total-capacity gates shared.
- Track per-session counts for observability, not separate risk budgets.
- Do not add additional daily trade capacity.
- Treat `off_hours` as shadow-only until it has sufficient evidence.
- Treat `session_gap` as shadow-only initially because its sample is smaller and win rate lower.
- Initial paper eligibility should be limited to Asian, London, and New York.

Before allowing Asian/London paper orders, define and test the structural reference used by
ORB-related strategies. Do not blindly use a stale prior NY ORB unless that behavior is
explicitly approved and separately reported.

## Phase 4: Adaptive Opportunity Analyst

Extend the adaptive committee with a read-only analyst that summarizes shadow outcomes by:

- failed gate;
- strategy;
- instrument;
- session;
- timeframe;
- market condition;
- direction;
- confluence grade;
- chronological development, validation, and holdout periods.

Recommendations must:

- include sample size;
- include cost-adjusted expectancy, PF, drawdown, and win rate;
- state whether multiple gates failed;
- use minimum sample thresholds;
- never automatically edit configuration;
- say `INSUFFICIENT_SAMPLE` when evidence is too small.

## Phase 5: Dashboard and API Observability

Add concise read-only visibility to existing status endpoints/dashboard:

- active schedule mode;
- shadow-only versus paper-enabled sessions;
- latest opportunity candidates and outcomes;
- unresolved opportunity count;
- session-level results;
- schedule-only block counts;
- feed freshness by session;
- explicit warning when alert delivery is missing outside New York.

Do not redesign the dashboard. Follow existing UI patterns.

## Data and Timeframe Requirements

- Live expected decision timeframe remains 15 minutes.
- Do not switch live to 5 minutes.
- Use 60-minute data only as completed higher-timeframe context.
- Never expose a 60-minute bar's final values before that bar closes.
- Preserve raw timestamps and normalize comparisons to Eastern time where required.
- Keep instrument/session results separate.
- Apply realistic commissions, adverse slippage, and pessimistic ambiguous fills.
- Report one-contract-normalized results separately from dynamic-sizing results.
- Include unresolved candidates in reporting instead of silently dropping them.

## Tests Required

Add focused tests for:

- schedule mode defaults and config validation;
- current-mode behavioral equivalence;
- live rejection of always-on paper mode;
- all session/window/cutoff combinations;
- CME maintenance break;
- calendar-day and CME-session-day boundaries;
- prior NY ORB behavior overnight;
- completed-only HTF context;
- schedule-only opportunity attribution;
- multi-gate attribution;
- candidate expiry;
- pessimistic target/stop resolution;
- unresolved opportunities;
- estimated costs;
- adaptive minimum-sample behavior;
- API/dashboard status fields;
- replay determinism.

Run the complete test suite and `git diff --check`.

## Rollout Gates

Do not activate live always-on behavior.

Recommended sequence:

1. Evidence-correct offline replay.
2. Always-on shadow evaluation.
3. At least 30 calendar days of complete all-session alert delivery.
4. At least 50 resolved schedule-only candidates per proposed paper-enabled session.
5. Positive cost-adjusted expectancy and PF above 1.3 in validation and holdout.
6. No unacceptable combined-system drawdown increase.
7. Paper-enable Asian and London only.
8. Keep session gap and off-hours shadow-only.
9. Reassess before any live proposal.

## Explicit Non-Goals

- Do not enable live trading.
- Do not deploy to port 80 or the VPS.
- Do not merge the range-fade strategy into the main decision engine.
- Do not add three range trades or any separate range allowance.
- Do not loosen quality, regime, confluence, or risk gates to create more trades.
- Do not let adaptive recommendations edit rules automatically.
- Do not remove existing configuration options.
- Do not perform unrelated refactors.

## Deliverables

1. Audit report with file and line references.
2. Implementation plan updated from audit findings.
3. Shadow opportunity tracker and tests.
4. Feature-flagged schedule modes and tests.
5. Adaptive opportunity analyst and tests.
6. Dashboard/API observability and tests.
7. Offline comparison report using identical assumptions.
8. Clear list of remaining blockers before paper activation.
9. Full-suite verification results.

At the end, report exactly:

- files changed;
- behavior added;
- behavior deliberately unchanged;
- replay and test results;
- limitations;
- whether paper activation criteria were met;
- confirmation that live execution and deployment were untouched.
