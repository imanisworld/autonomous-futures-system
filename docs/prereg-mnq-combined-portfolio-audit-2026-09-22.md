# MNQ Combined Portfolio Audit — Preregistration (2026-09-22)

**AUDIT / RESEARCH ONLY. NO EXECUTION AUTHORITY.**

## Current implementation status — 2026-09-22

PR #915 is **DRAFT / AUDIT ONLY** and the combined portfolio replay has **not**
yet produced an accepted result. The branch is being actively built and audited;
branch movement during this work is not production drift and does not itself
change any strategy verdict.

Corrections proven during implementation so far are confined to the combined
research adapter and its tests:

- 4HR provenance now distinguishes 80 resolved outcomes from the one
  `EOD_BAR_MISSING_FAIL_CLOSED` unresolved exclusion;
- standalone controls preserve each family's frozen capacity semantics instead
  of inheriting the shared portfolio's three-fill cap;
- 3-2-2 is explicitly sourced from its frozen research corpus
  (`replay_polygon`);
- Miyagi is explicitly sourced from its frozen 5-minute evidence corpus
  (`replay_polygon_5m`).

These are provenance/reproduction corrections for this audit. They do **not**
revert newer corpora globally, modify the frozen strategy logic, or authorize
changes to risk, execution, broker, webhook, runtime, or deployment behavior.
Using a historical corpus here means reproducing the evidence population that
created a frozen family result; it does not declare that corpus superior to a
newer corrected corpus for other studies.

No combined P&L, portfolio classification, strategy promotion/demotion, or
runtime conclusion is accepted until all frozen controls reconcile on one
frozen CI-green commit and the full common-window replay completes fail-closed.

## Question

The system has mostly evaluated promising MNQ strategies in isolated ledgers.
That does not answer the actual account-level question:

> If the frozen MNQ strategies all compete for the same account, with one open
> position and three fills per CME observation day, what does the combined
> chronological portfolio actually do?

This study measures **portfolio complementarity and cannibalization**. It is not
a new strategy search and must not tune any component strategy.

## Included frozen MNQ families

Include these only if their historical candidate stream can be reproduced
causally from the existing frozen implementation/evidence contract over the
common test window:

1. **4HR Re-Trigger**
2. **60M 3-2-2 First Live**
3. **Daily 2-2 completed-close**
4. **12HR Miyagi**
5. **Asia D+EMA one-position cohort**
6. **MNQ Sustained Trend Continuation v1** from closed draft PR #912

Do not substitute broad shadow families for any missing family.

If a listed family cannot be reproduced causally on the common population,
record it as `UNAVAILABLE_FOR_COMBINED_REPLAY` and fail closed for the
six-family headline. A reduced portfolio may be reported separately, but must
be labeled with the exact included family set.

MES lanes are excluded. This is an MNQ single-account question.

## Strategy identities are frozen

Each family keeps its own already-documented:

- signal timing;
- candidate construction;
- entry semantics;
- stop;
- target;
- fill model;
- slippage;
- commission;
- holding horizon / EOD behavior;
- causal invalidation rules.

Do not harmonize brackets or retune a family merely to make it fit the
portfolio.

In particular:

- do not turn completed-close Daily 2-2 into first-touch;
- do not change 4HR or 3-2-2 trigger timing;
- do not alter Miyagi's corrected causal trigger-bar mechanics;
- do not change Asia D+EMA's archived cohort definition;
- do not change Sustained Trend v1 thresholds after its standalone result.

## Common historical window

Primary combined population is the **intersection of causal source-data
availability for every included family**, determined before portfolio P&L is
examined.

Expected target window, if all six can be reproduced on the available corpora:

**2025-07-24 through 2026-06-26**

This is the overlap between the canonical corrected 15m corpus and the frozen
5m higher-timeframe audit corpus.

If a family's actual reproducible coverage narrows the intersection, report the
exact resulting dates and why. Do not backfill missing evidence, fetch new data,
or silently substitute another corpus.

## Shared-account rules

Primary portfolio:

- instrument: MNQ only;
- one contract per fill;
- **one open MNQ position total across all families**;
- **maximum 3 filled trades per CME observation day total across all families**;
- shared chronological account state;
- no averaging down;
- no simultaneous MNQ positions;
- a signal arriving while another portfolio trade is open is
  `SKIPPED_BUSY_PORTFOLIO`;
