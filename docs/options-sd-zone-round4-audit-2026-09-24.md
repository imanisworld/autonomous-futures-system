# S/D zone Round 4 — independent audit (2026-09-24)

**Research only.** No runtime, strategy, risk, broker, scanner, scheduler, deployment or execution
change. Nothing here is promoted. Core rule: no proof, no promotion.

## 1. What was audited

- **Archive: found.** The private local archive `afs-private-archive/sdzones4-20260924/` holds 39 files: prereg with hash, sources, code, cached input bars, 12 per-cell ledgers, results JSON (5 and 500 seeds), logs and tables.
  - SHA-256 and size of every file are in `research/sd_zone_round4/round4_archive_manifest.json`.
  - The archive was not modified; all work ran on a scratch copy.
- **Round-4 prereg:** `PREREG_FROZEN.md`, sha256 `6d9b9aaa25671fa321bbfe3538d52ab7032730bdd874f823baf401a190107c44`.
  - The stored hash matches, and the hash file records the freeze at 2026-09-24 13:43:15Z.
  - Byte copy: `research/sd_zone_round4/ROUND4_PREREG_FROZEN.md`.
- **Frozen code:** byte copy with the same sha256s in `research/sd_zone_round4/frozen_round4_code/`.
- **Earlier rounds:** rounds 1–3 (archives `sdzones-`, `sdzones2-`, `sdzones3-20260924`) used the **same bars and the same IS/OOS split**.
  - Round 4's R1 geometry comes from round 3, which had already looked at the same OOS window.
  - Round 4's LOOSE rule was added because round 3's STRICT pattern set was too small to score.

## 2. Data and provenance

| item | value |
|---|---|
| source | `data/replay_polygon_5m/{MNQ,MES}/*.jsonl`: Polygon futures aggregates, 5m, raw front month, rolled 8 calendar days before the 3rd-Friday expiry. Gitignored; 621 day-files per instrument |
| corpus fingerprint | sha256 of all day-files concatenated in sorted order: MNQ `03794c2d75b80139d1f41ac0e6d7a0d9abcad82e1013020e53a55752625f43d5`, MES `1b54a3c80173a1eb1763a02cd51f061a92a935f2bacbde7495581c27b51fecf4` |
| span | 2024-07-02 13:30Z → 2026-06-26 20:55Z. MNQ 140,115 bars, MES 140,111 bars. 0 bad high/low rows |
| cache | `cache/{MNQ,MES}_5m.npz`, built by round-1 `load.py`; the same cache in all four rounds. **Rebuilt from the raw files: identical, array for array** |
| roll seams | 00:00Z on 2024-09-12, 2024-12-12, 2025-03-13, 2025-06-12, 2025-09-11, 2025-12-11, 2026-03-12, 2026-06-11. Zones never cross a seam |
| split | Trading day = (ET + 6h) date. IS 2024-07-02 … 2025-09-10; OOS 2025-09-11 … 2026-06-26; OOS halves split at the median OOS trading day. A trade's period = the trading day of its entry bar |
| costs | MNQ $2/pt, MES $5/pt, tick 0.25, $1.48 round trip, 1 contract |

## 3. Exact rules

Taken from the frozen prereg and confirmed in the code and by an independent re-implementation. Demand is shown; supply is the exact mirror.

**Pattern.**
- **A (bar i−1):** red and a Strat 2D: l_A < l_{i−2} and h_A ≤ h_{i−2}.
- **B (bar i):** green and a 2U: h_B > h_A and l_B ≥ l_A.
- **Range-engulfing close:** c_B > h_A.
- **Size:** ATR20 = Wilder RMA(20) of 5m true range, taken at bar **i−2**.
  - STRICT: body(A) ≥ 1.5·ATR20 and body(B) ≥ 1.5·ATR20.
  - LOOSE: body(B) ≥ 1.5·ATR20 only.

**Zones.**
- **FULL** = [l_B, h_B].
- **EDGE** = [min(l_A, l_B), min(o_B, c_B)]. Supply: [max(o_B, c_B), max(h_A, h_B)].
- Zones narrower than 1 tick are dropped.

**Context.**
- ALL-DAY = any creation time.
- NY-OPEN = B starts 09:30–10:55 ET.
- Lifetime runs through the 15:55 ET bar of the first RTH session that has any bar after B.

**Hold entry (H2 primary, H15 declared variant).**
1. **Arm:** a close above the zone top.
   - EDGE is armed at B itself.
   - FULL needs a later close above h_B.
   - A close below the bottom first kills the zone.
2. **Touch:** the first bar after arming with low ≤ top.
3. **Hold:** the touch bar or the next bar closes above the top.
   - A close below the bottom = broken.
   - Still inside after the extra bar = no hold.
   - First retest only.
