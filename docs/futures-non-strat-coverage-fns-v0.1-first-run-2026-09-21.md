# Futures non-Strat coverage (`fns-v0.1`) — first run 2026-09-21

**Status: RESEARCH RECORD. No trade, shadow, paper or promotion decision follows from this file.**
Prereg: `docs/prereg-futures-non-strat-coverage-2026-09-21.md` (frozen before the run).
Runner: `research/futures_non_strat_coverage.py` on the local Polygon 5m replay
(2024-07-02 → 2026-06-26). Raw outputs under `logs/fns_v01/` (gitignored).

## Coverage

| | MNQ | MES |
|---|---|---|
| sessions used / total files | 440 / 621 | 440 / 621 |
| skipped | {'incomplete': 134, 'no_prior': 9, 'roll': 38} | {'incomplete': 134, 'no_prior': 9, 'roll': 38} |
| events / episodes | 8876 / 7587 | 9195 / 8006 |

`incomplete` = 109 weekend files + holidays/early closes (whole-session rule). One
runner defect was found and fixed before the recorded run: the RTH window was
first written in fixed UTC and truncated every US-winter session to 66 bars;
it is now 09:30–16:00 America/New_York. The replay's own `session` label is
fixed in UTC and does not follow DST — do not use it as a window.

## Level identity findings (the same class of ambiguity as EBAY's PDL)

Payload field == frozen definition: MNQ: orb_high 150/440, orb_low 146/440, pdh 226/440, pdl 200/440; MES: orb_high 134/440, orb_low 136/440, pdh 238/440, pdl 199/440.
The payload `previous_day_high/low` is a Globex-day level (agrees ~50% of
sessions, when the extreme fell inside RTH); the payload `orb_high/low` is
not a 09:30–10:00 range (agrees ~1/3). "PDH" and "ORB" therefore mean
different numbers in the futures alert payload and in this observer. Any
future futures level study must name which one it uses.

## Episode-level 60m close return (bps), by instrument and calendar half

Pre-registered candidate rule: median > 0 on both instruments and both halves
with n ≥ 100 per half. Post-hoc drift control (NOT pre-registered, added after
seeing that every LONG family was positive and every SHORT family adverse on a
bull tape): the 60m forward return of **every** post-ORB RTH bar, long
direction — MNQ H1 +1.27 / H2 +2.22, MES H1 +1.27 / H2 +1.44 (n ≈ 16k / 11k).

| family | MNQ ep | MES ep | MNQ H1 n/med | MNQ H2 n/med | MES H1 n/med | MES H2 n/med | verdict |
|---|---:|---:|---|---|---|---|---|
| ORB_BREAKOUT_LONG | 753 | 785 | 450/+4.02 | 294/+6.04 | 458/+3.46 | 314/+3.84 | **above drift 4/4** |
| ORB_BREAKOUT_SHORT | 655 | 683 | 397/-2.32 | 251/+2.49 | 399/+0.00 | 274/+0.00 | closed |
| ORB_BREAK_RETEST_LONG | 354 | 352 | 218/+6.42 | 130/+7.13 | 206/+3.21 | 140/+1.46 | above drift 4/4 — MES H2 by 0.02 bps: noise, drift-explained |
| ORB_BREAK_RETEST_SHORT | 255 | 304 | 156/-3.23 | 97/-4.67 | 186/-0.62 | 116/-0.87 | closed |
| ORB_REJECTION_LONG | 512 | 503 | 313/+3.94 | 191/-0.39 | 288/+4.41 | 208/-0.19 | closed |
| ORB_REJECTION_SHORT | 491 | 467 | 296/-5.23 | 188/-3.27 | 255/-4.11 | 207/-1.84 | closed |
| PDH_BREAK_RETEST_LONG | 212 | 223 | 127/+2.32 | 84/+1.58 | 131/-0.47 | 90/+5.08 | closed |
| PDH_RECLAIM_LONG | 461 | 471 | 279/+1.21 | 174/+2.85 | 287/-0.83 | 176/+4.15 | closed |
| PDH_REJECTION_SHORT | 341 | 318 | 198/-1.43 | 136/-6.51 | 183/-1.28 | 124/-2.39 | closed |
| PDL_BREAK_RETEST_SHORT | 123 | 163 | 67/-5.67 | 55/+7.08 | 84/+1.50 | 76/-7.82 | closed |
| PDL_RECLAIM_SHORT | 292 | 364 | 166/-1.29 | 124/-0.36 | 190/+0.22 | 167/-8.96 | closed |
| PDL_REJECTION_LONG | 216 | 262 | 125/+5.43 | 86/+3.34 | 143/-1.25 | 119/+2.15 | closed |
| VWAP_FAILED_RECLAIM_SHORT | 291 | 322 | 180/+3.56 | 109/-1.84 | 206/+2.18 | 114/+0.18 | closed |
| VWAP_RECLAIM_LONG | 1035 | 1115 | 628/+3.83 | 394/+4.42 | 659/+0.00 | 436/+2.60 | closed |
| VWAP_TEST_HOLD_LONG | 825 | 885 | 508/+3.19 | 311/+1.17 | 521/+1.67 | 357/+1.06 | prereg pass, drift-explained |
| VWAP_TEST_HOLD_SHORT | 771 | 789 | 458/-2.95 | 303/-5.03 | 480/-1.24 | 296/-0.55 | closed |

