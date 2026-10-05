# Preregistration — Options 30m 2-1-2 continuation pre-entry factor tables, companion to the floor-outcome forward study (2026-10-04)

<!-- trial_id: T-2026-10-04-prereg-options-212c-preentry-factors-2026-10-04-01 -->

**Status: SUPERSEDED — NOT APPROVED — NOT RUN.** Replaced by `E-2026-10-04-options-212c-floor-factor-01` / `T-2026-10-04-prereg-options-212c-floor-factor-2026-10-04-01` (floor-eligible population). Parent `-01` is also SUPERSEDED. 2026-10-05 was never admitted. Do not collect or score this trial.

**Trial ID:** `T-2026-10-04-prereg-options-212c-preentry-factors-2026-10-04-01`

**Experiment ID:** `E-2026-10-04-options-212c-preentry-factors-01`

**Parent trial (unchanged by this document):** `T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01` / `E-2026-10-02-options-212c-floor-outcome-01` (`docs/prereg-options-212c-floor-outcome-2026-10-02.md`).

This companion freezes, before the parent study's first eligible session (2026-10-05), which pre-entry factors will be tabulated against the parent study's scored outcomes, how each factor level is defined, and what may and may not be concluded. It adds no collection job, no provider call, no scorer, no file reader, no experiment adapter, and no change to the parent contract. It is a definitions seal.

## Question

Among the forward `floor_ge1r` activations that the parent study scores, do any preregistered pre-entry factor levels show a materially different TARGET_FIRST / INVALIDATION_FIRST / UNRESOLVED_AT_CLOSE composition?

This trial describes. It does not test a filter, and it cannot promote one.

## What this companion must not touch in the parent

