# Futures — Validated vs. Unvalidated (Status Pointer)

_As of 2026-09-16 13:53 EDT._

## Status of this document

The previous 2026-07-08 contents of this file are **retired as a current-state summary**. They remain available in Git history as historical evidence, but they are too stale to describe the September system safely.

The authoritative current handoff is:

- `docs/futures-current-state-handoff.md`

The evidence-classification inventory is:

- `docs/strategy-rules/Strategy_Inventory.md`

Do not use this file to infer current VPS deployment, enabled campaigns, environment pins, broker state, or strategy promotion status.

## Current verified repository facts

At GitHub `main` **`6bf3691b5b87e19f50b5e7d98133c0db81607cdf`**:

- cross-instrument observation transport is merged (#585);
- evidence-quality/provenance gating is merged (#586);
- fail-closed MGC/MCL campaign-OFF routing is merged (#587);
- release-manifest provenance + quality-gated status are merged (#588);
- deterministic historical test-fixture repair is merged (#589, test-only);
- the optional Discord observation route is merged (#590);
- failure/safety routing to the error channel is merged (#591);
- read-only missed-opportunity / why-no-trade reporting is merged (#592).

These are **repository facts only**. They do not prove the VPS is on `main` or that associated environment routes/campaigns are active.

## Current open research / audit facts

- **#593** — preserved MNQ missed-opportunity producer; deterministic real-data reproduction passed; exact-head CI green; audit/research only.
- **#594** — causal BOS/MSS first-retest event study; exact-head CI green; remaining proof is the preserved multi-month MNQ 5m event run and interpretation; not a strategy.
- **#595** — MNQ Asian D+EMA forward-paper cohort; exact-head CI green; historical population parity materially established; draft, default OFF, not activated.
- **#596** — matched Asian pre-signal precursor audit; exact-head CI green; remaining proof is the real preserved-corpus cohort run; MES requires #598 output first.
- **#598** — MES D+EMA portability producer; exact-head CI green; real deterministic two-period evidence runs and roll-seam quarantine still required before the MES leg of #596.
- **#597** — docs-only current-state refresh.

## Current unvalidated / unresolved questions

The project does **not** currently have proof sufficient to call a new strategy `VALIDATED` merely because the audit infrastructure exists.

The main active evidence questions are:

1. whether the MNQ Asian D+EMA forward-paper lane in #595 should ever be activated; green CI and historical parity are not forward evidence;
2. where useful market moves are being lost in the signal pipeline — missing inputs, detector blindness, gate/classification suppression, or execution-only rejection;
3. whether the pre-signal winner/loser differences studied in #596 repeat across preserved independent samples and chronological splits without threshold tuning;
4. whether MES independently reproduces the D+EMA population under canonical IOC methodology through #598 rather than merely echoing MNQ;
5. whether BOS/MSS structure in #594 shows repeatable directional information in the preserved MNQ multi-month corpus; #594 is separate and not an automatic dependency of #596;
6. current box facts: running SHA, environment pins, feed health, campaign counts/outcomes, Discord route activation, positions/orders, and broker state.

## Current posture

**PAPER / OBSERVATION / RESEARCH ONLY. NO LIVE EXECUTION OR STRATEGY PROMOTION.**

No open research PR is deployment authority. No repository merge is box proof. Missing runtime evidence blocks activation.

For the exact current work sequence and hard boundaries, read `docs/futures-current-state-handoff.md`.
