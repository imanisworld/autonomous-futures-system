# Cross-instrument evidence quality v1

Status: **default-passive / evidence-only**. Parent transport: PR #585 at frozen
head `091f97d1682f61cee8ac298bc1f9dfdb3b7b35df`.

Core rule: **bad evidence stays visible but cannot count as proof.**

This layer does not create signals, alter brackets, change a strategy, route an
order, grant execution eligibility, or rewrite raw evidence. It reads the
`cross_instrument_observation_v1` evidence and BarHistory and produces a
quality-gated review status.

## Authority

`ops/cross_instrument_observation_report.py` uses
`execution.cross_instrument_evidence_quality.build_quality_report`.

The raw campaign report remains available as an implementation detail and its
raw counts remain visible, but **raw READY FOR REVIEW is informational only**.
Only `quality_eligible_terminal_outcomes` and
`quality_distinct_terminal_days` may satisfy the 30-outcome / 10-day gate.

No quality result grants strategy, risk, paper-broker, Tradovate, or live
execution permission.

## Quality statuses

A terminal row is `VALID` only when all currently-proven checks pass. Otherwise
it stays in the journal but is blocked from the review gate. Blocking reasons
include:

- `DATA_GAP_CONTAMINATED` — an expected 15m bar is absent inside the detector or
  signal-to-resolution window.
- `ROLL_CONTAMINATED` — more than one exact dated source contract appears inside
  the evidence window.
- `ROLL_PROVENANCE_UNKNOWN` — roll safety cannot be proven. A continuous ticker
  such as `M2K1!` does not identify which dated contract supplied each bar, so
  continuous MNQ/MES/M2K/MGC/MCL/MBT evidence remains here until exact contract
  identity is proven by another source.
- `CODE_PROVENANCE_UNKNOWN` — no usable generating release SHA is attached.
- `DETECTOR_PROVENANCE_UNKNOWN` — detector identity/dependency window cannot be
  reconstructed.
- `TIMEFRAME_PROVENANCE_UNKNOWN` — the row cannot prove a 15m source.
- `CALENDAR_PROVENANCE_UNKNOWN` — product-session expectations cannot be
  established.

`MBT` structural populations also carry the population-level blocker
`MBT_OUTCOME_HORIZON_UNPROVEN`. The 24/7 product has no proven strategy outcome
horizon yet, so even individually clean terminal rows cannot make the MBT
structural population review-ready. No replacement horizon is invented here.

## Continuity

The quality gate reconstructs the detector dependency window conservatively
from up to the last eight recorded 15m bars ending at the signal. It then checks
every expected 15m bar open through the terminal exit.

Expected-bar logic uses the product-aware feed calendar so known maintenance,
weekend closures, and the equity-index 16:15–16:30 ET halt do not become fake
gaps. Holiday and early-close exceptions are **not guessed**. A false-positive
quality block is acceptable; falsely clean evidence is not.

Missing bars are never fabricated or automatically backfilled by this layer.

## Contract / roll provenance

`BarHistory.record` has an optional `source_ticker` field. Existing callers that
do not provide it retain the legacy row shape. The collection-only observation
transport writes the exact incoming TradingView ticker for new 15m bars.

Rules:

- one stable **dated** contract through the evidence window: roll provenance can
  be clear;
- more than one exact dated source contract: `ROLL_CONTAMINATED`;
- any continuous ticker (`*1!` / `*!`): `ROLL_PROVENANCE_UNKNOWN` unless exact
  underlying contract identity is independently proven;
- missing per-bar source ticker: fail closed as unknown.

The repo's historical quarterly roll convention is not treated as proof of the
live continuous feed's switch time. The 2026-09-14 M2K live-feed check showed the
continuous switch later than that convention, so using the helper as a quality
certificate could produce false-clean evidence.

### Current conservative limitation

The collection-only path now preserves exact source ticker. The existing
MNQ/MES trading-path BarHistory writer does not yet attach that metadata, so
new cross-instrument MNQ/MES outcomes can remain roll-provenance-blocked until
that additive provenance field is wired through the already-existing runner.
That is a quality limitation, not permission to infer a contract from the root.

## Code / detector provenance

Rows must carry a usable `generating_git_sha` from the deployed release
environment. `unknown` never counts.

Detector identity is read from the frozen campaign population config. Detector
input timestamps are reconstructed from the 15m BarHistory window when explicit
per-row dependencies are unavailable. The reconstruction is intentionally
conservative.

## Review semantics

Raw counts are never discarded. Each population reports:

- raw terminal outcomes;
- quality-eligible terminal outcomes;
- quality-blocked terminal outcomes;
- quality-eligible distinct days;
- quality issue counts;
- population-level quality blockers;
- raw status and authoritative quality status.

A population can be `READY FOR REVIEW` only when:

1. it has at least 30 **quality-eligible** WIN/LOSS outcomes;
2. those outcomes span at least 10 distinct observation days; and
3. it has no population-level quality blocker.

Thirty dirty rows cannot satisfy the gate.

## Regression scope

`tests/test_cross_instrument_evidence_quality.py` covers:

- a complete exact-dated M2K window qualifying;
- a complete continuous M2K window spanning the observed 2026-09-14 live roll
  failing closed as `ROLL_PROVENANCE_UNKNOWN`;
- an unexplained missing 15m bar blocking the sample;
- the equity-index 16:15–16:30 ET halt not becoming a false gap;
- continuous MGC failing closed while exact contract identity is unproven;
- an exact contract switch inside a window being roll-contaminated;
- unknown release SHA blocking evidence;
- MBT's structural outcome-horizon population blocker;
- 30 raw dirty outcomes being unable to satisfy the quality gate;
- source ticker being additive to BarHistory without changing legacy row shape.

## Explicitly not solved here

- actual TradingView alert creation or live bar-arrival proof;
- continuous-to-dated contract identity for any root;
- MGC/MCL/MBT historical continuous-contract schedules;
- MBT strategy outcome horizon;
- holiday/early-close calendar completeness;
- commission/slippage economics;
- strategy rules or instrument-specific risk;
- broker routing or execution;
- deployment or campaign activation.

This layer is designed to make those unknowns visible rather than silently
turning them into evidence.
