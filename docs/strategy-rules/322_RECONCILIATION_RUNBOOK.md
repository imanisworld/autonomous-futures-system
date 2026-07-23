# 60M 3-2-2 First Live Reconciliation

## Scope

This gate validates the pure MNQ detector in
`research/detector_322_first_live.py`. It does not add replay, execution,
configuration, broker, or deployment behavior.

## Resolved result

Study range: 2024-07-02 through 2026-06-26.

- Prior reconstruction: 31 entries
- Detector positives: 32
- All prior entries recovered: 31/31
- Added executable-rule entry: 2024-08-30
- Resolved ground truth: 32/32, zero misses, extras, direction mismatches, or
  level mismatches
- Historical 10:00 gap-open entries: zero

The old 31-entry sample still passes the formal gate because its one
detector-only date is a 3.125% false-positive rate, below the 10% ceiling.
Ground truth is nevertheless updated to 32 because the discrepancy is a rule
definition difference, not detector looseness.

## 2024-08-30 discrepancy

The old reconstruction rejected the date because the 10AM candle traded above
the 9AM high before later breaking the short trigger at the 9AM low. That
reconstruction imposed an undocumented `invalidated_first` condition.

The executable rules define:

- 9AM 2U establishes a SHORT setup
- first 10AM break below the 9AM low is the entry
- stop is the 9AM high after entry

They do not say that a pre-entry touch of the future stop boundary voids the
setup. On 2024-08-30 the valid short trigger occurred later within the 10AM
hour, so the date belongs in the resolved ground truth.

## Gap-open contract

If the 10AM open is already beyond the trigger:

- the setup is valid
- `entry_price` is the 10AM open, not the 9AM boundary
- `gap_open` is `true`

No gap-open cases occur in the cached historical sample. Both LONG and SHORT
gap opens, plus the exact-at-trigger edge case, are covered by unit tests.

## Detector versus replay timestamp

The hourly detector proves that a break occurred within the 10:00–11:00 bar.
For non-gap entries, honest replay must use 5-minute/tick data to recover the
actual first-cross timestamp and apply realistic fills. The detector's
`entry_bar_ts` identifies the 10AM hourly entry bar, not an inferred intrabar
fill time.

## Pass criteria

- True-positive rate at least 95%
- False-positive rate at most 10%
- No direction or expected level mismatches
