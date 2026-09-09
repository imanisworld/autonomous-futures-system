# Options — Current State Handoff

_As of 2026-09-09. This is the current options evidence handoff. Do not recreate completed scanner/risk work, do not tune Policy V1 during collection, and do not treat a functioning scanner as proof of strategy edge._

## Verdict

**ADVISORY / PAPER EVIDENCE ONLY. PIPELINE BUILT. STRATEGY EDGE NOT YET PROVEN.**

PR #533 established the frozen `OPTIONS_PAPER_V1` multi-setup collector. PR #536 adds the evidence hardening required before deployment: rejected-filter counterfactuals, 1H/4H observation cohorts, ambiguity telemetry, active-vs-counterfactual summary separation, and a fail-closed V1 runtime preflight. No broker execution is added.

## Current Policy V1 — frozen during this test

- max planned risk per active paper trade: **$300**
- max aggregate active planned risk: **$1,000**
- no hard position-count cap; aggregate planned risk controls exposure
- preferred DTE: **45+**
- 14–44 DTE: allowed and tagged `DTE_EXCEPTION`
- <14 DTE / weeklies: excluded from the V1 population
- planned risk: `(entry premium - premium stop) × 100 × contracts`
- entry basis: ask
- premium stop: **25% adverse from entry premium**
- underlying invalidation and premium stop are both required
- no averaging down
- Signa is observational context only
- GEX is optional context; missing GEX is not a strategy rejection
- missing critical setup/quote/risk data fails closed as `DATA_INVALID`

Any change to these rules creates a new policy version and a separate evidence population.

The generic manual `options_manager` contract-quality surface is not the V1 evidence authority. The V1 collector itself hard-rejects <14 DTE. A manual `dte_exceptional` override must not be used to inject short-DTE rows into this campaign. Runtime V1 preflight also requires the manager policy pins `OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS=1000` and `OPTIONS_MANAGER_RISK_MIN_DTE_DAYS=14`, and requires the old `options_companion` lane to remain disabled.

## Bankroll / drawdown evaluation — separate from frozen trade policy

The first evidence campaign must also answer whether the strategy is practical at the account size the operator actually wants to use. This is **analysis only** and does not change which V1 trades are collected.

Evaluate the same ACTIVE V1 evidence through parallel cash-only long-option account scenarios:

- **$1,500** starting balance — initial test bankroll
- **$2,500** starting balance — intermediate comparison
- **$5,000** starting balance — current maximum allocation ceiling if the demo proves itself

The **$5,000 figure is an allocation ceiling, not an acceptable drawdown**.

The account-equity analyzer uses conservative executable accounting:

- entry debit at the recorded **ask**
- open positions marked to the recorded **bid**
- exits at the recorded **bid**
- unrealized P&L is included in equity/drawdown, not hidden until close
- long calls/puts are treated as cash-funded premium positions; a scenario that cannot fund an otherwise valid entry records a capital block rather than assuming margin
- counterfactual rows are excluded from account P&L because they were never active paper positions
- same-timestamp entry is processed before exit for conservative cash sufficiency
- the existing V1 cost model remains `entry_at_ask_exit_at_bid_no_commission`; realistic fee sensitivity can be added to analysis later without altering the collected signal population

Track at minimum for each bankroll:

- ending equity and total return
- realized and unrealized P&L
- peak-to-trough drawdown in dollars and percent
- lowest equity
- longest time underwater
- maximum simultaneous capital deployed
- maximum simultaneous planned risk
- maximum open positions
- trades blocked only because that bankroll lacked available cash
- minimum observed starting cash needed to fund every active entry

### Swing / overnight accounting

Swing positions remain open risk until they actually close. The analyzer therefore also preserves:

- number of trades held across at least one market date
- total position-nights
- maximum simultaneous overnight positions
- worst observed option-premium gap from the prior session's last bid to the next session's first bid
- open-position unrealized P&L in the equity curve

This lets the demo answer whether the system works economically at $1,500 or whether it needs more capital, without changing setup rules to force a desired answer.

## Active strategy populations

Independent setup/timeframe identity is preserved so one population cannot erase another even when two setups choose the same option contract.

- **30m 2-1-2 continuation**
- **Daily 2-1-2 continuation**
- **Daily 2-2-2 continuation**
- **Daily 2-2-2 reversal**
- **Daily 3-2 developing** — WATCH only until actionable
- **Daily 3-2-2 continuation**
- **Daily 3-2-2 reversal**

Daily entries are mechanical previous-candle high/low breaks. Underlying invalidation is the opposite side of the previous Daily candle. Daily targets use already-completed prior-session levels. Existing market-alignment proof remains the active promotion gate; missing higher-order trade proof keeps user-facing alerts fail-closed while paper evidence can still be journaled.

## Counterfactual / timeframe observation populations

PR #536 fixes the survivor-bias problem in filter analysis. A mechanically valid signal that a market/target filter rejects is no longer simply lost.

Counterfactual rows:

- never send Discord alerts
- never consume the active **$1,000** aggregate-risk budget
- use the same real-chain contract selection and per-trade quality rules when enough setup facts exist
- re-quote the exact selected option contract through resolution
- carry `paper_evidence_lane=COUNTERFACTUAL` and `risk_budget_consumed=false`
- remain distinct from an active row for the same contract/setup so an observer cannot dedupe a later active candidate
- are excluded from the normal dashboard win-rate/P&L summary

Collected observer populations include:

