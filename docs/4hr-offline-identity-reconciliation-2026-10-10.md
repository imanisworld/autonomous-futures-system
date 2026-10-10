# Offline 4HR natural 1m / canonical 5m identity reconciliation

**2026-10-10 | source-only QA | NOT a trading experiment or permission to run an experiment.**

This extends [PR #1212](https://github.com/imanisworld/autonomous-futures-system/pull/1212) with a *pure, read-only and I/O-free* reconciliation helper. The underlying 5m full-arm identity sidecar is OFF by default; this helper does not enable it or read old/current evidence.

## Scope and guardrails

The only public API is `research.four_hr_identity_reconciliation.reconcile_identity_only(touches, five_rows)`. Callers provide arrays from an *explicitly authorized, unsealed, prospective cohort*. It does **not** discover or read files, access a broker, compute trade P&L, select strategies, update ledgers, or decide DEMO promotion. It preserves all input rows, duplicates, missing/mismatching identities and unmatched 5m candidates; it never chooses a better-looking result to resolve ambiguity.

A `MATCHED_IDENTITY_ONLY` result requires:
- one eligible natural `TRIGGER_TOUCH` with exactly one arm key in this batch, a true observer-only origin, causal `armed_available_at <= natural 1m bar open`, and `decision_time == 1m bar open + 1 minute`;
- one and only one corresponding 5m full-arm row under `wide_stop_4hr_5m_join_provenance_v2`, whose source open to decision close is exactly 5 minutes and whose source is explicitly non-executable;
- exact original arm key, direction, trigger, target and **MATCH** of asserted dated arm/1m/5m contracts.

Even a valid match reports 1m observation timestamp, 5m decision availability, which came first and whether structural stop *prices* are equal. It explicitly leaves both **entry/fill parity** and **outcome parity** as `UNPROVEN`. One minute versus five minutes and one-hour protective stop selection can produce a genuinely different entry.

Any duplicate 1m arm or multiple 5m source candidates for one arm is **AMBIGUOUS**, not resolved by timestamps or performance. Blocked/duplicate/non-touch events remain visible but excluded from eligible touches. Unknown contracts, source schema errors and invalid clocks are **UNMATCHABLE**. No archive backfills or historical sidecar reconstruction.

## Next authorized run, *only after collection approval*

1. Freeze a separate review-only observational cohort and its source fingerprints. Do not read sealed MGC/trading trial outcomes; do not reset the existing Oct 4 observer epoch.
2. Source real 1m natural touches and future 5m v2 sidecars from only authorized read-only paths. Treat missing 5m v2 provenance as **UNMATCHED**; do not infer it from legacy `candidate_key`.
3. Output counts for eligible/blocked/duplicate, 5m unpaired, matched identity, ambiguous, unknown contract, and all timing differences. Do not compute a profit factor or call DEMO-ready.
4. Have independent Claude/Codex breaker review the provenance, completeness, 1m/5m timing and any evidence-availability races. Operator approval is still necessary before enabling any evidence producer or DEMO route.

**Exact QA:** `pytest -q tests/test_four_hr_identity_reconciliation.py`, then full GitHub `pytest -q` on exact head. Existing #1212 remains the parent draft. This branch does not alter #1212 production-adjacent collector code.

## 2026-10-10 source-schema breaker addition

A saved 5m sidecar's `arm_key` is **not self-authenticating**. A reader that only compares the stored key to the 1m touch may accept a record whose `setup_bar_ts`, `four_am_bar_ts`, `direction` or `trigger` has subsequently changed. The read-only matcher now **reconstructs the canonical arm key from the independently recorded 5m fields** and refuses a disagreement with `FIVE_MIN_ARM_KEY_INCONSISTENT`. It also requires canonical source schema/kind/timeframe/strategy values. This checks internal evidence consistency; it is **not cryptographic provenance or external verification that the alert was natural**.

An integration regression now obtains a real `TRIGGER_TOUCH` via `context.one_min_trigger.evaluate_armed_4hr_touch` using synthetic seeded 5m bars and an observation arm. It matches this genuine emitted schema to a constructed, valid 5m v2 sidecar and asserts only `MATCHED_IDENTITY_ONLY`, with both entry/outcome parity explicitly `UNPROVEN`. This test **does not count toward prospective natural-touch gates**.

The new exact-head GitHub CI result is needed before reliance. Independent review and explicit authorization to start future-only collection remain separate blockers.