4. **Entry:** the next bar's open + 1 tick. That bar must start 09:30–15:50 ET and fall within the lifetime.
5. **Stop:** min(bottom, l_touch, l_hold) − 1 tick, filled with 1 more tick of slippage.
6. **Target:** entry + 2R (H2) or + 1.5R (H15).
   - Fills only on a 1-tick trade-through.
   - The stop wins when both are hit in the same bar.
7. **Exit:** at the 15:55 ET bar close if neither stop nor target is hit.
8. **One position** per instrument per measure: greedy by entry bar; a new trade only after the previous exit.

**T2 touch-fill comparison** (same zones).
- Limit at the near edge, filled on the touch bar only on a 1-tick trade-through.
- Stop = far edge ∓ 1 tick.
- The stop can trigger on the fill bar; the target only from the next bar.

## 4. Reproduction

- **Verification scripts rerun.** `verify_detectors4.py` gives 0 synthetic failures. `verify_engine4.py` gives 0 mismatches between the slow and vectorised engines on every real zone plus one random draw, across all 12 cells. Both logs are identical to the archived logs.
- **Full rerun.** `run4.py 500` produces a **byte-identical** `results_500seeds.json`, and all **12 ledgers are identical array for array**. **Reproduction: PASS.**
- **Independent re-implementation.** `research/sd_zone_round4/independent_l_full.py` uses plain loops written only from the prereg text and imports no archived engine or zone code. It matches the archived ledgers **trade for trade** (zone set, entry bar, $ P&L) on 8 cells: MNQ and MES × L_FULL and L_EDGE × H2 and H15.

## 5. Causality and fill audit

| question | finding |
|---|---|
| Future information in zone formation? | **No.** ATR comes from bar i−2. The zone is complete at B's close. Arming, touch and hold all use later closes |
| Entry attainable at decision time? | **Yes.** Market order at the next bar's open + 1 tick adverse, after the hold close. No price from inside a bar is credited |
| Favourable fills? | **No.** Stops fill 1 tick beyond the level. Targets need a 1-tick trade-through. The stop wins ties. T2 fills a demand limit **at the top even when the bar opens below it**, which is conservative |
| Stop uses unknown bars? | **No.** Only the touch and hold bars, both closed before entry |
| Outcome-selected population within Round 4? | **No.** The 6 definitions, 3 measures, pass rule and verdict map were frozen before any count or P&L. The prereg is unchanged after scoring |
| Outcome-informed design **across rounds**? | **Yes — material caveat.** Rounds 1–3 used the same data and the same OOS window (96 tests cumulatively). Round 3 saw its R1 geometry do well in that OOS window. **Round 4's OOS is therefore not blind** |
| Duplicates / overlap | One position per instrument per measure, so trades never overlap in time. Overlapping zones are allowed, but each zone gets at most one retest and one trade. MNQ L_FULL H2: at most 2 trades on any day, and only 7 of 129 trade days have 2 |
| Pooling | None. MNQ and MES are separate throughout |
| Friction | 1 tick on entry, 1 tick on stops, $1.48 round trip. No queue model for T2 beyond the 1-tick trade-through. No stress beyond 1 tick |
| Trend drift | MNQ rose about 46% over the sample. The null is time-matched with the same direction mix. MNQ L_FULL H2 over 2 years: demand +$3,370, supply +$2,896. In OOS, supply is only +$84 |
| MAE / MFE | Not in the archive; computed by this audit from the bars (see §6) |

## 6. Results by cell

Numbers come from the reproduced ledgers. Concentration and R were computed by this audit.

- **Top-5% n** = ceil(5% × n).
- **Null pct** = OOS PF percentile against 500 time-matched random-zone seeds. The frozen gate is ≥ 95.
- **Frozen verdict:** PASS / FAIL / INSUFFICIENT (< 30 OOS trades).

