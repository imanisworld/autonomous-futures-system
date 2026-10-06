# Canonical prospective signal + outcome evidence (2026-10-06)

Code: `options_evidence/signal.py`, `options_evidence/outcome.py`. Builds on
the strategy epoch registry (`options_evidence/strategy_epochs.py`).
Pure schema and validation: no provider, no file writer, no runtime wiring, no
authority.

## One structure, one identity

| Id | Derived from | Meaning |
|---|---|---|
| `structure_id` (`st_…`) | ticker, timeframe, pattern, direction, structure close time (UTC), trigger, invalidation (4 dp) | the market structure. Scanner, watcher and journal compute the same id independently. |
| `signal_id` (`sg_…`) | `structure_id` + `strategy` + `strategy_epoch` | that structure judged under one strategy epoch. Two strategies give two signals with the same structure. They are never merged. |

`verify_record` recomputes both ids from a stored row, so an edited trigger or
relabelled epoch is detected.

## Lifecycle (event-sourced, append-only)

```
WATCHING ─┬─> TRIGGERED ──> OUTCOME_CLOSED
          ├─> MISSED_LATE ─> OUTCOME_CLOSED   (counterfactual, executed=false)
          ├─> MISSED_GAP ──> OUTCOME_CLOSED   (counterfactual, executed=false)
          ├─> INVALIDATED  (terminal)
          └─> EXPIRED      (terminal)
```

`SignalJournal` refuses the following:
- illegal transitions, and any event after a terminal state
- a duplicate OPENED (structure-level dedupe)
- sequence gaps
- out-of-order detection times
- a trigger detected before it traded
- a trigger before the structure closed
- `TRIGGERED` for a structure first seen after its trigger traded. That case must be `MISSED_LATE`.
- `MISSED_GAP` without the gap open price
- `OUTCOME_CLOSED` without `outcome_ref`

`prearmed` and `detection_lag_seconds` are derived values. They are never
stored as separate facts.

## Required fields

Every field in `REQUIRED_RECORD_FIELDS` appears in `to_record`:
- `signal_id`, `structure_id`, `ticker`, `strategy`, `strategy_epoch`
- `direction`, `timeframe`, `pattern`
- `structure_close_time`, `setup_ready_time`, `first_seen_time`
- `trigger`, `invalidation`, `trigger_market_time`, `trigger_detection_time`
- `data_source`, `context_snapshot_ids`
- `observation_only`, `execution_authority`
- `data_integrity`, `signal_integrity`, `execution_integrity`
- `lifecycle_state`

The record also carries these links:
- scanner sightings
- watcher refs
- context snapshots
- contract plan refs
- one `outcome_ref`

## Authority

- A signal always opens with `execution_authority=false`.
- Setting it true requires all three of these:
  - a non-observation-only signal
  - a registered strategy epoch, so not `LEGACY_UNVERSIONED` or `UNREGISTERED_EPOCH`
  - an explicit `authority_ref` to a separate human-approved authority record
- The signal records authority. It never grants it.

## Outcome evidence (Workstream 8)

`OutcomeEvidence` carries the following:
- T1/T2
- invalidation (must equal the signal's frozen value)
- premium entry/stop
- MAE/MFE price
- T1/T2/invalidation hit times
- trim, runner, result R, gross/net P&L (`pnl_basis` = `executed` | `paper_equivalent`)
- the observed premium path
- the three integrity statuses

Derived values are computed only from the signal:
- MAE_R/MFE_R, using the signal's own risk unit
- time to trigger, T1, T2, and invalidation
- entry half-spread
- mid-to-mid premium change

The mid-to-mid change is reported as observed change, not as "decay", because a sparse path cannot separate theta from delta and vega.

These rules keep the evidence honest:
- Each value is `OBSERVED` (needs a source), `DERIVED`, `UNAVAILABLE`, or `NOT_APPLICABLE`. The last two need a reason and cannot carry a value.
- `result_r_value` returns `None` for unknown results. It never returns 0.
- Premium marks are observed quotes with a source, strictly time-ordered, and not before the trigger. A path marked `UNAVAILABLE` cannot hold marks. Interpolation does not exist.

## Dependency on Cursor's early-capture work

The runtime watcher work belongs to Cursor and is not yet on GitHub:
- WATCHING / TRIGGERED / INVALIDATED / EXPIRED
- cross-session persistence
- dedupe
- MISSED_LATE and gap-through
- 30m / anchored-1H / Daily timing

This PR does **not** touch that work. Wiring it is a follow-up:
1. Cursor's collector emits `open_signal` / `state_event` / `link_event`, or its rows are mapped onto them.
2. Its journal persists `to_record` snapshots plus the events.

Field names should be reconciled against Cursor's final schema before wiring. If they differ, map them in an adapter rather than renaming either side.

Existing 1-2-2 journal rows are **not migrated**. A reader labels them
`LEGACY_UNVERSIONED` unless they carry a registered `strategy_epoch`.
