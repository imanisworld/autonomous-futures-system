# Preregistration — Options 30m 2-1-2 continuation floor-activation underlying outcome (2026-10-02)

<!-- trial_id: T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01 -->

**Status: DRAFT — NOT APPROVED — NOT RUN.**

**Trial ID:** `T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01`

**Experiment ID:** `E-2026-10-02-options-212c-floor-outcome-01`

This draft asks whether already-defined `floor_ge1r` activations have economically useful underlying outcomes. It does not answer that question. It freezes the population and the measurement contract for a later one-look. It does not retune `floor_ge1r`, compare another target rule, select an option contract, change `OPTIONS_PAPER_V1`, expand the watchlist, enable SPXW, or authorize a paper or live order.

## Question

When a 30m `STRAT_212_CONTINUATION` setup on the fixed 20-symbol V1 universe passes the existing `floor_ge1r` activation logic, what underlying path outcome does the frozen first-sight execution model record?

The closed trial `T-2026-09-25-prereg-options-212c-target-geometry-2026-09-25-01` already answered a different question: on the frozen 59 episodes from 2026-09-09 through 2026-09-15, `floor_ge1r` produced 2 activations and `nearest_v1` produced 0. That result is coverage only. It supplies the historical rate used in the sample-size section below. It is not an outcome sample for this trial.

## Why this population is forward

No defensible untouched retrospective holdout was found in this checkout.

| Window | Exposure | Use in this trial |
|---|---|---|
| 2026-09-09..2026-09-15 | Closed. Byte-frozen outcome file `outcomes_2026-09-09_2026-09-15.json`, SHA-256 `1963db73bccf0fd366eaaa077bb4e9582ed453ff220f1c5e789961096f3f113c`. Gate counts and the one-look activation result are already published. | Ineligible. Do not rescore, extend, or reopen. |
| 2026-09-16..2026-09-18 | Prospective family validation published first-sight ≥1R outcomes for `STRAT_212_REVERSAL` and `OTHER:strat_122`. The same `out-v0.1` aggregate contains a path view for every family. The 2026-09-19 postmortem published option P&L for two 30m 2-1-2 continuation rows: NVDA `9318` and MRK `9320`. | Ineligible. |
| 2026-09-19..2026-09-29 | Epoch-3 audit read the production options journal through `2026-09-29T19:46:18Z` and published ACTIVE and counterfactual dollar results. That journal is the live nearest-geometry scanner path, not a sealed `floor_ge1r` holdout. | Ineligible. |
| 2026-09-30..2026-10-02 | No coverage outcome file for these dates is in this checkout. The 2026-10-02 session was still open when this draft was written, and the collector's existing pipeline writes path outcomes after the close. Two or three sessions cannot support an edge claim at the closed activation rate. Box sessions after 2026-09-18 were not listed in this session. | Ineligible. Do not open them to manufacture a holdout. |

Local inventory checked without reading path fields: `logs/coverage_outcomes/` contains only `outcomes_2026-09-09_2026-09-15.{json,csv,md}`. No later `outcomes_*.json` or `episodes_*.json` is in `logs/` or `data/`.

Eligible sessions are NYSE regular sessions on or after **2026-10-05**. Collection stops at the earlier of:

- 25 `floor_ge1r` activations, counted from `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` only; or
- 60 completed eligible NYSE sessions.

## Blind collection contract

Until the stopping rule is met, the only study readout an operator or agent may view is:

- NYSE sessions elapsed, as one count
- cumulative `floor_ge1r` activation count, as one count

Those two numbers are the entire interim report. A favorable or unfavorable impression from them is not a reason to stop, continue, or change the rule.

Before the one look, this trial must not expose:

- ticker identities of activations
- activation dates or timestamps
- direction
- target, stop, or timeout classification
- MAE or MFE
- R outcome
- win or loss counts
- per-session activation counts
- any option-contract or P&L field

