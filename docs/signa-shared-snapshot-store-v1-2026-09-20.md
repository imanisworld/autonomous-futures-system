# Signa Shared Snapshot Store v1 — 2026-09-20

## Verdict

APPROVE as passive infrastructure only.

This is a shared read/cache layer for Signa response snapshots. It does not call Signa and does not alter options/futures decisions. Later 2026-09-20 work wired options and futures consumers to reference this store while preserving observation-only authority boundaries.

## Purpose

Options and futures both need overlapping proxy context such as SPY, QQQ, IWM, DIA, GLD, USO/XLE, TLT, and VIX. Without a shared layer, options and futures can independently pull the same endpoint/symbol/timeframe and end up with duplicate API calls, rate-limit exposure, and mismatched timestamps.

The store is designed so one snapshot can be stored once and then referenced by both systems using the same `snapshot_id`.

## Scope in v1

Implemented:

- `sources/signa_snapshot_store.py`
- `tests/test_signa_snapshot_store.py`
- SQLite table: `signa_snapshots`
- deterministic `snapshot_id`
- stable `params_hash`
- normalized `source`, `endpoint`, `symbol`, `timeframe`, `status`
- bucket-based dedupe key
- `latest(...)` lookup
- `find_fresh(...)` lookup for TTL-style reuse
- compact `to_reference()` row for downstream options/futures context
- forced observation flags:
  - `observation_only=True`
  - `trade_authority=False`

Not implemented in the store itself:

- no network/API calls
- no Signa-based gating
- no scanner/risk/broker/execution imports
- no deployment requirement by itself

Later consumers now use the store:

- #805 routes options Signa pulls through `signa_snapshots` before `options_signa_context`;
- #807 lets futures `context.signa_futures_context` reference shared snapshots in read-only mode.

## Snapshot identity

A snapshot is keyed by:

```text
source
endpoint
symbol
timeframe
params_hash
snapshot_bucket
```

That means if options and futures both request the same QQQ action-card snapshot in the same bucket, they should reuse the same row.

## Completed follow-on work

1. #805 refactored options Signa pulls to write raw responses into `signa_snapshots` first, then write options-specific interpretation rows separately.
2. #807 built Futures Signa Context Lane v2 as a consumer of shared snapshots, not as a separate Signa puller.
3. The overlapping futures proxy set remains the correct initial set:
   - MNQ/NQ → QQQ
   - MES/ES → SPY
   - rates context → TLT
   - volatility context → VIX
4. Interpretation remains separate:
   - options writes options context rows;
   - futures writes futures context rows;
   - both may reference the same `snapshot_id`.

## Current next step

Seed or verify the shared snapshot store with a controlled read-only Signa pull for the overlapping proxy set, then audit the first future natural futures setup for v2 `snapshot_ids`, `snapshot_refs`, and correct `snapshot_status`.

## Safety ruling

This layer is data infrastructure only. It provides shared evidence references. It must not be used to enter, block, rank, resize, or route trades without a later evidence-backed promotion.
