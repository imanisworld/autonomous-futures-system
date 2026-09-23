# Prereg Amendment 1 — Cross-Instrument Historical Expansion

**Parent prereg:** `docs/prereg-cross-instrument-historical-expansion-2026-09-17.md`

**Status:** SPEC ONLY / PAPER-RESEARCH ONLY / NO OUTCOMES AUTHORIZED

> **2026-09-23 provenance correction:** the statement below that a 2026-09-14 M2K live-feed check established the switch timing is superseded. The box did not observe the exact M2K U6→Z6 transition. Operator-captured TradingView Contract Switch markers now prove the date-level switches as **2026-06-16 M6→U6** and **2026-09-16 U6→Z6**; exact intraday/UTC timing remains unknown. The amendment's core rule is unchanged: the generic quarterly scheduler is not roll authority. See `docs/equity-index-tradingview-roll-provenance-2026-09-23.md`.

## Reason for amendment

The parent prereg correctly requires a fresh source probe before M2K corpus construction, but its readiness table can be read too strongly because the repository already contains a proven counterexample to treating the generic quarterly roll convention as live continuous-feed authority.

Merged PR #586 proved that on **2026-09-14** the repository's historical quarterly roll convention switched M2K earlier than the actual TradingView continuous feed. A complete `M2K1!` window could therefore straddle the real roll while appearing structurally clean if continuous-to-dated contract identity were inferred from the generic schedule alone.

## Frozen clarification

For this expansion study:

1. `sources/polygon_client.py` support for M2K means only that it can generate a **candidate quarterly dated-contract schedule**.
2. That schedule is **not** authoritative roll provenance and does not by itself satisfy X0.
3. Before any M2K X1 corpus build is admitted, X0 must independently prove the exact dated contracts used and each roll seam for the chosen historical provider/vintage.
4. Continuous symbols such as `M2K1!` / `M2K!` do not prove dated-contract identity. If exact contract identity cannot be established, classify the affected population `ROLL_PROVENANCE_UNKNOWN` and stop that root before parity.
5. Any proposed M2K roll rule must reconcile the known 2026-09-14 counterexample or explicitly show why that date is outside the study/source semantics. A generic `roll_days=N` convention is not sufficient proof.
6. MGC/MCL/MBT remain unchanged: all are still `BLOCKED_ON_ROLL_PROOF` until dated-contract selection and continuous-roll provenance are independently established.

## Effect on parent prereg

Interpret the parent prereg's M2K entries as follows:

- `READY_FOR_SOURCE_PROBE` remains correct.
- "existing quarterly equity-index roll machinery supports it" means **tooling can form a candidate chain**, not that the chain is validated.
- "M2K may use the existing quarterly equity-index roll machinery after a fresh source-coverage probe and manifest proof" means only after X0 separately proves exact dated-contract identity and roll seams; the scheduler itself cannot be the proof source.

All other parent-prereg gates remain unchanged: no outcomes, no P&L/expectancy, no tuning, no pooling, no execution eligibility, no deployment, and stop after source/roll proof + corpus integrity + live/replay parity.