- mechanically confirmed **30m** signals rejected before active promotion
- mechanically confirmed **Daily** signals rejected by market/target proof
- **1H** Strat observations
- **4H_RTH** Strat observations

`4H_RTH` is explicitly defined from the causal 30m source as regular-session blocks anchored to the session open. Completed sessions retain the shortened final RTH block. It is deliberately labeled `4H_RTH` rather than pretending to be a native/vendor 4H candle whose construction has not been validated.

## Strategy isolation protocol — what we will measure

Do **not** immediately tune these dimensions. First collect the frozen V1 population. Then isolate each strategy with the same questions:

1. **Signal / timeframe** — does the pattern itself predict direction, and how does the same setup behave on 30m, 1H, 4H_RTH and Daily where comparable?
2. **Entry** — is the entry too early, too late, or already extended when filled?
3. **Stop** — does the planned stop sit inside normal adverse excursion for that setup?
4. **Target / exit** — are target geometry or hold duration mismatched to the timeframe?
5. **Filters** — which market/trend/regime/context filters add value, remove value, or merely reduce sample? Compare active vs counterfactual cohorts rather than only survivors.
6. **Execution realism** — does the edge survive executable bid/ask, spread, realistic costs, snapshot/path ambiguity, pessimistic handling, and aggregate-open-risk constraints?

### Status of that isolation work

**Not complete yet.** That is intentional: the collector is being made capable of answering these questions before the first V1 evidence epoch is started. No edge result should be manufactured from an empty/new population.

Historical underlying bars can test price-action behavior, but they cannot fully reconstruct historical option-chain selection, spreads, IV, DTE, Greeks, and executable premium exits. The forward collector exists specifically to preserve those facts so the later study is contract-realistic rather than an underlying-price proxy.

The first useful comparisons after sample accumulation are:

- 30m vs 1H vs 4H_RTH vs Daily setup behavior where setup definitions overlap
- Daily 2-2-2 continuation vs reversal
- Daily 3-2-2 continuation vs reversal
- active-filter survivors vs rejected-filter counterfactuals
- 45+ DTE vs 14–44 DTE
- premium-stop-based planned risk vs realized adverse premium movement
- Signa aligned/opposed/missing as observational cohorts
- GEX available/unavailable/regime cohorts, without turning GEX into a hard gate mid-test
- $1,500 vs $2,500 vs $5,000 bankroll feasibility and drawdown
- intraday vs overnight/swing drawdown contribution

## Lifecycle / execution evidence

The exact selected option contract is re-quoted on the scheduled resolver cadence. Every resolution now states that the path between snapshots is not directly observed.

If premium-stop and underlying-target conditions are both true on the same observed snapshot, the row is not allowed to masquerade as a clean loss. It is tagged:

- `resolution_ambiguity=AMBIGUOUS`
- `pessimistic_status=LOSS`
- `pessimistic_resolution_used=true`

Accounting remains pessimistic, while later execution studies can explicitly include/exclude the ambiguous cohort.

## Evidence required on every usable candidate

Preserve at minimum:

- setup type and timeframe
- active vs counterfactual evidence lane
- original filter/suppression reason
- direction
- DTE bucket
- expiration / strike / exact contract
- entry premium, bid, ask and spread
- premium stop
- planned dollar risk
- whether risk budget was consumed
- underlying invalidation
- target 1 / target 2
- contract marks through resolution
- ambiguity/path telemetry
- realized outcome and realistic costs
- Signa state
- GEX state
- policy id

## What is already done — do not redo

- scanner/advisory plumbing
- scheduled scan + SQLite persistence
- real-chain contract selection and `OPTIONS_PAPER_V1` risk validation
- 2099/far-future contract sanity protection
- premium-stop-based risk accounting
- aggregate active-risk accounting
- Signa observational rather than hard strategy authority
- GEX optional context
- causal setup proof and fail-closed alerting
- #533 independent multi-setup Daily collection
- #536 counterfactual/filter evidence plumbing
- #536 1H and 4H_RTH observer cohorts
- #536 active-vs-counterfactual risk/summary separation
- #536 snapshot-path ambiguity telemetry
- #536 explicit V1 scheduled runtime preflight
- V1 account-equity replay for **$1,500 / $2,500 / $5,000** bankroll scenarios, including unrealized equity and swing/overnight exposure

Do not reopen these because there are not yet enough winners/lossers. Missing strategy evidence is a collection problem, not proof of an implementation defect.

## Smallest safe next step

Use `docs/options-paper-v1-deployment-checklist.md`.

1. Deploy the reviewed current `main` through the normal options deployment path.
2. Set the explicit V1 runtime pins, including `OPTIONS_PAPER_V1_COLLECTION_ENABLED=true`, causal bar context, `$1,000` manager aggregate-risk pin, 14-day manager minimum DTE, and `OPTIONS_COMPANION_ENABLED=false`.
3. Run one normal market-hours smoke proof covering active and counterfactual identity, real contract selection, exact-contract marks, active-risk separation, dashboard-summary separation, ambiguity telemetry, Discord suppression/alert behavior, and no order path.
4. Run the bankroll/drawdown report against the smoke database and confirm the $1,500 / $2,500 / $5,000 scenarios can read the same V1 rows and exact marks without changing collector state.
5. Record the deployed commit SHA + smoke timestamp as the V1 evidence epoch.
6. Collect natural candidates without tuning the frozen rules.
7. Review both strategy decomposition and bankroll/drawdown evidence only when the sample is large enough to say something useful.

**No optimization during collection. No proof, no trade.**
