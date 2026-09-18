# OPTIONS — OUTCOME-INDEPENDENT UNSUPPORTED-FAMILY VALIDATION

Read-only evidence task, 2026-09-16. Nothing changed, fetched, restarted, tuned or deployed. Prior audits imported, not rerun.

## VERDICT: PARTIAL EVIDENCE

Sufficient to classify each family on five sessions; insufficient for any expectancy claim, any production decision, or any statement about option contracts.

## POPULATION

- Files: `coverage_events.json` (observer cov-v0.1 raw structural events), `outcomes_2026-09-09_2026-09-15.json` (reducer ep-v0.1 episodes + outcome out-v0.1 views), `bars.json` (delayed SIP 30m bars, used only to prove completeness). Collector source SHA 771b6cf, manifest fingerprint 690f259f.
- Sessions: 2026-09-09, 09-10, 09-11, 09-14, 09-15. September 16 is excluded because the observer had not yet run it; the window was not extended.
- Primary universe: the 20-symbol audit universe. Secondary: the full 148-symbol observer corpus, reported separately and never mixed.
- Completeness proof: 944 directional (2U/2D) regular-session 30m bars exist for the 20 symbols in these sessions; the observer holds 944 raw events for them; 0 directional bars lack an event; all 82 opening-bar directional bars are present. Non-directional bars (1, 3) are not events by definition.
- Causality proof: family is assigned by `classify_window` from the three prior bars and the current bar only; `first_sight_at` is the first scanner tick after bar close plus the 960 s SIP buffer (17.9 min on every event, never before the close); outcomes are walked forward on 5m bars only after the setup exists and never re-family or re-trigger it.
- Counts (primary): 944 raw occurrences, 835 independent episodes, 835 triggered. Secondary: 7071 raw, 6196 episodes.

## SELECTION AND EPISODE RULES

- Occurrence: any completed 30m bar whose current bar is 2U or 2D, classified before any outcome is read.
- Episode (reducer rule, imported): same symbol, session, family and direction on contiguous 30m bars = one episode; a gap, family change or direction change starts a new one. Fields from the first event. One family per bar by construction, so families never overlap on a bar.
- Trigger: the structure exists only when the current bar broke the previous bar's extreme, so every occurrence is TRIGGERED by construction. NEVER_TRIGGERED is not a state in this taxonomy. Mechanical entry is at the trigger price inside the breakout bar; first-sight entry is the price at the first scanner tick after the bar.
- Horizon: same regular session for every family (fixed by the outcome module). Bars-to-1R is reported at 2, 4 and 6 bars for sensitivity; no horizon was chosen per family.
- R denominator: structural risk = |trigger − invalidation| for both views.

## CRITICAL CAVEAT: OPENING-BAR MECHANICAL ENTRIES

Opening-bar (14:00 close) episodes reach 1R mechanically 96.3% of the time versus 42.6% for all other bars. The mechanical entry is placed at the trigger price inside the first bar, but when the session opens through the trigger that fill is not attainable. First-sight on the same bars is 54.9%. Every family table is therefore reported twice: all episodes, and opening bars excluded. Families whose apparent strength comes from opening bars are flagged.

## FAMILY RESULTS — PRIMARY 20 SYMBOLS

Independent episodes; matched baseline = other-family episodes with the same symbol, direction and clock bucket (opening / 14:30–16:00 / later). Diff = family minus baseline, percentage points.

