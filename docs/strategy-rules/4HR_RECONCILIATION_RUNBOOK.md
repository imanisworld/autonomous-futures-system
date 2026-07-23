# 4HR Re-Trigger Reconciliation Runbook

**Status:** tooling ready; evidence run blocked until the external dated sample
list and historical bars are supplied.

This workflow is offline and research-only. It does not import or modify the
strategy engine, execution, risk, configuration, environment, or deployment
paths. A passing reconciliation does not authorize paper or live trading.

## Required private inputs

Keep proprietary source data outside the public repository.

### Manual samples CSV

Required columns:

```csv
date,instrument,direction,expected_entry_trigger,expected_stop_reference,expected_target
2026-01-06,MNQ,LONG,0,0,0
```

The three expected price columns are optional. `date`, `instrument`, and
`direction` are required. There may be no duplicate instrument/date rows.

For the existing MNQ study, the expected count is 32. The original dated list
must be used unchanged.

### Historical bars

Supply separate JSON Lines files for 4-hour, 5-minute, and 1-hour bars. Each
line must contain:

```json
{"ts":"2026-01-06T09:30:00-05:00","open":1,"high":2,"low":0,"close":1}
```

`ts` must include a timezone. Epoch seconds or milliseconds are also accepted.
The data must span the full manual-study range, including the prior reference
sessions needed for the first evaluation date.

## Run

```bash
python3 -m research.reconcile_4hr_retrigger \
  --manual-samples /private/path/4hr_mnq_manual.csv \
  --bars-4h /private/path/mnq_4h.jsonl \
  --bars-5m /private/path/mnq_5m.jsonl \
  --bars-1h /private/path/mnq_1h.jsonl \
  --instrument MNQ \
  --start 2024-07-01 \
  --end 2026-06-30 \
  --expected-manual-count 32 \
  --output /private/path/4hr_mnq_reconciliation.json
```

Use `--exclude-date YYYY-MM-DD` once per known weekday market closure. Excluded
dates are recorded in the report and cannot contain a manual sample.

Exit status is `0` only when the gate passes, `2` when valid inputs produce a
failed reconciliation, and `1` for invalid or missing inputs.

## Gate

- True-positive rate must be at least 95%.
- False-positive rate must be at most 10%.
- Any direction mismatch fails the gate.
- Any supplied expected-level mismatch fails the gate.
- The MNQ manual row count must be exactly 32 when
  `--expected-manual-count 32` is used.
- Every non-excluded weekday must have the minimum 4H, pre-open 5-minute,
  9:30 AM, and 1H bar coverage needed to evaluate the setup.

Direction mismatches count as both a false negative and a false positive. This
prevents a same-date but opposite-direction signal from receiving credit.

Every mismatch must be classified as a data difference, rule-interpretation
difference, or bar-alignment difference. Do not edit the external sample to
make the detector pass.

## Scope guard

Run before committing:

```bash
python3 scripts/check_4hr_reconciliation_scope.py --base origin/main
```

The check fails if anything outside `research/`, `tests/`, `scripts/`, or
`docs/strategy-rules/` changed.

## Explicit next gate

Do not build Miyagi, build 3-2-2, add replay behavior, change runtime wiring, or
promote this strategy until the dated 4HR reconciliation passes.
