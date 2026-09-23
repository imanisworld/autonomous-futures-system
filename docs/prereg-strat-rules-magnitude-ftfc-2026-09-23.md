# Strat Source Rules (Magnitude + FTFC) vs. Observer Brackets, MNQ/MES 15m — Preregistration (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Nothing in this document
enables, disables, promotes, demotes, or re-parameterizes any strategy, lane,
risk rule, broker path, observer population, or deployment. The live runtime
stays PAPER / OBSERVE only, and `LIVE_TRADING_ENABLED=false` is out of scope.
The running `cross_instrument_observation_v1` campaign (epoch
`62546883+2026-09-16T12:17:19Z`) is **not modified** by this study; its rules
stay frozen and it keeps collecting.

## 1. Why this exists

The observer (`strategy/shadow_setups.py::_missing_strat_family` and
`strategy.strat_212_122`) draws Strat bar sequences but trades them with a
**fixed 2R target** and **no continuity filter**:

- entry at the prior bar's extreme ±1 tick;
- stop at the prior bar's far side ±1 tick;
- target = entry ± 2 × risk.

TheStrat's own published rules (thestrat.ai/docs, read 2026-09-23) differ on
exactly those two points:

- **Target = magnitude**, "the high or low of the bar being reversed through",
  not a multiple of risk.
- **Full Timeframe Continuity (FTFC):** "Take reversals back in the direction
  of full timeframe continuity". **Conflict**, meaning the monthly, weekly,
  daily and 60-minute opens split around the last sale, "is not a signal",
  and signals taken against it "carry the risk that the underwater groups
  resolve the other way".

This study asks one question: **on the canonical history, do the source rules
beat the observer's brackets, setup by setup?** It does not adopt range-bound
rules (see §7).

## 2. What we already know (frozen at registration; disclosed, not scored)

- **Observer, 2026-09-16 → 09-23 (about one week, all six micros):** 1,722
  terminal rows, about 31% wins at 2R (break-even is 33.3%), net about −$3,500.
  2-2 reversal −$943 (n=352), 3-2-2 reversal −$764 (n=44), trend-consolidation
  break −$1,109 (n=122). MNQ is the only positive instrument (+$4,115, n=297).
  RANGE_BOUND −$4,748 (n=652). These are forward observations on continuous
  contracts, and the quality gate does not count them as proof.
- **313-day MNQ grid (2026-09-21, `private/grid_2026_09_21`):** 2-2 reversal /
  EMA-aligned / 1.5R / NY: n=312, PF 1.36. That was a different filter and exit,
  selected from 1,253 cells.
- **#911 (closed REJECT):** trend-consolidation break: 484 trades, PF 0.749,
  both halves negative. 2-2 continuation: PF 1.107, first half negative.

**Prior tests of these same Strat families. Read these before expecting a pass:**

- **2026-07-31, shadow-lane null test on this same 313-day corpus:** 41,750
  candidates, 11 families, MNQ and MES, with the observer's bracket (prior-bar
  break, opposite-side stop).
  - Pooled PF was 0.944. It beat its own direction-flipped null (0.863,
    p<0.001), so the detectors carry some direction, but both arms lose.
  - **Target sweep at 0.5 / 1 / 1.5 / 2 / 3R:** "no geometry rescues it". The
    MNQ peak was 1.5R at PF 1.02 before costs. The MES detectors were worse
    than their own flipped null at every R.
  - Conclusion recorded: "the binding lever is entry-trigger quality, not
    exits".
  - Only the memory record survives; the harness was deleted.
