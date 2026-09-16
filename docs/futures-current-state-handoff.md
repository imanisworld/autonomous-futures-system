# Futures — Current State Handoff

_As of 2026-09-16 evening. Historical audit docs remain evidence records but do not override this file. Repository state and runtime state are tracked separately._

## Verdict

**PAPER / OBSERVATION ONLY. HOLD on strategy changes. NO LIVE EXECUTION OR STRATEGY PROMOTION.**

Core rule: **No proof, no run.**

The retrospective futures investigation has reached a stopping point. The observation/evidence infrastructure is in place, the major representation/gate alternatives were tested, MES did not validate the MNQ idea, and no retrospective study justified changing strategy gates or execution rules.

## Repository versus runtime

Current GitHub `main` is **`2521ebb92c4add36fb2c21f04c13df84250b332f`** after docs-only Options PR #599. This does **not** mean the futures box runs that SHA.

Final operator/Claude runtime ledger reports deployed futures release **`62546883` (#588)**, with the cross-instrument observation campaign armed at **12:17Z** using a fresh epoch. All six roots — MNQ, MES, M2K, MGC, MCL, MBT — are reported healthy and collecting since. TradingView auth was repaired for the new roots, the feed watchdog was installed, and both running futures evidence campaigns were left untouched after arming.

Release **`f9d5395`** containing #590/#591 is built but held because `DISCORD_ROUTE_OBSERVATION` is still missing on the box.

## What was completed

### Observation and evidence infrastructure

- **#585–#588** built the six-root observation system with 15m isolation, epoch isolation, idempotent evidence writes, feed-health proof, continuity/roll/provenance gates, and fail-closed routing.
- **#589** repaired time-expiring historical fixtures only; no runtime behavior change.
- **#590/#591** separated observation Discord events from operational/safety errors.
- **#592** added repeatable read-only why-no-trade and counterfactual reporting.

### Reproducible missed-opportunity research

- **#593** preserved and deterministically reproduced the MNQ missed-opportunity producer, including WIN / LOSS / NO_FILL / EXPIRED semantics and byte-identical reruns.
- The full retrospective representation chain was then run: why-no-trade, layer-isolated counterfactuals, outcome-first missed moves, representation v2–v4, pre-entry, lateness, low-displacement, session validation, Asia validation, MES portability, and precursor analysis.

**Retrospective conclusion:** no gate relaxation, stop change, lateness filter, low-displacement filter, or alternative representation produced enough robust evidence to justify a strategy change. New York failed on both MNQ and MES. Broad gate relaxation and tighter-stop ideas failed or overfit.

## #595 — MNQ Asian D+EMA prospective paper cohort

**Code verdict: APPROVE FOR MERGE. Operational verdict: HOLD / NOT ACTIVATED.**

#595 is the smallest isolated prospective test of the only historical population still worth observing.

Safety boundary:

- MNQ only;
- Asian session only;
- `ema_pullback_trend` + `strat_22_continuation_observed` only;
- 15m only;
- PaperBroker only;
- one open cohort position at a time;
- default OFF;
- activation requires both explicit env keys;
- crash-safe/idempotent persistence;
- central contract economics;
- archived-population parity and provenance checks.

The population-delta follow-up found no material historical population change.

Historical evidence is **not strong enough for validation**:

- collapsed MNQ Asia PF roughly **1.46–1.59**;
- about **107 independent trades**;
- null p95 PF about **1.94**;
- September approximately flat;
- MES did not replicate.

Classification: **PROMISING BUT UNPROVEN / PAPER ONLY.** The result may still be sample noise.

## MES validation / #598

The MES 5m corpus was extended through 2026-09-16 with provenance. The data matched the box feed within one tick, the 15m snapshot had zero duplicate/conflicting bars, the known 2026-09-14 22:00–23:55Z roll seam was quarantined, and study reruns were byte-identical.

**MES D+EMA verdict: BROKEN.** It failed after one-position collapse in every session and in both historical windows. It did not reproduce the MNQ Asian result. No MES forward cohort should be created from this evidence.

## #596 precursor / representation result

**PARTIAL / UNPROVEN. NO RULE CHANGE.**

The broad shape partially replicated: winners more often emerged from range/chop rather than from an already-`TRENDING` label, and that tendency appeared in both MNQ and MES.

But:

- only `strat_22_continuation` had adequate September sample size;
- **94 of 118 features were unstable**;
- no stable feature set justified threshold tuning or a new gate;
- usable BOS/MSS coverage in the matched precursor data was **zero**.

## #594 BOS/MSS status

#594 remains a transparent causal BOS/MSS event-study tool, not a strategy. Its code/CI boundary is sound, but the completed precursor chain provided **zero usable BOS/MSS coverage**, so BOS/MSS was not empirically tested as a useful missing signal.

Status: **WAIT / NO DATA.** Do not build BOS/MSS strategy logic now. Reopen only if future preserved data provides real structure coverage or a specific new question requires it.

## What is settled enough to stop investigating

- Observation-only collection does not grant new instruments trading eligibility.
- Bad or provenance-ambiguous evidence cannot count as proof.
- The MNQ missed-opportunity analysis is reproducible.
- Broad retrospective gate relaxation: **NO CHANGE**.
- Tighter-stop / lateness / low-displacement rescue attempts: **NO CHANGE**.
- New York D+EMA on MNQ or MES: **BROKEN**.
- MES D+EMA portability: **BROKEN**.
- Existing gates are not shown by this research to be obviously wrong.
- BOS/MSS is not currently supported by usable evidence.
- More retrospective variants are unlikely to answer the remaining question better than prospective evidence.

## What is still genuinely unknown

- Whether MNQ Asian D+EMA is a real repeatable edge or sample noise.
- Whether its forward paper behavior will resemble the historical result.
- Whether the range/chop precursor observation is causal/useful or merely descriptive.
- Whether future data will eventually provide enough BOS/MSS coverage to study structure separately.

## Required next sequence

1. Operator adds the missing observation Discord route key.
2. Release `f9d5395` under the existing lock-window rules and restart/re-baseline the watcher through the sanctioned path.
3. Decide stack cleanup / merge disposition for #593 and merge disposition for #595 on explicit operator order.
4. Remove detached #598/#596 research worktrees when those lanes are formally closed.
5. Keep the current observation campaigns collecting.
6. **Do not add more retrospective strategy experiments without a new, evidence-backed question.**
7. When the September 30 no-release/no-restart restriction lifts, and only with explicit authorization, activate #595 prospectively using its two required env keys plus sanctioned release/restart.

That prospective cohort is the next meaningful strategy-learning event.

## Later maintenance already logged

- non-ASCII webhook secret should return 401 rather than 500;
- calendar-sync unit removal before October 1;
- add `source_ticker` provenance on the MNQ/MES trading path.

These are maintenance items, not reasons to reopen strategy research.

## Hard boundaries

Do not enable live execution; add broker submission from research code; relax global risk rules to rescue a study; create an MES D+EMA forward lane; turn the precursor observation into a tuned rule; build BOS/MSS strategy logic without new real coverage; activate #595 before the restriction lifts and explicit authorization is given; or restart the retrospective research chain merely because the prospective result is unknown.

## Safe next step

**Collect evidence, do not invent another retrospective study.** Add the observation Discord key, release `f9d5395` through the sanctioned path, keep the campaigns collecting, and treat prospective #595 paper evidence as the next meaningful strategy-learning event once activation is explicitly authorized after the restriction lifts.
