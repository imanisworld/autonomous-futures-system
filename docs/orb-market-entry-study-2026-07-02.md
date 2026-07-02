# ORB Market-Entry Study — 2026-07-02

## Question

The retest lane (#142) was rejected: ORB retests rarely happen (1.6–8.6% fill),
so the edge lives in breaks that don't come back. The remaining entry candidate
is immediate entry at the real market price at authorization, paying the gap
(market − level) as slippage, with a predefined cap grid {2, 4, 8, unbounded}
ticks. 622-day causal study on the #142 5-minute dataset; arms are the exact
15-minute-authorized ORB brackets from the baseline replay journals; original
structural stop; 1 tick adverse slip; pessimistic ordering; walk-forward
midpoint split. Reproduce: `python3 scripts/orb_market_entry_study.py`.

## Results (runner exit, 1.0R activation / 0.5R trail)

| Leg | MES net (622d) | MES halves exp | MNQ net | MNQ halves exp |
|---|---:|---:|---:|---:|
| assumed-fill fiction | $19,103 (78% WR) | 42.4 / 32.4 | $17,176 (78% WR) | 65.0 / 44.8 |
| market, cap 2t | $371 | 18.9 / −6.7 | $226 (n=8) | −10.1 / 41.0 |
| market, cap 4t | $738 | 19.7 / −1.6 | $251 (n=14) | 6.8 / 21.0 |
| market, cap 8t | $1,009 (n=122) | 12.8 / 3.6 | $142 (n=19) | 21.5 / 3.7 |
| market, unbounded | −$543 (PF 0.97) | 1.8 / −4.1 | **$7,129 (n=300, PF 1.79)** | **31.2 / 16.6** |

- **MNQ unbounded market entry is robust:** 7 of 8 quarters positive, both
  strategies positive (breakout +$1,044/60, reclaim +$6,085/240), avg adverse
  gap 71 ticks absorbed by MNQ's runner magnitude.
- **MES is not promotable:** only cap-8 passes both halves (single-cell island,
  second half +$3.57/trade), the edge is entirely orb_reclaim (breakout
  −$162/13), and unbounded is negative — consistent with the #94 re-anchor
  reversal.
- The assumed-fill rows quantify the fiction: realistic pricing costs ~57%
  (MNQ) to ~95% (MES) of the believed edge.

## The decisive control: exit mode

MNQ unbounded market entry re-run with the **static** (current live) exit:
net $465, expectancy $1.51, halves +3.45 / −0.43, PF 1.06 — despite 59% WR,
avg win $46 < avg loss $64. **The entry fix is worthless without the runner
exit; the runner exit is what converts these fills into asymmetry.**

## Recommendation (operator decision, nothing enabled by this study)

1. Sequence: runner exit first (shadow evidence → `EXIT_MODE=runner_shadow` →
   `runner_live` in demo), THEN wire MNQ ORB market entry as a per-strategy
   entry mode (small PR; do NOT bump the global
   `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ` — that changes every strategy's fills).
2. MES ORB stays dead live for now; pdh_reclaim carries MES.
3. Do not revisit unbounded market entry for MES (16.7% WR live in #94; negative
   here at 622d).
