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

Eligible sessions are NYSE regular sessions on or after **2026-10-05**. Collection stops after the earlier of these completed sessions:

- the session in which the `floor_ge1r` activation count, counted from `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` only, first reaches 25; or
- the 60th completed eligible NYSE session.

25 is a stop trigger, not an exact final-N requirement. The threshold-crossing session is included in full. If 24 activations already exist and the next session contains 3, the study includes all 27, and no session after that one is added. The same rule applies at the 60-session cap: that 60th session is included in full, and no later session is added. Activations are not dropped to force the count back to 25.

## Blind collection contract

Until the one look, the only human-visible study readout is the return value of `study_readout` in `ops/options_212c_floor_outcome_monitor.py`:

- `sessions_elapsed`: the count of completed eligible NYSE sessions
- `stop_condition_met`: `true` or `false`
- `stop_condition`: `null` while the stop is false; `activation_cap` when the internal count reaches 25; `session_cap` when 60 eligible sessions have elapsed; `activation_cap_and_session_cap` when both are true on the same readout

That object is the entire interim report. A favorable or unfavorable impression from sessions elapsed, or from the stop flag, is not a reason to change the rule.

The function may count activations internally. It reads `ep-v0.1` episode and gate fields: `family`, `symbol`, `session_date`, `direction`, `first_bar_start`, and `gate_bucket_floor`. Direction is part of the canonical identity and is not part of the readout. It does not open a path record, an `out-v0.1` outcome file, or any stored R or path field. Duplicate canonical identities count once. A row that lacks direction or any other identity field is not an activation.

Sessions are applied in date order. `sessions_elapsed` is the number of sessions inside the stopping window, through and including the threshold-crossing session or the 60th session. A later session passed to the function is outside the window and does not increase `sessions_elapsed`.

Before the one look, the readout and every other human-facing surface for this trial must leave hidden:

- the cumulative activation count, including the number 25, until the one look
- the daily activation count
- ticker identities
- activation dates or timestamps
- direction
- target, stop, or timeout classification
- MAE or MFE
- R outcome
- win, loss, timeout, ambiguous, and data-invalid counts
- any option-contract or P&L field

When the internal count reaches 25, the readout may say that the activation stopping condition fired. At 60 eligible sessions it may say that the session cap fired. It still does not print the activation count.

The ordinary coverage collector may keep writing its own files. Those files are not a study readout. Opening an ordinary outcome file, or opening a sealed path record, for this trial before the one look makes the trial `INVALID`. One scoring pass is allowed only after `stop_condition_met` is true.

## Frozen population

Include every episode satisfying all of:

- `family == STRAT_212_CONTINUATION`
- session date is an eligible NYSE session on or after 2026-10-05, inside the stopping window above
- symbol is in the exact 20-symbol V1 universe: `AAPL, AMZN, BAC, COIN, GE, GOOGL, INTC, IWM, JPM, MRK, MSFT, NFLX, NVDA, PLTR, QQQ, SPY, TLT, TSLA, WMT, XOM`
- episode identity is the existing `ep-v0.1` first-opportunity reduction, with canonical keys `symbol`, `session_date`, `direction`, `first_bar_start`, `family`, joined in that order as `episode_id`
- membership is selected from family, symbol, and session date

The 66-symbol candidate universe is not the population. Membership is not filtered on a path outcome, a target hit, or a P&L sign.

Expected structural rate, carried forward from the closed window only: 59 setups in 5 sessions, about 11.8 setups per session. That rate is a planning figure. A different count at the stopping boundary is a reported fact, not an integrity failure, because this window is prospective and its size is not frozen in advance.

## Setup and activation

Collection identity stays `cov-v0.1` for the observer and `ep-v0.1` for the episode reducer. `floor_ge1r` activation stays the existing gate. The Stage A score for this trial is a separate frozen scorer, `options_212c_floor_outcome-v0.1`.

`OPTIONS_COVERAGE_OUTCOMES / out-v0.1` remains the ordinary coverage outcome study. Its walk classifies a 5-minute bar from the bar's extremes: a bar that reaches both Target 1 and invalidation is `AMBIGUOUS`, and an invalidation touch becomes `INVALIDATION_FIRST`. That walk does not price an adverse gap at the bar open, and a completed row does not keep the decisive bar's open except on an ambiguous bar. Those semantics are the ordinary collector's semantics. They are not this trial's fill rule. Ordinary `out-v0.1` files may continue to be written. They are not the authoritative scored outcome for `T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01`.

`floor_ge1r` is the existing activation rule, unchanged:

- target geometry = `find_targets(min_target_rr=1.0)`
- activation = stored `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` at first sight
- that bucket already requires V1-supported family, valid floor geometry, market alignment, and a first-sight state that is not late and not after the close
- activation is not permission to pick a later or better fill

`nearest_v1` is not a second arm. No other target variant is added.

## Prospective path record

The Stage A scorer needs the decisive 5-minute bar open. It must be able to score from data captured during the forward window. A later historical refetch is forbidden.

Frozen path identity: `options_212c_floor_outcome_path-v0.1`.

Frozen location, one file per eligible session:

`logs/research_sealed/T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01/path-records/options_212c_floor_outcome_path-v0.1/<session_date>.json`

`sealed_path_record_relpath` in `ops/options_212c_floor_outcome_monitor.py` is that location. The directory is under gitignored `logs/`. It is not a study readout.

Each session file is one immutable seal of the non-bar episode snapshot and the 5-minute bars. The one-look reads only that file. It does not reopen the ordinary episode output, an `out-v0.1` row, or a later bar fetch.

Session fields, frozen as `SESSION_SEAL_FIELDS`: `path_record_version`, `trial_id`, `session_date`, `session_open`, `session_close`, `source`, `captured_at`, `episodes`. `source` is the provider name and the causal request window. It contains no credentials.

