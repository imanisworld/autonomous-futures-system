# Preregistration — Options 30m 2-1-2 floor-eligible pre-entry factor description (2026-10-04)

<!-- trial_id: T-2026-10-04-prereg-options-212c-floor-factor-2026-10-04-01 -->

**Status: DRAFT — NOT APPROVED — NOT RUN.**

**Trial ID:** `T-2026-10-04-prereg-options-212c-floor-factor-2026-10-04-01`

**Experiment ID:** `E-2026-10-04-options-212c-floor-factor-01`

**Supersedes:** `E-2026-10-04-options-212c-preentry-factors-01` / `T-2026-10-04-prereg-options-212c-preentry-factors-2026-10-04-01` (activation-only tables; never approved, never collected).

**Companion to:** `T-2026-10-04-prereg-options-212c-floor-outcome-2026-10-04-01` / `E-2026-10-04-options-212c-floor-outcome-02`

This draft is **descriptive / hypothesis-generating only**. It does not claim that any pre-entry factor discriminates winners, proves edge, or authorizes a rule change. It freezes how floor-eligible first-sight paths will be stratified at the same one-look as the primary Stage A study, using labels derived at score time from contemporaneous fields sealed before any outcome is viewed.

It does not retune `floor_ge1r`, search interactions, optimize thresholds, retrofit Signa/GEX, change `OPTIONS_PAPER_V1`, expand the watchlist, enable SPXW, or authorize a paper or live order.

## Why a companion population

On `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` activations, SPY/QQQ/hourly/daily alignment and `late_floor == false` are already enforced by the gate, so those factors are constant and cannot explain winners. The companion therefore scores the **floor-eligible** population, where those factors vary:

`family == STRAT_212_CONTINUATION` ∧ V1 universe ∧ `floor_geometry_ok` ∧ `first_sight_after_close == false` ∧ `late_floor == false`

which corresponds to `gate_bucket_floor ∈ {MARKET_ALIGNMENT_REJECTED, WOULD_OTHERWISE_QUALIFY}`.

Direction is a stratifier only, not a primary factor.

## Window and seals

Same forward window, same stop, and same `options_212c_floor_outcome_path-v0.2` seals as the primary trial. The eligible start is **UNSET** until a pre-collection amendment of both specs fixes it to the first NYSE session strictly after the approved real capture path is merged to `main` **and deployed**. Sessions through **2026-10-05** inclusive are ineligible and are never backfilled.

The companion does not write a second seal. It reads the primary session files. One scoring pass for both trials after the primary `stop_condition_met` is true.

Until that look, the only human-visible study readout remains the primary `study_readout`: `sessions_elapsed`, `stop_condition_met`, `stop_condition`, `advance_refused`. The companion adds no extra interim surface.

## Frozen factors

Raw fields sealed on path-v0.2 (copied from the first `cov-v0.1` event, never reconstructed later):

- `spy_trend`
- `qqq_trend`
- `hourly_candle_type`
- `daily_candle_type`
- `alignment_failures`
- `floor_remaining_rr`
- `late_floor`

Labels are derived at score time by `options_212c_floor_factor-v0.1` and are never stored in the seal:

| Factor | Labels |
|---|---|
| `spy_alignment` | `ALIGNED` / `NOT_ALIGNED` / `MISSING` (`MISSING` when `spy_trend` is null) |
| `qqq_alignment` | `ALIGNED` / `NOT_ALIGNED` / `MISSING` |
| `hourly_alignment` | `ALIGNED` / `NOT_ALIGNED` / `MISSING` (`MISSING` when `hourly_candle_type` is null; missing is explicit and is not treated as aligned) |
| `daily_alignment` | `ALIGNED` / `NOT_ALIGNED` / `MISSING` |
| `remaining_r_bucket` | `LATE` / `ge1_lt1p5` / `ge1p5_lt2` / `ge2` / `MISSING` — edges from existing `MIN_REMAINING_RR = 1.0` and fixed half-R steps, not optimized |

Desired trend/candle follows stored direction (`bullish`/`two_up` for LONG, `bearish`/`two_down` for SHORT). No ML, no interaction cells, no ranking, no Signa/GEX retrofit, no retrospective outcome mining.

## Scoring

The walk is the unchanged primary scorer `options_212c_floor_outcome-v0.1` (`measured_entry_price = first_sight_price`; same gap/stop/target/timeout/`DATA_INVALID` rules; Target 2 recorded, never an exit). Membership, not the walk, differs.

Stage B option fills are **NOT EVALUATED**.

## Metrics

After the one look, publish:

- the primary `required_metrics` table over the floor-eligible population (`overall`)
- the same table per `gate_bucket_floor` and per direction
- the same table per primary-factor label
- the same split by direction inside each factor label

Denominator rule, frozen: `population_size` and `setups_evaluated` count floor-eligible episodes only. `activation_count` counts `gate_bucket_floor == WOULD_OTHERWISE_QUALIFY` inside that population. Activation rate is activations / floor-eligible population. W/L/timeout/R describe every scored floor-eligible row, including `MARKET_ALIGNMENT_REJECTED`. The same rule applies inside each factor, direction, and gate stratum; a `MARKET_ALIGNMENT_REJECTED` cell has `activation_count` 0.

A cell with fewer than 5 completed outcomes reports counts only (`suppressed: true`, `metrics: null`). No numeric threshold is an acceptance criterion.

The generic experiment runner does not emit `by_factor`, `by_factor_and_direction`, `by_gate_bucket_floor`, or `by_direction`. A generic run that lacks them is not a completed one look. The one-look adapter must independently verify the manifest SHA-256 of each sealed file and enforce **INSUFFICIENT SAMPLE** (overall completed outcomes below 15) / **DESCRIPTIVE MEASUREMENT** (15 or more).

## Decision rule

Runner `acceptance_criteria` and `rejection_criteria` are null. Allowed written results: `INVALID`, `INSUFFICIENT SAMPLE`, `DESCRIPTIVE MEASUREMENT`. A table that looks like winner discrimination is still only a description. This trial may not support edge, profitability, a production rule change, watchlist expansion, SPXW enablement, or deployment.

## Authority boundary

DRAFT registration only. Not collection and not scoring. Do not approve, admit a forward session, or score real data until the primary capture path is independently reviewed, on `main`, and deployed. 2026-10-05 is ineligible. Do not backfill. The closed 59-episode trial stays closed.