The coverage collector may keep writing its ordinary files. Path and outcome fields in those files are not a study readout. Opening them for this trial before the stopping rule is met makes the trial `INVALID`. One scoring pass is allowed only after the stopping rule is met.

## Frozen population

Include every episode satisfying all of:

- `family == STRAT_212_CONTINUATION`
- session date is an eligible NYSE session on or after 2026-10-05, inside the stopping window above
- symbol is in the exact 20-symbol V1 universe: `AAPL, AMZN, BAC, COIN, GE, GOOGL, INTC, IWM, JPM, MRK, MSFT, NFLX, NVDA, PLTR, QQQ, SPY, TLT, TSLA, WMT, XOM`
- episode identity is the existing `ep-v0.1` first-opportunity reduction
- membership is selected from family, symbol, and session date

The 66-symbol candidate universe is not the population. Membership is not filtered on a path outcome, a target hit, or a P&L sign.

Expected structural rate, carried forward from the closed window only: 59 setups in 5 sessions, about 11.8 setups per session. That rate is a planning figure. A different count at the stopping boundary is a reported fact, not an integrity failure, because this window is prospective and its size is not frozen in advance.

## Setup and activation

Versions stay `cov-v0.1` / `ep-v0.1` / `out-v0.1`.

`floor_ge1r` is the existing rule, unchanged:

- target geometry = `find_targets(min_target_rr=1.0)`
- activation = stored `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` at first sight
- that bucket already requires V1-supported family, valid floor geometry, market alignment, and a first-sight state that is not late and not after the close
- activation is not permission to pick a later or better fill

`nearest_v1` is not a second arm. No other target variant is added.

## Stage A — underlying path

Score only activated episodes, and score them once, after the stopping rule.

**Measured entry.** `measured_entry_price = first_sight_price`. Every Stage A R uses that price as the entry. The stored Strat trigger (`entry_trigger`) remains the structural trigger that defines the setup and the invalidation geometry. A later scorer must not substitute `entry_trigger` for `first_sight_price`, and must not substitute `first_sight_price` for `entry_trigger`. The mechanical trigger-cross view may be stored as setup context. It is not the fill and it is not the decision metric.

Preregistered fields:

| Item | Frozen rule |
|---|---|
| Activation timestamp | Stored `first_sight_at` (bar close + 960 seconds, rounded up to the scanner grid). This is when the activation is judged. It is not a license to search for a better price |
| Direction | Stored episode direction |
| Measured entry | `measured_entry_price = first_sight_price` |
| Structural trigger | Stored `entry_trigger`. Setup context only. Never the measured fill |
| Invalidation | Stored episode invalidation. No trailed stop |
| Target 1 | Stored floor `target_1`. Exit classification uses Target 1 |
| Target 2 | Stored when the floor geometry has one. Recorded when reached. Not an exit |
| Maximum hold | Same regular session. `UNRESOLVED_AT_CLOSE` at the session close. No overnight carry |
| Missing bars | A missing 5-minute bar or an incomplete forward session flags `missing_forward_bars` or `incomplete_forward_session`. The outcome is `DATA_INVALID` and is excluded from expectancy |
| Price gaps | Deterministic rules below. No discretionary fill inside a gap |
| Same-bar ambiguity | One 5-minute bar whose range reaches both Target 1 and invalidation is `AMBIGUOUS`. It is not a win and not a loss |
| Fees and slippage | No dollar P&L is calculated. No extra slippage is applied on top of `first_sight_price`. R uses structural risk as the denominator and `measured_entry_price` as the entry |
| MAE / MFE | Adverse and favorable extremes versus `measured_entry_price`, divided by structural risk, on bars scored before the decisive event |
| R-multiple | See the price-gap rules. Timeout: `close_r` from the session close versus `measured_entry_price`. Ambiguous and data-invalid rows are excluded from mean, median, and expectancy |
| Classification | `TARGET_FIRST`, `INVALIDATION_FIRST`, `UNRESOLVED_AT_CLOSE`, `AMBIGUOUS`, `DATA_INVALID` |

