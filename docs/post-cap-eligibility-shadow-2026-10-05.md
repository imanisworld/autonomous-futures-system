# Post-cap eligibility shadow — 2026-10-05

**Status:** MERGED TO `main` (`4433c0e`) / OBSERVATION ONLY / **HOLD DEPLOYMENT**

Independent review: **APPROVE REPO-SIDE ONLY** (exact head `deb2346`).
Deployment-readiness audit (2026-10-05): **HOLD DEPLOYMENT** — leave the box on
`c44d32b`; do not promote this observer onto the active 4HR natural-1m epoch.

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
pipeline with exactly one deliberate change: an observation-only RiskEngine
subclass bypasses only `_check_daily_trade_limit`.

The copied `DailyState.trade_count` is preserved unchanged so every other gate
that depends on the real count, including reduced-mode
`news_blackout_trade_limit`, remains authoritative.

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
- another risk gate still rejects after the daily-cap gate is bypassed;
- reduced-mode news blackout still sees the real post-cap `trade_count` and can
  reject with `news_blackout_trade_limit`;
- the evaluator refuses to run before capacity is actually reached;
- stop-width transformation is applied only to the shadow copy;
- the runner journals the post-cap eligibility record;
- the post-cap path does not construct either broker factory.

## Deployment boundary

Do not deploy this observer merely because it is merged or CI passed.

**2026-10-05 decision: HOLD DEPLOYMENT.** Futures box stays on exact release
`c44d32bc4961e56fae5c5f88a976eb6783341638`. Canonical 4HR natural-1m epoch
start remains `2026-10-04T21:30:05Z`. Promoting current `main` would ride along
unrelated post-`c44d32b` commits and requires a restart that breaks the epoch
release pin. Deployed `c44d32b` already journals raw `BLOCKED_MAX_TRADES`
candidates via `observe_past_capacity`; eligibility classification can wait.

A cherry-picked `c44d32b` + #1137-only candidate may be built later only under
an explicit operator GO after the epoch may end or be re-baselined. Any runtime
promotion must preserve observer-only posture and live-disarmed state.
