# Asian-session pre-signal precursor audit v1

## Verdict

**RESEARCH ONLY / AUDIT ONLY.**

This study does not create a new strategy, relabel historical outcomes, alter
session permissions, relax gates, or change execution. It asks one narrower
question:

> Within the same instrument + strategy + Asian session + direction, what
> information was already present in the raw bars before baseline winners versus
> baseline losers?

The goal is to find descriptive precursor differences worth testing later,
without turning a one-off P&L result into a new rule.

## Why this is not duplicate work

The repository already contains strategy-specific replay studies, gate
counterfactuals, structural-level break/retest research, and the draft BOS/MSS
event study. None of those is a standardized matched winner/loser feature audit
that:

1. takes the original strategy/outcome labels as fixed inputs,
2. looks strictly backward 15/30/60/120 minutes,
3. keeps strategy/session/direction/instrument populations separate, and
4. reports feature differences and chronological-half stability without
   changing entries, stops, targets, fill rules, or risk gates.

## Input contract

Candidate input is JSONL or JSON and must explicitly contain:

- `candidate_id` — unique
- `instrument`
- `strategy`
- `session` — must be `asian`
- `direction` — `LONG` or `SHORT`
- `signal_ts` — offset-aware timestamp
- `outcome_label` — `WIN` or `LOSS`

The audit **never derives WIN/LOSS from bars or P&L**. This is intentional.
The caller must normalize the already-approved canonical baseline population
before running the audit.

Optional candidate fields are passed through and compared when numeric:

- `baseline_pnl_dollars`
- `baseline_stop_ticks`
- `target_r`
- `relative_volume`
- `source_variant`
- `source_file`

One run may contain only one instrument. Results are never pooled across roots.

## Canonical Asian session

The same server-side session boundary used by `webhook/state_builder.py` is
used for validation:

- Asian: 18:00–02:59 America/New_York
- London: 03:00–09:29
- New York: 09:30–16:59
- 17:00–17:59 maintenance halt

This audit accepts Asian candidates only.

## Bar timing and lookahead boundary

Historical Polygon replay bars are treated as 5-minute bars timestamped at the
**start** of the bar by default. A bar can contribute only after its close:

`bar_start + 5 minutes <= signal_ts`

The supported explicit alternative is `--bar-timestamp-mode close`.

Lookback windows are fixed at:

- 15 minutes
- 30 minutes
- 60 minutes
- 120 minutes

A window is `complete=true` only when every expected 5-minute bar is present at
the exact expected timestamp. Missing bars are not interpolated or padded.
Incomplete-window feature values stay unavailable and coverage remains visible.

## Pre-signal features

### Raw directional behavior

For each complete window:

- direction-signed net move in points
- fraction of candles whose bodies agree with signal direction
- path efficiency
- total high-low range
- mean true range
- mean candle-body size

### Volume / expansion

- mean volume
- volume ratio versus the immediately preceding equal-length window
- 15m / 60m true-range ratio
- 30m / 120m true-range ratio
- 15m / 60m volume ratio
- 30m / 120m volume ratio

These are continuous measurements. v1 does not tune a threshold for
"compression" or "expansion."

### Market-condition / trend transition

When the historical rows carry the existing fields:

- first / last `market_condition`
- TRENDING fraction
- DEAD / RANGE_DEAD fraction
- transition into TRENDING
- DEAD/RANGE_DEAD → TRENDING transition
- last `trend_direction`
- trend-direction alignment fraction
- last `trend_strength`
- existing `regime`, `structural_market_condition`, and
  `structural_direction` when present
- existing EMA 9/21/55 stack aligned with the candidate direction

The audit does not substitute one trend formula for another.

### Session location

From the Asian-session bars already observed before the signal:

- signed distance from session open
- session VWAP from causal OHLCV
- signed distance from the causal session VWAP
- signed distance from source-provided VWAP when the replay row already has one
- direction-adjusted position inside the session high-low range
- remaining distance to the session extreme in the signal direction
- session-open sweep/reclaim flag

Absolute session VWAP price is recorded in each feature row for provenance but
is deliberately excluded from cross-date winner/loser numeric comparisons.

### Prior-day levels / sweep-reclaim

When replay rows provide PDH/PDL:

- close minus PDH
- close minus PDL
- PDH sweep then close back below
- PDL sweep then close back above
- direction-matched sweep/reclaim flag

### Existing BOS/MSS context

When the historical input already carries the current repo fields:

- `bos_direction`
- `mss_direction`
- `market_structure`

the audit reports:

- number of structure events in each window
- direction-aligned / opposed event counts
- latest structure label
- whether the latest structure label aligns with the candidate
- explicit structure-data coverage

**Important:** v1 does not reconstruct missing BOS/MSS labels from raw bars.
If historical rows do not carry those fields, the audit reports no structure
coverage. That keeps this audit independent of draft PR #594. A separate
raw-bar BOS/MSS event study remains available only if this broader audit leaves
a meaningful unanswered structure question.

## Matched comparison contract

Every report population is partitioned by:

`instrument × strategy × session × direction`

No cross-strategy, cross-direction, cross-session, or cross-instrument pooling.

For numeric features the audit reports:

- WIN / LOSS coverage
- WIN / LOSS median
- WIN - LOSS median difference
- WIN / LOSS mean
- Cliff's delta (WIN versus LOSS)

For categorical / boolean features it reports WIN/LOSS value counts and
coverage.

No feature is automatically promoted, ranked, or converted into a gate.

## Chronological halves

Within each matched population, rows are sorted by `signal_ts` and split into
H1 / H2. The same numeric winner/loser comparison is reported independently for
each half.

This is a stability check, not a promotion threshold.

## Determinism and provenance

The CLI writes:

- `features.jsonl`
- `summary.json`
- `manifest.json`

The manifest records:

- candidate source path / SHA-256 / row count
- every input bar file path / SHA-256 / row count
- first / last bar timestamp
- bar count
- feature-row count
- output SHA-256 values

Repeat runs over the same inputs must be byte-identical.

## Safety boundary

`research/asian_precursor_audit.py` imports no:

- execution
- risk
- webhook runtime
- broker / PaperBroker / Tradovate
- HTTP client
- environment mutation
- service / deployment path

The script performs local read/write only.

No Pine, strategy, risk, broker, campaign, deployment, session permission, or
runtime file is changed by this study.

## Interpretation rule

This audit can identify **candidate separators**. It cannot validate a new
strategy or justify weakening an existing gate.

Any apparent separator must still survive, at minimum:

- useful sample size in both winners and losers,
- same-direction evidence in H1 and H2 rather than one-half concentration,
- no dependence on a single strategy/day/outlier cluster,
- a later causal rule specification frozen before trade simulation,
- realistic fill/cost testing if it ever reaches a bracket study.

Until then the output remains descriptive research only.
