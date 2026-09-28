# 3-2-2 exact-EOD resolver erratum — 2026-09-28

Status: **EVIDENCE CORRECTION / NO RERUN AUTHORIZED**

## Verified defect

PR #1057 fixed `scripts/edge_decomposition_audit.py::resolve_bracket` so a
day-only lane can no longer walk bars after the exact EOD slot on the same
America/New_York calendar date.

Before that fix, the resolver stopped when the ET date changed, but if the
exact 15:55 ET 5-minute bar was missing it could continue into same-date evening
Globex bars. That violates the frozen day-only contract: stop/target may resolve
through the exact EOD bar; if that exact bar is absent, the result fails closed
as `EOD_BAR_MISSING`.

Synthetic regression tests in #1057 cover the known early-close/same-date
evening shape, DST wall time, exact-EOD both-hit handling, and non-day-only
carry behavior.

## Evidence impact

The 2026-09-18 60M 3-2-2 pre-armed aggregate used the affected research
resolver. Therefore its published 33-resolved-win / +$2,709.66 three-tick
headline and H1/H2 totals are preserved as historical provenance but are not
treated as current validated evidence.

At least one historical row (2025-01-20) is known to have been resolved on a
same-date evening bar after the exact EOD bar was absent. This erratum does not
claim a corrected portfolio total, win/loss count, PF, or H1/H2 split.

## Current posture

- Corpus rerun: **HOLD**.
- No strategy, risk, broker, config, deployment, or execution change.
- No promotion authority is created.
- Current real-account stop-width / R:R incompatibility remains unchanged.
- Dated 2026-09-18 reports remain in the repository as sealed historical
  provenance; current authority is the Strategy Inventory plus this erratum.

A corrected aggregate may replace the historical headline only after a
separately authorized, rule-faithful rerun with preserved provenance.
