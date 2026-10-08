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

The canonical-stamp boundary bumps the collector from
`122-iex-collector-v0.1` to `122-iex-collector-v0.2`. The loader explicitly
accepts v0.1 as legacy so existing journal rows continue to load, but only a
v0.2 ARMED row may establish `canonical_binding`. A later v0.2 row cannot
upgrade a legacy setup into the canonical population. Delayed reconciliation of
a pre-binding setup may finish, but it remains unbound because missing
historical identity is never invented.

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