| inst | def | measure | period | n | W/L | net $ | PF | exp $ | max DD $ | median R | top-1 share | net ex top-1 | net ex top-3 | top-5% n | net ex top-5% | null pct (OOS) | frozen verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MNQ | S_FULL | H2 | ALL | 14 | 9/5 | 773 | 1.753 | 55.2 | 403 | 0.32 | 0.518 | 373 | -370 | 1 | 373 |  |  |
| MNQ | S_FULL | H2 | IS | 9 | 6/3 | 406 | 1.773 | 45.08 | 277 | 0.26 | 0.918 | 33 | -392 | 1 | 33 |  |  |
| MNQ | S_FULL | H2 | OOS | 5 | 3/2 | 367 | 1.731 | 73.42 | 403 | 0.38 | 1.09 | -33 | -502 | 1 | -33 | 72.4 | INSUFFICIENT |
| MNQ | S_FULL | H15 | ALL | 14 | 9/5 | 738 | 1.718 | 52.7 | 403 | 0.32 | 0.729 | 200 | -449 | 1 | 200 |  |  |
| MNQ | S_FULL | H15 | IS | 9 | 6/3 | 233 | 1.444 | 25.88 | 277 | 0.26 | 1.198 | -46 | -392 | 1 | -46 |  |  |
| MNQ | S_FULL | H15 | OOS | 5 | 3/2 | 505 | 2.006 | 100.97 | 403 | 0.38 | 1.065 | -33 | -502 | 1 | -33 | 77.4 | INSUFFICIENT |
| MNQ | S_FULL | T2 | ALL | 18 | 10/8 | 982 | 1.677 | 54.58 | 620 | 0.27 | 0.517 | 475 | -286 | 1 | 475 |  |  |
| MNQ | S_FULL | T2 | IS | 10 | 4/6 | -518 | 0.482 | -51.83 | 620 | -1.01 |  | -823 | -956 | 1 | -823 |  |  |
| MNQ | S_FULL | T2 | OOS | 8 | 6/2 | 1501 | 4.331 | 187.58 | 284 | 1.99 | 0.338 | 993 | 232 | 1 | 993 | 97.0 | INSUFFICIENT |
| MNQ | S_EDGE | H2 | ALL | 13 | 5/8 | -404 | 0.617 | -31.06 | 514 | -1.01 |  | -679 | -898 | 1 | -679 |  |  |
| MNQ | S_EDGE | H2 | IS | 6 | 1/5 | -315 | 0.202 | -52.56 | 315 | -1.02 |  | -395 | -288 | 1 | -395 |  |  |
| MNQ | S_EDGE | H2 | OOS | 7 | 4/3 | -88 | 0.866 | -12.62 | 418 | 1.96 |  | -364 | -583 | 1 | -364 | 56.2 | INSUFFICIENT |
| MNQ | S_EDGE | H15 | ALL | 13 | 8/5 | 24 | 1.028 | 1.81 | 418 | 1.46 | 8.774 | -183 | -463 | 1 | -183 |  |  |
| MNQ | S_EDGE | H15 | IS | 6 | 4/2 | 256 | 2.456 | 42.69 | 91 | 1.47 | 0.641 | 92 | -107 | 1 | 92 |  |  |
| MNQ | S_EDGE | H15 | OOS | 7 | 4/3 | -233 | 0.647 | -33.23 | 418 | 1.46 |  | -439 | -602 | 1 | -439 | 42.6 | INSUFFICIENT |
| MNQ | S_EDGE | T2 | ALL | 22 | 6/16 | -102 | 0.854 | -4.64 | 239 | -1.04 |  | -343 | -583 | 2 | -474 |  |  |
| MNQ | S_EDGE | T2 | IS | 12 | 2/10 | -146 | 0.536 | -12.15 | 160 | -1.09 |  | -277 | -309 | 1 | -277 |  |  |
| MNQ | S_EDGE | T2 | OOS | 10 | 4/6 | 44 | 1.114 | 4.37 | 239 | -1.02 | 5.504 | -197 | -355 | 1 | -197 | 67.0 | INSUFFICIENT |
| MNQ | L_FULL | H2 | ALL | 136 | 70/66 | 6267 | 1.632 | 46.08 | 1534 | 0.09 | 0.128 | 5462 | 4188 | 7 | 2006 |  |  |
| MNQ | L_FULL | H2 | IS | 85 | 42/43 | 3594 | 1.635 | 42.28 | 1192 | -0.06 | 0.167 | 2994 | 1901 | 5 | 887 |  |  |
| MNQ | L_FULL | H2 | OOS | 51 | 28/23 | 2673 | 1.629 | 52.4 | 1534 | 0.1 | 0.301 | 1868 | 612 | 3 | 612 | 91.0 | FAIL |
| MNQ | L_FULL | H15 | ALL | 138 | 75/63 | 6473 | 1.659 | 46.91 | 1210 | 0.23 | 0.106 | 5784 | 4653 | 7 | 2785 |  |  |
| MNQ | L_FULL | H15 | IS | 86 | 45/41 | 3499 | 1.628 | 40.69 | 842 | 0.17 | 0.197 | 2811 | 1833 | 5 | 1020 |  |  |
| MNQ | L_FULL | H15 | OOS | 52 | 30/22 | 2974 | 1.701 | 57.19 | 1210 | 0.42 | 0.203 | 2371 | 1389 | 3 | 1389 | 91.6 | FAIL |
| MNQ | L_FULL | T2 | ALL | 194 | 88/106 | 6791 | 1.505 | 35.0 | 1030 | -1.01 | 0.117 | 5995 | 4580 | 10 | 1366 |  |  |
| MNQ | L_FULL | T2 | IS | 116 | 48/68 | 2571 | 1.341 | 22.17 | 1030 | -1.01 | 0.307 | 1782 | 810 | 6 | -369 |  |  |
| MNQ | L_FULL | T2 | OOS | 78 | 40/38 | 4220 | 1.714 | 54.1 | 695 | 0.07 | 0.189 | 3424 | 2291 | 4 | 1839 | 97.8 | PASS |
| MNQ | L_EDGE | H2 | ALL | 156 | 53/103 | 339 | 1.047 | 2.17 | 1622 | -1.02 | 1.044 | -15 | -678 | 8 | -1992 |  |  |
| MNQ | L_EDGE | H2 | IS | 94 | 27/67 | -741 | 0.818 | -7.88 | 1622 | -1.03 |  | -1071 | -1564 | 5 | -1929 |  |  |
| MNQ | L_EDGE | H2 | OOS | 62 | 26/36 | 1080 | 1.343 | 17.42 | 835 | -1.01 | 0.327 | 726 | 76 | 4 | -183 | 90.8 | FAIL |
| MNQ | L_EDGE | H15 | ALL | 156 | 63/93 | 400 | 1.061 | 2.56 | 1114 | -1.02 | 0.663 | 135 | -362 | 8 | -1360 |  |  |
| MNQ | L_EDGE | H15 | IS | 94 | 34/60 | -185 | 0.949 | -1.97 | 1114 | -1.02 |  | -432 | -812 | 5 | -1151 |  |  |
| MNQ | L_EDGE | H15 | OOS | 62 | 29/33 | 585 | 1.201 | 9.44 | 791 | -1.01 | 0.453 | 320 | -167 | 4 | -361 | 84.2 | FAIL |
| MNQ | L_EDGE | T2 | ALL | 269 | 54/215 | -2370 | 0.618 | -8.81 | 2431 | -1.09 |  | -2611 | -3031 | 14 | -4474 |  |  |
| MNQ | L_EDGE | T2 | IS | 162 | 34/128 | -1051 | 0.68 | -6.49 | 1423 | -1.09 |  | -1291 | -1711 | 9 | -2345 |  |  |
| MNQ | L_EDGE | T2 | OOS | 107 | 20/87 | -1319 | 0.55 | -12.33 | 1621 | -1.07 |  | -1485 | -1800 | 6 | -2174 | 26.2 | FAIL |
| MNQ | L_FULL_NY | H2 | ALL | 78 | 42/36 | 2956 | 1.434 | 37.9 | 1613 | 0.2 | 0.272 | 2152 | 900 | 4 | 418 |  |  |
| MNQ | L_FULL_NY | H2 | IS | 44 | 24/20 | 865 | 1.248 | 19.67 | 1229 | 0.2 | 0.667 | 288 | -474 | 3 | -474 |  |  |
| MNQ | L_FULL_NY | H2 | OOS | 34 | 18/16 | 2091 | 1.628 | 61.49 | 1613 | 0.24 | 0.385 | 1286 | 130 | 2 | 613 | 92.4 | FAIL |
| MNQ | L_FULL_NY | H15 | ALL | 78 | 46/32 | 3636 | 1.565 | 46.62 | 1591 | 0.41 | 0.166 | 3033 | 1991 | 4 | 1514 |  |  |
| MNQ | L_FULL_NY | H15 | IS | 44 | 27/17 | 1371 | 1.44 | 31.16 | 837 | 0.51 | 0.316 | 938 | 367 | 3 | 367 |  |  |
| MNQ | L_FULL_NY | H15 | OOS | 34 | 19/15 | 2265 | 1.682 | 66.63 | 1591 | 0.4 | 0.266 | 1662 | 620 | 2 | 1125 | 94.2 | FAIL |
| MNQ | L_FULL_NY | T2 | ALL | 116 | 53/63 | 3321 | 1.357 | 28.63 | 1109 | -1.01 | 0.24 | 2526 | 1393 | 6 | 76 |  |  |
| MNQ | L_FULL_NY | T2 | IS | 63 | 27/36 | 180 | 1.038 | 2.85 | 1109 | -1.01 | 1.85 | -153 | -784 | 4 | -1087 |  |  |
| MNQ | L_FULL_NY | T2 | OOS | 53 | 26/27 | 3142 | 1.676 | 59.27 | 699 | -0.41 | 0.253 | 2346 | 1213 | 3 | 1213 | 97.0 | PASS |
| MNQ | L_EDGE_NY | H2 | ALL | 77 | 22/55 | -978 | 0.778 | -12.7 | 1082 | -1.02 |  | -1311 | -1958 | 4 | -2217 |  |  |
| MNQ | L_EDGE_NY | H2 | IS | 40 | 9/31 | -941 | 0.547 | -23.53 | 951 | -1.03 |  | -1271 | -1615 | 2 | -1449 |  |  |
| MNQ | L_EDGE_NY | H2 | OOS | 37 | 13/24 | -37 | 0.984 | -0.99 | 1082 | -1.02 |  | -370 | -946 | 2 | -687 | 56.4 | FAIL |
| MNQ | L_EDGE_NY | H15 | ALL | 77 | 29/48 | -617 | 0.845 | -8.01 | 1082 | -1.01 |  | -866 | -1350 | 4 | -1545 |  |  |
| MNQ | L_EDGE_NY | H15 | IS | 40 | 14/26 | -423 | 0.768 | -10.58 | 669 | -1.02 |  | -670 | -954 | 2 | -820 |  |  |
| MNQ | L_EDGE_NY | H15 | OOS | 37 | 15/22 | -194 | 0.91 | -5.23 | 1082 | -1.01 |  | -443 | -875 | 2 | -680 | 47.0 | FAIL |
| MNQ | L_EDGE_NY | T2 | ALL | 125 | 26/99 | -1623 | 0.589 | -12.98 | 1684 | -1.05 |  | -1850 | -2209 | 7 | -2790 |  |  |
| MNQ | L_EDGE_NY | T2 | IS | 67 | 15/52 | -575 | 0.683 | -8.58 | 956 | -1.05 |  | -802 | -1127 | 4 | -1238 |  |  |
| MNQ | L_EDGE_NY | T2 | OOS | 58 | 11/47 | -1048 | 0.51 | -18.07 | 1364 | -1.05 |  | -1213 | -1528 | 3 | -1528 | 21.2 | FAIL |
| MES | S_FULL | H2 | ALL | 9 | 4/5 | -61 | 0.864 | -6.76 | 155 | -1.02 |  | -266 | -400 | 1 | -266 |  |  |
| MES | S_FULL | H2 | IS | 3 | 1/2 | -94 | 0.393 | -31.48 | 155 | -1.03 |  | -155 | 0 | 1 | -155 |  |  |
| MES | S_FULL | H2 | OOS | 6 | 3/3 | 34 | 1.115 | 5.6 | 141 | -0.37 | 6.091 | -171 | -292 | 1 | -171 | 62.4 | INSUFFICIENT |
| MES | S_FULL | H15 | ALL | 9 | 4/5 | 63 | 1.141 | 6.99 | 155 | -1.02 | 5.22 | -266 | -400 | 1 | -266 |  |  |
| MES | S_FULL | H15 | IS | 3 | 1/2 | -94 | 0.393 | -31.48 | 155 | -1.03 |  | -155 | 0 | 1 | -155 |  |  |
| MES | S_FULL | H15 | OOS | 6 | 3/3 | 157 | 1.539 | 26.23 | 141 | -0.37 | 2.088 | -171 | -292 | 1 | -171 | 72.2 | INSUFFICIENT |
| MES | S_FULL | T2 | ALL | 18 | 8/10 | 256 | 1.364 | 14.21 | 241 | -0.69 | 1.167 | -43 | -405 | 1 | -43 |  |  |
| MES | S_FULL | T2 | IS | 11 | 4/7 | -165 | 0.685 | -15.0 | 241 | -1.02 |  | -339 | -471 | 1 | -339 |  |  |
| MES | S_FULL | T2 | OOS | 7 | 4/3 | 421 | 3.346 | 60.13 | 71 | 0.42 | 0.709 | 122 | -132 | 1 | 122 | 91.8 | INSUFFICIENT |
| MES | S_EDGE | H2 | ALL | 15 | 4/11 | -333 | 0.441 | -22.23 | 507 | -1.04 |  | -482 | -594 | 1 | -482 |  |  |
| MES | S_EDGE | H2 | IS | 7 | 1/6 | -182 | 0.012 | -25.94 | 182 | -1.09 |  | -184 | -148 | 1 | -184 |  |  |
| MES | S_EDGE | H2 | OOS | 8 | 3/5 | -152 | 0.632 | -18.98 | 383 | -1.03 |  | -300 | -412 | 1 | -300 | 47.9 | INSUFFICIENT |
| MES | S_EDGE | H15 | ALL | 15 | 4/11 | -358 | 0.399 | -23.9 | 480 | -1.04 |  | -469 | -557 | 1 | -469 |  |  |
| MES | S_EDGE | H15 | IS | 7 | 1/6 | -140 | 0.237 | -20.05 | 165 | -1.09 |  | -184 | -148 | 1 | -184 |  |  |
| MES | S_EDGE | H15 | OOS | 8 | 3/5 | -218 | 0.471 | -27.26 | 383 | -1.03 |  | -329 | -412 | 1 | -329 | 39.5 | INSUFFICIENT |
| MES | S_EDGE | T2 | ALL | 18 | 4/14 | -258 | 0.199 | -14.33 | 258 | -1.12 |  | -279 | -308 | 1 | -279 |  |  |
| MES | S_EDGE | T2 | IS | 11 | 2/9 | -101 | 0.226 | -9.21 | 131 | -1.31 |  | -117 | -126 | 1 | -117 |  |  |
| MES | S_EDGE | T2 | OOS | 7 | 2/5 | -157 | 0.181 | -22.37 | 191 | -1.08 |  | -178 | -183 | 1 | -178 | 20.4 | INSUFFICIENT |
| MES | L_FULL | H2 | ALL | 140 | 53/87 | -782 | 0.893 | -5.59 | 1123 | -0.7 |  | -1216 | -1867 | 7 | -2928 |  |  |
| MES | L_FULL | H2 | IS | 79 | 27/52 | -317 | 0.921 | -4.01 | 1102 | -0.78 |  | -750 | -1401 | 4 | -1685 |  |  |
| MES | L_FULL | H2 | OOS | 61 | 26/35 | -465 | 0.858 | -7.63 | 1033 | -0.43 |  | -721 | -1201 | 4 | -1407 | 49.4 | FAIL |
| MES | L_FULL | H15 | ALL | 142 | 56/86 | -811 | 0.889 | -5.71 | 1187 | -0.62 |  | -1288 | -1941 | 8 | -2965 |  |  |
| MES | L_FULL | H15 | IS | 81 | 29/52 | -535 | 0.869 | -6.6 | 1069 | -0.78 |  | -1012 | -1565 | 5 | -1988 |  |  |
| MES | L_FULL | H15 | OOS | 61 | 27/34 | -277 | 0.914 | -4.53 | 1007 | -0.39 |  | -605 | -977 | 4 | -1156 | 58.6 | FAIL |
| MES | L_FULL | T2 | ALL | 197 | 69/128 | -419 | 0.954 | -2.13 | 1630 | -1.02 |  | -993 | -1635 | 10 | -3254 |  |  |
| MES | L_FULL | T2 | IS | 119 | 41/78 | 151 | 1.029 | 1.27 | 1362 | -1.03 | 3.789 | -422 | -1019 | 6 | -1732 |  |  |
| MES | L_FULL | T2 | OOS | 78 | 28/50 | -570 | 0.854 | -7.31 | 1600 | -1.02 |  | -869 | -1274 | 4 | -1457 | 43.2 | FAIL |
| MES | L_EDGE | H2 | ALL | 201 | 57/144 | -1601 | 0.698 | -7.97 | 1907 | -1.07 |  | -1770 | -2082 | 11 | -3002 |  |  |
| MES | L_EDGE | H2 | IS | 124 | 33/91 | -991 | 0.67 | -7.99 | 1297 | -1.08 |  | -1145 | -1402 | 7 | -1818 |  |  |
| MES | L_EDGE | H2 | OOS | 77 | 24/53 | -610 | 0.735 | -7.92 | 819 | -1.07 |  | -779 | -1076 | 4 | -1179 | 45.0 | FAIL |
| MES | L_EDGE | H15 | ALL | 202 | 66/136 | -1469 | 0.705 | -7.27 | 1616 | -1.07 |  | -1595 | -1828 | 11 | -2602 |  |  |
| MES | L_EDGE | H15 | IS | 124 | 38/86 | -1026 | 0.639 | -8.27 | 1173 | -1.07 |  | -1141 | -1333 | 7 | -1644 |  |  |
| MES | L_EDGE | H15 | OOS | 78 | 28/50 | -443 | 0.793 | -5.68 | 668 | -1.06 |  | -569 | -798 | 4 | -906 | 59.6 | FAIL |
| MES | L_EDGE | T2 | ALL | 290 | 72/218 | -1064 | 0.711 | -3.67 | 1064 | -1.2 |  | -1185 | -1382 | 15 | -2252 |  |  |
| MES | L_EDGE | T2 | IS | 180 | 43/137 | -698 | 0.679 | -3.88 | 727 | -1.21 |  | -799 | -988 | 9 | -1417 |  |  |
| MES | L_EDGE | T2 | OOS | 110 | 29/81 | -367 | 0.757 | -3.33 | 375 | -1.16 |  | -488 | -655 | 6 | -835 | 76.2 | FAIL |
| MES | L_FULL_NY | H2 | ALL | 78 | 27/51 | -1779 | 0.641 | -22.81 | 1796 | -1.01 |  | -2060 | -2522 | 4 | -2727 |  |  |
| MES | L_FULL_NY | H2 | IS | 39 | 12/27 | -1070 | 0.554 | -27.44 | 1325 | -1.02 |  | -1351 | -1663 | 2 | -1517 |  |  |
| MES | L_FULL_NY | H2 | OOS | 39 | 15/24 | -709 | 0.723 | -18.18 | 1131 | -1.01 |  | -965 | -1376 | 2 | -1171 | 33.6 | FAIL |
| MES | L_FULL_NY | H15 | ALL | 78 | 27/51 | -1921 | 0.613 | -24.63 | 1921 | -1.01 |  | -2250 | -2652 | 4 | -2813 |  |  |
| MES | L_FULL_NY | H15 | IS | 39 | 12/27 | -1239 | 0.483 | -31.77 | 1345 | -1.02 |  | -1449 | -1707 | 2 | -1583 |  |  |
| MES | L_FULL_NY | H15 | OOS | 39 | 15/24 | -682 | 0.734 | -17.49 | 1141 | -1.01 |  | -1011 | -1363 | 2 | -1202 | 32.4 | FAIL |
| MES | L_FULL_NY | T2 | ALL | 114 | 36/78 | -1206 | 0.789 | -10.58 | 1308 | -1.03 |  | -1505 | -1989 | 6 | -2612 |  |  |
| MES | L_FULL_NY | T2 | IS | 65 | 19/46 | -809 | 0.732 | -12.44 | 960 | -1.03 |  | -1052 | -1512 | 4 | -1685 |  |  |
| MES | L_FULL_NY | T2 | OOS | 49 | 17/32 | -398 | 0.853 | -8.11 | 1151 | -1.02 |  | -696 | -1101 | 3 | -1101 | 45.2 | FAIL |
| MES | L_EDGE_NY | H2 | ALL | 92 | 26/66 | -374 | 0.846 | -4.06 | 899 | -1.07 |  | -542 | -844 | 5 | -1101 |  |  |
| MES | L_EDGE_NY | H2 | IS | 48 | 12/36 | -211 | 0.816 | -4.4 | 511 | -1.09 |  | -365 | -622 | 3 | -622 |  |  |
| MES | L_EDGE_NY | H2 | OOS | 44 | 14/30 | -163 | 0.873 | -3.7 | 459 | -1.06 |  | -331 | -583 | 3 | -583 | 70.4 | FAIL |
| MES | L_EDGE_NY | H15 | ALL | 92 | 29/63 | -463 | 0.799 | -5.03 | 713 | -1.07 |  | -589 | -815 | 5 | -1026 |  |  |
| MES | L_EDGE_NY | H15 | IS | 48 | 13/35 | -344 | 0.693 | -7.16 | 547 | -1.08 |  | -458 | -650 | 3 | -650 |  |  |
| MES | L_EDGE_NY | H15 | OOS | 44 | 16/28 | -120 | 0.899 | -2.72 | 370 | -1.05 |  | -246 | -464 | 3 | -464 | 77.2 | FAIL |
| MES | L_EDGE_NY | T2 | ALL | 140 | 33/107 | -681 | 0.683 | -4.86 | 761 | -1.16 |  | -802 | -997 | 7 | -1328 |  |  |
| MES | L_EDGE_NY | T2 | IS | 77 | 16/61 | -448 | 0.594 | -5.81 | 477 | -1.2 |  | -549 | -726 | 4 | -807 |  |  |
| MES | L_EDGE_NY | T2 | OOS | 63 | 17/46 | -233 | 0.777 | -3.7 | 414 | -1.11 |  | -354 | -521 | 4 | -585 | 74.8 | FAIL |