- **2026-07-16, tranche 2 (`docs/strat-shadow-tranche2-2026-07-16.md`, PR
  #287), 622 days:** runner exit with market entry, after cost, one position at
  a time. **Every** family was REJECTED: 2-2 reversal, 2-2 continuation, 3-1-2,
  3-2-2 reversal and EMA pullback, on MNQ and MES, ALL and NY. The best cell
  (MNQ 2-2 reversal NY, PF 1.02) sign-flipped between halves.
- **2026-06-29, runner-edge finding:** a "minor lift" from an FTFC filter,
  "big for 312/reversals, neutral on 122/continuation". That FTFC was the
  `context/htf_loader.py` **daily + 4H trend-confluence** proxy, **not** the
  Strat definition of price versus the monthly, weekly, daily and 60-minute
  opens. It also ran under the legacy fill model that was later shown to
  manufacture edge (`docs/ioc-faithful-baseline-622d-2026-07-06.md`).

**What is genuinely untested, and is the only reason this study exists:**

1. The Strat **structural magnitude** target, meaning the extreme of a specific
   prior bar. Earlier sweeps only used fixed R multiples.
2. The Strat **stop at the entry bar's extreme**. Earlier work only used the
   opposite side of the prior bar.
3. **Open-based FTFC** (monthly / weekly / daily / 60-minute) as an *entry*
   filter under the honest IOC fill model. Entry-trigger quality is the lever
   the 07-31 test pointed to.
4. **Hammer/shooter** as a standalone setup.

Given the record above, the expected result is **DOES NOT CLEAR** for arms A
and S. The FTFC arms (A+F, S+F) are the ones with a live prior. A clean
rejection is still worth recording: it closes the "the observer isn't using
the real Strat rules" question for good.

The arms below come from the **published source rules**, chosen before any
result of this study was seen. They were not chosen from the losses above.

## 3. Data

- **Primary:** `data/replay_corpus_v1_market_condition_fixed/MNQ`: 313 daily
  files, 15m bars, 2025-07-24 → 2026-07-23. This is the same corpus as the null
  baseline and #911/#912.
- **Replication only:** the same corpus for `MES` (313 files).
- **Scoring window:** signals whose decision bar belongs to CME trading dates
  **2025-08-01 → 2026-07-23**. August 2025 is the first calendar month whose
  monthly open is inside the corpus.
- **Half split:** trading date < 2026-01-22 = H1, ≥ 2026-01-22 = H2 (as #911).
- **Out-of-sample (OOS) window, scored separately and never pooled with the
  main window:**
  - **OOS-A: 2026-07-24 → 2026-09-14**, from
    `data/replay_polygon_parity_2026_07_16_09_14/{MNQ,MES}`. It was built by
    the same Polygon builder as the main corpus (`slc-build-v1.3`,
    `polygon_to_replay.derive_candles`), with the manifest committed at
    `docs/structural-level-corpus-manifests/`.
    - Verified before registration: its Jul 16–23 overlap with the main corpus
      is **552/552 bars OHLC-identical for both MNQ and MES**.
    - No contract roll inside the window (MNQU6/MESU6 only).
    - Gaps: 2026-09-07 is the Labor Day early close (a real closure, not a
      gap). 2026-09-11 is missing 11 MNQ / 12 MES bars; patterns spanning it
      are skipped as `GAP`.
  - **OOS-B: 2026-09-15 → CME trading date 2026-09-22 (fixed)**, to be pulled
    with `scripts/structural_level_corpus_build.py` from `main`. It covers the
    Sep 15 roll to the December contracts (MNQZ6/MESZ6) under the same
    `roll_days=3` rule. **OOS-B is admitted only if all of these hold:**
    1. the pull's bars for 2026-07-16 → 2026-09-14 are ≥ 99.5% OHLC-identical
       to OOS-A (Polygon revisions are allowed, but must be listed);
    2. the roll ledger shows exactly one seam, U6 → Z6 on 2026-09-15;
    3. the gap ledger has no unexplained gap inside CME hours.

    If any check fails, OOS-B is dropped and the reason is recorded; OOS-A
    stands alone. **No pattern may span a roll seam.** Such patterns are
    skipped (`ROLL`).
  - The box's live `bars_*.jsonl` files are **not** used. They are a different
    feed and are only 89% (MNQ) / 99% (MES) OHLC-identical to Polygon over
    Jul 24 → Sep 14.
  - FTFC opens in the OOS windows come from the concatenated series (main
    corpus + extension). The overlap is identical, so no seam is introduced
    except the Sep 15 roll.
- **Gold (MGC), crude (MCL), M2K, MBT:** **out of scope.** The repo has no
  canonical corpus for them. They stay collection-only in the observer; nothing
  here changes that.

## 4. Setups in scope (15m, detected from OHLC bar types)

Bar types relative to the prior bar: **1** = inside, **2u** = takes the prior
high only, **2d** = takes the prior low only, **3** = takes both. Notation:
`t` = the bar that completes the pattern (the decision bar), `t-1`, `t-2`, …
are earlier bars. "Extreme" is the high for LONG and the low for SHORT, and
mirrored for SHORT. One tick = the instrument's tick size.

| Setup | Sequence (LONG shown) | Strat trigger | Strat stop | Strat magnitude (target) |
|---|---|---|---|---|
| **2-2 reversal** | `t-1`=2d, `t`=2u | `t-1` high | 1 tick below `t` low | high of `t-2` |
| **3-2-2 reversal** | `t-2`=3, `t-1`=2d, `t`=2u | `t-1` high | 1 tick below `t` low | high of `t-2` (the 3) |
| **1-2-2 rev-strat** | `t-2`=1, `t-1`=2d, `t`=2u | `t-1` high | 1 tick below `t` low | high of `t-3` (the motherbar) |
| **2-1-2 reversal** | `t-2`=2d, `t-1`=1, `t`=2u | `t-1` high | 1 tick below `t-1` low | high of `t-2` |
| **3-1-2** (reversal and continuation) | `t-2`=3, `t-1`=1, `t`=2u | `t-1` high | 1 tick below `t-1` low | high of `t-2` (the 3) |
| **Hammer / shooter** (normal) | `t-1` = hammer: open and close both in the top 33% of its range; `t` breaks `t-1` high | `t-1` high | 1 tick below `t` low | high of `t-2` |
| **2-2 continuation** | `t-1`=2u, `t`=2u | *no magnitude in the source* | — | — (arm A / A+F only) |

**Excluded, with reasons (not tested here):**

- **1-3 one-bar rev-strat:** the trigger is intrabar ("do not wait for the bar
  to close").
- **3-2:** "carries no magnitude of its own".
- **2-2-2 continuation:** a lower-timeframe trigger and no target.
- **1-3-2:** the magnitude bar is ambiguous at 15m.
- **Momentum hammer / measured-move continuations:** "the leg before the
  pause" is not defined mechanically.
- **Pivot machine gun, kicker, I-O-I, simultaneous breaks, 30-60 gapper,
  broadening/levels-of-reclaim trading:** these need multi-timeframe or level
  logic beyond a single 15m series.
- **Trend-consolidation break:** closed REJECT by #911; no rescue.

A setup is skipped (counted as `MAGNITUDE_INVALID`) when its magnitude is not
beyond the fill price by at least one tick. A pattern whose bars span an
unexpected data gap is skipped (`GAP`). The expected-bar calendar is the
product-aware one; the daily 17:00–18:00 ET maintenance break and weekends
are not gaps.

## 5. Arms (fixed)

| Arm | Stop / target | Filter |
|---|---|---|
| **A** (baseline) | observer geometry: stop = far side of `t-1` ±1 tick, target = 2R | none |
| **A+F** | observer geometry | FTFC gate |
| **S** | Strat trigger, stop and magnitude from §4 | none |
| **S+F** | Strat trigger, stop and magnitude | FTFC gate |

Hammer/shooter has no observer detector, so it has arms **S** and **S+F** only.
2-2 continuation has no source magnitude, so it has **A** and **A+F** only.

**FTFC gate (standard set, per thestrat.ai):** at the decision bar's close `c`:

- **LONG** allowed only if `c` > the monthly, weekly, daily **and** 60-minute
  opens.
- **SHORT** allowed only if `c` < all four.
- Anything else, including equality, is **conflict**, and the signal is not
  taken.

The opens are computed causally from the 15m series (Eastern Time):

- **day open** = open of the first 15m bar of the CME trading day that starts
  18:00 ET;
- **week open** = the day open of the week's first trading day, starting
  Sunday 18:00 ET;
- **month open** = the day open of the first trading day whose trading date
  falls in that calendar month;
- **60-minute open** = open of the 15m bar that starts at the top of the
  current ET hour.

If any open's bar is missing, FTFC is `UNKNOWN`. Every arm, not only the
filtered ones, is scored **only on signals where FTFC is known**, so all four
arms share one denominator. The count of UNKNOWN signals is reported.

No other filter, session restriction, volume, EMA, or regime label is applied
in any arm.

## 6. Fill, cost and exit model (identical for every arm; reused from #911)

- `PaperBroker` in `ioc_limit` mode at the decision-bar close. IOC tolerance:
  MNQ 32 ticks, MES 16 ticks (the existing per-root values). 1 adverse tick of
  slippage. $1.48 round-turn commission. 1 contract.
- **Pessimistic both-hit:** if a bar touches both stop and target, it counts as
  a loss. No same-bar resolution on the fill bar.
- **Session exit:** if neither the stop nor the target is hit, the trade is
  closed at the close of the last 15m bar before 17:00 ET on the same CME
  trading day (`SESSION_END`). It is terminal and its P&L counts.
- Per cell (setup × arm × instrument): at most one open position at a time,
  and at most 3 fills per CME trading day. Signals while a position is open are
  counted as `BUSY` and not taken.
- **Known limitation, stated in advance:** the Strat enters intrabar on the
  break. This model enters at the 15m close within tolerance, which is a later
  and generally worse price for S/S+F. That bias works against the source
  rules, not for them.

## 7. Hypotheses and gates

**Candidate cells:**

- the five reversal setups × {A+F, S, S+F} = 15;
- hammer/shooter × {S, S+F} = 2;
- 2-2 continuation × {A+F} = 1.

That makes **18 candidate cells** on MNQ. Arm A is the baseline and cannot
"pass" into anything new.

**Q1 (per candidate cell, MNQ).** A cell **PASSES** only if **all** of these
hold:

1. ≥ 40 terminal trades;
2. PF ≥ **2.55**, the max-of-500 null. With 18 cells, the single-test p95 of
   1.94 is not enough;
3. H1 net > 0 **and** H2 net > 0;
4. net after removing the single best trading day > 0;
5. the top 3 trading days are < 50% of net;
6. PF strictly greater than the same setup's arm-A PF.

A cell with PF between 1.94 and 2.55 that meets items 1 and 3–6 is labeled
**PROMISING / FORWARD-ONLY**. Everything else is **DOES NOT CLEAR**.

**Q2 (replication).** For every MNQ cell labeled PASS or PROMISING, the same
cell on **MES** is reported. It is labeled **REPLICATED** only if MES net > 0
and MES PF > 1.0. MES never creates a pass on its own.

**Q2b (out-of-sample confirmation).** Every cell is also scored on the OOS
window: OOS-A, plus OOS-B if admitted, reported both separately and together.
The window is short (about 1.5–2 months), so the OOS bar is directional, not a
second PF hurdle. A main-window PASS or PROMISING cell is labeled **OOS
CONFIRMED** only if its OOS result has **≥ 10 terminal trades, net > 0 and
PF > 1.0** on MNQ. It is labeled **OOS CONTRADICTED** if OOS net < 0 with
≥ 10 trades, and **OOS INSUFFICIENT** otherwise. OOS results never create a
pass on their own, and a cell that fails the main window is not rescued by OOS.

**Q3 (descriptive only, no gate, no rule).** For arms A and S of every setup,
P&L is broken down by FTFC state at signal (aligned / against / conflict) and
by the corpus `market_condition` label (TRENDING / RANGE_BOUND / CHOPPY /
DEAD). This measures how each setup behaves in range and chop. **No
range-bound rule is adopted from Q3.** Range rules are decided later, from more
forward observer data and a separate preregistration.

## 8. Integrity checks (run before any scoring; a failure stops the study)

1. **Detector parity:** arm-A signals for 2-2 reversal, 3-2-2 reversal,
   2-2 continuation, 2-1-2 and 1-2-2 must match the existing observer
   detectors run through `ReplayEngine` on the same corpus: ≥ 99% identity
   match on (bar timestamp, direction, entry, stop). Mismatches are listed.
2. **Corpus fingerprint** (sha256 over the sorted file list and contents) is computed and recorded. If #911/#912 recorded one, it must match; otherwise this run becomes the recorded baseline.
3. **Causality:** no FTFC open or pattern bar may use a bar whose close is
   after the decision bar's close. A unit test proves this on a synthetic
   series.
4. **Hammer definition** unit-tested on hand-built bars (in / out of the top
   33%).

## 9. Procedure

1. Merge this document **before** any script is written or run.
2. Write `scripts/strat_rules_magnitude_ftfc_audit.py` plus tests on a
   research branch. Record the script sha256 in the results.
3. Run **once** on MNQ and MES. Commit the JSON and markdown results unchanged.
4. **No reruns with different thresholds, windows, setups, stops, targets, or
   filters.** A materially different idea needs a new preregistration.

## 10. What a result can and cannot do

- **OOS CONTRADICTED:** blocks the next step for that cell. It stays on record
  as a main-window result, but gets no new observer population.
- **PASS or PROMISING** (and not OOS CONTRADICTED): permits only a *separate*
  PR proposing a **new**
  observer population in a **new** epoch for forward collection. It never
  touches the running epoch, a trading lane, risk, or a broker path. Any
  trading use would need its own preregistration, forward evidence, staged
  rollback, and explicit operator GO.
- **DOES NOT CLEAR everywhere:** recorded as a rejection of the source rules
  at 15m on this corpus. No further variants on this corpus.
- **In every case:** the current observer keeps running unchanged, and the
  range-bound question stays open for forward data.