| Family | Episodes | Triggered | 1R | 2R | Stop first | Median MFE R | Baseline 1R | Diff | First-sight 1R (n) | FS baseline | FS diff | Ex-opening 1R / diff | Ex-opening FS 1R / diff | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2-2-2 reversal | 176 | 176 | 48.3 | 26.1 | 10.2 | 0.95 | 46.2 | +2.1 | 35.6 (160) | 34.8 | +0.8 | 43.4 / +1.3 | 35.0 / +2.7 | ENOUGH_FOR_FOLLOW-UP |
| 2-1-2 reversal | 81 | 81 | 51.9 | 21.0 | 12.3 | 1.10 | 40.8 | +11.1 | 44.2 (77) | 32.3 | +11.9 | 51.9 / +11.1 | 44.2 / +11.9 | ENOUGH_FOR_FOLLOW-UP |
| 2-2-2 continuation | 197 | 197 | 47.7 | 27.9 | 22.3 | 0.89 | 54.1 | −6.4 | 34.6 (185) | 41.4 | −6.8 | 36.2 / −12.0 | 27.7 / −11.4 | ENOUGH_FOR_FOLLOW-UP |
| 1-2-2 | 60 | 60 | 51.7 | 18.3 | 11.7 | 1.02 | 41.1 | +10.6 | 45.6 (57) | 33.5 | +12.1 | 50.0 / +10.9 | 43.6 / +12.4 | DESCRIPTIVE_ONLY |
| Outside-bar follow-through | 80 | 80 | 38.8 | 16.2 | 21.2 | 0.56 | 46.0 | −7.2 | 24.7 (73) | 31.7 | −7.0 | 23.4 / −13.2 | 14.0 / −14.8 | ENOUGH_FOR_FOLLOW-UP |
| 3-2-2 reversal | 29 | 29 | 44.8 | 20.7 | 20.7 | 0.88 | 42.3 | +2.5 | 25.0 (24) | 38.7 | −13.7 | 42.9 / +2.6 | 26.1 / −10.5 | INSUFFICIENT |
| 3-1-2 | 21 | 21 | 47.6 | 19.0 | 28.6 | 0.88 | 44.2 | +3.4 | 14.3 (21) | 32.2 | −17.9 | 42.1 / +3.8 | 10.5 / −19.8 | INSUFFICIENT |
| 2-2 continuation | 83 | 83 | 54.2 | 15.7 | 18.1 | 1.18 | 47.3 | +6.9 | 37.3 (75) | 35.6 | +1.7 | 51.9 / +5.9 | 36.6 / +2.0 | ENOUGH_FOR_FOLLOW-UP |
| Inside-bar break | 25 | 25 | 48.0 | 40.0 | 24.0 | 0.88 | 45.0 | +3.0 | 32.0 (25) | 30.2 | +1.8 | 48.0 / +3.0 | 32.0 / +1.8 | INSUFFICIENT |
| 3-2-2 continuation | 24 | 24 | 25.0 | 12.5 | 41.7 | 0.50 | 32.8 | −7.8 | 9.1 (22) | 23.3 | −14.2 | 19.0 / −10.6 | 10.5 / −9.1 | INSUFFICIENT |
| 2-1-2 continuation (reference, supported) | 59 | 59 | 52.5 | 23.7 | 22.0 | 1.12 | 45.9 | +6.6 | 56.4 (55) | 35.7 | +20.7 | 52.5 / +6.6 | 56.4 / +20.7 | DESCRIPTIVE_ONLY |

All directional episodes, primary: n 835, 1R 47.9%, 2R 23.0%, invalidation-first 18.2%, median MFE 0.92R, median close 0.09R; first-sight 1R 35.8% (n 774). Mechanical outcomes: TARGET_FIRST 527, UNRESOLVED 155, INVALIDATION_FIRST 152, AMBIGUOUS 1. First-sight: 61 episodes were first seen after the close and are unpriced.

Sample-size labels use episode count only: under 30 INSUFFICIENT, 30 to 79 DESCRIPTIVE_ONLY, 80 and above ENOUGH_FOR_FOLLOW-UP. No universal minimum exists in the repository; these labels are this report's, stated explicitly.

## SECONDARY — 148 SYMBOLS (robustness only, not the decision population)

| Family | Episodes | 1R | 2R | Baseline 1R | Diff | FS 1R | FS diff |
|---|---|---|---|---|---|---|---|
| 2-2-2 reversal | 1501 | 48.0 | 22.9 | 43.3 | +4.7 | 33.2 | +2.7 |
| 2-1-2 reversal | 503 | 49.5 | 22.1 | 40.0 | +9.5 | 36.0 | +7.4 |
| 2-2-2 continuation | 1381 | 44.6 | 21.8 | 50.2 | −5.6 | 32.1 | −2.9 |
| 1-2-2 | 388 | 57.0 | 28.9 | 44.1 | +12.9 | 41.4 | +10.8 |
| Outside-bar follow-through | 661 | 38.7 | 16.9 | 48.7 | −10.0 | 24.8 | −8.3 |
| 3-2-2 reversal | 202 | 44.6 | 21.8 | 44.2 | +0.4 | 32.2 | −0.2 |
| 3-1-2 | 164 | 39.0 | 15.9 | 43.4 | −4.4 | 22.9 | −8.3 |
| 2-2 continuation | 547 | 43.3 | 15.9 | 47.1 | −3.8 | 31.7 | −1.7 |
| Inside-bar break | 132 | 58.3 | 36.4 | 41.0 | +17.3 | 38.5 | +8.7 |
| 3-2-2 continuation | 274 | 42.7 | 19.3 | 46.2 | −3.5 | 26.2 | −4.2 |
| 2-1-2 continuation (reference) | 443 | 54.0 | 23.7 | 42.1 | +11.9 | 38.2 | +6.8 |

## BASELINE

Ordinary matched directional price action reaches 1R mechanically about 41 to 54 percent of the time depending on stratum, and about 30 to 41 percent at first sight. Most families sit within a few points of their matched baseline. Two families exceed it consistently in both views, both populations and with opening bars excluded: 2-1-2 reversal and 1-2-2. Two families sit consistently below it: 2-2-2 continuation and outside-bar follow-through, whose full-sample rates are propped up by opening-bar mechanical fills.

