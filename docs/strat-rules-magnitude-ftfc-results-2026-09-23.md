# Strat Source Rules (Magnitude + FTFC) vs. Observer Brackets — Results (2026-09-23)

**RESEARCH / AUDIT ONLY. NO EXECUTION AUTHORITY.** Preregistration:
`docs/prereg-strat-rules-magnitude-ftfc-2026-09-23.md` (#934, merged `41e09e9`
before the script was written). Run once, at script commit `de4f522`,
script sha256 `aa465535…a24bf13e`. Machine-readable results are in
`docs/strat-rules-magnitude-ftfc-results-2026-09-23.json`. Nothing here changes
the running observer epoch or any trading lane.

## Verdict

**DOES NOT CLEAR: all 18 candidate cells on MNQ.** No cell reaches the PF 1.94
PROMISING bar, let alone the 2.55 PASS bar, so Q2 (MES) and Q2b (OOS) never
become gating. This is recorded as a rejection of TheStrat source rules at 15m
on this corpus. Per §10, there are no further variants on this corpus.

Two descriptive findings follow. Neither is a rule.

1. **The Strat magnitude target did not help any reversal setup on MNQ.**
   Arm S's PF was below arm A's (the observer's fixed 2R) on 2-2 reversal
   (0.72 vs 0.95) and 2-1-2 reversal (0.84 vs 1.09), and about tied on 1-2-2
   and 3-1-2. It was higher only on 3-2-2 (0.85 vs 0.81), where both lose.
   Win rates go up (to 32–52%), but the prior-bar extreme sits too close to
   pay for the stop plus costs.
2. **The FTFC entry filter improved four of five MNQ reversal baselines**
   (A → A+F): 2-2 reversal 0.95 → 1.24, 1-2-2 0.77 → 1.11, 2-1-2 1.09 → 1.27,
   3-1-2 0.65 → 0.88. None comes close to the gate. On MES the filter helped
   only 2-1-2 (0.85 → 1.03) and 3-1-2 (0.91 → 1.22).

## Integrity (prereg §8): all passed before scoring

- **Detector parity (MNQ, main window):** identity match on (bar, setup,
  direction) was **100%** (14,229 signals, 0 script-only, 0 engine-only)
  against the observer's `ReplayEngine` classifier. Entry/stop geometry
  matched `_missing_strat_family` **100%** (12,508 signals).
- **Corpus fingerprints (sha256; baseline, none previously recorded):**
  - MNQ main `a118637b16c217cc…`, MES main `5b606eb2821d43cd…`;
  - extension fingerprints are in the JSON.
- **Rolls:** five seams applied (2025-09-11, 12-11, 2026-03-12, 06-11,
  09-15). Their bars were verified against the committed manifests.
- **Causality and hammer definition:** unit-tested
  (`tests/test_strat_rules_magnitude_ftfc_audit.py`, 9 tests).
- **FTFC UNKNOWN in the scoring window:** 5 signals of about 18,500.

## MNQ: main window (2025-08-01 → 2026-07-23), all arms

Net is after costs.

| Setup | A (2R, no filter) | A+F | S (Strat stop + magnitude) | S+F |
|---|---|---|---|---|
| 2-2 reversal | 752 · PF 0.95 · −$1,395 | **290 · PF 1.24 · +$2,472** | 753 · 0.72 · −$3,219 | 280 · 0.60 · −$2,124 |
| 3-2-2 reversal | 334 · 0.81 · −$2,740 | 46 · 0.72 · −$558 | 328 · 0.85 · −$940 | 39 · 1.61 · +$329 |
| 1-2-2 rev-strat | 424 · 0.77 · −$3,545 | 74 · 1.11 · +$251 | 421 · 0.77 · −$1,673 | 70 · 0.63 · −$465 |
| 2-1-2 reversal | 544 · 1.09 · +$1,550 | 68 · 1.27 · +$518 | 502 · 0.84 · −$1,644 | 49 · 1.07 · +$42 |
| 3-1-2 | 230 · 0.65 · −$3,040 | 56 · 0.88 · −$268 | 191 · 0.65 · −$1,365 | 40 · 1.36 · +$213 |
| Hammer / shooter | — | — | 622 · 0.52 · −$4,763 | 89 · 0.39 · −$965 |
| 2-2 continuation | 748 · 1.24 · +$7,857 | 503 · 1.23 · +$5,638 | — | — |

Cell format: terminal trades · PF · net.

**Closest cells, and why they fail:**
- **2-2 reversal A+F:** PF 1.24. Both halves positive (+$948 / +$1,524), OOS
  +$343 on 47 trades. Fails on PF (< 1.94). MES: PF 0.91, −$800.
- **3-2-2 S+F:** PF 1.61, but only 39 trades (< 40).
- **2-2 continuation A+F:** PF 1.23, but H2 is −$582 and OOS is −$2,160 on 83
  trades.

## MES (replication) and OOS: context only, since nothing gated

- **MES:** no reversal cell has PF above 1.23 (3-1-2 A+F, 62 trades). 2-2
  reversal is negative in all four arms.
- **OOS:** OOS-A 2026-07-24 → 09-14 plus OOS-B 09-15 → 09-21.
  - Every MNQ **S** arm with ≥ 10 trades is negative, except 3-1-2 S (+$87,
    30 trades).
  - Reversal A+F cells positive out of sample with ≥ 10 trades: 2-2
    reversal (+$343, 47 trades), 2-1-2 (+$328, 11) and 3-1-2 (+$1, 10).

## Q3: descriptive only, no rule adopted

MNQ arm A, P&L by FTFC state at signal, main window:

| Setup | aligned | against | conflict |
|---|---|---|---|
| 2-2 reversal | 89 · PF 1.37 · +$1,064 | 130 · 0.73 · −$1,395 | 533 · 0.95 · −$1,064 |
| 1-2-2 | 67 · 1.13 · +$265 | 41 · 0.44 · −$970 | 316 · 0.76 · −$2,839 |
| 2-1-2 reversal | 52 · 1.37 · +$565 | 70 · 1.35 · +$727 | 422 · 1.02 · +$259 |
| 3-2-2 reversal | 43 · 0.74 · −$504 | 43 · 0.43 · −$942 | 248 · 0.88 · −$1,295 |
| 3-1-2 | 53 · 0.86 · −$293 | 17 · 0.53 · −$272 | 160 · 0.60 · −$2,475 |
| 2-2 continuation | 217 · 1.55 · +$5,144 | 29 · 0.95 · −$56 | 502 · 1.12 · +$2,770 |

About 70% of signals occur in FTFC **conflict**, and conflict is where most of
the losses sit. By the corpus `market_condition` label:
- 2-1-2 reversal loses in RANGE_BOUND (−$2,161) but wins in CHOPPY
  (+$2,407) and TRENDING (+$1,538);
- 2-2 reversal loses most in TRENDING (−$1,799);
- 2-2 continuation is positive in TRENDING (+$3,490) and CHOPPY (+$3,066).

All regime numbers are in the JSON.
The range-bound question stays open for forward data, per §7.

## Implementation notes (interpretations of the prereg, disclosed)

1. **Setup precedence:**
   - Sequences use the observer's own `classify_sequence` precedence, so a
     2-2 reversal excludes 3-2-2 and 1-2-2 openers, as in the observer. This
     is why parity is 100%.
   - Hammer/shooter requires `t` to be a 2u / 2d. A 3 that breaks both sides
     of `t-1` is not taken, because the break order is unknown.
2. **Parity scope:**
   - Identity parity covered all six classifier setups.
   - Geometry parity is only possible for the four families that
     `_missing_strat_family` brackets.
   - The observer's 1-2-2 detector uses a different bracket (break of `t`,
     stop 4 ticks beyond `t`), and there is no observer 2-1-2 reversal
     bracket. Arm A for those two follows §5 literally.