**MAE / MFE in R, hold trades (H2), measured from entry to exit.**

| cell | median MAE | median MFE | share MFE ≥ 1R | share MFE ≥ 2R |
|---|---|---|---|---|
| MNQ L_FULL | 0.85 | 1.06 | 50.7% | 20.6% |
| MNQ L_FULL_NY | 0.74 | 1.12 | 52.6% | 19.2% |
| MNQ L_EDGE | 1.19 | 1.20 | 56.4% | 32.7% |
| MES L_FULL | 0.98 | 0.77 | 45.0% | 13.6% |
| MES L_FULL_NY | 1.00 | 0.68 | 39.7% | 11.5% |
| MES L_EDGE | 1.19 | 0.83 | 44.8% | 27.4% |

## 7. Key findings

- **MNQ L_FULL hold (H2):** 136 trades, +$6,267, PF 1.63 over 2 years.
  - Positive in-sample (PF 1.63, null 97th percentile) and out-of-sample (PF 1.63, +$2,673).
  - Positive in both OOS halves (+$129 / +$2,544) and in all four half-years.
  - **Fails the frozen null gate out-of-sample: 91st percentile against the 95th required.**
  - **Concentration:** the top 7 trades (5%) are 68% of 2-year net. Without them, +$2,006. Without the top 3, +$4,188 over 2 years and +$612 out-of-sample.
  - The median trade is only +0.09R. Most wins are session-close exits, not 2R targets; only 21% reach 2R.
