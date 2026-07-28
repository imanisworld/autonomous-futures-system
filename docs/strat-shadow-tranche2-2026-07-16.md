# Strategy-matrix tranche 2 — shadow families under honest lane economics (2026-07-16)

Read-only research record. No production behavior, broker routing, risk, config, or strategy code changed.

## Question

The restoration roadmap proposed converting the strongest shadow strategies into executable paper lanes, starting with MNQ `strat_22_reversal`, then `strat_22_continuation` and `ema_pullback_trend`. Do these families carry edge under the SAME fill model the existing proof lanes use — market entry + runner exit, after cost — with walk-forward stability?

Prior evidence was unusable in both directions:
- The 2026-07-09 mechanical research showed these families negative, but used resting-entry fills with pessimistic same-bar stop-first handling and static 2R targets — the fill-model class that produced the false "vwap fiction" verdict later overturned by the PR #283 paired fill study.
- The older "Strat combos have edge with a RUNNER exit" finding predates the honest-IOC corrections (the legacy fill model was separately shown to manufacture edge — see `docs/ioc-faithful-baseline-622d-2026-07-06.md`).

## Method

`scripts/strat_shadow_tranche2_study.py` (results: `scripts/strat_shadow_tranche2_results.json`), reusing the tranche-1 machinery verbatim via import (identical `fill_price` market model, identical `resolve` runner via the real `PaperBroker` with `pessimistic_both_hit=True`, runner activation 1.0R / trail 0.5R, identical cost model: $1.24 commission RT + 2 ticks slippage RT).

- Population: every `shadow_candidates` row for the five families in `logs/replay_622d_market_static/{MNQ,MES}` (622 days), brackets exactly as the shadow layer journaled them (prior-bar-break entry, opposite-side stop).
- Fills resolved on 5-minute Polygon bars (`data/replay_polygon_5m/`), armed at signal bar close + 15 min, same as tranche 1.
- **Honesty addition over tranche 1**: shadow signals fire many times per day with no position management (e.g. 15,127 raw MNQ strat_22_continuation signals). Arms are resolved SEQUENTIALLY NON-OVERLAPPING per (instrument, family, variant) — a signal arriving while the prior trade is open is skipped, exactly as a one-position-at-a-time lane behaves. All-signal counts are reported as `signals_raw`; verdicts use the lane-shaped sequential population.
- Variants: all-sessions and NY-only, both walk-forward halved.

## Results (after cost, runner exit, sequential lane shape)

| arm | raw | resolved | net | $/trade | WR | PF | half 1 $/t | half 2 $/t | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| MNQ strat_22_reversal ALL | 7,254 | 3,492 | −$2,692 | −$0.77 | 49.9% | 0.98 | +2.67 | −4.27 | REJECT |
| **MNQ strat_22_reversal NY** | 2,336 | 964 | +$1,537 | +$1.59 | 51.2% | 1.02 | **+10.65** | **−8.01** | **REJECT (sign flip)** |
| MNQ strat_22_continuation ALL | 15,127 | 4,495 | −$4,277 | −$0.95 | 49.6% | 0.98 | −4.14 | +2.29 | REJECT (sign flip) |
| MNQ strat_22_continuation NY | 5,028 | 1,127 | −$898 | −$0.80 | 50.0% | 0.99 | −3.19 | +1.72 | REJECT (sign flip) |
| MNQ ema_pullback_trend ALL | 6,764 | 2,696 | −$5,861 | −$2.17 | 48.6% | 0.95 | +0.39 | −4.69 | REJECT |
| MNQ ema_pullback_trend NY | 2,222 | 854 | −$2,719 | −$3.18 | 47.2% | 0.95 | +4.35 | −10.54 | REJECT (sign flip) |
| MNQ strat_312 ALL | 796 | 612 | −$3,770 | −$6.16 | 47.4% | 0.86 | −4.67 | −7.71 | REJECT |
| MNQ strat_312 NY | 234 | 169 | −$3,590 | −$21.24 | 44.4% | 0.72 | −18.00 | −24.60 | REJECT |
| MNQ strat_322_reversal ALL | 977 | 735 | −$1,372 | −$1.87 | 49.1% | 0.96 | +3.00 | −6.64 | REJECT |
| MNQ strat_322_reversal NY | 332 | 221 | −$1,849 | −$8.36 | 48.0% | 0.89 | +2.08 | −19.10 | REJECT |
| MES (all five families, both variants) | — | 127–3,412 | all negative | −$4.05 to −$14.66 | ≤48.2% | ≤0.91 | — | — | REJECT |

Reference bar (tranche 1, same model): orb_reclaim +$23.12/t, orb_breakout +$15.16/t (halves 26.64/8.15), vwap_hold +$10.51/t (both halves positive).

## Verdict

**No shadow family qualifies for a paper lane on current evidence.** Every arm is either negative full-period or flips sign across walk-forward halves. Win rates cluster at 47–51% with profit factors ~0.7–1.02: coin-flip structure whose costs eat the residue. The distinguishing feature of the three validated lanes — BOTH halves independently positive — is absent from all 20 arms.

Specifically against the proposed roadmap:
1. **MNQ strat_22_reversal lane: DO NOT BUILD.** The NY-only full-period positive is entirely a first-half artifact (+$5,284 h1, −$3,747 h2).
2. **MNQ strat_22_continuation: DO NOT BUILD** (negative, unstable in the opposite direction).
3. **MNQ ema_pullback_trend: DO NOT BUILD** (negative everywhere; NY second half −$10.54/t).

## What would change these verdicts

- A regime/condition gate that predicts WHICH half-like environment is active (the reversal family made +$10.65/t for ~300 days — if that environment is identifiable ex-ante, the arm becomes conditional, not dead). This is a research question, not a lane build.
- A different bracket construction than the shadow layer's prior-bar-range default (e.g. structure-anchored stops) — would need its own study; nothing here licenses it.
- Forward shadow evidence diverging materially from this replay (the shadow resolver keeps accumulating live rows at zero cost).

## Roadmap consequence

The "convert the strongest shadow strategies into lanes" premise fails empirically. The items on the roadmap that survive this study are the infrastructure ones — strategy-conflict/ownership layer, generalized multi-lane monitor, live/replay parity harness, GEX role testing, and constructing actual entry definitions for supply/demand and liquidity-sweep (which have no testable entry candidates yet, hence no study possible). Lane-expansion priority reverts to the tranche-1 ranking: vwap_reclaim NY-only (+$3.14/t, needs its own walk-forward scrutiny before building) and pdl_reclaim observation (n=13).