3. **Beats arm A (item 6):** N/A for hammer/shooter, which has no arm A under
   §5. It did not change any verdict.
4. **Clock details:**
   - The 60-minute open is the open of the 15m bar at the top of the ET hour
     containing the decision bar.
   - Session exit is at the close of the trading date's last bar, with 1
     adverse tick.
   - A signal on a session's last bar therefore exits at the same close (a
     cost-only trade). 608 SESSION_END exits across all cells.
5. **Roll rule:** a pattern is skipped if a seam falls between `t-3` and the
   end of the decision bar's trading day, which covers "forward resolution
   window crosses a seam". As a result, no ROLL_EXIT occurred (164 signals
   skipped as ROLL across all cells; 58 as GAP).
6. **The 3-fills-per-day cap binds hard:** 21,436 signals across all cells
   were `SKIPPED_MAX_FILLS`, and 7,610 were `SKIPPED_BUSY`. This is the
   preregistered cap, identical across arms.
7. **Trade rows:** per-trade rows are kept out of the repo because of their
   size and strategy detail. The JSON carries every cell summary, both halves,
   both OOS windows and the Q3 breakdowns.

## What this closes, and what it does not

- **Closed:** the idea that "the observer isn't using the real Strat rules, so
  that's why it loses". The real rules (magnitude target, entry-bar stop) do
  worse at 15m. FTFC helps, but not enough to clear a multiple-testing-honest
  bar.
- **Unchanged:** the running observer epoch, every trading lane, and the
  standing record for all these families (07-16 tranche 2, 07-31 null, 09-21
  grid, #911).
- **Not tested here:** the Strat setups on higher timeframes (60m / 4H /
  daily, where magnitudes are larger relative to costs), and intrabar
  (stop-order) entries. Either would need its own preregistration.
