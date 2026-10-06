# Canonical prospective signal + outcome evidence (2026-10-06)

Code:
- `options_evidence/signal.py`
- `options_evidence/capture_adapter.py`
- `options_evidence/outcome.py`

Builds on `options_evidence/strategy_epochs.py`.

This is a schema, validation and read-only adapter. It has no provider, no file writer, no runtime wiring and no authority.

> **Integration pass (post-#1145 `ae8c897` / #1146 `445393f`).** The first draft
> of this module was written while #1145 was not on GitHub. It assumed levels
> and direction were part of the identity. That assumption is gone. The model now follows the merged
> setup-capture observer exactly, and the adapter consumes the #1145 journal instead of a
> speculative format.

## Source of truth

#1145 (`alert_ranker/setup_capture*.py`, journal
`/root/afs-shared/logs/options_setup_capture.jsonl`) owns:
- detection
- WATCHING persistence across sessions
- dedupe
- late and gap classification
- SPX observation

The canonical layer **links** to those records. It does not re-detect or reclassify anything, and it does not copy the journal.

## Identity

| Id | Value | Meaning |
|---|---|---|
| `structure_id` | **exactly** the #1145 `structure_key`: `TICKER|timeframe|structure_close(UTC Z)|pattern` (parity test against `alert_ranker.setup_capture.structure_key`) | one market structure, shared by the watcher, scanner sightings and the canonical record |
| `signal_id` | `[structure_id, strategy, strategy_epoch]` | that structure judged under one strategy epoch; two strategies make two signals, never a merge |

Levels (`boundary_high` / `boundary_low` / `revision`) are attributes, as in #1145. A
`SOURCE_DRIFT` revision updates the same signal.

WATCHING is two-sided. Direction, trigger and invalidation exist only after the first break:
- LONG: trigger is `boundary_high`, invalidation is `boundary_low`
- SHORT: the reverse

## Lifecycle

```
WATCHING ─┬─> TRIGGERED ───┬─> OUTCOME_CLOSED
          │                └─> DATA_BLOCKED    (#1145 SIP reconciliation failed)
          ├─> MISSED_LATE ─┬─> OUTCOME_CLOSED  (counterfactual)
          │                └─> DATA_BLOCKED
          ├─> MISSED_GAP ──┬─> OUTCOME_CLOSED  (counterfactual; #1145 GAP_THROUGH_OPEN)
          │                └─> DATA_BLOCKED
          └─> INVALIDATED | EXPIRED | DATA_BLOCKED | AMBIGUOUS   (terminal)
```

Event types:
- `OPENED`
- `STATE`
- `LEVELS` (WATCHING only, revision must increase)
- `OBSERVATION` (capture evidence only, such as `capture_late`, `prospective_catch` or `sip_crossed_at`; never identity, state or authority)
- `LINK` (timeless metadata, allowed after terminal)
- `INTEGRITY` (allowed after terminal, never with authority)

The journal refuses all of the following:
- illegal transitions
- duplicate opens
- sequence gaps
- clock inversions
- resolution without direction or trigger time
- a `TRIGGERED` for a structure first seen after its trigger (that must be `MISSED_LATE`)
- `MISSED_GAP` without gap evidence
- `OUTCOME_CLOSED` without `outcome_ref`

## Watcher → canonical mapping (`capture_adapter.fold_capture_rows`)

| #1145 row | canonical | preserved |
|---|---|---|
| first `WATCHING` | `OPENED` (two-sided `Levels`) | `structure_key`, timeframe, structure close, `knowable_at` → setup-ready, `first_seen_at`, `level_source`/`capture_id` → data source |
| re-persisted `WATCHING` | nothing, or `LEVELS` if the revision increased | dedupe |
| `SOURCE_DRIFT` | `LEVELS` | same signal |
| `RESOLUTION TRIGGERED` | `STATE TRIGGERED` + `OBSERVATION` + `INTEGRITY` | direction; market time = SIP cross, else first cross (the #1145 "true cross"); `detected_at`; feed/source/resolution; lags; `capture_late`; `prospective_catch` |
| `RESOLUTION MISSED_LATE` | `STATE MISSED_LATE` | reason (e.g. `iex_no_cross_sip_cross`), unchanged |
| `RESOLUTION GAP_THROUGH_OPEN` | `STATE MISSED_GAP` | `gap_through`, `first_print_price` |
| `EXPIRED` / `NO_TRIGGER` | `STATE EXPIRED` | `NO_TRIGGER` kept in the reason |
| `INVALIDATED` / `DATA_BLOCKED` / `AMBIGUOUS` | same-named state | — |
| `RECONCILIATION` | `OBSERVATION` + `INTEGRITY`, or `STATE DATA_BLOCKED` when #1145 demoted the row | SIP lag, `capture_late`, `prospective_catch` |
| `COLLECTOR_*`, `SOURCE_BLOCKED`, `JOURNAL_REPAIR` | ignored (also diagnostic-only in #1145) | — |

Integrity derivation:
- `signal_integrity` is VALID **iff** #1145 `is_prospective_catch` holds for a TRIGGERED row. A test checks equality with `catch_count`.
- It is UNKNOWN while a TRIGGERED row is pending SIP.
- It is DEGRADED for late, gap or missed captures.
- It is INVALID for DATA_BLOCKED and AMBIGUOUS.
- `data_integrity` is DEGRADED when `data_delayed` (SPX), and INVALID when the row is blocked or ambiguous.

These guarantees are tested against real #1145 engine output (#1145's own Oct 5 fixtures):
- one #1145 key maps to exactly one canonical signal
- repeated scanner sightings (`shadow:9925` / `9933`) only extend `scanner_sighting_ids`, and an unmatched sighting never creates a signal
- late, gap and expired classifications survive the mapping unchanged
- a row that claims authority, or is not observation-only, is refused
- missing trigger evidence becomes `DATA_BLOCKED` with no synthesized time
- the #1145 journal is never written, and a torn tail is skipped, not repaired

The adapter's default `strategy_epoch` is the collector version (`capture-v0.2`). That is not a registered strategy epoch, so research and fitness exclude these signals until an operator registers a real one.

## Authority

- Signals open with `execution_authority=false`.
- Authority can be recorded only on a non-observation, non-terminal signal that has a non-reserved epoch label and an explicit `authority_ref`.
- The adapter only produces observation-only signals.

## Outcome evidence (Workstream 8)

There is no change in intent. `OutcomeEvidence` now requires a resolved signal, because a two-sided WATCHING structure has no risk unit. R and MAE/MFE come from the resolved trigger and invalidation. Every value is OBSERVED, DERIVED, UNAVAILABLE or NOT_APPLICABLE, and UNAVAILABLE and NOT_APPLICABLE need a reason. Premium paths are observed quotes only.

## Not done here

- No persistence of canonical events. A runtime consumer would replay `fold_capture_rows` over the #1145 journal, which is cheap and deterministic.
- No wiring of scanner sighting rows. Scanner rows carry setup types such as `H1_222_CONTINUATION`, not #1145 pattern tokens, so the caller must supply the pattern key, as #1145's `link_scanner_first_sight` does.
