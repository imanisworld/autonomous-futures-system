# Options 1-2-2 canonical producer binding

Date: 2026-10-07

## Status

**SOURCE-ONLY / NOT DEPLOYED / NOT A PROOF-WINDOW START**

This note settles the producer-identity question from #1177 without changing the
frozen strategy definition.

`122-IEX-E1` belongs to the dedicated observation-only 1-2-2 collector:

- `scripts/options_122_prospective_collect.py`
- collector id `OPTIONS_122_IEX_PROSPECTIVE_COLLECTOR`
- strategy `options_122`
- policy epoch `122-IEX-E1`
- universe `PRIMARY_20`
- 30m arm rule from the preregistered 1-2-2 collector
- Public regular-session chart arm source
- exact Alpaca IEX first-break clock
- delayed Alpaca SIP reconciliation

The broad #1145 setup-capture observer is a separate producer and must not be
aliased into this epoch.

## Why a forward-only binding is needed

Historical dedicated-collector rows predate the canonical #1151 signal layer.
They carry `policy_epoch` but not enough exact canonical identity to create a
verified signal safely. In particular, old rows do not explicitly retain the
arm structure-close identity used by the canonical structure key.

Therefore historical rows remain legacy/unbound. They are never relabelled,
backfilled or admitted to fitness/research by this change.

Newly written rows receive an explicit `canonical_binding` stamp containing:

- `strategy = options_122`
- `strategy_epoch = 122-IEX-E1`
- `timeframe = 30m`
- `universe = PRIMARY_20`
- stable `122:2U` / `122:2D` structure family identity
- explicit structure-close time
- `arm_source = public_regular_session_chart`
- `provisional_trigger_source = alpaca_iex_trades`
- `authoritative_reconciliation_source = alpaca_sip_trades_delayed`
- collector id/version
- the raw Public chart source string separately

These canonical labels are the policy vocabulary frozen in the epoch. They do
not replace the raw source payloads.

## Compatibility

### Grok B1 — one authoritative arm per setup

The journal loader now rejects every second `ARMED` for the same setup
(including legacy ARMED followed by a valid-looking stamped ARMED). This
refuses duplicate-event laundering before canonical population membership
can be established. Historical rows remain untouched.

### Grok B2 — rollback is a schema-boundary migration, not a code-only revert

**BLOCKED until an explicit, separately reviewed operator-approved
journal-partition procedure is demonstrated on the release host.**

The old v0.1 collector refuses v0.2 rows in a shared cumulative journal,
so a straightforward rollback of the executable while pointing it at the
v0.2 journal can fail at startup. Neither deployment nor rollback may silently
delete, truncate, rewrite, or rename the historical evidence in place.

Required staged procedure before any v0.2 promotion or rollback:

1. Snapshot and hash the original cumulative JSONL, its raw trade inputs,
   source release SHA, collector schema, registry definition hash, timestamps
   and provenance manifest; verify the snapshot is immutable/read-only.
2. Select a **new, distinct, empty journal partition path** for the active
   code version. Update unit/config journal location only under a separate
   operator-approved change after a reviewed path/permission check. Never
   reuse an old path containing incompatible rows.
3. For a rollback, stop/switch the producer only under operator GO and
   point the prior v0.1 release at a fresh v0.1-compatible partition; retain
   the original v0.2 journal and its manifest for audit, unrelabelled.
4. Prove the **actual pinned v0.1 collector executable** can start and
   complete a closed-session no-evidence cycle against the empty rollback
   partition, without mutating preserved journals, executing orders or
   starting proof. A v0.2 loader test alone is **not** this proof.
5. Record the two exact release identities, path ownership, cutover time,
   episode boundaries and one-way provenance. Do not stitch the partitions
   into a single admitted catch population without separate registration
   and reproducible identity verification. Fail closed if evidence is missing.

Source-only regressions demonstrate that partition files remain separate,
a combined duplicate arm is refused and historic bytes stay unchanged.
They do **not** simulate or authorize a real VPS rollback rehearsal.

The canonical-stamp boundary bumps the collector from
`122-iex-collector-v0.1` to `122-iex-collector-v0.2`. The loader explicitly
accepts v0.1 as legacy so existing journal rows continue to load, but only a
v0.2 ARMED row may establish `canonical_binding`. A later v0.2 row cannot
upgrade a legacy setup into the canonical population. Delayed reconciliation of
a pre-binding setup may finish, but it remains unbound because missing
historical identity is never invented.

## AFS-0167 — no late ARMED after any earlier setup row

The v0.2 loader additionally refuses an `ARMED` event if **any** earlier
journal row already mentioned the same `setup_id` (including unstamped
RESOLUTION, RECONCILIATION, SOURCE_DRIFT and error rows). A legacy setup
must never acquire a first stamped ARMED merely because no old ARMED was
present. The original journal is read-only; no retrospective fixes or
backfills are permitted.

The collector entry-point's Git mode is retained as executable (`100755`).
Before any production repin, scan the exact live journal read-only for both
duplicate ARMED rows **and** setup IDs whose first record precedes ARMED.
Also confirm the effective pin against the reported `db9bc7e2` release.
Any discrepancy remains HOLD; never rewrite or rotate a live journal implicitly.

## Source-stamp continuity and pre-repin audit (AFS-0164)

- One ARMED stamp is authoritative for each setup. Every later RESOLUTION and
  RECONCILIATION for that setup must use collector v0.2 and match that exact
  original structure-close, pattern and source binding, not just a newly
  recomputed self-consistent stamp. An unstamped old-format row is refused.
- SOURCE_DRIFT is always excluded from eligible catches. Its optional stamp
  is checked against its own observation because a genuine Public chart
  revision can change a 2U structure into 2D; it never replaces the ARMED stamp.
- **Before any repin, migration or v0.2 activation**, obtain a read-only,
  timestamped scan of the **actual live JSONL** grouped by collector ID,
  epoch and setup ID for duplicate ARMED rows. Report path/release, count,
  duplicate IDs and journal hash without exposing secrets. Any duplicate
  means **HOLD**: the new loader correctly fails startup. Do not rewrite,
  dedupe, truncate, re-arm or silently switch journals.
- A fresh segregated journal and old-release startup rehearsal are still
  separate, operator-approved B2/B8 gates; the tests are source-only.

## What this does not do

This first slice does **not** yet make the rows scoreable.

A separate read-only adapter must consume only rows with the explicit binding
and map them into the canonical signal lifecycle. That adapter must land only
after #1183 / #1176 settles the shared canonical-record contract.

Expected mapping:

- ARMED -> WATCHING
- IEX reversal -> provisional TRIGGERED, not yet a verified catch
- same-direction first break -> cancellation / invalidation
- no IEX break -> non-catch pending delayed reconciliation
- CONFIRMED_SAME_REVERSAL -> eligible to become a prospective catch only when
  pre-armed and existing capture-lag / option-evidence rules also pass
- false provisional, source inconsistency or blocked reconciliation -> fail closed
- SIP reversal missed by IEX -> explicit miss, never backfilled as a catch

## Safety boundary

Nothing here:

- installs or changes a timer;
- deploys the collector;
- changes broker/risk/order authority;
- starts forward proof;
- creates a paper trade;
- changes stop/target policy;
- edits the frozen `122-IEX-E1` definition.

No proof, no trade.