- a signal arriving after three fills that observation day is
  `SKIPPED_MAX_TRADES_PORTFOLIO`;
- no strategy receives a private extra slot.

Starting accounting balance for portfolio drawdown reporting: **$5,000**.

This balance is a research normalization only. It does not make a currently
risk-incompatible strategy executable on a real $5,000 account.

## Selection rule

The portfolio selection rule is frozen before results:

1. Earliest causal fill timestamp wins.
2. If two or more eligible signals have the exact same causal fill timestamp,
   use this fixed tie order:

   `4HR_RETRIGGER`
   → `60M_322_FIRST_LIVE`
   → `DAILY_22_COMPLETED_CLOSE`
   → `12HR_MIYAGI`
   → `ASIA_D_EMA`
   → `SUSTAINED_TREND_V1`

The tie order is administrative only and is not based on retrospective
performance. Report the number of exact-timestamp collisions so its practical
importance is visible.

Do not choose the higher-PF strategy after observing outcomes.

## Required standalone controls

For the exact common historical window, regenerate each included family's
standalone one-position stream using its frozen contract and report:

- candidates;
- fills;
- W/L/open or timeout outcomes;
- net;
- PF;
- expectancy;
- max drawdown;
- distinct filled days.

These controls prove the portfolio input streams match their source evidence
before combination.

A material mismatch from a family's accepted canonical evidence must be
explained before the combined result is trusted.

## Required combined outputs

Report:

### Portfolio headline

- eligible signals by family;
- fills by family;
- busy skips by family;
- max-trades skips by family;
- exact-timestamp collisions;
- wins / losses / open or timeout;
- net P&L after each family's frozen costs;
- expectancy per terminal fill;
- profit factor;
- max drawdown dollars and percent of $5,000;
- distinct filled days;
- H1/H2 chronological net and PF;
- monthly net / PF;
- max consecutive losses.

### Complementarity / cannibalization

For every family, compute a **leave-one-family-out portfolio** using the exact
same chronological rules.

Report the delta versus the full portfolio:

- delta net;
- delta PF;
- delta max drawdown;
- delta fills;
- delta winning/losing days;
- newly available slots when that family is removed;
- which other families consume those slots.

This is the primary answer to whether a modest standalone family improves the
system or merely steals capacity from a stronger setup.

### Coverage

Using the already-documented descriptive large-move denominator only:

- number of large MNQ directional-move windows;
- represented by at least one included family;
- actually filled by the shared portfolio;
- missed because no family signaled;
- missed because another position occupied the account;
- missed because the 3/day cap was exhausted.

Coverage is descriptive. It cannot override bad economics.

## Interpretation rules

A family may be useful to the portfolio even if its standalone PF is below a
standalone promotion hurdle, but only if the leave-one-family-out comparison
shows that including it improves the frozen combined portfolio without
materially worsening drawdown/stability.

Conversely, a strong standalone family may add little if its signals are mostly
duplicates of higher-priority occupied periods.

Do not add standalone P&Ls together. Only the chronological shared-account
stream is the portfolio result.

## Portfolio evidence classification

This audit does **not** validate any strategy for live trading.

Possible portfolio findings:

- `COMPLEMENTARY_EVIDENCE` — full portfolio improves materially over the
  relevant leave-one-out control while preserving stability;
- `REDUNDANT` — family contributes little because another family already
  occupies the same opportunities;
- `CANNIBALIZES_PORTFOLIO` — adding the family worsens combined economics or
  drawdown under the frozen selection rule;
- `INSUFFICIENT_COMMON_DATA` — required family streams cannot be reproduced
  over a common causal window.

Do not convert these labels directly into runtime enablement.

## Safety boundary

Do not modify:

- `risk_rules.yaml`;
- `strategy/`;
- `risk/`;
- `execution/`;
- `webhook/`;
- broker adapters;
- runtime environment;
- active paper/DEMO/live lanes;
- strategy thresholds or brackets.

Implementation belongs under `research/` and/or `scripts/` only.

No merge, deployment, restart, paper activation, DEMO activation, or broker
action is authorized by this study.

**No proof, no run.**
