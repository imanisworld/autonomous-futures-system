# Cross-market improvement grid — what changes would improve the trades? (2026-09-23)

**RESEARCH ONLY. No runtime, paper, broker or deployment change.** This is
in-sample exploration with an out-of-sample read. Nothing here is evidence
under any preregistration. A promising idea needs its own prereg and a
forward test before it can trade, even on paper.

## Question

The operator asked, for MCL and MGC first, then MES, M2K, MBT and MNQ: would
a different timeframe, entry, stop or exit make the setups profitable?

**Note.** On 2026-09-18 the operator parked the cross-instrument campaign
("no tuning, do not promote post-hoc positive cells"). This study was run on
the operator's new request of 2026-09-23. It keeps to the spirit of that
ruling: nothing found here is promoted.

## Method

**Data.** Polygon 15m bars, 2024-09-17 → 2026-09-22.
- MNQ, MES and M2K use the unchanged `scripts/polygon_to_replay.py`
  quarterly roll.
- MCL, MGC and MBT use a causal volume-front roll: each UTC day uses the
  dated contract with the highest volume on the previous day. That chain
  matches the tranche-2 probe (PR #634).

**Grid, per market.** About 76,000 cells:

| Dimension | Values |
|---|---|
| Timeframe | 15m, 30m, 60m, 240m (session-anchored) |
| Setup | 2-2 continuation, 2-2 reversal, 3-2-2 reversal, inside-bar break, EMA pullback, 20-bar breakout, Bollinger fade, prior-day high/low break; on 15m only, NY opening range, London opening range and (MCL) EIA Wednesday 10:30 ET break |
| Entry | next bar open, stop order past the signal bar, limit at the signal-bar midpoint |
| Stop | signal-bar extreme, 1 / 1.5 / 2 × ATR(14) |
| Exit | 1R, 1.5R, 2R, 3R, trail after +1R, time exit (4 bars) |
| Filter | none, 200-EMA trend, EMA 9/21/55 stack |
| Session | Asia, London, NY, all |
| Direction | long, short, both |

**Simulation.**
- Runs on 15m bars.
- When the stop and the target both touch in the same bar, the stop is
  assumed to fill first.
- Costs are the admission prereg's (#945): MCL and MGC $4.00, M2K $2.48 and
  MBT $7.00 per trade. MES is $3.98 and MNQ $2.48, meaning commission plus
  2 ticks.

**Validation.**
- Cells are picked on **in-sample** data only (before 2026-01-01) by a fixed
  rule:
  - n ≥ 150;
  - ≥ 60 days;
  - PF ≥ 1.30;
  - a plateau: ≥ 60% of the neighboring stop/exit variants have PF ≥ 1.10.
- The rule keeps the best cell per setup and timeframe, up to 12 picks per
  market.
- Each pick is read **once** on 2026 data (out of sample, "OOS") and must
  beat:
  - null A, the same trades with random direction (500 draws);
  - null B, random entry bars with the same rules (200 draws).
- PASS needs:
  - OOS n ≥ 40;
  - PF ≥ 1.30;
  - net > 0;
  - both halves > 0;
  - PF above the 95th percentile of both nulls.

## Results

| Market | Picks | PASS | Best OOS read | Note |
|---|---|---|---|---|
| MGC (gold) | 12 | **0** | 60m 2-2 continuation, long, NY: OOS PF 1.96, but below random direction (2.26) | the gains came from gold rising, not from the setup |
| MCL (crude) | 1 | **0** | 30m EMA pullback, long, NY: OOS PF 1.23 | every knob's median PF < 1; costs are 20–40% of a 15m risk unit |
| MES | 12 | **0** | 60m 2-2 reversal, long, Asia: OOS PF 1.21 | most picks fell below 1.0 on 2026 |
| M2K | 5 | **0** | 15m inside-bar break, long, London, above the 200 EMA: OOS PF 1.60, both halves +, equal to null A (1.60) | the closest near miss of the night |
| MNQ | 12 | **0** | 15m 2-2 reversal short, NY, EMA stack: 1.49; 15m 20-bar breakout, NY, 1R: 1.46 | beat one null each, not both |
| MBT (bitcoin) | 5 | **0** | all < 1.0 on 2026 | **data-limited:** Polygon serves no MBT contracts for 2025, so the corpus has 253 days with a 15-month hole |

**0 of 47 picks pass.**
- In every market, in-sample picks survive OOS more often than random cells
  do (for example MNQ 16.5% vs 6.6%, M2K 14.3% vs 1.8%). The survivors
  still don't beat the random-entry or random-direction versions of
  themselves.
- **Setup timing is not shown to add value** once a trade is long or short
  this market in this period.

## What consistently helps or hurts (median in-sample PF across cells)

The table covers cells with n ≥ 100, all sessions, both directions and no
filter.

| Knob | Consistent finding |
|---|---|
| Stop | A wider **2 × ATR stop beats the signal-bar stop in all six markets**. For example, MCL 0.81 vs 0.69, MBT 0.90 vs 0.74, MGC 1.05 vs 0.96. |
| Exit | **A 3R target or trailing after +1R is at least as good as 1R and the 4-bar time exit in all six markets, and better in five.** On MCL, trailing ties 1R at 0.76. |
| Timeframe | **15m is the worst timeframe in five of six markets.** 240m is best on MGC (1.29) and MBT (1.23). On MNQ, M2K and MES the gap is small. |
| Setup | **Bollinger fades and 3-2-2 reversals are among the worst** almost everywhere. The **NY opening-range breakout is weak** (MNQ 0.87, M2K 0.64), in line with published falsification work on MNQ ORB. Inside-bar breaks and the London opening range sit near the top. |
| Entry | Small differences; no entry type wins consistently. |

**Implication for the observation lane.** The live lane
(`cross_instrument_observation_v1`) records setups with a 15m, bar-extreme
stop and 2R target. That is the weakest corner of this grid in every market.
It matches the lane's losing first week: grid baseline MGC 2-2 continuation
PF 0.79–0.88 vs a live 0.81. Changing the geometry alone did not produce a
setup that survives out of sample.

## Caveats

- The setups are simple re-implementations, not the live detectors.
  - Example: this grid's MNQ Asia 2-2 continuation with EMA stack and 1.5R
    gives PF ≈ 1.00 in-sample and 1.01 OOS.
  - The 2026-09-21 detector-based grid reported 1.25 for its version.
  - The two differ in entry, stop and alignment definitions, so neither
    replaces H6's forward test.
- The monthly roll seams (MCL, MBT) are spliced without back-adjustment.
- About 76,000 cells were explored per market, so any single in-sample
  number here is heavily selection-biased. Only the OOS read with both nulls
  counts, and nothing passed it.

## What would be worth a preregistered forward test (hypotheses only)

1. **Wide-geometry observation variant.**
   - Record the same setups with a 2 × ATR stop and a trailing exit on
     60m/240m, next to the current 15m / bar stop / 2R records.
   - The live campaign would then collect forward evidence on the geometry
     this grid prefers.
   - This changes collector config, so it needs a prereg, the standing
     change rule and an operator GO.
2. **The near misses:**
   - M2K 15m inside-bar break, long, London, above the 200 EMA;
   - MNQ 15m 2-2 reversal short, NY, EMA stack;
   - MNQ 15m 20-bar breakout, NY, 1R, 200-EMA trend.
   - Each is a single hypothesis with fixed rules, scored forward only.

Research scripts and per-cell CSVs are kept out of the public repo.
