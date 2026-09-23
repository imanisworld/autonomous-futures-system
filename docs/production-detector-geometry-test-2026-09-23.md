# Production-detector geometry test: 15m control vs 60m wide vs 4H wide (2026-09-23)

**RESEARCH ONLY. Read-only and offline.** No collector, strategy, risk,
broker, deployment, VPS or paper/live change. Nothing is promoted by this
test.

## What was tested

These are the same production setup detectors the observation lane runs:

- `strategy.shadow_setups.evaluate_shadow_setups(..., include_canonical_observers=False)`;
- the canonical 2-1-2 / 1-2-2 state machine
  (`execution.cross_instrument_observation._strat_212_122_candidate`).

Both run on the `MarketState` built by `ReplayEngine._market_state_from_candle`.
Only the structural-outcome populations configured for each market are kept.
There are exactly three configurations, with no grid and no tuning:

| Config | Signals | Stop | Exit |
|---|---|---|---|
| **CONTROL** | 15m | the detector's own stop | the detector's own 2R target, via the production resolver (`_resolve_one` / `_expired`) |
| **60M WIDE** | 60m | 2 × ATR(14) of the signal bar | trail: after +1R, from the next 15m bar the stop is max(entry, best − 1R) |
| **4H WIDE** | 240m | 2 × ATR(14) | same trail |

**The same in all three configurations:**
- the detector's entry price;
- a touch fill;
- stop first when a bar touches both levels;
- expiry at the observation-day roll, at the last close;
- the path resolved on 15m bars after the signal bar closes;
- costs of commission plus 2 ticks: MNQ $2.48, MES $3.98, M2K $2.48, MGC
  $4.00, MCL $4.00, MBT $7.00.

**Bars.** 60m and 240m bars are session-anchored (18:00 ET) resamples of the
same Polygon 15m bars, enriched by the unchanged `derive_candles`.

**Samples.** Historical: 2024-09 → 2025-12. Untouched: 2026-01 → 2026-09-22,
read once.

**Nulls.**
- Random direction: 500 draws, each trade's bracket mirrored at random.
- Random entry time: 200 draws of random 2026 bars with the same
  direction mix, stop, exit and costs.

**PASS** needs 2026 n ≥ 40, PF ≥ 1.30, net > 0, both halves > 0, and PF
above the 95th percentile of both nulls.

**Material improvement over the control** means the bootstrap 95% interval
of the 2026 PF difference lies entirely above 0.

## Parity gate (run before any result)

Pre-declared rule: at least 90% of the live lane's recorded setups
(2026-09-16..22) must be reproduced (same setup, time and direction), and at
least 95% of those must have entry, stop and target within 1 tick.

| Market | Live setups | Reproduced | Geometry exact | Gate |
|---|---|---|---|---|
| MNQ | 366 | 97.0% | 100% | pass |
| MES | 329 | 97.3% | 100% | pass |
| M2K | 319 | 96.2% | 100% | pass |
| MGC | 338 | 97.0% | 100% | pass |
| MBT | 444 | 96.9% | 99.8% | pass |
| MCL | 312 | **89.1%** | **81%** | **FAIL** |

**MCL is BLOCKED.** The live TradingView feed was on a different crude
contract month than the Polygon roll: on 09-18 the live entry was 96.21 vs a
Polygon 100.77. That is a data-roll mismatch, not a detector-formula
mismatch.

**MBT is BLOCKED** because Polygon has no 2025 MBT contracts, so the
historical sample is Sep–Dec 2024 only.

The 60m and 240m configurations have no live counterpart to check against.
They use the same verified code path on resampled bars.

## Results (2026 = untouched)

