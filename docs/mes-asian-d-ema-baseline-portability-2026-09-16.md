# MES Asian D+EMA baseline portability v1

## Verdict

**RESEARCH ONLY / AUDIT ONLY.**

This work creates the missing canonical MES baseline required before PR #596 can
compare Asian-session winners versus losers. It is not a new strategy and does
not change any runtime rule.

## Why this exists

The first #596 proof stopped correctly because two prerequisites were absent:

1. the proven D+EMA canonical WIN/LOSS producer was MNQ-only;
2. the preserved MES 5m replay corpus ended before the requested July/September
   periods.

This PR addresses only prerequisite (1): make the already-proven #593 D0/D+EMA
research population mechanically portable to MES with explicit MES economics.
It does **not** fetch or extend historical data.

## Parent proof

This branch is stacked on PR #593 exact head:

`22a96b34fdfd4470014d708ba84d4588f9c74cdf`

The parent producer was independently reproduced against its preserved MNQ
snapshot and matched the archived study. This MES adapter intentionally leaves
that proven MNQ code unchanged.

## Canonical MES population

The emitted baseline is exactly:

- instrument = `MES`
- 15-minute decision rows
- session = `asian`
- cohort D = Pine `market_condition != TRENDING` and repo structural condition
  is not `STRUCTURAL_TREND_UP` / `STRUCTURAL_TREND_DOWN`
- candidate direction must equal the existing trend/EMA direction
- source candidates are the already-journaled `shadow_candidates`
- dedupe identity is unchanged from #593:
  `observation_day × strategy × direction × entry × stop × target`
- source candidate entry/stop/target geometry is not rewritten

The D+EMA predicate is retrieved directly from #593's D0 variant rather than
re-authored as a second semantic implementation.

## MES economics

The adapter resolves contract units through `config/futures_contracts.py` and
fails immediately if the repo's proven metadata is not:

- tick size = `0.25`
- tick value = `$1.25`
- point value = `$5.00`

IOC tolerance is pinned to the repo's existing PaperBroker MES proof:

- 16 ticks
- 4.0 points

It is not a CLI tuning parameter.

## Preserved fill / resolution assumptions

The only instrument substitutions from #593 are MES root/economics/tolerance.
The research fill model otherwise remains:

- first forward bar that touches planned entry is the fill opportunity
- adverse one-tick entry slippage
- one-tick adverse stop slippage
- clean target fill
- fill-bar stop is pessimistic even if target also trades
- later same-bar stop + target resolves stop first through the existing
  observation resolver
- no configured commission is invented
- `EXPIRED` means entry filled but remained unresolved before observation-day
  rollover and is excluded from terminal WIN/LOSS P&L

This is a portability reproduction model, not a claim that every historical
candidate would have received a live broker fill.

## Inputs

The CLI requires a proven local snapshot containing:

- `bars_MES_*.jsonl` with 15m bars
- `journal_YYYY-MM-DD*.jsonl` carrying MES 15m decision rows and their original
  shadow candidates

The requested `--start-date` / `--end-date` select journal files. Malformed
journal JSON fails closed unless the operator explicitly uses the compatibility
flag `--allow-journal-parse-skips`.

The tool does not create the upstream journal or fetch market data. If a MES
replay/journal snapshot does not exist for the desired period, the study stays
blocked.

## Outputs

### Full baseline

`--out` writes every selected Asian D+EMA candidate, including:

- WIN
- LOSS
- NO_FILL
- EXPIRED

Each row contains source geometry, provenance state, MES economics, IOC
assumptions, and a deterministic candidate ID.

### PR #596 cohort

`--precursor-out` writes **terminal WIN/LOSS only** in the explicit input format
required by PR #596:

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
- `source_file`

NO_FILL and EXPIRED are never relabeled as losses; they remain visible in the
full baseline and manifest but are not sent to the winner/loser precursor audit.

### Manifest

The manifest records:

- exact parent #593 head
- date range
- input file hashes
- journal parse skip count
- selected/terminal/WIN/LOSS/NO_FILL/EXPIRED reconciliation
- MES economics
- IOC/slippage assumptions
- both output hashes

## Required two-period proof

Once trustworthy MES data exists, run two independent baselines:

1. historical: `2026-07-13 .. 2026-08-31`
2. September: `2026-09-01 .. latest proven baseline date`

Do not pool the inputs, outputs, manifests, or precursor conclusions.

Each period must be run twice with byte-identical baseline and precursor JSONL
hashes before #596 consumes it.

## Missing data prerequisite remains separate

This PR does **not** authorize or perform a Polygon data fetch. The earlier proof
showed the preserved MES 5m corpus ends at 2026-06-26. Extending and preserving
MES 5m history must be done separately with:

- source/provenance recorded
- exact date coverage
- row/file counts
- per-file hashes
- continuity checks
- no silent mixing with scratch/live bars

Only after that data foundation exists should the normal historical replay path
create the 15m bars/journal snapshot consumed here.

## Safety boundary

No existing runtime file is modified.

The adapter imports the proven #593 offline research producer and therefore
indirectly reuses `execution.cross_instrument_observation._resolve_one` for
pessimistic outcome resolution. It does not import or call:

- PaperBroker order submission
- Tradovate
- webhook runner
- DecisionEngine execution path
- service control
- deployment
- HTTP/network clients
- env mutation

No session permission, Pine logic, strategy gate, risk rule, campaign, broker,
or VPS state is changed.

## What this can prove

A successful MES run can establish a reproducible **baseline population** for
subsequent winner/loser precursor research.

It cannot, by itself, establish a new edge or approve MES execution.
