# Massive historical options one-sample proof — 2026-09-18

## Ruling

The isolated Massive/Polygon options test credential has historical option-quote entitlement and materially advances the historical backtest path, but it does **not** by itself complete exact replay of the frozen selector. `strat_212_reversal_30m_options` remains **BLOCKED / WAIT**.

No runtime, deployment, broker, DEMO, risk-budget, or live-trading change is authorized by this proof.

## Frozen sample

The first outcome-independent primary-20 212R acquisition point was used:

- underlying: AMZN
- direction: CALL
- decision basis: first sight
- decision timestamp: `2026-09-09T15:17:57Z`
- frozen underlying trigger: 252.65
- frozen underlying invalidation: 251.52

The selector rule in `options_manager/contracts/selector_rule_v1.json` prefers the nearest eligible expiration at or above 45 DTE. Massive's historical contract reference, queried `as_of=2026-09-09`, shows 2026-11-20 as the nearest listed preferred-band expiration for this sample.

## What Massive proves

Massive historical quote access is authorized. For AMZN 2026-11-20 calls around the decision boundary, the historical quote endpoint returned executable NBBO updates **before** the frozen decision timestamp, including:

| Strike | Last pre-decision bid | ask | Approx. age at decision |
|---:|---:|---:|---:|
| 245 | 21.00 | 21.45 | 1.83 s |
| 250 | 18.30 | 18.55 | 0.60 s |
| 255 | 16.00 | 16.15 | 4.35 s |
| 260 | 13.75 | 14.00 | 3.79 s |
| 265 | 11.80 | 12.05 | 1.59 s |
| 270 | 10.10 | 10.35 | 0.42 s |
| 275 | 8.60 | 8.85 | 1.57 s |

All sampled spreads are below the frozen 20% selector spread ceiling.

Massive historical 1-minute aggregates also provide trade volume that can be accumulated causally from market open through the decision time. Cumulative sampled volumes through the decision boundary were 245C=17, 250C=289, 255C=215, 260C=234, 265C=118, 270C=554, 275C=173. The 245C sample fails the selector's minimum-volume threshold of 100; the sampled 250C–275C rows clear it.

The test credential also has AMZN 1-minute underlying aggregates. The latest fully completed minute before the 15:17:57Z decision is the 15:16Z bar (O=251.12, H=251.345, L=251.10, C=251.345). Historical stock quotes, stock trades, and 1-second aggregates returned `NOT_AUTHORIZED`, so the 15:16 close is only causal context and is **not** substituted for the exact decision-time underlying price.

This is the first real proof that the historical quote and volume parts of the 212R selector/fill evidence can be reconstructed without using future quotes.

## What Massive does not prove

The frozen selector also requires `open_interest` and `delta` on every candidate row, and it requires a finite positive `underlying_price` for the decision.

Massive's documented historical quote endpoint returns bid/ask, sizes, exchange identifiers, sequence number, and SIP timestamp. Its historical aggregates provide OHLCV. Neither documented historical endpoint supplies historical delta or historical open interest at the intraday decision boundary.

Massive's option snapshot endpoint does supply current Greeks, IV, open interest, quotes, and volume, but it has no historical `as_of`/timestamp parameter. A probe with an added `as_of=2026-09-09` parameter returned current 2026-09-18 snapshot timestamps, so those current values are **not** admissible as 2026-09-09 selector evidence.

No historical delta/open-interest value is synthesized or back-filled from the current snapshot. The completed 15:16 underlying minute is likewise not promoted into an exact 15:17:57 decision price.

## External evidence paths checked

OCC's public Series Search exposes per-series open interest and states that it is derived from the previous day's settlement, but the documented batch interface accepts symbol/symbol type and does not expose a historical business-date parameter. OCC's downloadable Daily Open Interest report tested here was aggregate market open interest, not per-contract history. Neither is treated as a reproducible September 9 contract-level OI source.

Cboe DataShop Option Quotes supports historical 1-minute or N-minute option snapshots and can optionally include:
- implied volatility and Greeks including Delta;
- open interest;
- NBBO bid/ask and size;
- underlying bid/ask;
- interval trade volume.

That product is a plausible exact-selector source because one interval record can carry the selector's historical bid/ask + delta + open interest on one causal timestamp, while cumulative interval volume can be calculated only through the decision boundary. The commercial price is calculated dynamically by Cboe DataShop and has not been approved or purchased.

## Canonical source handling

PR #704 added `quote-retention-v2` without changing the frozen v1 bytes. V2 keeps the same 900-second freshness and 20% spread rules and adds the explicit historical source `massive:/v3/quotes/{optionsTicker}` for new evidence. PR #705 adds a pure Massive historical quote normalizer that preserves contract identity, bid/ask, and nanosecond SIP timestamp but deliberately leaves volume/open interest/delta/IV missing. Quote-only Massive evidence therefore remains `MISSING` and cannot silently become selector-ready.

## Current safest architecture

Do **not** alter the frozen selector just to fit Massive's available fields.

The cleanest current proof path is:

1. use a causal historical chain snapshot source that contains bid/ask, delta, open interest, interval volume, and decision-time underlying price at/before the decision timestamp;
2. normalize only fields actually supplied by that source; never substitute current snapshot analytics or a later/earlier underlying mark as exact decision data;
3. run the existing canonical selector unchanged on those frozen bytes;
4. use Massive tick-level historical quotes for exact executable entry/exit fill reconstruction of the selected contract;
5. hash/materialize the quote dataset and manifest through the merged Item-2 tooling;
6. require replay/forward parity and all existing contract/risk gates, including the premium cap.

Until step 1 is solved, the historical data blocker is narrowed but not retired.

## Cost boundary

The currently available Massive/Polygon access is already useful: it provides authorized tick-level historical option quotes and historical trade/aggregate data for the tested options scope. No additional data purchase should be made merely to continue code work.

A separate historical analytics purchase is justified only after confirming that it supplies the missing selector fields for the actual 81-point acquisition set at causal timestamps and that its cost is known before checkout.