- **MES L_FULL hold, same rule:** loses in-sample (−$317), out-of-sample (−$465) and at both 1.5R and 2R. The two instruments disagree.
- **Whole candle vs thin edge:** the whole candle is better on MNQ.
  - The edge zone loses on MNQ in-sample (PF 0.82). Its 2-year net is only +$339, turns negative without the single best trade, and its half-years alternate sign.
  - On MES the edge zone loses everywhere.
- **NY open:**
  - MNQ L_FULL_NY: out-of-sample PF 1.63 (92nd percentile), but in-sample PF is only 1.25. The top 3 trades are 70% of 2-year net, and out-of-sample net without the top 3 is +$130.
  - MES: loses in both periods.
  - The open window is not better than all-day. Post-hoc, MNQ all-day profits came mostly from entries after 11:00.
- **Hold vs touch (whole candle, MNQ):** the touch fill made more than the hold. Out-of-sample it made +$4,220 at the 97.8th percentile, a formal PASS as a comparison cell. But it is more concentrated: the top 10 trades are 80% of 2-year net. On MES both lose.
- **STRICT (both candles big):** 48 zones per instrument and 5–8 out-of-sample trades. Insufficient in every cell.
- **Multiple testing:** 36 tests this round, 96 cumulatively. A few single-instrument cells at the 90th–98th percentile are expected by chance.

