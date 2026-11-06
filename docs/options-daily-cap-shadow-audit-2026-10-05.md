# Options daily-cap / post-cap shadow audit — 2026-10-05

**Status:** AUDIT ONLY / NO RUNTIME CHANGE / NO POLICY CHANGE

## Verdict

**DOES NOT DO THIS** for the active `OPTIONS_PAPER_V1` scanner.

The active V1 scanner does **not** currently implement a three-trade-per-day
ACTIVE capacity gate. Therefore there is no active-V1 equivalent of futures
"first 3 executable, #4+ observation only" behavior to preserve or report.

The repository does contain `max_daily_trades: 3` in the separate
`options_trading` / `OptionsRiskEngine` path, but that is the isolated
options companion path and is currently disabled. It must not be treated as
proof that the production/advisory `OPTIONS_PAPER_V1` scanner has the same
daily cap.

## What the active V1 actually enforces

The active paper policy in `alert_ranker/paper_v1.py` currently enforces:

- max planned risk per ACTIVE option row: **$300**;
- max aggregate open ACTIVE planned risk: **$1,000**;
- one contract;
- 45+ DTE preferred, 14-44 DTE exception, <14 DTE excluded;
- contract-quality gates for spread, volume, open interest, and delta;
- premium stop policy;
- setup/entry geometry and fail-closed data checks;
- duplicate / episode identity controls in the scanner.

There is no active-V1 daily opening-count gate in that policy or in the
scanner path reviewed for this audit.

## Existing COUNTERFACTUAL lane

The active scanner already has a strong ACTIVE / COUNTERFACTUAL evidence
separation.

Existing COUNTERFACTUAL rows preserve rejected or observation-only populations
such as:

- market/filter rejected mechanical setups;
- 1H / 4H observation-only cohorts;
- ENTRY_LATE refusals.

Those rows:

- remain separate from ACTIVE identity;
- set `risk_budget_consumed=false`;
- do not consume the ACTIVE $1,000 aggregate-risk budget;
- can preserve exact contract/quote/setup facts when the observation path is
  valid;
- can later be resolved by the existing options evidence lifecycle where that
  lifecycle is supported.

However, no current counterfactual reason represents
`daily_active_cap_exceeded`, because the active V1 has no daily count cap to
exceed.

## Separate disabled companion path

`risk/options_risk_engine.py` has a
`daily_trade_limit` check against `max_daily_trades`.

`options_companion/evaluator.py` uses that risk engine and its default
`max_daily_trades` is 3.

This is **not** the active `OPTIONS_PAPER_V1` scanner path. The repository
configuration currently has `options_trading.enabled: false`, so this cannot
be used as evidence that the active scanner is capped at three.

## What "similar to futures" would mean for options

If the operator chooses to add a daily ACTIVE-cap experiment later, the safe
design is:

1. Keep all current V1 setup, contract, quote, DTE, liquidity, entry-geometry,
   per-trade risk, aggregate-risk, and duplicate/episode gates unchanged.
2. Count only **new ACTIVE openings** for the NYSE trading date.
3. Permit at most the first **3 otherwise-valid ACTIVE openings**.
4. A fourth or later otherwise-valid setup is **not ACTIVE** and never consumes
   ACTIVE risk/P&L.
5. Convert that row into a COUNTERFACTUAL observation with an explicit reason
   such as `daily_active_cap_exceeded`.
6. Preserve the exact contract, ask-entry reference, premium stop, underlying
   invalidation, target, timestamps, setup identity, and later quote/outcome
   evidence under the existing COUNTERFACTUAL lifecycle.
7. Keep #4+ out of ACTIVE dashboards, account equity, aggregate-risk accounting,
   alert/trade counts, and any broker/order path.
8. Report #4, #5, #6+ separately only after they have passed **every other**
   V1 gate.

## Critical cohort boundary

Adding a three-ACTIVE-openings-per-day rule would change the frozen V1 paper
policy.

It therefore must **not** be silently inserted into the current evidence cohort.
If approved, the rule requires:

- a new policy/version or explicit evidence-epoch boundary;
- a preregistered start before collection;
- no retroactive relabeling of current ACTIVE rows;
- no backfill of prior days as though the cap had existed;
- independent review before deployment.

This is different from the futures #1137 work. Futures already has a deployed
three-trade capacity rule and #1137 only observes what was blocked by that
existing rule. Options V1 currently has no equivalent active rule, so adding one
would be a strategy/risk-policy change, not merely an observation enhancement.

## Recommendation

**Do not add the options cap to #1137 and do not modify current V1 yet.**

The next decision is operator-level:

- **KEEP CURRENT V1:** no daily opening-count cap; continue using the existing
  $300 per-row / $1,000 aggregate ACTIVE risk policy and counterfactual lanes.
- **START A NEW CAPPED COHORT:** preregister V2 (or a new V1 epoch if governance
  allows) with max 3 ACTIVE openings/day and #4+ COUNTERFACTUAL-only.

No deployment, scanner restart, env change, broker change, risk-rule change, or
evidence-epoch mutation was performed by this audit.
