# Options Paper V1 — Diagnostic Evidence Report

This report is analysis-only. It does not change setup admission, contract selection, DTE, stops, targets, sizing, alerts, or broker behavior.

Run it against the scanner SQLite database after the deployment smoke and during the forward evidence campaign:

```bash
python -m alert_ranker.v1_diagnostics logs/options_scanner.sqlite \
  --output logs/options_v1_diagnostics.json
```

## What it measures

For every `OPTIONS_PAPER_V1` shadow row, including ACTIVE and COUNTERFACTUAL lanes where contract evidence exists, the report preserves:

- option MAE and MFE from the recorded executable bid path relative to ask entry;
- underlying MAE and MFE from the diagnostic underlying snapshots;
- directional entry extension beyond the mechanical Strat trigger;
- option/underlying observation counts and maximum observation gap;
- entry quote age and maximum observed quote age;
- missing Greeks/IV and provider-error flags;
- ambiguous-resolution/path flags;
- event-risk state when present, otherwise `EVENT_RISK_UNKNOWN`;
- hold duration;
- three friction views of the same recorded trade.

## Entry-extension definition

The metric is directional and signed:

- LONG: `entry_underlying - setup_entry_trigger`
- SHORT: `setup_entry_trigger - entry_underlying`

Positive means the entry was already beyond the mechanical trigger. Negative means the captured entry price was still on the pre-trigger side and should be investigated rather than silently treated as a normal fill.

Both absolute and percent extension are reported.

## MAE / MFE definitions

Option MAE/MFE is based on the long-option premium path:

- MAE = how far the executable bid fell below the recorded ask entry;
- MFE = how far the executable bid rose above the recorded ask entry.

Underlying MAE/MFE is direction-aware:

- LONG adverse excursion is below entry and favorable excursion is above entry;
- SHORT adverse excursion is above entry and favorable excursion is below entry.

These are forward-observed snapshot excursions, not tick-perfect intrabar extrema. Snapshot-path ambiguity remains separately flagged.

## Friction scenarios

The canonical V1 fill model remains unchanged: ask entry, bid exit.

The report adds stress overlays only:

1. `RECORDED_EXECUTABLE` — ask entry / bid exit, no added fee.
2. `FEE_STRESS_065` — recorded executable result minus $0.65 per contract per leg.
3. `FEE_065_PLUS_1C_SLIPPAGE` — the same fee stress plus an extra $0.01/share adverse slippage on both entry and exit.

The two stress scenarios are sensitivity assumptions, not claims about the user's actual broker fee schedule. They must never be written back into the canonical V1 outcome.

## Evidence-quality labels

Each trade receives deterministic flags and a summary label:

- `HIGH` — no diagnostic flags;
- `MEDIUM` — telemetry is usable but has non-critical limitations such as missing Greeks/IV or explicitly unobserved intra-interval path;
- `LOW` — critical evidence defects such as a stale entry quote, large observation gap, missing entry/trigger data, sparse resolved path, missing exit bid, provider error, or ambiguous resolution.

Current reporting thresholds:

- expected scanner cadence: 5 minutes;
- observation gap warning: >12 minutes;
- stale entry quote warning: >120 seconds;
- resolved rows should have at least 2 valid bid marks.

These labels grade evidence quality only. They do not approve or reject a strategy.

## Strategy/timeframe summary

Rows are grouped independently by:

- ACTIVE vs COUNTERFACTUAL evidence lane;
- setup type;
- timeframe.

Each group reports:

- total rows and priced closed rows;
- wins/losses and win rate;
- 95% Wilson interval for win rate;
- mean dollar expectancy and an approximate 95% interval;
- profit factor;
- sequential closed-trade drawdown;
- total P&L under each friction scenario;
- HIGH/MEDIUM/LOW quality counts;
- median option MAE/MFE;
- median underlying MAE/MFE;
- median directional entry extension.

Sample labels are intentionally descriptive, not promotion rules:

- `<20` priced closed rows: `INSUFFICIENT`
- `20–49`: `EARLY`
- `50+`: `REVIEWABLE`

A `REVIEWABLE` label does not mean the strategy is proven. It only means the sample is large enough to begin a more serious review; expectancy stability, drawdown, timeframe consistency, execution quality, and out-of-sample behavior still matter.

## What this lets us answer later

Without changing V1 mid-campaign, the collected evidence can answer:

- whether the 25% premium stop is inside normal adverse excursion;
- whether entries are routinely late/extended;
- whether winners typically have substantially more MFE than the current exit captures;
- whether apparent expectancy survives fee/slippage stress;
- whether a setup/timeframe result is driven by low-quality telemetry;
- whether ACTIVE filters improve results versus COUNTERFACTUAL signals;
- which conclusions are still too sample-starved to trust.

No optimization should be made from a handful of rows. Diagnose first; any rule change becomes a separate policy/evidence cohort.
