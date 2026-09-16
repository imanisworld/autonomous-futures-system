# Futures — Validated vs. Unvalidated (Status Pointer)

_As of 2026-09-16._

## Status of this document

The previous 2026-07-08 contents of this file are **retired as a current-state summary**. They remain available in Git history as historical evidence, but they are too stale to describe the September system safely.

The authoritative current handoff is:

- `docs/futures-current-state-handoff.md`

The evidence-classification inventory is:

- `docs/strategy-rules/Strategy_Inventory.md`

Do not use this file to infer current VPS deployment, enabled campaigns, environment pins, broker state, or strategy promotion status.

## Current verified repository facts

At GitHub `main` **`6bf3691b5b87e19f50b5e7d98133c0db81607cdf`**:

- cross-instrument observation transport and evidence-quality/provenance support are merged (#585–#588);
- the optional Discord observation route is merged (#590);
- failure/safety routing to the error channel is merged (#591);
- read-only missed-opportunity / why-no-trade reporting is merged (#592).

These are **repository facts only**. They do not prove the VPS is on that SHA or that the associated environment routes/campaigns are active.

## Current unvalidated / unresolved questions

The project does **not** currently have proof sufficient to call the trading system or a new strategy `VALIDATED` merely because the infrastructure exists.

The main active evidence questions are:

1. whether the MNQ Asian D+EMA forward-paper lane in draft #595 survives its independent audit and separate runtime/deployment proof; its latest follow-up closed the historical population-delta concern but did not authorize activation;
2. where useful market moves are being lost in the current signal pipeline — missing inputs, detector blindness, gate/classification suppression, or execution-only rejection;
3. whether the broad pre-signal precursor differences studied in #596 repeat across adequate independent samples without threshold tuning;
4. whether MES independently reproduces the D+EMA population under canonical IOC methodology: draft #598 now exists to generate that canonical MES cohort, with separate July–August and September runs plus roll-seam quarantine required before the MES leg of #596 can proceed;
5. whether BOS/MSS structure in #594 shows repeatable directional information in the preserved MNQ multi-month corpus; #594 remains a separate event study, not a strategy and not an automatic dependency of #596;
6. current box facts: deployed SHA, environment pins, feed health, campaign counts/outcomes, Discord route activation, positions/orders, and broker state.

## Current posture

**PAPER / OBSERVATION / RESEARCH ONLY. NO LIVE EXECUTION OR STRATEGY PROMOTION.**

No open research PR is deployment authority. No repository merge is box proof. Missing runtime evidence blocks activation.

For the exact current work sequence and hard boundaries, read `docs/futures-current-state-handoff.md`.
