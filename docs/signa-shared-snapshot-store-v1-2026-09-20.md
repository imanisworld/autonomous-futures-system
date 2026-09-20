# Signa Shared Snapshot Store v1 — 2026-09-20

## Verdict

APPROVE as passive infrastructure only.

This is a shared read/cache layer for Signa response snapshots. It does not call Signa, does not alter options/futures decisions, and does not attach itself to runtime consumers yet.

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

Not implemented in v1:

- no network/API calls
- no scheduled collector
- no options route refactor
- no futures journal attachment change
- no Signa-based gating
- no scanner/risk/broker/execution imports
- no deployment requirement by itself

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

## Intended next steps

1. Refactor the options Signa discovery branch to write raw Signa responses into `signa_snapshots` first, then write options-specific interpretation rows separately.
2. Build Futures Signa Context Lane v2 as a consumer of shared snapshots, not as a separate Signa puller.
3. Start with overlapping futures proxies:
   - MNQ/NQ → QQQ
   - MES/ES → SPY
   - rates context → TLT
   - volatility context → VIX
4. Preserve separate interpretation layers:
   - options writes options context rows
   - futures writes futures context rows
   - both may reference the same `snapshot_id`

## Safety ruling

This layer is data infrastructure only. It provides shared evidence references. It must not be used to enter, block, rank, resize, or route trades without a later evidence-backed promotion.