Each structurally selected `STRAT_212_CONTINUATION` episode on the V1 universe is one object in `episodes`. Its fields are frozen as `EPISODE_SNAPSHOT_FIELDS`:

- `episode_id`, formed as `symbol|session_date|direction|first_bar_start|family`
- `symbol`, `session_date`, `direction`, `first_bar_start`, `family`
- `reducer_version` (`ep-v0.1`)
- `gate_bucket_floor`
- `entry_trigger`
- `invalidation`
- `structural_risk`, copied at seal time from the episode `risk` field
- `first_sight_at`, `first_sight_price`, `first_sight_after_close`
- `floor_target_1`, `floor_target_2` (`floor_target_2` is null when the floor geometry has no second target)
- `bars`

`bars` holds `start`, `open`, `high`, `low`, `close` for exactly this grid: the first full 5-minute bar whose `start` is greater than or equal to `first_sight_at`, through the last bar whose end (`start` plus 5 minutes) is less than or equal to `session_close`, in time order. A bar that starts before `first_sight_at` is excluded. A bar whose end is after `session_close` is excluded. The file contains no precomputed outcome class, realized R, MAE, or MFE. The filename is the session date. It is not a ticker.

This draft defines that artifact. It does not add a capturing job, and it does not collect a session.

Integrity rule: once collection is approved, the capturing job writes the file once, after the session has settled and before any study readout, as canonical UTF-8 JSON with sorted keys and a trailing newline. It appends one `manifest.jsonl` line with `session_date`, byte length, and the SHA-256 of those exact bytes. The manifest line has no activation count and no outcome. The file is not rewritten. At the one look the scorer checks the hash, then scores from the sealed snapshot and the sealed bars only. A missing file, a hash mismatch, a missing snapshot field, a null `first_sight_price`, `invalidation`, `structural_risk`, or `floor_target_1` on an activated episode, or a 5-minute grid that is not exactly the rule above, is `DATA_INVALID` for the affected episode and is excluded from expectancy. Do not refetch historical bars or episode fields to fill that gap, and do not repair the record after any outcome has been viewed. Doing either makes the trial `INVALID`.

The study operator does not open these path records, or `manifest.jsonl`, before the one look. `study_readout` does not read them. Activation for the stopping rule comes from the episode gate, not from this artifact.

## Stage A — underlying path

Score only activated episodes, and score them once, with `options_212c_floor_outcome-v0.1`, after `stop_condition_met` is true. The only inputs are the sealed episode snapshot and the sealed 5-minute bars in the path record above. The scorer does not reopen a live episode file and it does not read an `out-v0.1` row.

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
| Missing bars | A missing sealed file, a hash mismatch, a missing snapshot field, or a 5-minute grid other than the first full bar with `start >= first_sight_at` through the last bar whose end is `<= session_close` is `DATA_INVALID` and is excluded from expectancy. Do not repair it by a later refetch |
| Price gaps | Deterministic rules below. No discretionary fill inside a gap |
| Same-bar ambiguity | One 5-minute bar whose range reaches both Target 1 and invalidation is `AMBIGUOUS`. It is not a win and not a loss |
| Fees and slippage | No dollar P&L is calculated. No extra slippage is applied on top of `first_sight_price`. R uses structural risk as the denominator and `measured_entry_price` as the entry |
| MAE / MFE | Adverse and favorable extremes versus `measured_entry_price`, divided by structural risk, on bars scored before the decisive event |
| R-multiple | See the price-gap rules. Timeout: `close_r` from the session close versus `measured_entry_price`. Ambiguous and data-invalid rows are excluded from mean, median, and expectancy |
| Classification | `TARGET_FIRST`, `INVALIDATION_FIRST`, `UNRESOLVED_AT_CLOSE`, `AMBIGUOUS`, `DATA_INVALID` |

### Price-gap rules

These rules belong to `options_212c_floor_outcome-v0.1`. They are applied to each sealed 5-minute bar in time order. The first matching rule ends the walk. "Beyond the stop" means the open is at or through invalidation in the adverse direction (`LONG` open `<=` invalidation; `SHORT` open `>=` invalidation). "Beyond Target 1" means the open is at or through Target 1 in the favorable direction. "Beyond Target 2" uses the same favorable test when Target 2 exists. "Reaches" means the bar's favorable or adverse extreme touches the level, including the open.

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

Report these after the one look. Activation count is not the result. The spec `required_metrics` lists every field below. Approval of this spec, and any scoring pass, stays blocked until `options_212c_floor_outcome-v0.1` emits each field and a validation check refuses to call the one look complete when any field is absent. The generic experiment runner's metric dictionary does not emit the study-only fields (`wins`, `losses`, `timeouts`, `ambiguous_count`, `data_invalid_count`, `timeout_rate`, `mean_r`, `mae_distribution`, `mfe_distribution`, `concentration_by_ticker`, `concentration_by_session_date`, `concentration_by_clock_bucket`, `outcome_concentration`). A generic run that lacks them is not a completed one look. That check is not implemented in this draft.

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

This draft registers the contract. It does not approve collection or scoring. The spec status remains `DRAFT`. No adapter is registered for `options_212c_floor_underlying_outcome`, so the current runner cannot execute this spec. Do not approve the spec, and do not score, until the `options_212c_floor_outcome-v0.1` metric check described above exists and fails closed on a missing preregistered field.

Do not start this trial's forward collection until the registration commit is on `main`. The first intended session is 2026-10-05, and that session counts only if its coverage collection has not already run before the merge. If 2026-10-05 is collected before this registration reaches `main`, do not slide the window forward after the fact. Stop and register again.