### Price-gap rules

These rules are applied to each 5-minute bar in time order. The first matching rule ends the walk. "Beyond the stop" means the open is at or through invalidation in the adverse direction (`LONG` open `<=` invalidation; `SHORT` open `>=` invalidation). "Beyond Target 1" means the open is at or through Target 1 in the favorable direction. "Beyond Target 2" uses the same favorable test when Target 2 exists. "Reaches" means the bar's favorable or adverse extreme touches the level, including the open.

1. **Gap or range spans both stop and Target 1.** If the bar reaches both invalidation and Target 1, the outcome is `AMBIGUOUS` with flag `gap_or_range_spans_stop_and_target`. This includes a bar that opens beyond one level and reaches the other. No fill price is assigned. The row is excluded from R expectancy.
2. **Bar opens beyond the stop.** If the open is beyond the stop and the bar does not reach Target 1, the outcome is `INVALIDATION_FIRST`. Realized R is the R of that open versus `measured_entry_price`. The stop price inside the gap is not a fill.
3. **Bar opens beyond Target 1.** If the open is beyond Target 1 and the bar does not reach the stop, the outcome is `TARGET_FIRST`. Realized R is `target_1_r`. The open beyond Target 1 is not a fill. If that open is also beyond Target 2, record Target 2 as reached and still use `target_1_r`.
4. **No gap open.** If the open is strictly inside the stop and Target 1, a later touch of the stop without a Target 1 touch is `INVALIDATION_FIRST` at the R of the invalidation versus `measured_entry_price`. A later touch of Target 1 without a stop touch is `TARGET_FIRST` at `target_1_r`. A bar that then reaches both is rule 1.

Target 2 never changes the exit. A gap through Target 2 is a gap through Target 1 when Target 2 lies further in the favorable direction, and rules 1–3 still decide the class.

There is no breakeven band. A timeout whose `close_r` is exactly zero remains a timeout. No trailing exit is added after outcomes are seen.

Wins are `TARGET_FIRST`. Losses are `INVALIDATION_FIRST`. Timeouts are `UNRESOLVED_AT_CLOSE`. Ambiguous and data-invalid rows are reported separately and stay out of the win rate and the R expectancy.

## Stage B — option contract

**NOT EVALUATED.**

This checkout cannot support a trustworthy historical option fill for a `floor_ge1r` activation:

- the coverage observer records structure and an underlying path; it does not select or store a contract
- `selector-v1` requires, at the decision timestamp, bid, ask, quote age, volume, open interest, delta, and a finite underlying price (`min_dte` 14, `preferred_min_dte` 45, absolute delta 0.30–0.70 with target 0.50, max spread 20%, max quote age 900 seconds, min volume 100, min open interest 500)
- the 2026-09-18 Massive/Polygon one-sample proof, on a different family, showed historical NBBO bid/ask and trade volume for known contracts, and showed that the historical quote and aggregate endpoints do not supply historical delta or open interest
- the current chain snapshot has no admissible historical `as_of`; a current snapshot must not be written back onto an old timestamp
- `options_contract_marks` exist for scanner journal rows the production nearest-geometry path actually selected, not for the future `floor_ge1r` activation set

Missing fields that block option expectancy: causal chain identity at the activation timestamp, historical bid, historical ask, spread percent, interval volume, open interest, IV, delta, entry premium, exit premium, premium-stop path, and a transaction-cost record tied to that contract. Until those fields come from a causal historical source, this trial reports no option P&L and no option expectancy.

A later amendment would be a new trial. It would have to keep `selector-v1` unchanged and fail closed on any missing contract field. This draft does not make that amendment.

## Metrics

Report these after the one look. Activation count is not the result.

