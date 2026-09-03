# Options Risk Concentration Telemetry

Phase 1 measurement only. This module does not choose concentration limits,
block trades, size positions, or change portfolio policy.

`options_manager.risk.measure_concentration()` accepts explicit exposure facts
and reports planned risk and full-debit concentration by ticker, direction,
sector, industry, expiration, DTE bucket, and index overlap. It also reports
the canonical correlation-group risk (see below).

## Authority boundary

`options_manager.validation.portfolio_risk_gate` is the only authority for
correlation-group aggregation. Its `PortfolioRiskResult.correlation_risk`
tuple (persisted as `RiskPlanSnapshot.correlation_risk`, projected by the
telemetry core as `RiskTelemetrySnapshot.correlation_risk`) already sums
planned dollar risk per group over the open positions plus the candidate at
evaluation time.

Concentration **reports** that tuple. It does not:

- accept a per-position correlation-group label and re-aggregate it;
- sum the tuples of different facts together (each tuple is already a
  whole-portfolio projection, so that would double count); or
- reconcile two disagreeing projections.

`exposure_fact_from_risk_telemetry()` copies the snapshot's tuple onto the
`ExposureFact.correlation_risk` field verbatim. `by_correlation_group` is
reported from the facts flagged `is_candidate=True`, because the gate's
projection attached to a candidate evaluation already covers every open
position. Tuples on non-candidate (already-open) facts are earlier projections
and are neither reported nor merged. If several candidate facts carry
different tuples the result is `INVALID` (`correlation_risk_conflict`). If a
canonical group carries more planned risk than the supplied facts total, the
result is `INVALID` rather than a share above 100%.

`correlation_risk_reported` is `True` only when a candidate fact supplied the
canonical tuple. Correlation buckets carry planned dollar risk and its share
only; the gate records nothing else per group and nothing else is invented.

## Fail-closed rules

- Non-finite, non-numeric, or negative planned risk / full debit -> `INVALID`.
- A total that overflows to `inf` after summing finite addends -> `INVALID`
  (`total_planned_risk_not_finite` / `total_full_debit_not_finite`), never
  `COMPLETE` with an infinite total and zero shares.
- Malformed canonical correlation tuples -> `INVALID`.
- Wrong types, missing ticker/expiration, invalid direction, non-positive
  contracts, or non-boolean candidate flags -> `INVALID`.

## Label normalization

Ticker, sector, industry, index-overlap and correlation-group labels are
stripped and upper-cased before bucketing, so `"Technology"` and
`"technology"` are one bucket named `TECHNOLOGY`. Two canonical correlation
groups that collide after normalization are `INVALID`, not merged. Expiration
strings are stripped only.

Unknown sector, industry, and index-overlap metadata is counted as unknown and
is never inferred. Index overlap is multi-valued, so overlap shares can sum
above 100%; that is evidence of overlap, not an allocation model.

Position count is telemetry only. There is no position-count rejection gate.
No risk threshold is selected or calibrated by this module.