## 8. Classification

This is retrospective; nothing is promoted.

| population | class | why |
|---|---|---|
| **MNQ L_FULL hold (H2, H15)** | **PROMISING BUT UNPROVEN** | Causal, reproducible, positive in both periods and all half-years. But it fails the frozen null gate, is not replicated on MES, sits among 96 cumulative tests with a non-blind OOS window, and 68% of net is in the top 5% of trades |
| MNQ L_FULL touch fill (T2) | PROMISING BUT UNPROVEN (comparison only) | Frozen PASS as a comparison cell, but 80% of 2-year net is in the top 10 trades and MES loses. It is not the operator's hold entry |
| MNQ L_FULL_NY (H2, H15, T2) | WAIT | Out-of-sample positive but in-sample weak. Top 3 trades = 70% of net. Not better than all-day |
| MNQ L_EDGE, L_EDGE_NY (all measures) | BROKEN | Loses in-sample. Net is negative without the top trade. Half-years alternate. T2 loses |
| MNQ S_FULL, S_EDGE | WAIT | Insufficient (5–10 out-of-sample trades) |
| MES L_FULL, L_EDGE, L_FULL_NY, L_EDGE_NY (all measures) | BROKEN | Every cell loses out-of-sample; most lose in-sample too |
| MES S_FULL, S_EDGE | WAIT | Insufficient (6–8 out-of-sample trades) |