Frozen as the parent registered them on `main` (#1115, `65847295521be1ff0d6b9ef89c8fb8699aff7735`):

- population: `STRAT_212_CONTINUATION`, exact 20-symbol V1 universe, eligible NYSE sessions from 2026-10-05, stopping rule 25 `floor_ge1r` activations (full threshold-crossing session included) or 60 sessions;
- activation: `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` at first sight;
- measured entry `first_sight_price`, stored invalidation, floor `target_1`, same-session horizon, no sizing, no friction model, no option P&L;
- scorer `options_212c_floor_outcome-v0.1` and its price-gap rules;
- sealed path record `options_212c_floor_outcome_path-v0.1`, `SESSION_SEAL_FIELDS`, `EPISODE_SNAPSHOT_FIELDS`, `manifest.jsonl`;
- blind-collection contract and the `study_readout` surface;
- one-look rule; written results `INVALID` / `INSUFFICIENT SAMPLE` / `DESCRIPTIVE MEASUREMENT`.

If any of those must change to run this companion, this companion is withdrawn, not the parent amended.

## Why the originally recommended factors cannot be primary factors

The operator brief recommended SPY alignment, QQQ alignment, hourly candle state (with `MISSING`), daily candle state, and `late_floor` / remaining-R eligibility as primary factors. Each is a **gate condition of activation** and is therefore constant inside the scored population:

| Recommended factor | Why it is constant among activations | Code |
|---|---|---|
| SPY directional alignment | `WOULD_OTHERWISE_QUALIFY` requires `alignment_ok`, which requires `spy_trend == desired_trend` | `alert_ranker/coverage_observer.py` `observe_symbol` failures list; `alert_ranker/coverage_outcomes.py` `gate_bucket` |
| QQQ directional alignment | same, `qqq_trend == desired_trend` | same |
| Hourly candle state | same, `hourly == desired_candle`; a `None` hourly fails the comparison, so `MISSING` can never activate | same |
| Daily candle state | same, `daily == desired_candle` | same |
| `late_floor` / remaining-R | `gate_bucket` returns `LATE_AT_FIRST_SIGHT` unless `late is False`; `_late` already encodes `remaining < MIN_REMAINING_RR` | `coverage_outcomes.gate_bucket`; `coverage_observer._late` |

A contingency table on a constant factor has one row. These five are therefore recorded as **gate-verification counts** (each must be 100% within activations; any other value is `INVALID` for the affected episode) and are not primary factors. Testing whether the alignment gate itself discriminates outcomes requires scoring episodes the gate rejected; the parent scorer cannot do that, so the contrast is UNAVAILABLE in this trial (see below).

## Primary factors (Tier 1) — frozen

At most five. Every factor is computed from fields already frozen in the parent's sealed episode snapshot or session seal, using information timestamped at or before `first_sight_at`. No bar after `first_sight_at` and no outcome field is an input.

| # | Factor id | Levels | Definition (pre-entry only) | Seal fields used |
|---|---|---|---|---|
| F1 | `direction` | `LONG`, `SHORT` | Stored episode direction. **Stratification variable only.** Every other table is reported pooled and per direction. Never a filter candidate. | `direction` |
| F2 | `blind_window` | `RETRACED`, `FLAT`, `EXTENDED` | `b = sign(direction) × (first_sight_price − entry_trigger) / structural_risk`, `sign = +1 LONG, −1 SHORT`. `RETRACED` when `b ≤ −0.25`, `EXTENDED` when `b ≥ +0.25`, else `FLAT`. The boundary is the existing constant `BLIND_WINDOW_MATERIAL_R = 0.25` in `alert_ranker/coverage_outcomes.py`; no other boundary may be substituted. | `direction`, `first_sight_price`, `entry_trigger`, `structural_risk` |
| F3 | `clock_bucket` | `10`, `11`, `12`, `13`, `14`, `15` (America/New_York hour) | Exchange-local hour containing `first_sight_at`. This is the parent's own preregistered `concentration_by_clock_bucket` definition reused verbatim. Levels are not merged after outcomes are seen; empty levels are reported as empty. | `first_sight_at` |
| F4 | `instrument_class` | `INDEX_ETF`, `SINGLE_STOCK` | `INDEX_ETF` = {`SPY`, `QQQ`, `IWM`, `TLT`}; `SINGLE_STOCK` = the other 16 V1 symbols. Fixed list; no reassignment. | `symbol` |
| F5 | `opening_bar` | `OPENING`, `LATER` | `OPENING` when the setup bar `first_bar_start` equals the session's regular open (`session_open` in the session seal, i.e. 09:30 America/New_York) **as parsed instants**, not as strings; otherwise `LATER`. Reuses the 2026-09-18 family-validation convention "opening bars excluded". | `first_bar_start`, `session_open` |

Secondary pre-entry descriptors, reported as distributions only and **not** tabulated against outcome: floor `target_1_r` at measured entry (continuous; no bucket is defined here), presence of `floor_target_2` (expected constant, geometry requires it), `first_sight_after_close` (expected constant `false`).

### Known mechanical confounds — declared before any outcome

- **F2 is geometrically tied to win rate.** With `b` the blind-window extension in R, the scored trade's stop distance is `1 + b` R and its target distance is `floor_rr_1 − b` R. A larger `b` means a closer target and a farther stop, so `TARGET_FIRST` frequency rises with `b` by construction while the win is smaller and the loss larger in R. The F2 table therefore **must** co-report, per level, mean `target_1_r`, mean stop distance in R, and mean completed R; the displayed comparison statistic for F2 is completed R, not win rate. A win-rate gradient across F2 levels is expected mechanically and is not evidence of discrimination. `floor_remaining_rr` and `target_1_r` are reparametrizations of the same quantity and are never a second factor.
- **F3 is tied to the UNRESOLVED class.** The horizon is the same session, so a later `first_sight_at` leaves fewer bars and more `UNRESOLVED_AT_CLOSE` by construction. `minutes_to_close_at_first_sight = session_close − first_sight_at` is recorded as a nuisance covariate and its median is reported per outcome class. Every table shows `WIN/(WIN+LOSS)` and the UNRESOLVED share side by side, never a single three-column rate. No clock-correlated factor (F3, F5) may appear in a discrimination sentence.
- **F1 is a regime proxy.** Because SPY and QQQ trend must match direction for every activation, the LONG/SHORT mix is set by the index regime during collection. Direction mix and per-direction counts are reported; **no cross-direction comparative sentence is permitted** in the written result.

### Excluded by name

The following are never inputs, factors, or covariates: `n_events`, `last_bar_start`, `run_id` (computed from bars after the first opportunity), any quantity derived from `bars`, any `out-v0.1` field, `spy_trend`, `qqq_trend`, `hourly_candle_type`, `daily_candle_type`, `alignment_failures`, `late_floor`, `floor_remaining_rr`, `target_1_r` as a factor.

## Gate-contrast control — UNAVAILABLE in this trial

The sealed record contains every structurally selected episode with its `gate_bucket_floor`, not only activations, so a contrast of `WOULD_OTHERWISE_QUALIFY` against `MARKET_ALIGNMENT_REJECTED` outcomes would be the natural test of whether the alignment gate does work. **It is not part of this trial.** The parent scorer `options_212c_floor_outcome-v0.1` refuses non-activated snapshots (`score_snapshot` raises `not_activated` for any `gate_bucket_floor` other than `WOULD_OTHERWISE_QUALIFY`) and its session walker scores activations only; `MARKET_ALIGNMENT_REJECTED` episodes are additionally returned before lateness and after-close are evaluated, so they may be unpriced or after-close. No outcome class for a non-activated episode exists in the parent's one look, and this document defines no scorer. Producing one would require a separately registered scorer extension; reading it here would violate the single-pass rule under "Outcome definition" and make this trial `INVALID`. Only gate-bucket **counts** per session are reported, as coverage context, with no outcomes attached, over the parent's five recognized levels (`UNSUPPORTED_FAMILY`, `TARGET_GEOMETRY_REJECTED`, `MARKET_ALIGNMENT_REJECTED`, `LATE_AT_FIRST_SIGHT`, `WOULD_OTHERWISE_QUALIFY`); the five counts must sum to the parent's `population_size` for that session or the session is reported as a count mismatch.

**Component-level alignment is UNAVAILABLE.** `EPISODE_SNAPSHOT_FIELDS` does not retain `spy_trend`, `qqq_trend`, `hourly_candle_type`, `daily_candle_type`, `late_floor`, or `floor_remaining_rr`. Recovering which component failed would require a separate companion record captured from the `cov-v0.1` first event on the same timer as the path seal, from the first eligible session. No such capture job exists, none is defined by this document, and none is authorized. If it is later built, it needs its own registration; component-level fields for sessions sealed before that are unavailable and are never reconstructed from a later observer run.

## Explicitly unavailable factors

Signa context, GEX, option-chain identity, bid/ask/spread, IV, delta, open interest, bar volume, and liquidity are **UNAVAILABLE** for this trial. None is in the parent seal; none is captured contemporaneously for `floor_ge1r` activations from 2026-10-05. They are not reconstructed later, and a later retrofit is a new trial.

## Outcome definition (parent's, restated, not changed)

Per activated episode, from the parent one-look only:

- WIN = `TARGET_FIRST`
- LOSS = `INVALIDATION_FIRST`
- UNRESOLVED = `UNRESOLVED_AT_CLOSE`
- EXCLUDED = `AMBIGUOUS`, `DATA_INVALID`

UNRESOLVED is never folded into WIN or LOSS. `close_r` for UNRESOLVED rows is reported as a separate distribution per factor level. Any episode whose outcome class comes from any source other than the parent's single scoring pass makes this trial `INVALID`.

## Analysis — fixed tables only

For each Tier 1 factor F2–F5: one table of counts `WIN / LOSS / UNRESOLVED / EXCLUDED` by factor level, pooled, then stratified by F1. Reported per cell: counts; and, only where the cell qualifies under the sample rule, `WIN / (WIN + LOSS)` with a Wilson 95% interval, and mean completed R.

Forbidden: any model fit; any interaction or two-factor table; any merge or split of levels after outcomes are seen; any threshold other than the frozen constants; selecting or ranking subgroups; computing additional factors; any p-value presented as a decision.

Multiplicity disclosure: exactly four primary outcome contrasts (F2–F5), each shown pooled and in two direction strata. Anyone who nonetheless computes a test must divide α by 4 and label the result descriptive.

## Sample rules — frozen

1. No table exists before the parent's `stop_condition_met` is true and the parent one-look is complete. This companion has no interim readout of its own, including factor marginals (for example a LONG count so far).
2. The companion publishes a factor table only when the parent's written result is `DESCRIPTIVE MEASUREMENT` (completed outcomes 15 or more). Parent `INVALID` or `INSUFFICIENT SAMPLE` is inherited verbatim and nothing else is published.
3. A factor level displays any rate (`WIN/(WIN+LOSS)`, UNRESOLVED share, mean completed R) only when that level has at least 10 completed outcomes **and** at least 5 decided (WIN+LOSS) outcomes; otherwise the level shows counts only. Displayed rates carry Wilson 95% intervals.
4. A comparative sentence between levels ("X did better than Y") requires every compared level to have at least 20 decided outcomes and an expected count of at least 5 in every cell under independence. **Under the parent's 25-activation cap this is unreachable; this companion is therefore counts-only and hypothesis-generating by design, and says so in its result.**
5. Tables F2–F5 are published in full or not at all. No level is merged, no table is dropped, no table is ranked as primary after the look.
6. `effective_clusters` = the number of distinct (`session_date`, America/New_York hour of `first_sight_at`) pairs with at least one completed outcome, and `max_episodes_per_cluster`, are required fields: simultaneous activations across symbols on one 30m bar share one index move and are not independent. No inferential test is run.
7. No level, cell, or contrast becomes a filter, scanner change, sizing rule, or watchlist change. The only permitted follow-up is a new preregistered prospective trial that names one hypothesis before any new outcome is seen.

## Written results

- `INVALID` — parent invalid, or any outcome class taken from outside the parent's one look, or any factor definition changed after 2026-10-05.
- `INSUFFICIENT SAMPLE` — parent completed outcomes below 15.
- `DESCRIPTIVE TABLES` — the frozen tables above, with sample-rule labels on every cell.

There is no `SUPPORTED` outcome. Winner discrimination remains **not proven** under every result of this trial.

## Governing distinction — carried verbatim

- Coverage improvement is proven: on the closed 59-episode window, `floor_ge1r` activated 2 episodes against 0 for `nearest_v1`.
- Profitable missed trades are not proven.
- Winner discrimination is not proven, and this trial can at most describe.

## Provenance and hash binding

- Factor definitions: this file, byte-frozen at the registration commit on `main`. The ledger `PLANNED` line cites this path; CI requires that commit to precede any evidence commit.
- Code constants the definitions depend on are pinned to `main` `d970b4072a772e3ba50d81bac5b152c41e3ffbe6`: `BLIND_WINDOW_MATERIAL_R = 0.25` in `alert_ranker/coverage_outcomes.py`, the `gate_bucket` ordering in the same file, and `first_sight` / alignment evaluation in `alert_ranker/coverage_observer.py`. A later change to any of them is an amendment that requires a new ledger line before the look; silently reading the new value makes this trial `INVALID`.
- Collector identity: the parent seal's `source.source_sha` is recorded per session in the companion artifact; a drift across sessions is reported, not repaired.
- Spec: `docs/research-experiment-specs/E-2026-10-04-options-212c-preentry-factors-01.json`; `python scripts/afs_experiment_runner.py validate --spec …` prints its `spec_hash`; that hash is recorded in the evidence artifact.
- Inputs: exclusively the parent's sealed session records, identified per session by the `manifest.jsonl` SHA-256 already defined by the parent. The companion evidence artifact lists `session_date`, parent seal SHA-256, and the parent one-look artifact SHA-256 it consumed. A record whose digest does not match is not an input.
- Prior exposure, disclosed: a 2026-09-18 descriptive family validation stratified 2-1-2 continuation 1R rates by SPY/QQQ/daily/hourly alignment on a retrospective 150-symbol corpus that contains the closed 59 episodes; the closed one-look exposed two activations, their identities, and their `out-v0.1` outcome class (both `UNRESOLVED_AT_CLOSE`), read on 2026-10-04 during the read-only inventory that preceded this document. That window (2026-09-09 to 2026-09-15) is ineligible for the parent and cannot enter this trial. No forward-window outcome has been seen. Tier 1 factors F2–F5 were chosen because they are not those already-exposed gate factors.

## Independent review before registration

A fresh-context red-team was given the operator's protocol text only (no outcome file, no closed-trial evidence) on 2026-10-04 and returned `PROTOCOL NEEDS REVISION`. Its report is unarchived: it exists only in the originating agent session and is summarized here; treat the summary as the author's, not as an independent artifact. Its blocker — every recommended factor is constant inside the activated population and none is in the seal — matches the finding above. Its major findings (F2 geometric confound, F1 regime proxy, F3/UNRESOLVED time confound, post-entry `ep-v0.1` fields, cell sizes under the 25 cap, inheritance of the parent verdict, code-constant pinning) are incorporated in the sections marked above. Its suggested factors `floor_target_2` presence and `floor_rescued` were not adopted: the first is constant because the floor geometry rejects a missing second target, and the second needs a companion capture record that does not exist and cannot exist before 2026-10-05.

A second fresh-context reviewer read the registration diff against the pinned code on 2026-10-04 and returned `REQUEST CHANGES` with one blocker: the earlier draft's Tier 2 gate-contrast asked the parent's unchanged scorer to score `MARKET_ALIGNMENT_REJECTED` episodes, which that scorer refuses by contract, while this document's own single-pass rule forbade any other source. Tier 2 was therefore removed and recorded as UNAVAILABLE above; the prior-exposure disclosure was completed; F5 equality was restated on parsed instants. That review is likewise unarchived outside the originating session.

## Authority boundary

This document registers definitions. It does not approve the parent, admit a forward session, write or read a seal, run a scorer, or produce a table. Spec status remains `DRAFT` and `approved_by` / `approved_at` null. No adapter is registered for `options_212c_preentry_factors`, so the runner cannot execute this spec. If 2026-10-05 is not sealed by the parent, this companion's window moves with the parent's re-registration and never independently.