- structurally selected episode count
- activation count
- activation rate
- completed outcome count (`TARGET_FIRST` + `INVALIDATION_FIRST` + `UNRESOLVED_AT_CLOSE`)
- wins, losses, and timeouts
- ambiguous count and data-invalid count
- win rate, with timeouts kept in the completed denominator and ambiguous/data-invalid rows kept out
- mean R and median R
- expectancy in R
- average winner and average loser
- payoff ratio (average winner divided by the absolute average loser), undefined when either side has zero rows
- MAE and MFE distributions in R
- target-hit rate, stop-hit rate, and timeout rate
- concentration by ticker, session date, and clock bucket
- outcome concentration (share of total R from the single largest contributor, and from the largest ticker)

Maximum drawdown is not reported. No sequential portfolio, sizing, or overlap model is preregistered.

No numeric expectancy, win rate, or payoff threshold is an acceptance criterion. A sample that looks profitable does not pass this trial.

## Sample size

The only observed `floor_ge1r` activation rate is 2 activations in 59 setups, which is 2 in 5 sessions, or 0.4 activations per session. A 95% Poisson interval on 2 events is about 0.24 to 7.22 activations per 5 sessions, or about 0.05 to 1.44 per session. Over the 60-session cap that interval is about 3 to 87 activations. The calendar forecast is too wide to promise a useful sample.

If the point rate repeats, 60 sessions produce about 24 activations, and 25 activations take about 63 sessions, so the session cap is the likely stop. If the low end of the interval is closer to the truth, 60 sessions produce a handful of activations.

What that can support:

- At 15 completed outcomes, a 50% win rate has a standard error of about 13 percentage points. If R outcomes have a standard deviation near 1, the standard error of mean R is about 0.26R.
- At 25 completed outcomes those figures are about 10 percentage points and 0.20R.
- Either size can show a very large, stable separation from zero. It cannot identify a modest edge, and it cannot authorize deployment.

What that cannot support: a claim that `n=15`, `n=25`, `n=30`, or `n=50` proves edge. Those numbers are collection and insufficiency bounds only.

If completed outcomes are below 15 when the stopping rule fires, the written result is **INSUFFICIENT SAMPLE**. No expectancy sentence is allowed. If completed outcomes are 15 or more, publish the metric table and the uncertainty. That publication is a measurement record.

## Decision rule

Runner `acceptance_criteria` and `rejection_criteria` are null on purpose. A mechanical `SUPPORTED BY THIS EXPERIMENT` label is not available, because that label was the coverage result of the closed trial and does not mean edge.

Allowed written results after the one look:

- `INVALID` — population, version, or missing-data rules were broken
- `INSUFFICIENT SAMPLE` — completed outcomes below 15
- `DESCRIPTIVE MEASUREMENT` — completed outcomes are 15 or more and the frozen table is published

This is **forward Stage A descriptive evidence**. It is not the final confirmatory edge test. It may support a description of the underlying R distribution for preregistered `floor_ge1r` activations, using `measured_entry_price = first_sight_price`, in the forward window.

A materially positive descriptive distribution can justify a later confirmatory trial with its own preregistration and an economic threshold chosen before that trial's outcomes are seen. This draft does not set that threshold.

This trial may not support edge, profitability, option expectancy, a production rule change, a watchlist expansion, SPXW enablement, deployment, or a comparison against `nearest_v1` or any new target.

## One-look rule

One scoring pass after the stopping rule. No parameter search. No second target. No redefinition of entry, stop, target, hold, or friction after the path is seen. Any follow-up, including Stage B, requires a new trial.

## Authority boundary

This draft registers the contract. It does not approve collection or scoring. The spec status remains `DRAFT`. No adapter is registered for `options_212c_floor_underlying_outcome`, so the current runner cannot execute this spec.

Do not start this trial's forward collection until the registration commit is on `main`. The first intended session is 2026-10-05, and that session counts only if its coverage collection has not already run before the merge. If 2026-10-05 is collected before this registration reaches `main`, do not slide the window forward after the fact. Stop and register again.
