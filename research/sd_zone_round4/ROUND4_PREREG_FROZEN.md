# PREREG (FROZEN) — Round 4: the operator's actual entry — retest of the reversal zone that HOLDS

Frozen 2026-09-24 BEFORE any outcome (pattern count, touch, hold, trade P&L) was computed.
Research only. Nothing here is wired into any lane, config or deploy. Not edited after freezing;
any deviation is reported in results/DEVIATIONS_AND_NOTES.txt. Hold-rule sources: PHASE1_SOURCES.md.

## 0. Data, seams, split, costs — IDENTICAL to rounds 1 and 3

- Round-1/3 cache of `data/replay_polygon_5m/{MNQ,MES}`, 2024-07-02 → 2026-06-26, 5m, raw front month.
- Roll seams 00:00Z on 2024-09-12, 2024-12-12, 2025-03-13, 2025-06-12, 2025-09-11, 2025-12-11,
  2026-03-12, 2026-06-11: segments; every zone is dropped at the seam (nothing scans across it).
- Trading day = (ET + 6h).date(). IS = 2024-07-02 … 2025-09-10; OOS = 2025-09-11 … 2026-06-26;
  OOS halves split at the median OOS trading day (as round 3). A trade belongs to the period of
  its ENTRY bar's trading day.
- MNQ $2/pt, MES $5/pt, tick 0.25, $1.48 round trip, 1 contract. 5m chart only.
- Costs/fills: market entry = bar open + 1 tick adverse; stop fill = stop level + 1 tick slippage
  (adverse); target fills only on a 1-tick trade-through, at the target price; when stop and
  target are both reachable in one bar, the STOP wins. One position per instrument per measure
  (greedy by entry/fill bar; ties by earliest zone creation).
- RTH entry window: the entry (or limit-fill) bar must START 09:30 … 15:50 ET. Unresolved trades
  exit at the close of the 15:55 ET bar of that same ET date (the round-3 session exit).

## 1. Patterns (creation bar = reversal candle B = bar i; impulse candle A = bar i−1)

