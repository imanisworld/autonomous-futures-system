# Options Risk Concentration Telemetry

Phase 1 measurement only. This module does not choose concentration limits,
block trades, size positions, or change portfolio policy.

`measure_concentration()` accepts explicit exposure facts and reports planned
risk and full-debit concentration by ticker, direction, caller-supplied
correlation group, sector, industry, expiration, DTE bucket, and index overlap.

Unknown grouping metadata is counted as unknown and is never inferred. Index
overlap is multi-valued, so overlap shares can sum above 100%; that is evidence
of overlap, not an allocation model.

Position count is telemetry only. There is no position-count rejection gate.
No risk threshold is selected or calibrated by this module.
