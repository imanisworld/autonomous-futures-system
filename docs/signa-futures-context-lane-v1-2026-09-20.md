# Signa Futures Context Lane v1 — 2026-09-20

## Verdict

**APPROVE as read-only observation context only.**

Signa is not a futures trade trigger. This lane records whether the Signa proxy
context is aligned, conflicting, mixed, missing or errored when a futures setup is
processed. It cannot permit, block, size, route, submit, cancel, or modify a
trade.

## Scope

Proxy mapping:

| Futures | Proxy |
|---|---|
| MES / ES | SPY |
| MNQ / NQ | QQQ |
| M2K / RTY | IWM |
| MYM / YM | DIA |
| MGC / GC | GLD |
| MCL / CL | USO, XLE |
| MBT | BTC |

The regime proxy list also includes `TLT` and `VIX` for future read-only
context collection. v1 does not fetch them in the webhook path.

## Runtime behavior

The webhook does not call Signa from this lane. It uses whatever Signa fields are
already present on `MarketState`, which come from the existing optional Signa
enrichment/payload path. Missing Signa data remains explicitly missing.

Every journal context now includes:

`context.signa_futures_context`

Important fields:

- `definition = signa_futures_context_v1`
- `authority = observation_only`
- `gate_authoritative = false`
- `broker_evaluated = false`
- `risk_evaluated = false`
- `trade_authorized = false`
- `execution_authority = false`
- futures symbol/root
- proxy/proxies
- requested timeframes
- observations
- FTFC state
- primary bias
- alignment/conflict versus futures setup direction when available
- tags
- missing/error surfaces

## Derived tags

Examples:

- `SIGNA_FTFC_LONG`
- `SIGNA_FTFC_MIXED`
- `SIGNA_INCOMPLETE`
- `SIGNA_INDEX_LONG`
- `SIGNA_INDEX_SHORT`
- `SIGNA_INDEX_UNKNOWN`
- `RISK_ON`
- `RISK_OFF`
- `SIGNA_TRADE_ALIGNED`
- `SIGNA_TRADE_CONFLICT`
- `SECTOR_SUPPORTIVE`
- `SECTOR_CONFLICT`
- `SIGNA_CONTEXT_MISSING`
- `SIGNA_CONTEXT_ERRORS`

## What this does not do

This lane does not:

- enter futures trades;
- block futures trades;
- replace futures strategy rules;
- replace risk validation;
- change DEMO routing;
- change live routing;
- call the Signa API from the futures path;
- infer missing VIX, FOMC, OPEX, Faber, sector rotation or options-flow data.

Those fields require their own live payload/API proof before they can be added as
recorded context.

## Next research use

After enough futures signals are collected, segment outcomes by:

- Signa aligned vs conflicting vs missing;
- QQQ/SPY/IWM/DIA proxy bias;
- `RISK_ON` vs `RISK_OFF`;
- mixed FTFC versus clean FTFC;
- error/missing data periods.

No execution rule should be changed until this segmentation has enough forward
sample and survives the usual concentration/drawdown review.

No proof, no run.