## CONCENTRATION

No family's 1R hits depend on one symbol: top-symbol share is 9 to 17 percent for the larger families. Session dependence is moderate: the top session holds 23 to 33 percent of hits for the four largest families. Direction matters: 2-1-2 reversal LONG 41.9% vs SHORT 63.2%; 2-2-2 continuation LONG 34.1% vs SHORT 58.0%; 2-2 continuation LONG 46.7% vs SHORT 63.2%. The sample sessions were net bearish, so a SHORT skew across families is a regime signature, not a family property. Outside-bar follow-through takes 52 percent of its hits from opening bars.

## FIRST-SIGHT

First-sight one-R rates run 8 to 15 points below mechanical across the board, and the gap is larger for the continuation and outside-bar families. 2-1-2 reversal and 1-2-2 keep their excess over baseline at first sight (+11.9 and +12.1). The supported 2-1-2 continuation family shows the largest first-sight excess of all (+20.7), which is consistent with the prior finding that this family's losses come after detection, at alignment and target geometry, not from the structure.

## CONTEXT STRATIFICATION (descriptive, cells under 10 omitted)

SPY and QQQ alignment add 10 to 25 points of mechanical 1R inside most families but also shrink the sample by half or more. Daily alignment helps 2-1-2 reversal (+21.7) and 1-2-2 (+28.0) and hurts nothing materially. Hourly alignment is inconsistent: positive for 2-2-2 reversal, negative for 2-2-2 continuation. Signa v2 is not in the observer corpus; GEX is NOT_APPLICABLE. No subgroup search was performed; these are the fixed splits requested.

## ROOT CONCLUSIONS (one per family, primary population)

| Family | Classification | Basis |
|---|---|---|
| 2-2-2 reversal | NO OBSERVED EDGE | +2.1 mech, +0.8 FS, within noise of matched baseline in both populations |
| 2-1-2 reversal | POSSIBLE SIGNAL — PROSPECTIVE OBSERVATION JUSTIFIED | +11 to +12 in both views, survives opening-bar exclusion, +9.5 / +7.4 in the 148 corpus; direction-skewed; 5 sessions only |
| 2-2-2 continuation | NO OBSERVED EDGE | below baseline once opening bars are excluded, in both views and both populations |
| 1-2-2 | POSSIBLE SIGNAL — PROSPECTIVE OBSERVATION JUSTIFIED | +10.6 / +12.1 primary on 60 episodes (descriptive), +12.9 / +10.8 on 388 secondary |
| Outside-bar follow-through | NO OBSERVED EDGE | below baseline in every cut; half its hits are opening-bar artifacts |
| 3-2-2 reversal | INSUFFICIENT SAMPLE | 29 episodes; secondary at baseline |
| 3-1-2 | INSUFFICIENT SAMPLE | 21 episodes; first-sight sharply negative; secondary below baseline |
| 2-2 continuation | NO OBSERVED EDGE | +6.9 mech but +1.7 FS, and negative in the secondary corpus |
| Inside-bar break | INSUFFICIENT SAMPLE | 25 episodes; secondary +17.3 / +8.7 on 132 flags it for future counting |
| 3-2-2 continuation | INSUFFICIENT SAMPLE | 24 episodes; negative in all cuts |

No family is production-ready. No detector is authorized. "Possible signal" means only that a future observer lane or prospective count may be justified.

## PROVEN

- Every directional 30m bar in the primary universe and sessions is in the population; selection preceded outcome.
- Unconditional structure reaches 1R about 48% mechanically and 36% at first sight; most named families do not differ from matched directional price action by more than a few points.
- Mechanical opening-bar results are a gap artifact and must not be used.
- 2-1-2 reversal and 1-2-2 exceed their matched baselines by about 10 to 12 points in both views, in both populations, with and without opening bars, over five sessions.
- 2-2-2 continuation and outside-bar follow-through underperform matched baselines; their raw counts in the prior coverage audit reflected frequency, not signal.

## NOT PROVEN

- Expectancy, profit factor or any P&L for any family: the horizon is one session, targets are structural levels, and no costs or options are modelled.
- Persistence beyond five net-bearish sessions; the direction skew may be regime.
- Anything about contracts: feasibility, spreads, theta, expectancy.
- Whether the two "possible" families survive the production alignment and target-geometry gates, which the prior audit showed remove most 2-1-2 continuation episodes.

## NEXT EVIDENCE STEP (if justified)

Prospective, observer-only counting of 2-1-2 reversal and 1-2-2 on 30 minutes across the coming sessions with the identical outcome module, plus continued accumulation of inside-bar break, which is too small to classify in the primary universe. This is an observation request, not a detector request.

## NO CHANGES AUTHORIZED.
