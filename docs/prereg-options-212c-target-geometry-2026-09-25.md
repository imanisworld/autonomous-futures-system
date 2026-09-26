# Preregistration — Options 30m 2-1-2 continuation target-geometry ablation (2026-09-25)

**Status: PLANNED / DRAFT — NOT APPROVED TO RUN.**

**Trial ID:** `T-2026-09-25-prereg-options-212c-target-geometry-2026-09-25-01`

This preregistration formalizes the already-parked target-geometry ablation named
in `docs/options-current-state-handoff.md`. It does not invent a new strategy,
does not authorize execution, and does not change OPTIONS_PAPER_V1.

## Question

On the complete, outcome-independent retrospective population of **59**
`STRAT_212_CONTINUATION` 30-minute episodes from the fixed 20-symbol V1 universe
over 2026-09-09 through 2026-09-15, does replacing the current V1
**nearest-level target geometry** with the already-existing **>=1R floor target
geometry** increase the number of episodes that survive the complete
pre-option gate funnel?

The study is about **coverage attrition caused by target geometry**. It is not
an option-P&L or expectancy test.

## Frozen population

Include every episode satisfying all of:

- `family == STRAT_212_CONTINUATION`
- session date 2026-09-09 through 2026-09-15 inclusive
- symbol is in the exact 20-symbol V1 universe:
  `AAPL, AMZN, BAC, COIN, GE, GOOGL, INTC, IWM, JPM, MRK, MSFT, NFLX, NVDA, PLTR, QQQ, SPY, TLT, TSLA, WMT, XOM`
- episode identity is the existing `ep-v0.1` first-opportunity reduction
- membership is selected from structure/symbol/date only, never from forward outcome

Expected population size: **59 episodes**. Any other count is an integrity
failure and the experiment must not produce a research conclusion.

This is the same full structural 2-1-2 continuation population previously
reported as 59 of 835 retrospective structural episodes. That prior evidence
also reported 58/59 rejected by current target geometry and 48/59 rejected by
market alignment. Those counts motivate this controlled ablation; they are not
acceptance thresholds.

## Arms

### Baseline — `nearest_v1`

Use the stored `gate_bucket_nearest` path:

- target geometry = current V1 nearest structural level
- geometry is valid only when the existing nearest-target rule is valid
- market alignment, first-sight timing, after-close handling, and late-entry
  logic remain unchanged

### Candidate — `floor_ge1r`

Use the stored `gate_bucket_floor` path:

- target geometry = existing `find_targets(min_target_rr=1.0)` floor rule
- every non-target input is identical to baseline
- because the target changes, the existing floor-rule remaining-R / late check
  changes with it; that is part of the single target-geometry treatment

No target is invented and no threshold is optimized.

## Held constant

- the exact 59 episode identities
- symbol/session/direction
- trigger and invalidation
- first-sight timestamp and first-sight price
- SPY/QQQ/hourly/daily market-alignment inputs
- gate ordering outside the target-geometry-dependent fields
- 30-minute setup timeframe
- `cov-v0.1` / `ep-v0.1` / `out-v0.1` semantics
- no option contract selection, Greeks, premium stop, broker fill, or sizing
  model is introduced

## Dataset requirement

The execution adapter consumes the frozen coverage outcome aggregate carrying
the 59-member population. The authoritative manifest already pins the expected
raw dataset SHA-256 as:

`1963db73bccf0fd366eaaa077bb4e9582ed453ff220f1c5e789961096f3f113c`

The 34 MB member file itself is intentionally not tracked in the public repo and
is currently absent from this checkout. Before this spec may move from `DRAFT`
to `APPROVED`:

1. restore the exact `outcomes_2026-09-09_2026-09-15.json` bytes (or a
   provenance-preserving 59-row extract cut from those bytes before scoring);
2. verify the restored file against the manifest-pinned SHA-256 above;
3. make the adapter reproduce exactly 59 selected episodes;
4. pass the embedded date/version/provenance checks.

If any of those conditions cannot be proven, the run is blocked.

## Required metrics

- population size
- setups evaluated
- rejection counts by reason
- activation count / rate

`activation` means the stored geometry-specific gate bucket is
`WOULD_OTHERWISE_QUALIFY`. It does **not** mean an option order was entered.

No realized P&L, win rate, expectancy, drawdown, or option-return claim is
authorized by this experiment.

## Mechanical classification

Acceptance criterion:

`candidate.activation_count.count > baseline.activation_count.count`

Rejection criterion:

`candidate.activation_count.count <= baseline.activation_count.count`

Interpretation boundary:

- `SUPPORTED BY THIS EXPERIMENT` means only that the existing >=1R floor
  geometry rescues more full-funnel 2-1-2 continuation activations on this
  exact frozen population.
- `NOT SUPPORTED` means it does not.
- `INVALID EXPERIMENT` covers population/data/required-metric integrity failure.
- None of those labels proves edge, profitability, option expectancy, or
  production readiness.

## One-look rule

One approved execution against the frozen dataset. No parameter search, no
variant expansion, no redefinition of the population after results are seen.
Any follow-up requires a new registered trial.

## Authority boundary

This trial cannot modify scanner logic, options policy, risk, deployment,
broker capability, strategy status, or execution posture. Operator approval of
the linked experiment spec would authorize measurement only.
