# Options Epoch-3 local audit handoff — 2026-09-29

## Purpose

This is the read-only local step needed to separate the current options cohort from older evidence.

It answers:

1. exact ACTIVE option P&L for the cohort named in `docs/options_v1_evidence_epoch.json`;
2. financial profit/loss counts derived from recorded `pnl_dollars`, not structural WIN/LOSS labels;
3. COUNTERFACTUAL observations grouped by their exact `counterfactual_filter_reason`.

It does not change scanner rules, alerts, targets, stops, risk, contract selection, universe, broker posture, or deployment.

## Why this is needed

The existing daily report has an all-time total across cohorts. That cannot answer whether `V1-EPOCH-3` itself is positive or negative.

The current epoch file defines `V1-EPOCH-3` as starting at:

- timestamp: `2026-09-21T19:23:01.426685+00:00`
- first shadow row: `options_shadow_journal.id = 9398`

Rows opened before that boundary stay in their earlier cohort even if they resolved later.

## Command

Run locally against a read-only copy of the real options scanner SQLite:

```bash
python ops/options_epoch_pnl_report.py \
  --db <path-to-options_scanner.sqlite>
```

For machine-readable output:

```bash
python ops/options_epoch_pnl_report.py \
  --db <path-to-options_scanner.sqlite> \
  --json \
  --output options_epoch3_audit.json
```

## Interpretation rules

- ACTIVE P&L is the cohort result.
- Financial profit/loss is based on recorded option P&L. A structural `WIN` can still be a financial loss.
- `TARGET_CONSUMED_AT_ENTRY` and `STOP_CONSUMED_AT_ENTRY` are non-outcomes and are excluded from P&L.
- COUNTERFACTUAL rows are grouped by the exact reason they were filtered out.
- COUNTERFACTUAL pricing is descriptive only. Those rows did not consume ACTIVE risk and are not a valid trade population or expectancy estimate.
- Cost model remains the scanner's recorded ASK-entry / BID-exit P&L with no commission.

## Gate

Do not claim an exact `V1-EPOCH-3` P&L until this is run against the real SQLite and the output is preserved.

Do not change filters because one counterfactual reason happens to show positive observed P&L. First require enough independent observations and a separately controlled test.
