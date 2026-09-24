# PREREG (FROZEN): independent validation of MNQ S/D zone "L_FULL hold, 2R"

Frozen on 2026-09-24, **before any validation-period outcome was computed or opened**. This is research only: nothing here is wired into any lane, config or deploy, and a PASS authorizes nothing beyond writing a separate forward-paper prereg, which would need an explicit operator GO.

**Source of the hypothesis:** the Round-4 discovery study (`docs/options-sd-zone-round4-audit-2026-09-24.md`). It is **PROMISING BUT UNPROVEN**. The study is causal and reproducible, but it failed its frozen null gate (91st vs 95th percentile), MES disagrees, 96 tests have been run cumulatively, and its out-of-sample window was not blind. **This is one confirmatory test with a hard retire rule.**

## 1. Population (exactly the Round-4 `L_FULL` hold; nothing re-tuned)

- **Instrument:** MNQ only, 1 contract. MES is not part of the pass rule.
- **Timeframe:** 5m bars.
- **Pattern:** LOOSE R1, identical to `research/sd_zone_round4/ROUND4_PREREG_FROZEN.md` §1.
  - Candle A (bar i−1) is red and a Strat 2D. Candle B (bar i) is green and a 2U with c_B > h_A.
  - body(B) ≥ 1.5 × ATR20, where ATR20 is Wilder RMA(20) of the 5m true range taken at bar i−2.
  - Supply setups mirror this exactly.
- **Zone:** FULL, [l_B, h_B]. Zones narrower than 1 tick are dropped.
- **Context:** ALL-DAY. The zone lives through the 15:55 ET bar of the first RTH session that has any bar after B, and never across a roll seam.
- **Hold entry (Round-4 §5):**
  1. **Arm:** a close beyond the zone on the trade side. If price closes through the far edge first, the zone is dead.
  2. **Touch:** the first bar after arming whose low reaches the zone top (demand) or whose high reaches the zone bottom (supply).
  3. **Hold:** the touch bar or the next bar closes back outside on the trade side. A close through the far edge means broken; still inside after that means no hold. Only the first retest counts.
  4. **Entry:** the next bar's open + 1 tick adverse. That bar must start 09:30–15:50 ET and fall within the zone's lifetime.
  5. **Stop:** 1 tick beyond the further of the far edge and the touch/hold bar extremes, with 1 more tick of slippage on the fill.
  6. **Target:** 2R (**H2, primary**). It fills only when price trades 1 tick through the target. If the stop and target are both hit in one bar, the stop wins.
  7. **Exit:** at the 15:55 ET bar close of the entry date if neither is hit.
  8. **Positions:** one at a time, taken greedily by entry bar.
- **Costs:** $2/pt, tick 0.25, $1.48 round trip.

## 2. Implementation (fixed)

- **Primary:** the frozen Round-4 code, byte copy in `research/sd_zone_round4/frozen_round4_code/`. The file sha256s are listed in `research/sd_zone_round4/round4_archive_manifest.json`, under `code/`.
- **Permitted changes, only these:**
  - point the cache to the validation bars;
  - extend `ROLLS` with the seams below;
  - treat every validation trade as the validation period.

  No rule, threshold or fill logic may change.
- **Parity:** `research/sd_zone_round4/independent_l_full.py` must reproduce the frozen engine's validation trade list **trade for trade**. A mismatch is a STOP, fixed first; it is not a verdict.

## 3. Validation data (untouched; the discovery data are never reused)

- **Discovery sample (excluded):** 2024-07-02 → 2026-06-26.
- **Validation window:** trading days (ET + 6h date) from **2026-06-29 through 2027-01-29**, inclusive.
- **Source:** Polygon futures aggregates, 5m, raw front month, rolled 8 calendar days before the 3rd-Friday expiry. It is built with the same construction and row format as `data/replay_polygon_5m`, and assembled **once, after 2027-01-29**.
  - Existing local files for this period (e.g. `data/replay_polygon_parity_2026_07_16_09_22`) may be used only if their rows match a fresh pull. They must **not** be run through detection, or through anything that produces P&L, before the evaluation date.
- **Roll seams (00:00Z):** 2026-09-10 and 2026-12-10. If the extension below is used, also 2027-03-11 and 2027-06-10.
- **Data gate, applied before any scoring:**
  - at least 95% of validation RTH sessions have all 78 bars;
  - no conflicting duplicate timestamps;
  - no high < low rows.

  If the gate fails: FIX DATA FIRST. That is not a verdict.

## 4. Evaluation (one look)

- **When:** once, after 2027-01-29.
- **If fewer than 30 H2 trades** accrue: a single pre-set extension to **2027-06-25**, evaluated once after that date. Nothing else may be looked at in between; not even the trade count.
- **Null:** 500 seeds of the frozen time-matched random-zone generator, run on the validation bars (the frozen `random_zones` / `TimeGroups`, seeds 0–499). Real PF percentile is measured against the null PFs, with ties counted as half.
- **Halves:** split at the median validation trading day.

## 5. Pass rule: ALL of the following, on H2 trades in the validation window

| # | criterion |
|---|---|
| 1 | n ≥ 30 trades |
| 2 | PF ≥ 1.3 |
| 3 | net > 0 in **both** halves |
| 4 | net with the top 3 trades removed > 0 |
| 5 | net with the top 5% of trades removed (ceil(5% × n)) > 0, **and** the top 5% of trades make up no more than 60% of net |
| 6 | real PF at or above the null's **95th** percentile |
| 7 | net > 0 under a 2-tick stress: entry +2 ticks, stop fill +2 ticks, targets unchanged |
| 8 | parity (§2) and the data gate (§3) both pass |

## 6. Verdict (fixed now)

- **PASS** (all 8 criteria): "survived one independent window". It stays **PROMISING BUT UNPROVEN**, and the only permitted next step is a separate forward-paper prereg. No promotion, no activation.
- **FAIL** (any of criteria 1–7 fails, with n ≥ 30): **RETIRE** the Round 1–4 S/D-zone family, all definitions. No rescue: no re-tuning of the candle-size rule, zone width, context, target, entry reading or filters.
- **INSUFFICIENT** (n < 30 after the extension): **RETIRE**. The pattern is too rare to validate.
- **Parity or data failure:** FIX DATA / EXECUTION DEFECT first, then evaluate once.

## 7. Reported descriptively (no pass authority)

The following may be reported but cannot change the verdict:

- H15 (1.5R) and T2 (touch fill) on the same zones;
- demand vs supply;
- entries before vs after 11:00 ET;
- MAE/MFE;
- MES L_FULL H2 on the same window, as a control.

## 8. Multiple testing

There is one primary test (MNQ L_FULL H2), and there are no variants. The history behind it is 96 discovery tests across Rounds 1–4. This prereg exists precisely because that history makes the discovery result insufficient on its own.
