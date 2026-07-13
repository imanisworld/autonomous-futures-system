# MNQ 5-minute impulse -> pullback -> continuation study (2026-07-13)

Follow-up to the REJECTED structural-level break/retest study
(`docs/mnq-structural-level-5m-study-2026-07-13.md`). That design's failure
mode was entering immediately on a mapped-level break/retest with a very
tight structural stop against a distant single-level target. This is a
genuinely different setup family, per the operator's explicit direction, not
a retuned version of that one.

## Method

`research/mnq_5m_impulse_pullback_continuation.py` (pure, stateless-per-call
detector, research-only):

1. **Established impulse** — reuses the existing `trend_direction`/
   `trend_strength`/`market_condition` fields (already computed upstream);
   requires STRONG + TRENDING in the trade direction on the bar immediately
   before the pullback begins. Nothing new invented here.
2. **Pullback** — the longest contiguous run of bars (1 to 8, i.e. up to 40
   minutes) closing against the impulse direction, immediately following an
   established-impulse bar. Bounded so a genuine reversal isn't mistaken for
   a pullback.
3. **Continuation confirmation** — current bar's close breaks back beyond
   the pullback's own high (long) / low (short).
4. **Stop** beyond the pullback's own swing low/high (+2pt buffer) — never a
   mapped level.
5. **Target** capped at a configurable R-multiple (swept 1.5R/2.0R/3.0R per
   the operator's suggested range) — never "the next mapped level."
6. Long and short scored and reported **separately**.
7. Session bucketed overnight/premarket/rth (same taxonomy as the prior
   study), reported separately.
8. **2-tick adverse slippage used from the start** (not an optimistic
   1-tick assumption retrofitted later, per explicit instruction), $1.48
   round-trip commission, pessimistic both-hit resolution via the same
   `PaperBroker` used throughout this codebase's replay studies. A 3-tick
   and 4-tick stress pass was run afterward on the standout result.

`scripts/mnq_5m_impulse_pullback_continuation_study.py` walks all 621 days
of `data/replay_polygon_5m/MNQ`, one hypothetical position per direction at
a time (a 3-bar cooldown after each trigger prevents near-duplicate
retriggers in a choppy continuation).

## Results — combined long+short, all sessions

| R-multiple | n | win rate | net P&L | expectancy | PF | 1st half exp | 2nd half exp |
|---|---|---|---|---|---|---|---|
| 1.5R | 3,911 | 40.1% | -$11,424 | -$2.92 | 0.923 | -$4.37 | -$1.39 |
| 2.0R | 3,862 | 33.5% | -$13,326 | -$3.45 | 0.917 | -$4.86 | -$1.96 |
| 3.0R | 3,751 | 24.8% | -$23,025 | -$6.14 | 0.869 | -$7.98 | -$4.17 |

**Combined, this family is negative at every R-multiple tested, both
halves.** But direction split (per the required "test longs and shorts
separately") reveals the negative result is not symmetric:

## Results — split by direction (R=1.5R, the best-performing multiple)

| Direction | n | win rate | net P&L | expectancy | PF |
|---|---|---|---|---|---|
| Long | 2,296 | 39.2% | -$12,740 | -$5.55 | 0.836 |
| **Short** | **1,615** | **41.4%** | **+$1,316** | **+$0.81** | **1.019** |

Long is a clear, consistent loser at every R-multiple (-$5.55 at 1.5R, -$6.25
at 2.0R, -$8.00 at 3.0R). **Short at R=1.5 is the one segment that clears
the operator's own explicit gate**: walk-forward positive both halves
(first half +$1.02/trade, second half +$0.59/trade) at the specified 2-tick
slippage baseline.

## Slippage stress on the short-only, R=1.5 result

| Slippage | n | net P&L | expectancy | PF | 1st half exp | 2nd half exp |
|---|---|---|---|---|---|---|
| 2 ticks (baseline, per instruction) | 1,615 | +$1,316 | +$0.815 | 1.019 | +$1.02 | +$0.59 |
| 3 ticks | 1,615 | +$508 | +$0.315 | 1.007 | +$0.52 | +$0.09 |
| 4 ticks | 1,615 | -$299 | -$0.185 | 0.996 | +$0.02 | -$0.41 |

This is a materially more robust result than the rejected level-fade study
(which flipped negative at just one tick beyond its own optimistic
baseline) — this one survives an *additional* tick of stress before
weakening to roughly breakeven. It is still thin: it does not survive a
4-tick assumption, and the margin decays quickly with each added tick.

## By session (R=1.5, combined long+short — long's losses dominate every
bucket; a session-split of the short-only result specifically was not
computed in this pass)

| Session | n | win rate | net P&L | expectancy | PF |
|---|---|---|---|---|---|
| asian (overnight) | 1,456 | 42.8% | -$632 | -$0.43 | 0.983 |
| london (premarket) | 1,175 | 38.4% | -$4,348 | -$3.70 | 0.891 |
| new_york (rth) | 1,280 | 38.6% | -$6,444 | -$5.03 | 0.910 |

## Classification

**PROMISING BUT UNPROVEN — short-only, R=1.5, at the 2-tick baseline.**
Combined long+short family: still not viable at any tested R-multiple. Long
alone: REJECTED, consistently negative. Short alone at R=1.5: clears
"survives both halves with realistic (2-tick) fills," per the operator's
explicit gate for this follow-up study — but the margin is thin ($0.81/
trade net) and decays to roughly breakeven by 4 ticks, and it has not been
manually validated against a real chart event the way the prior study was.

**No shadow/live integration has been built.** This clears the specific
technical bar set for this study, but given (a) it's only one direction of
the two requested, (b) the margin is thin and slippage-sensitive, and (c)
building a shadow lane is a consequential step, this is being reported for
an explicit decision rather than built out automatically.

## What a decision to proceed would require, if made

- Restricting the config to short-only, R-multiple pinned at 1.5, since
  that is the only combination that cleared the bar — not a general
  "impulse/pullback/continuation, both directions" lane.
- A session-specific breakdown of the short-only result (not yet computed)
  to check whether the edge concentrates in one session or is genuinely
  broad.
- A manual validation against a real chart event, the way the rejected
  study's finding was concretely reconstructed against today's real box
  data, before any live wiring.
