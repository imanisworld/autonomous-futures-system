# Options — Current State Handoff

_As of 2026-09-09. This is the current options evidence handoff. Do not recreate completed scanner/risk work, do not tune Policy V1 during collection, and do not treat a functioning scanner as proof of strategy edge._

## Verdict

**ADVISORY / PAPER EVIDENCE ONLY. PIPELINE BUILT. STRATEGY EDGE NOT YET PROVEN.**

PR #533 is merged on `main` and expands the frozen `OPTIONS_PAPER_V1` evidence collector without changing broker execution or the frozen risk/contract policy.

## Current Policy V1 — frozen during this test

- max planned risk per trade: **$300**
- max aggregate open planned risk: **$1,000**
- no hard position-count cap; aggregate planned risk controls exposure
- preferred DTE: **45+**
- 14–44 DTE: allowed and tagged as an exception/warning for later analysis
- 0DTE/weeklies: excluded from the normal strategy population
- planned risk: `(entry premium - premium stop) × 100 × contracts`
- underlying invalidation and premium stop are both required
- no averaging down
- Signa is observational context only
- GEX is optional context; missing GEX is not a strategy rejection
- missing critical setup/quote/risk data fails closed as `DATA_INVALID`

Any change to these rules creates a new policy version and a separate evidence population.

## Strategy populations now collected

Independent setup/timeframe identity is preserved so one population cannot erase another even when two setups choose the same option contract.

- existing **30m 2-1-2 continuation**
- **Daily 2-1-2 continuation**
- **Daily 2-2-2 continuation**
- **Daily 2-2-2 reversal**
- **Daily 3-2 developing** — WATCH only until it becomes a completed actionable sequence
- **Daily 3-2-2 continuation**
- **Daily 3-2-2 reversal**

Daily entries are mechanical previous-candle high/low breaks. Underlying invalidation is the opposite side of the previous Daily candle. Daily targets use already-completed prior-session levels. Daily paper promotion requires the currently implemented market-alignment proof; missing higher-order trade proof keeps user-facing alerts fail-closed while paper evidence can still be journaled.

## Strategy isolation protocol — what we will measure

Do **not** immediately tune these dimensions. First collect the frozen V1 population. Then isolate each strategy with the same questions:

1. **Signal / timeframe** — does the pattern itself predict direction, and how does the same setup behave by timeframe?
2. **Entry** — is the entry too early, too late, or already extended when filled?
3. **Stop** — does the planned stop sit inside normal adverse excursion for that setup?
4. **Target / exit** — are target geometry or hold duration mismatched to the timeframe?
5. **Filters** — which market/trend/regime/context filters add value, remove value, or merely reduce sample?
6. **Execution realism** — does the edge survive executable bid/ask, spread, slippage/cost assumptions, pessimistic same-bar handling where relevant, and aggregate/open-position constraints?

### Status of that isolation work

**Not complete for the newly added Daily options populations yet.** That is intentional: #533 created the independent evidence populations; it did not manufacture an edge study before a trustworthy sample exists.

Historical underlying bars can test price-action behavior, but they cannot fully reconstruct historical option-chain selection, spreads, IV, DTE, Greeks, and executable premium exits. The forward collector exists specifically to preserve those facts so the later options study is contract-realistic rather than an underlying-price proxy.

The first useful comparisons after sample accumulation are:

- 30m vs Daily 2-1-2
- Daily 2-2-2 continuation vs reversal
- Daily 3-2-2 continuation vs reversal
- 45+ DTE vs 14–44 DTE
- premium-stop-based planned risk vs realized adverse premium movement
- Signa aligned/opposed/missing as observational cohorts
- GEX available/unavailable/regime cohorts, without turning GEX into a hard gate mid-test

## Evidence required on every candidate

Preserve at minimum:

- setup type and timeframe
- direction
- DTE bucket
- expiration / strike / contract
- entry premium, bid, ask and spread
- premium stop
- planned dollar risk
- underlying invalidation
- target 1 / target 2
- contract marks through resolution
- realized outcome and realistic costs
- Signa state
- GEX state
- rejection / suppression reason
- policy id

## What is already done — do not redo

- scanner/advisory plumbing exists
- scheduled scan + SQLite persistence exists
- contract selection and `OPTIONS_PAPER_V1` risk validation exist
- premium-stop-based risk accounting exists
- aggregate-open-risk accounting exists
- Signa is observational rather than hard strategy authority
- GEX is optional context
- causal setup proof and fail-closed alerting exist
- #533 adds independent multi-setup/timeframe collection and Daily Strat populations

Do not reopen these because there are not yet enough winners/lossers. Missing strategy evidence is a collection problem, not proof of an implementation defect.

## Smallest safe next step

1. Deploy the reviewed `main` containing #533 through the normal options deployment path.
2. Run a market-hours smoke proof: fresh bars/quotes, scan writes, independent setup identity, contract marks, resolver behavior, Discord suppression/alert behavior, and no order path.
3. Freeze the evidence epoch/policy id.
4. Collect natural candidates.
5. Run the six-part isolation study only when each comparison has enough trustworthy observations to say something useful.

**No optimization during collection. No proof, no trade.**
