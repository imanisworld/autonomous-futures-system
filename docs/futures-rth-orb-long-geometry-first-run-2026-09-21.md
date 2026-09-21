# Futures RTH ORB_BREAKOUT_LONG geometry + fill studies — first run 2026-09-21

**Status: RESEARCH RECORD. Every cell is WAIT / REJECT / PROMISING BUT UNPROVEN. Nothing is VALIDATED.**

Run on clean `main` `83cee1e` (#883 merged), Mac, local Polygon 5m replay
(`data/replay_polygon_5m/{MNQ,MES}`, 621 day files each, 2024-07-02 → 2026-06-26).
Cell tables are byte-identical to the pre-merge run on PR head `fa50ee1`.

```
python research/futures_rth_orb_long_geometry.py --instrument ALL --out logs/fng_v01
python research/futures_orb_breakout_time_exit.py --out logs/orbx_v01
```

## fng-v0.1 (prereg `docs/prereg-futures-rth-orb-long-geometry-2026-09-21.md`)

### MNQ

population: 753 candidates; skipped {'incomplete': 134, 'roll': 38, 'no_prior': 9}
identity: payload ORB available 753/753; equal frozen ORB 321; same-bar legacy boundary 397

| cell | n | net | exp | PF | maxDD | stop med/p90/max | >cap | detach med/p90 | H1 n/net/PF | H2 n/net/PF | top month |
|---|---:|---:|---:|---:|---:|---|---:|---|---|---|---:|
| G1_CANONICAL_OFFSET:base | 744 | -3963.06 | -5.3267 | 0.8602 | 5815.6 | 84.0/171.0/2684.0 | 193 | 36.0/123.0 | 450/-5015.0/0.7147 | 294/1051.94/1.0977 | 0.197 |
| G1_CANONICAL_OFFSET:stress | 744 | -4603.56 | -6.1876 | 0.8404 | 6118.6 | 84.0/171.0/2684.0 | 193 | 36.0/123.0 | 450/-5410.5/0.6977 | 294/806.94/1.0737 | 0.2074 |
| G2_TRIGGER_LOW:base | 744 | 928.94 | 1.2486 | 1.027 | 4248.6 | 114.0/284.0/2810.0 | 349 | 36.0/123.0 | 450/-2012.5/0.9009 | 294/2941.44/1.2081 | 0.2055 |
| G2_TRIGGER_LOW:stress | 744 | 300.44 | 0.4038 | 1.0086 | 4455.6 | 114.0/284.0/2810.0 | 349 | 36.0/123.0 | 450/-2393.5/0.8839 | 294/2693.94/1.1882 | 0.2113 |
| G3_ORB_MIDPOINT:base | 744 | 8078.44 | 10.8581 | 1.1522 | 4995.78 | 311.0/559.0/3884.0 | 737 | 36.0/123.0 | 450/2379.5/1.0759 | 294/5698.94/1.2623 | 0.2149 |
| G3_ORB_MIDPOINT:stress | 744 | 7386.94 | 9.9287 | 1.1381 | 5017.78 | 311.0/559.0/3884.0 | 737 | 36.0/123.0 | 450/1962.0/1.0621 | 294/5424.94/1.248 | 0.2178 |

### Risk-feasible view (secondary; raw gate remains authoritative)

| cell | n | net | exp | PF | maxDD | H1 n/net/PF | H2 n/net/PF |
|---|---:|---:|---:|---:|---:|---|---|
| G1_CANONICAL_OFFSET:base | 551 | -2274.24 | -4.1275 | 0.8517 | 2828.08 | 345/-2418.8/0.7547 | 206/144.56/1.0264 |
| G1_CANONICAL_OFFSET:stress | 551 | -2747.24 | -4.9859 | 0.8253 | 3233.08 | 345/-2719.8/0.7311 | 206/-27.44/0.9951 |
| G2_TRIGGER_LOW:base | 395 | -1021.8 | -2.5868 | 0.9002 | 1312.98 | 262/-540.38/0.9187 | 133/-481.42/0.8659 |
| G2_TRIGGER_LOW:stress | 395 | -1355.3 | -3.4311 | 0.8709 | 1448.0 | 262/-760.88/0.8885 | 133/-594.42/0.8384 |
| G3_ORB_MIDPOINT:base | 7 | -175.18 | -25.0257 | 0.4015 | 234.46 | 7/-175.18/0.4015 | 0/0/None |
| G3_ORB_MIDPOINT:stress | 7 | -181.68 | -25.9543 | 0.3912 | 238.46 | 7/-181.68/0.3912 | 0/0/None |

### MES

population: 785 candidates; skipped {'incomplete': 134, 'roll': 38, 'no_prior': 9}
identity: payload ORB available 785/785; equal frozen ORB 355; same-bar legacy boundary 449

| cell | n | net | exp | PF | maxDD | stop med/p90/max | >cap | detach med/p90 | H1 n/net/PF | H2 n/net/PF | top month |
|---|---:|---:|---:|---:|---:|---|---:|---|---|---|---:|
| G1_CANONICAL_OFFSET:base | 772 | -1913.53 | -2.4787 | 0.9032 | 2670.69 | 25.0/42.0/719.0 | 24 | 9.0/26.0 | 458/-1012.92/0.9142 | 314/-900.61/0.887 | 0.2075 |
| G1_CANONICAL_OFFSET:stress | 772 | -3558.53 | -4.6095 | 0.8309 | 4129.44 | 25.0/42.0/719.0 | 24 | 9.0/26.0 | 458/-1982.92/0.842 | 314/-1575.61/0.8145 | 0.2521 |
| G2_TRIGGER_LOW:base | 772 | -2601.03 | -3.3692 | 0.874 | 3625.16 | 24.0/54.0/790.0 | 58 | 9.0/26.0 | 458/-1825.42/0.8532 | 314/-775.61/0.9056 | 0.2844 |
| G2_TRIGGER_LOW:stress | 772 | -4228.53 | -5.4774 | 0.8069 | 4911.41 | 24.0/54.0/790.0 | 58 | 9.0/26.0 | 458/-2784.17/0.7885 | 314/-1444.36/0.8345 | 0.3379 |
| G3_ORB_MIDPOINT:base | 772 | 6261.47 | 8.1107 | 1.2244 | 2098.33 | 58.0/116.9/1001.0 | 359 | 9.0/26.0 | 458/3743.33/1.2343 | 314/2518.14/1.2113 | 0.2588 |
| G3_ORB_MIDPOINT:stress | 772 | 4523.97 | 5.8601 | 1.1561 | 2749.64 | 58.0/116.9/1001.0 | 359 | 9.0/26.0 | 458/2715.83/1.1634 | 314/1808.14/1.1463 | 0.2783 |

### Risk-feasible view (secondary; raw gate remains authoritative)

| cell | n | net | exp | PF | maxDD | H1 n/net/PF | H2 n/net/PF |
|---|---:|---:|---:|---:|---:|---|---|
| G1_CANONICAL_OFFSET:base | 748 | -1836.27 | -2.4549 | 0.8965 | 2214.17 | 443/-719.32/0.9296 | 305/-1116.95/0.8514 |
| G1_CANONICAL_OFFSET:stress | 748 | -3431.27 | -4.5873 | 0.8192 | 3727.92 | 443/-1658.07/0.8486 | 305/-1773.2/0.7792 |
| G2_TRIGGER_LOW:base | 714 | -2289.11 | -3.206 | 0.8575 | 2676.09 | 424/-688.26/0.9227 | 290/-1600.85/0.7762 |
| G2_TRIGGER_LOW:stress | 714 | -3791.61 | -5.3104 | 0.7798 | 4034.84 | 424/-1573.26/0.8358 | 290/-2218.35/0.7094 |
| G3_ORB_MIDPOINT:base | 413 | 912.88 | 2.2104 | 1.084 | 2154.48 | 285/-503.4/0.9361 | 128/1416.28/1.4734 |
| G3_ORB_MIDPOINT:stress | 413 | 5.38 | 0.013 | 1.0005 | 2563.9 | 285/-1133.4/0.8637 | 128/1138.78/1.359 |


### Pre-registered gate (runner output)

- G1_CANONICAL_OFFSET: **WAIT** — MNQ:H1:base_not_positive_pf; MNQ:H1:stress_net_not_positive; MNQ:H2:base_not_positive_pf; MES:H1:base_not_positive_pf; MES:H1:stress_net_not_positive; MES:H2:base_not_positive_pf; MES:H2:stress_net_not_positive
- G2_TRIGGER_LOW: **WAIT** — MNQ:H1:base_not_positive_pf; MNQ:H1:stress_net_not_positive; MES:H1:base_not_positive_pf; MES:H1:stress_net_not_positive; MES:H2:base_not_positive_pf; MES:H2:stress_net_not_positive
- G3_ORB_MIDPOINT: **WAIT** — MNQ:H1:base_not_positive_pf

### Raw-row audit (Step 3 of the handoff)

Checked on the raw `logs/fng_v01/fng_v01_*_rows.jsonl`:

| check | result |
|---|---|
| duplicate episodes within a cell | 0 |
| trigger times | all 10:00–15:55 ET (bar 6 onward) |
| next-bar-open decision price vs corpus (25 random resolved rows) | 25/25 match; every trigger bar closes above the frozen ORB high |
| 0.25 tick grid on decision/fill/stop/target | 0 misaligned |
| entry slippage | exactly 1 tick (base) on 744/744 MNQ rows |
| commission | net = gross − $1.24 on every row |
| non-EOD LOSS exit price | = stop − 1 tick on 100% of rows, all cells, both instruments |
| non-EOD WIN exit price | = target on 100% of rows |
| $/point | MNQ $2, MES $5 exact |
| H1/H2 | H1 ≤ 2025-08-28, H2 ≥ 2025-09-02 |
| stop-cap failures present in raw | yes (MNQ G3 737/744; MES G3 359/772) |
| monthly concentration (G3 base) | top month 21.5% MNQ / 25.9% MES (2025-04), under the 60% bar |

**What the audit adds beyond the summary (G3, the only net-positive cell):**

- G3's profit is **entirely EOD-flatten drift**. MNQ: EOD-flattened trades net
  **+$21,918** against a cell total of +$8,078 — i.e. trades that resolved at stop or
  target net **−$13,840**. MES: EOD +$10,138 vs total +$6,261. The 2R target hit only
  105/744 (MNQ) and 154/772 (MES). G3 is "buy the ORB breakout, hold to 16:00 with a
  median 311-tick (78-point) stop": the same drift `fns-v0.1` measured, with a
  bracket attached.
- **Not realizable with one contract:** 364/459 (MNQ) and 355/472 (MES) later
  same-session G3 entries occur while the earlier trade is still open. The per-episode
  P&L therefore overstates what a 1-contract account could take.
- MNQ G3 is 737/744 over the 120-tick stop cap; the risk-feasible view is n = 7.

### Classification

| geometry | class | why |
|---|---|---|
| G1 canonical offset | **REJECT** | net negative and PF < 1 in 7 of 8 instrument×half×slippage cells, both instruments |
| G2 trigger-bar low | **REJECT** | net negative, PF < 1 in 6 of 8 cells; MNQ H2 only positive cell |
| G3 ORB midpoint | **WAIT** | net positive in all four cells at both slippage levels, but MNQ H1 PF 1.076 < 1.10 fails rule 2; profit is EOD drift; stops outside cap; overlapping entries; pooled PF 1.14–1.22 ≪ null p95 1.94 |

## orbx-v0.1 — 60-minute time exit (prereg `docs/prereg-futures-orb-breakout-long-time-exit-2026-09-21.md`)

One trade per session per instrument, next-bar-open entry, stop = ORB low (primary) or
ORB midpoint (secondary), exit at the close of the 12th bar after entry or the stop;
$1.50 RT; 0/1/2 ticks adverse per side. Cells at **2 ticks**:

| cell | n | win% | net $ | exp $ | PF | maxDD $ | stop share | med bars | stop ticks med |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ORB_LOW:2:MNQ:H1 | 168 | 0.5714 | 1796.5 | 10.6935 | 1.1854 | 2243.5 | 0.0833 | 12.0 | 526.5 |
| ORB_LOW:2:MNQ:H2 | 117 | 0.6325 | 684.5 | 5.8504 | 1.0889 | 2119.5 | 0.0769 | 12 | 690.0 |
| ORB_LOW:2:POOLED | 585 | 0.5556 | 3152.25 | 5.3885 | 1.117 | 3339.75 | 0.106 | 12 | 248.0 |
| ORB_LOW:2:MES:H1 | 178 | 0.4888 | 503.0 | 2.8258 | 1.0873 | 1352.0 | 0.1573 | 12.0 | 93.5 |
| ORB_LOW:2:MES:H2 | 122 | 0.5574 | 168.25 | 1.3791 | 1.0443 | 902.0 | 0.0902 | 12.0 | 119.0 |
| ORB_MID:2:MNQ:H1 | 168 | 0.5298 | 1560.25 | 9.2872 | 1.1613 | 1962.75 | 0.2917 | 12.0 | 286.75 |
| ORB_MID:2:MNQ:H2 | 117 | 0.5983 | 743.5 | 6.3547 | 1.1051 | 2191.5 | 0.2137 | 12 | 373.5 |
| ORB_MID:2:POOLED | 585 | 0.5111 | 1344.97 | 2.2991 | 1.0513 | 4384.89 | 0.2786 | 12 | 137.0 |
| ORB_MID:2:MES:H1 | 178 | 0.4213 | -1582.01 | -8.8877 | 0.7425 | 1872.01 | 0.3371 | 12.0 | 52.25 |
| ORB_MID:2:MES:H2 | 122 | 0.5328 | 623.23 | 5.1084 | 1.1884 | 611.38 | 0.2377 | 12.0 | 64.75 |

**Verdict per its own prereg: PROMISING BUT UNPROVEN, no forward lane.** Primary pass
(expectancy > 0 in all four cells at 2 ticks) holds; pooled PF **1.117** vs null p95
1.94 fails the second gate by a wide margin. Expectancy ≈ $5/trade with a stop 500–700
ticks wide on MNQ; this is the drift statistic with a very wide stop, not an edge that
would survive a cap.

## Overall read

The `fns-v0.1` observation (ORB_BREAKOUT_LONG above drift in all four cells) is
real as a *coverage* fact and does not convert into a tradeable geometry under any of
four pre-registered exits with realistic fills. The July 2026 rejection is **extended**
to this population and these designs. No further futures ORB geometry cell may be
added post hoc.

Defects found in this run: none in the runner. (The `fns-v0.1` UTC/DST window defect was
found and fixed before that record and is inherited correctly here.)
