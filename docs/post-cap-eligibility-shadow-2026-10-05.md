# Post-cap eligibility shadow — 2026-10-05

**Status:** REPO-SIDE ONLY / OBSERVATION ONLY / NO DEPLOYMENT AUTHORITY

## Verdict

Keep the executable daily cap at **3 trades/day**.

The existing runner already continues the DecisionEngine after the cap is
reached and journals selected setups under `BLOCKED_MAX_TRADES`. The audit
found that this is not sufficient to call those rows "trade #4/#5/etc." because
the current block returns before:

- stop-width transformation;
- confluence scoring;
- journal-ledger sizing;
- tick rounding;
- RiskEngine validation;
- the countertrend risk-budget check.

Therefore a raw selected post-cap setup is only a **candidate**, not yet a true
cap-only shadow trade.

## Implemented repo-side

This branch adds an inert evaluator that mirrors the local post-decision risk
pipeline with exactly one deliberate change: `DailyState.trade_count` is reset
on a deep copy before RiskEngine validation.

Everything else in the reconstructed DailyState remains in force.

The evaluator:

- deep-copies the setup and DailyState;
- applies the same stop multiplier;
- computes the same confluence grade;
- derives contracts from the same RiskEngine sizing helper;
- tick-rounds the same entry/stop/target;
- runs the same RiskEngine;
- mirrors the runner's separate countertrend risk-budget check;
- records whether the setup is `eligible_except_daily_cap`.

It does **not**:

- instantiate PaperBroker or TradovateBroker;
- read broker positions or working orders;
- submit an order;
- change the actual journal trade count;
- mutate strategy state;
- change `risk_rules.yaml`;
- change the 3-trade cap;
- change the running 4HR observer.

The runner writes the result into the same blocked journal row as
`post_cap_eligibility`.

## Account-state boundary

The observer uses the **journal ledger** for account balance and account peak.

That is deliberate. Reading Tradovate after the cap would create a broker I/O
dependency in an observation-only path and could introduce session/auth side
effects. The record therefore carries:

- `account_balance_source = journal_ledger`;
- `broker_balance_parity_claimed = false`.

This means the result answers:

> Would the setup pass every local strategy/risk rule except the daily trade
> count, under the system journal ledger?

It does **not** claim exact equivalence to a differently funded Tradovate demo
account.

## What becomes a true shadow trade

Only a row satisfying all of these may later enter the post-cap outcome study:

1. `decision == BLOCKED_MAX_TRADES`;
2. `observed_decision == TRADE`;
3. `post_cap_eligibility.observation_only == true`;
4. `post_cap_eligibility.execution_reachable == false`;
5. `post_cap_eligibility.eligible_except_daily_cap == true`;
6. `post_cap_eligibility.risk_without_daily_cap.result == APPROVED`.

Rows failing another gate remain rejected candidates. They are not numbered as
hypothetical trade #4/#5/etc.

## Read-only reporting

`scripts/post_cap_eligibility_report.py` reads the journal records only. It
does not recreate strategy or risk logic.

For each day it reports:

- all raw selected candidates seen after the cap;
- candidates rejected by another gate;
- candidates still unverified or errored;
- only the candidates that passed every local gate except the cap.

Only those last rows receive hypothetical numbering:

- first eligible row after the 3-trade cap -> shadow trade #4;
- next eligible row -> #5;
- later eligible rows -> #6, #7, and so on.

The report intentionally has `outcome_scoring_enabled = false`. Numbering an
eligible setup is not the same as claiming its fill or P&L.

Example:

```bash
python scripts/post_cap_eligibility_report.py \
  --log-dir /root/afs-shared/logs \
  --date 2026-10-05
```

Older `BLOCKED_MAX_TRADES` rows that predate the new eligibility record remain
`UNVERIFIED`; the report does not retroactively guess.

## Outcome resolution remains HOLD

This change intentionally does **not** assign WIN/LOSS/P&L yet.

The generic opportunity resolver is a static-bracket counterfactual model. The
futures repo also contains strategies with special entry and exit mechanics
(IOC/market-entry differences, runner exits, day-only flattening, pre-resolved
Strat cases). Treating one generic resolver as exact for every future strategy
would recreate optimistic or identity-mismatched evidence.

Before outcome collection is activated, each executable strategy must be mapped
to a resolver with proven live/replay identity. Unsupported strategies must
remain `OUTCOME_UNVERIFIED`, not be forced through a convenient fill model.

## Safety tests

The branch adds tests proving:

- removing the cap does not mutate the caller's setup or DailyState;
- another risk gate still rejects after the count gate is bypassed;
- the evaluator refuses to run before capacity is actually reached;
- stop-width transformation is applied only to the shadow copy;
- the runner journals the post-cap eligibility record;
- the post-cap path does not construct either broker factory.

## Deployment boundary

Do not deploy this branch merely because CI passes.

The current 4HR natural-1m epoch remains the active prospective evidence lane.
Any runtime promotion of this observer requires a separate deployment-safety
decision and must preserve the 4HR epoch boundary and observer-only posture.