## 9. Independent test

**Earned, narrowly, for one population only: MNQ L_FULL hold, 2R (H2).**
- **Why:** it passes every causality, fill and reproduction check. The gaps are all about statistical strength: the null gate, cumulative tests, a non-blind OOS window and concentration. Only unseen data can resolve them.
- **Prior:** the MES failure is a strong negative prior, so the prereg includes a hard retire rule.
- **Prereg:** `docs/prereg-mnq-sd-zone-lfull-hold-validation-2026-09-24.md`, frozen before any validation outcome is seen. It uses only data after 2026-06-26.
- **Data already on disk:** Polygon MNQ 5m data for 2026-07-16 → 2026-09-22 exist locally (`data/replay_polygon_parity_2026_07_16_09_22`). **None of their outcomes has been opened.**

## 10. Still uncertain

- **MES:** is its failure instrument-specific, or is the MNQ result chance? The validation tests MNQ only, so it cannot answer this.
- **Retest reading:** FULL arming requires price to first *close beyond* the reversal candle. That is the frozen reading, following ORB-style sources. An "any touch counts" reading was never tested and is not being tested now.
- **Fill stress:** fills were stressed only at 1 tick. The validation adds a declared 2-tick stress.
- **Overnight retests:** these consume zones without a trade. An ETH-trading version was never tested.
- **Post-hoc:** everything in the archive's `descriptive_posthoc.txt` (side, time of day, half-years) is post-hoc.
