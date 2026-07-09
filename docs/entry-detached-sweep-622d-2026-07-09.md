# ORB/Entry-Detached Entry-Fill Sweep — Full 622-Day Extension

Scope: every `ENTRY_DETACHED_FROM_PRICE` `NO_TRADE` row across the full 622-day replay journal set (2024-07-01 to 2026-06-26, both instruments), extending the original 20-case/6-day audit (`docs/orb-entry-fill-ab-2026-07-06.md`) to 5268 deduplicated cases. Signal formation is unchanged — only the paper/replay entry-fill model is varied. `ioc_limit` is the production-matching baseline; `stop_market` is the causal looser alternative used for classification; `market` (always-fills) is shown for context only — it is a known fill-model artifact per `docs/ioc-faithful-baseline-622d-2026-07-06.md`, not a trustworthy edge signal on its own.

**Overall classification: `UNDERFILLING_NOT_ENTRY_DRIVEN`**

## Combined

| model | cases | filled | fill% | no-fill | no-data | W | L | net $ | exp $ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| market | 5268 | 5261 | 100% | 0 | 0 | 5054 | 207 | 278981.05 | 53.03 |
| ioc_limit | 5268 | 0 | 0% | 5268 | 0 | 0 | 0 | 0.00 | n/a |
| stop_market | 5268 | 2 | 0% | 5266 | 0 | 1 | 1 | -37.12 | -18.56 |

## By instrument

| group | n | classification | ioc_limit fill% | ioc_limit exp$ | stop_market fill% | stop_market exp$ | market exp$ |
|---|---:|---|---:|---:|---:|---:|---:|
| MES | 1644 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | 1.50 | 76.92 |
| MNQ | 3624 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | -38.62 | 42.18 |

## By strategy

| group | n | classification | ioc_limit fill% | ioc_limit exp$ | stop_market fill% | stop_market exp$ | market exp$ |
|---|---:|---|---:|---:|---:|---:|---:|
| orb_breakout | 2393 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | n/a | 63.31 |
| orb_reclaim | 55 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | n/a | 97.92 |
| pdh_reclaim | 865 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | n/a | 42.64 |
| pdl_reclaim | 131 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | n/a | 23.73 |
| vwap_hold | 1746 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | 1.50 | 45.98 |
| vwap_reclaim | 78 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 1% | -38.62 | 28.21 |

## By walk-forward half

| group | n | classification | ioc_limit fill% | ioc_limit exp$ | stop_market fill% | stop_market exp$ | market exp$ |
|---|---:|---|---:|---:|---:|---:|---:|
| H1 | 2635 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | -18.56 | 53.81 |
| H2 | 2633 | UNDERFILLING_NOT_ENTRY_DRIVEN | 0% | n/a | 0% | n/a | 52.24 |

## Reading

`ioc_limit` and `stop_market` both land near 0% fill on this population — this is expected, not a bug: `ENTRY_DETACHED_FROM_PRICE` means the structural entry is already far from the live price, and `stop_market` in `PaperBroker` is genuinely one-next-bar-only (confirmed via `_activate_pending_stop_entry` — it resolves fill-or-cancel on the immediate next candle, never retried on later bars), so requiring price to travel back to a stale level within 15 minutes is rare by construction. The only model showing edge here is `market` (always-fills) — the model already proven to overstate edge system-wide. Read together, this means: neither of the two realistic, causal fill mechanisms tested can practically capture this population — the fix isn't 'loosen the fill model,' it would need the entry price itself to re-anchor toward current price, which is exactly the `momentum_entry_reanchor` mechanism that prior work already tried and found caused real losses when enabled (see `project_momentum_entry_investigation` — resolved, stayed disabled). This extension does not overturn that finding; if anything it explains why the earlier 20-case sample looked more promising than it turns out to be at full scale.

## Notes

- `market` is the legacy assumed-fill replay model — always fills, proven to overstate edge system-wide; shown for context only, never the classification driver.
- `ioc_limit` uses MES=16 and MNQ=32 tolerance ticks, matching the live-box defaults.
- `stop_market` is one-next-bar causal: gap-through fills use the next bar open; missing or non-triggering next bar cancels.
- Cells with fewer than 15 cases are classified `INSUFFICIENT_DATA` rather than given a directional call.
- Classification taxonomy: `UNDERFILLING_ENTRY_MODEL` (looser model recovers fills without hurting expectancy) / `BAD_STRATEGY` (both models negative regardless of fill rate) / `PASSIVITY_PROTECTIVE` (looser model fills more but expectancy is worse) / `UNDERFILLING_NOT_ENTRY_DRIVEN` (fill rate barely moves) / `INSUFFICIENT_DATA`.
- This is docs/script/tests only — zero changes to execution/, risk/, config/, risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo orders, no strategy promotion or demotion.
