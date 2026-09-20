# Signa Futures Context Lane v2 — Shared Snapshot Consumer

Status: read-only / observation-only / no trade authority.

## Purpose

v2 lets futures journal context reference the shared Signa snapshot store created for options/futures reuse. Futures does not call Signa and does not schedule Signa pulls. It reads already-stored rows from `signa_snapshots` and attaches snapshot references to futures context.

## Data flow

```text
Shared Signa snapshot already exists in options scanner SQLite
→ futures context opens the DB read-only
→ latest matching proxy/timeframe snapshot is normalized
→ context.signa_futures_context records snapshot_id / snapshot_ref
```

## Proxy examples

- MNQ / NQ → QQQ
- MES / ES → SPY
- M2K / RTY → IWM
- MYM / YM → DIA
- MGC / GC → GLD
- MCL / CL → USO + XLE

## Safety boundary

The futures runtime:

- does not create the options DB;
- does not create Signa tables;
- does not call Signa;
- does not change entry, stop, target, route, broker, or risk logic;
- opens the shared SQLite file in read-only mode;
- fails soft when the file/table/snapshot is missing or stale.

Every record remains:

```text
observation_only
trade_authorized=false
gate_authoritative=false
risk_evaluated=false
broker_evaluated=false
execution_authority=false
```

## Freshness

Default max snapshot age is 3600 seconds. Stale snapshots are recorded as context errors and tagged, but they do not block or permit any futures setup.

## Fields added / extended

`context.signa_futures_context` now includes:

- `schema_version = signa_futures_context_v2`
- `snapshot_refs`
- `snapshot_ids`
- `snapshot_status`
- per-observation `snapshot_id`
- per-observation `snapshot_ref`
- per-observation `snapshot_age_seconds`

## Ruling

This is a research segmentation layer only. It is the correct foundation for later reports such as:

- MNQ 4HR with QQQ snapshot aligned vs conflicting;
- MES setup with SPY snapshot aligned vs conflicting;
- stale/missing Signa context vs fresh Signa context.

It does not promote Signa into a trade gate.
