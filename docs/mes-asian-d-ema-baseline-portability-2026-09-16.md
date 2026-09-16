# MES D+EMA canonical-baseline portability v2

## Verdict

**RESEARCH ONLY / AUDIT ONLY.**

This work creates the missing canonical MES D+EMA population required by the
Asian precursor audit. It does not change strategy, risk, broker routing,
session permission, Pine, runtime, deployment, or the VPS.

## Why this exists

The first PR #596 real-data proof stopped correctly because:

1. the proven candidate-level D+EMA WIN/LOSS work was MNQ-only; and
2. the preserved MES 5m corpus ended before the requested July/September
   periods.

This PR solves only the first problem on the repo side. Historical MES data
extension remains a separate local evidence step.

## Source-of-truth methodology

The preserved MNQ v3/v4 artifacts define the canonical D+EMA population as:

- cohort D = neither Pine `TRENDING` nor repo structural-trend classification
- shadow-candidate direction aligned with EMA/trend direction
- identical geometry de-duplicated once per observation day
- original candidate entry, stop, and target unchanged
- no daily trade cap for the isolated evidence population
- canonical repo `PaperBroker(entry_fill_model="ioc_limit")`
- decision-bar **close** passed to `PaperBroker.execute_bracket()` as
  `market_price`
- entry/decision bar never reused for bracket resolution
- one adverse tick of entry/stop slippage
- pessimistic stop-before-target when a later bar hits both
- original stop, static target, no breakeven, no runner
- unresolved opened positions become `EXPIRED` at observation-day rollover

The v3/v4 MNQ baseline reported 544 D+EMA candidates, 342 terminal results,
158 no-fills, and 44 expiries. That historical number is a reference for the
methodology; it is not a target MES must reproduce.

## Relationship to PR #593

This branch is stacked on PR #593 head:

`22a96b34fdfd4470014d708ba84d4588f9c74cdf`

#593 is reused for the representation/cohort mechanics that were already
reproduced independently:

- cohort assignment
- EMA-direction alignment predicate (`D0`)
- observation-day convention
- shadow-candidate geometry de-duplication
- journal/bar parsing helpers

**#593's first-future-touch IOC approximation is not used for MES outcome
resolution.** The canonical D+EMA source artifacts require the real repo
`PaperBroker` with decision-bar-close arrival pricing, so this adapter calls
`execution.paper_broker.PaperBroker` directly.

## Full population first, Asian slice second

The canonical MES producer emits **all D+EMA candidates across**:

- Asian
- London
- New York

This matches the MES validation plan, which requires session comparison before
any session is treated as interesting.

A separate `--precursor-session` output selects terminal WIN/LOSS rows for the
chosen session. It defaults to `asian` because PR #596 is the current consumer.
No-fill and expired rows remain visible in the full baseline and manifest; they
are never relabeled as losses.

## MES economics

The adapter resolves contract units from `config/futures_contracts.py` and
fails immediately if they differ from:

- tick size = `0.25`
- tick value = `$1.25`
- point value = `$5.00`

MES IOC tolerance is fixed to the repo-proven PaperBroker value:

- 16 ticks
- 4.0 points

It is not a tunable CLI parameter.

## Canonical PaperBroker call

For each selected shadow candidate:

1. Read the already-closed 15m decision bar.
2. Construct the original candidate bracket unchanged.
3. Create a fresh paper-only broker:
   - `entry_fill_model="ioc_limit"`
   - MES tolerance 16 ticks
   - 1 adverse slippage tick
   - `pessimistic_both_hit=True`
   - `breakeven_at_1r=False`
   - `runner_mode=False`
4. Call `execute_bracket(order, market_price=decision_bar.close)`.
5. `CANCELLED` becomes `NO_FILL`.
6. If the position opens, resolve it using only bars **strictly after** the
   decision bar through `PaperBroker.resolve_position()`.
7. If no exit occurs before observation-day rollover, label `EXPIRED`.

P&L R and baseline stop distance are normalized to the **actual IOC fill to the
original stop**, matching the v4 baseline-risk convention. Target geometry is
still the original target.

## Inputs

The CLI accepts either proven 15m shape:

- archived-study `bars_MES_*.jsonl` with `ts`; or
- canonical `polygon_to_replay.py` files `MES_*.jsonl` with `timestamp`.

Both are normalized internally. No operator rename/staging hack is required.

It also requires the matching MES `journal_*.jsonl` decision rows carrying the
normal shadow candidate output. The producer fails closed on conflicting
bar timestamps or conflicting duplicate journal decision rows.

The producer does **not** fabricate shadow candidates. If the historical replay
path does not produce the expected shadow population, stop and fix/prove the
upstream reconstruction rather than inventing rows here.

## Outputs

### Full baseline (`--out`)

Every D+EMA candidate across all three sessions:

- WIN
- LOSS
- NO_FILL
- EXPIRED

Rows retain candidate geometry, decision close, actual fill when one exists,
post-fill risk, exit information, cohort context, MES economics, and exact fill
assumptions.

### Precursor cohort (`--precursor-out`)

Terminal WIN/LOSS only for `--precursor-session`, normalized for PR #596:

- `candidate_id`
- `instrument`
- `strategy`
- `session`
- `direction`
- `signal_ts`
- `outcome_label`
- `baseline_pnl_dollars`
- `baseline_stop_ticks`
- `target_r`
- `source_variant`

### Manifest

Records:

- source methodology / #593 helper head
- input file hashes
- date range
- parse-skip count
- MES economics
- real PaperBroker configuration
- selected/terminal/no-fill/expired reconciliation
- per-session counts
- output hashes

## Required periods

Once trustworthy MES history exists, run independently:

1. `2026-07-13 .. 2026-08-31`
2. `2026-09-01 .. latest proven date`

Do not pool them before each period is separately reported. Each run must be
repeated and byte-identical before its Asian terminal cohort is sent to PR #596.

## Missing data prerequisite

This PR does **not** fetch market data. The prior proof established that the
preserved MES 5m corpus currently ends on 2026-06-26.

Any extension must be staged and preserved separately with:

- provider/source recorded
- requested date coverage
- file/row counts
- first/last timestamps
- per-file hashes
- duplicate/conflict checks
- continuity/gap review
- no overwrite of existing evidence

The existing repo path `scripts/polygon_to_replay.py` should be reused for
Polygon-derived replay candles; do not create a second converter.

## Safety boundary

`PaperBroker` is intentionally imported because it is the canonical offline fill
model being tested. `PaperBroker.is_live` is false and it performs no external
broker connection.

This producer does not import or call:

- Tradovate broker
- webhook runner
- order submission to an external service
- HTTP clients
- service/deploy controls
- environment mutation

No live, demo, or paper runtime order route is activated by this study.

## What this proves

A successful run can establish a reproducible MES D+EMA research population and
an explicit Asian terminal winner/loser cohort for precursor analysis.

It cannot validate MES, authorize MES execution, or justify a new gate by
itself.