## Read

- **ORB_BREAKOUT_LONG** is the only family that passes the prereg rule **and**
  sits above the drift control in all four cells (excess ≈ +2 to +4 bps at
  60m; ≈ 4–8 MNQ points). That is a coverage/behaviour observation, not a
  trade result: MFE ≥ 2×MAE share is ≈ 0.37, no geometry was applied, and the
  July 2026 MNQ structural-level study **rejected** ORB/PDH break-retest with
  fixed targets under realistic fills. The rejection stands. The only
  legitimate next step is a **new prereg'd geometry + fill study** for
  ORB_BREAKOUT_LONG specifically, with the 2-tick slippage stress as the bar,
  and only on operator GO.
- ORB_BREAK_RETEST_LONG (MES H2 +1.46 vs drift +1.44) and VWAP_TEST_HOLD_LONG
  (MNQ H2 +1.17 vs drift +2.22) pass the prereg rule but one cell each is at
  or below drift → drift-explained, closed. On MNQ alone ORB_BREAK_RETEST_LONG
  is the strongest family in the table (+6.4 / +7.1); that is recorded, not
  promoted — the rule was both instruments.
- Every SHORT family has an adverse or ≈0 median in this tape. That is
  consistent with drift, not evidence that shorts are wrong; a bear-tape
  slice does not exist in this dataset.
- SPY/QQQ alignment is `None` throughout (no index in the futures replay);
  the options-side alignment filter was not tested here.

## Doubts recorded

1. Polygon continuous-contract roll handling is unknown; roll weeks (+1
   session) were excluded blind. 48 sessions dropped.
2. bps on an index future understate cost sensitivity: 2 bps ≈ 4 MNQ points ≈ $8;
   round-trip commission + 2-tick slippage ≈ $3–4. Margins this thin die to
   fills — exactly what the July study found.
3. Volume-ratio/EMA20 history uses 4 prior RTH sessions only; the first
   ~20 bars of each session have a shorter baseline than the options lane.

## Correction — 2026-09-24: the VWAP families above used a lagged line, not VWAP

This run's VWAP was **not** the prereg's cumulative RTH VWAP.

**What went wrong**
- `load_rth_session` fed the replay payload's `vwap` field into `session_vwap` as if it were a per-bar price.
- That field is **already** the cumulative RTH session VWAP. Across 37,986 RTH bars, it matches sum(hlc3·v)/sum(v) to within 1 tick.
- `session_vwap` then volume-averaged it a second time, producing a smoothed, lagging line.
- This contradicts the frozen-definitions row above, which says the payload's `vwap` field is not used.

**What is affected**
- **Suspect:** every VWAP family in this record (`VWAP_FAILED_RECLAIM_SHORT`, `VWAP_RECLAIM_LONG`, `VWAP_TEST_HOLD_LONG`, `VWAP_TEST_HOLD_SHORT`). This includes the `VWAP_TEST_HOLD_LONG` "prereg pass, drift-explained" row.
- **Also affected:** PR #992, which was built on this path.
- **Not affected:** the non-VWAP families.

**Fix**
- The loader now uses each bar's typical price, (high + low + close) / 3.
- `session_vwap` therefore rebuilds the true cumulative RTH VWAP, which is the prereg's own definition.
- `tests/test_futures_non_strat_coverage_vwap.py` pins this.

**What was already rerun**
- #992 (VWAP failed reclaim within 3 bars) was rerun under both constructions on 2026-09-24, in research only. It fails its frozen gate either way: BROKEN, with no raw edge and H2 flipping sign.

**What was not rerun**
- The other VWAP rows above were **not** rescored. They stay as recorded and are flagged, not replaced.
- Any rescoring is a new study under a new prereg. The fns-v0.1 identity is unchanged, and nothing is pooled across the two VWAP constructions.