Round-3 R1 geometry, used by both STRICT and LOOSE (demand shown; supply = exact mirror):
- A red (c_A < o_A) and a Strat 2D: l_A < l_{i−2} and h_A ≤ h_{i−2}.
- B green (c_B > o_B) and a Strat 2U: h_B > h_A and l_B ≥ l_A.
- Range-engulfing close (round-3 R1 side rule): c_B > h_A (supply: c_B < l_A).
- i−2 in the same segment; ATR20 = Wilder RMA(20) of 5m true range, evaluated at bar i−2.
- **STRICT**: body(A) ≥ 1.5·ATR20 AND body(B) ≥ 1.5·ATR20 (exactly round-3 R1's pattern set).
- **LOOSE**: body(B) ≥ 1.5·ATR20 only; A needs only the direction + 2D/2U geometry above.
  (LOOSE ⊇ STRICT.)

## 2. Zones (created by B; live once B has closed)

Demand (supply mirrored):
- **FULL** = the reversal candle's full range: [l_B, h_B].
- **EDGE** = the turning-point sliver: [min(l_A, l_B), min(o_B, c_B)] = from the lower of the two
  lows up to B's body bottom. Supply: [max(o_B, c_B), max(h_A, h_B)].
- Zones narrower than 1 tick are discarded (count reported).

## 3. Context

- **ALL-DAY**: any creation time.
- **NY-OPEN**: B's bar start 09:30 … 10:55 ET (B closes by 11:00 ET).
- Zone lifetime (both contexts): from creation through the 15:55 ET bar of the FIRST RTH session
  that has any bar after B (i.e. overnight/premarket zones live through that day's RTH; RTH zones
  live through the rest of that RTH session; zones created after 16:00 ET live through the next
  day's RTH). Never across a seam. NY-OPEN zones therefore live to that day's close ("retest any
  time that session").

## 4. The six definitions (fixed; no sweeps)

| ID | pattern | zone | context |
|---|---|---|---|
| S_FULL | STRICT | FULL | ALL-DAY |
| S_EDGE | STRICT | EDGE | ALL-DAY |
| L_FULL | LOOSE | FULL | ALL-DAY |
| L_EDGE | LOOSE | EDGE | ALL-DAY |
| L_FULL_NY | LOOSE | FULL | NY-OPEN |
| L_EDGE_NY | LOOSE | EDGE | NY-OPEN |

## 5. HOLD entry rule (primary; same for all six) — demand shown, supply mirrored

1. **Leave first (arming).** The zone must first be left: a bar at or after B CLOSES above the zone
   top. For EDGE, B itself satisfies this (B is green, so c_B > o_B = zone top), so the zone is armed
   at B. For FULL, B closes inside its own range, so arming = the first bar j > i with c_j > h_B.
   If a bar closes below the zone bottom before arming, the zone is dead. No arming within the
   zone lifetime = no retest. (Interpretation of "come back to the zone", ORB-style: PHASE1 row 6.
   The generic rule applies identically to random null zones: armed at creation iff the creation
   bar's close is already outside on the trade side.)
2. **Touch.** The first bar k after the arming bar (k > arming bar, k within lifetime) whose low ≤
   zone top (supply: high ≥ zone bottom).
3. **Hold.** If c_k < zone bottom → BROKEN (dead). Else if c_k > zone top → HOLD at k. Else (close
   inside the zone) look at bar k+1 (must be within lifetime): c_{k+1} < bottom → BROKEN;
   c_{k+1} > top → HOLD at k+1; otherwise NO-HOLD (zone consumed). First retest only: a zone
   gets at most one touch evaluation and at most one trade.
4. **Entry.** At the open of the bar after the hold bar (h+1), + 1 tick adverse; bar h+1 must be in
   the segment, within the zone lifetime and start 09:30 … 15:50 ET, else no trade (zone consumed).
5. **Stop.** Level = 1 tick beyond the FURTHER of the zone's far edge and the touch/hold bars'
   extremes: demand stop = min(zone bottom, l_k, l_h) − 1 tick; supply = max(zone top, h_k, h_h)
   + 1 tick. Fill = level ∓ 1 tick slippage. If the entry price is at/through the stop level
   (R ≤ 0) the trade is skipped (count reported; expected ≈ 0).
6. **Target.** Primary **H2** = entry ± 2R (R = |entry − stop level|). Declared variant **H15** = 1.5R.
   Stop and target are both live from the entry bar; stop wins ties.
7. **Exit** at the close of the 15:55 ET bar of the entry bar's ET date if neither hit.

## 6. Touch-fill comparison **T2** (round-1/3 Measure B rule on the SAME zones, same touch bar)

Limit at the zone's near edge (demand: top) placed once the zone is armed; it fills on the touch
bar k from §5.2 only if bar k trades 1 tick through the near edge and bar k starts 09:30–15:50 ET.
Stop 1 tick beyond the zone's far edge (+1 tick slippage); target 2R (1-tick trade-through); on
the fill bar only the stop can trigger; later bars stop wins ties; exit at the 15:55 ET close of
bar k's ET date. No hold requirement. "Value of waiting" = H2 vs T2 on identical zones (reported
side by side; T2 is also scored by the pass rule).

## 7. Null (500 seeds; numpy default_rng(seed), seeds 0 … 499)

For every real zone: a random bar r in the SAME segment with the SAME 5m time-of-day slot (ET
minute of bar start) — a time-matched null (stricter than rounds 1/3; NY-OPEN nulls therefore sit
in the NY-open window too). Random zone: same direction, same width W and same signed distance
from the creation close (demand top = c_r − (c_B − top)); lifetime by §3 from r. The same arming,
touch, hold, entry, stop, target, exit, costs and one-position rules (H2, H15, T2). Real OOS PF
percentile vs the 500 null OOS PFs (ties count half).

## 8. Pass rule — IDENTICAL to rounds 1/3, per definition × instrument × measure (H2, H15, T2)

PASS only if ALL: OOS PF ≥ 1.3; OOS net > 0 in BOTH OOS halves; OOS net ex-top-3 trades > 0;
OOS PF ≥ the null's 95th percentile. < 30 OOS trades → INSUFFICIENT. IS is context only.

## 9. Round verdict mapping (fixed now)

- **ONE DEFINITION WORKS**: a definition PASSES on BOTH MNQ and MES for the same hold measure
  (H2 or H15).
- **SOME EDGE BUT INSUFFICIENT**: no two-instrument pass, but at least one hold cell PASSES on one
  instrument, or has ≥ 30 OOS trades with OOS PF ≥ 1.3 and null percentile ≥ 90.
- **INSUFFICIENT DATA**: every hold cell (H2 and H15, all 6 × 2) has < 30 OOS trades.
- **NO EDGE**: otherwise.

## 10. Reported per definition × instrument

Funnel: patterns, zones (≥1 tick), armed, retested (touched), BROKEN / NO-HOLD / HOLD, entries in
window, trades after one-position. IS/OOS: trades, PF, net $, max DD $, win rate, OOS H1/H2 net,
OOS net ex-top-3, null PF p95 and percentile, verdict — for H2, H15, T2. Median R (points).

## 11. Verification (before scoring)

- Synthetic bars: demand + supply touch-and-hold (hold on the touch bar and on the next bar),
  touch-and-break (close through far edge), touch-then-inside-twice (NO-HOLD), no-touch, FULL
  zone that never leaves, FULL zone dead before arming; STRICT vs LOOSE detection; EDGE bounds.
- Slow bar-by-bar engine vs the vectorised engine on ALL real zones and one random-zone draw
  per definition on MNQ and MES, for H2, H15 and T2: 0 mismatches required before scoring.

## 12. Multiple-testing count

Round 1: 12. Round 2: 12. Round 3: 36. Cumulative before this round: 60.
Round 4: 6 definitions × 2 instruments × 3 scored measures (H2, H15, T2) = 36.
Cumulative rounds 1–4 = 96 tests. With the p95 null gate alone, P(≥1 lucky pass in 96
independent tests) ≈ 1 − 0.95^96 ≈ 99%. The other gates cut this, but any single-instrument PASS
here is a candidate for fresh forward data, not proof.