| Market | Config | 2024–25 n / net / PF | 2026 n / days / net / PF / win / max DD | 2026 halves | Random-direction 95% | Random-time 95% | Result |
|---|---|---|---|---|---|---|---|
| MNQ | CONTROL | 22,447 / −$156,618 / 0.84 | 13,056 / 188 / −$133,011 / 0.83 / 32% / $139,626 | −/− | 0.86 | 0.96 | FAIL |
| MNQ | 60M WIDE | 4,960 / +$47,729 / 1.09 | 2,926 / 188 / −$2,462 / 1.00 / 49% / $28,511 | +/− | 1.09 | 1.12 | FAIL |
| MNQ | 4H WIDE | 956 / −$5,161 / 0.96 | 532 / 178 / +$2,409 / 1.02 / 50% / $17,234 | −/+ | 1.23 | 1.20 | FAIL |
| MES | CONTROL | 21,495 / −$155,693 / 0.73 | 12,568 / 188 / −$122,447 / 0.69 / 30% / $122,903 | −/− | 0.76 | 0.86 | FAIL |
| MES | 60M WIDE | 4,806 / −$6,709 / 0.98 | 2,892 / 188 / −$22,226 / 0.89 / 47% / $27,022 | −/− | 1.01 | 1.05 | FAIL |
| MES | 4H WIDE | 944 / −$20,379 / 0.75 | 538 / 183 / −$8,977 / 0.83 / 47% / $13,771 | −/+ | 1.16 | 1.20 | FAIL |
| M2K | CONTROL | 21,482 / −$93,581 / 0.68 | 12,496 / 188 / −$69,755 / 0.67 / 31% / $69,984 | −/− | 0.74 | 0.82 | FAIL |
| M2K | 60M WIDE | 4,704 / −$11,078 / 0.93 | 2,729 / 188 / −$20,469 / 0.82 / 46% / $21,143 | −/− | 1.01 | 1.06 | FAIL |
| M2K | 4H WIDE | 878 / −$1,701 / 0.96 | 481 / 179 / −$7,487 / 0.73 / 48% / $9,101 | −/+ | 1.13 | 1.12 | FAIL |
| MGC | CONTROL | 21,566 / −$142,928 / 0.82 | 12,398 / 187 / −$120,068 / 0.88 / 32% / $128,555 | −/− | 0.88 | 0.93 | FAIL |
| MGC | 60M WIDE | 4,471 / +$44,119 / 1.14 | 2,619 / 187 / +$51,653 / 1.13 / 51% / $17,558 | +/+ | 1.15 | 1.13 | FAIL |
| MGC | **4H WIDE** | 749 / +$12,751 / 1.21 | **438 / 174 / +$23,778 / 1.31 / 53% / $5,476** | +/+ | 1.28 | 1.19 | **PASS** |
| MCL | all | see BLOCKED | | | | | BLOCKED |
| MBT | all | see BLOCKED | | | | | BLOCKED |

**2026 PF differences (bootstrap 95% interval):**

| Market | 60M − CONTROL | 4H − CONTROL | 4H − 60M |
|---|---|---|---|
| MNQ | +0.16 (0.06 to 0.27) | +0.19 (−0.02 to 0.45) | +0.03 (−0.21 to 0.32) |
| MES | +0.21 (0.12 to 0.30) | +0.14 (−0.03 to 0.36) | −0.06 (−0.26 to 0.15) |
| M2K | +0.15 (0.07 to 0.24) | +0.06 (−0.10 to 0.26) | −0.09 (−0.27 to 0.12) |
| MGC | +0.25 (0.12 to 0.38) | +0.43 (0.12 to 0.84) | +0.18 (−0.17 to 0.60) |

## Verdicts

| Market | 60M WIDE | 4H WIDE |
|---|---|---|
| MNQ | PROMISING BUT UNPROVEN | REJECT |
| MES | PROMISING BUT UNPROVEN | REJECT |
| M2K | PROMISING BUT UNPROVEN | REJECT |
| MGC | PROMISING BUT UNPROVEN | **FORWARD TEST JUSTIFIED** |
| MCL | BLOCKED | BLOCKED |
| MBT | BLOCKED | BLOCKED |

**Reading the verdicts.**
- 60m WIDE beats the 15m control in every unblocked market; the control loses
  heavily everywhere. But 60m WIDE is itself at or below break-even on
  MNQ/MES/M2K, and on MGC it doesn't clear the random-direction null.
- **The one pass (MGC 4H WIDE) is marginal:**
  - 2026 PF 1.306 against a 1.30 threshold and a random-direction 95th
    percentile of 1.28.
  - 8 wide market-configurations were tested, so one marginal pass could be
    chance.
  - Profit comes from both longs (+$10,545, PF 1.28) and shorts (+$13,233,
    PF 1.33), so it isn't gold drift alone.
  - It is concentrated: the three best days are 53% of net, and January
    alone is +$10,713 of +$23,778.
  - Trades cluster by day (438 trades on 174 days), and the nulls treat
    trades as independent.

## Recommendation

- **No broad wide-geometry collector lane.** 4H is rejected on
  MNQ/MES/M2K, and 60m is only "promising" there.
- **At most one narrow forward test**, prereg-first: MGC only, 4H signals,
  the production detectors, 2×ATR stop, trailing after +1R, with fixed
  pass/fail thresholds set before any forward data.
  - It would be observation-only and scored forward.
  - It needs the standing change rule: prereg, evidence (this doc), a staged
    rollback, and an explicit operator GO.
- MCL needs its live-feed contract month reconciled before any test can be
  trusted. MBT needs a 2025 data source.
