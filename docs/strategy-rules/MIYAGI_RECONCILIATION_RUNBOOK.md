# 12HR Miyagi Detector Reconciliation

## Scope

This gate validates the pure `research/detector_12hr_miyagi.py` setup detector.
It does not add replay fills, strategy-engine wiring, configuration, execution,
deployment, or broker behavior.

## Ground-truth unit

The detector identifies a **valid setup at 9:30 AM ET**. It does not claim that
the Candle 3 midpoint is subsequently touched.

- Setup ground truth: the 1-3-1 pattern is present, the developing Candle 4 has
  not become cumulative outside before 9:30, and the 9:30 open is strictly
  above Candle 3 high (SHORT) or below Candle 3 low (LONG).
- Entry ground truth: a valid setup whose midpoint is later touched during the
  entry session. This belongs to fill replay, not detector reconciliation.

Do not mark a valid setup as a detector false positive merely because price
never returns to the midpoint.

## Resolved rule migration

The original MNQ n=13 study classified Candle 4 from its developing pre-open
range and then checked price against the midpoint. The resolved executable rule
classifies direction from the **9:30 AM open only**, strictly versus Candle 3
high/low. The two samples are therefore not interchangeable.

On the cached Polygon study range:

- Old n=13 entry sample versus resolved detector: 6 true positives, 7 misses,
  7 detector-only dates — expected failure due to incompatible definitions.
- Resolved setup ground truth: 13/13 true positives, zero misses, zero
  detector-only dates.
- Six of the 13 resolved setups later touched the midpoint and became entries.

## Pass criteria

- True-positive rate at least 95%
- False-positive rate at most 10%
- No direction mismatches
- No expected trigger/T1/T2 level mismatches

The authoritative machine-readable report is produced by
`research/reconcile_12hr_miyagi.py`.

## Fixed stop boundary

At the 9:30 reference time, only hourly bars whose completion timestamp is at
or before 9:30 are eligible. For top-of-hour bars, the latest completed candle
is 8:00–9:00. The 9:00–10:00 candle is still forming and must never supply the
reference stop.
