# MNQ structural-level 5-minute break/retest/reclaim/rejection study (2026-07-13)

## Motivation

Operator flagged a concrete failure: the system received the market context and
the mapped structural levels on a recent overnight-selloff/morning-bounce/
rejection sequence but never scored any of the three legs against those levels
at all. This study asks whether a plain structural-level break/retest/reclaim/
rejection family (LONG and SHORT, 5-minute, MNQ-only) has a real, walk-forward-
robust edge before any shadow/live integration is built.

## Scope actually testable

Requested levels: GEX flip, MID, HVI, PDL/PDH, MP, overnight high/low, ORB
high/low, generic support/resistance.

Checked empirically (not assumed) against `context/market_context.py` /
`webhook/payload.py` and a 3,456-row sample of `data/replay_polygon_5m/MNQ`:

| Level | In schema? | Populated in the historical replay set? |
|---|---|---|
| Previous-day high/low (PDH/PDL) | yes | **yes, 100%** |
| ORB high/low | yes | **yes, 100%** |
| GEX flip / mid_upper / mid_lower / call_wall / put_wall / hvl / max_pain | yes | **no, 0%** (never backfilled from Polygon; comes from Public.com/TradingView-manual GEX inputs at alert time, live-only) |
| Supply/demand zone (closest thing to "support/resistance") | yes | **no, 0%** in the raw payload passthrough — but a SEPARATE, already-deployed `wall_context`/`range_signal` module (see `project_rangebound_status`) computes a real SUPPLY_ZONE/DEMAND_ZONE from price action live on the box; not present in the historical replay set either, so still untestable here |
| "MID" (chart concept), "HVI", "MP" (market profile), overnight-session-specific high/low | **not in the schema at all** | n/a |

Per the explicit instruction not to invent new proprietary levels: MID/HVI/MP/
overnight-high-low are not implemented. GEX flip and supply/demand are wired
generically in `context/mnq_structural_level_5m.py::mapped_levels()` (used if
a live alert ever carries a non-null value) but are **backtest-blind** — a
disclosed data gap, not an overclaim. The replay below exercises **only PDH,
PDL, ORB high, ORB low** — the four levels actually present in both live
alerts and the 621-day historical set.

## Method

- `research/mnq_structural_level_5m.py`: pure, stateless detector. For each
  bar, close-cross reclaim/rejection (`reclaim`/`failed_breakdown` long,
  `rejection`/`failed_reclaim` short) and break-then-retest continuation
  (`break_and_retest`, both directions) are evaluated against every mapped
  level present on that bar, using only strictly-prior bars as history (no
  lookahead by construction). Stop = structural swing low/high + 2pt buffer.
  Target = nearest OTHER mapped level beyond entry in the trade direction.
  Context classified aligned/neutral/opposed/unclear from the existing
  `trend_direction`/`trend_strength`/`market_condition` fields (opposed ->
  reject). Session gated via the existing `detect_session()` taxonomy
  (asian/london/new_york -> overnight/premarket/rth).
- `scripts/structural_level_5m_study.py`: walks all 621 days of
  `data/replay_polygon_5m/MNQ` bar by bar, dedupes to one setup per
  (level, direction, setup_type) per day, resolves ACCEPTED candidates
  forward with `execution.paper_broker.PaperBroker` (pessimistic both-hit,
  1-tick adverse slippage, $1.48 round-trip commission) — the same resolver
  already validated in `scripts/mnq_entry_refresh_study.py`.

Two exit variants tested, per the spec's TARGET LOGIC section (primary =
fixed target at the next mapped level; "optional runner target" clause
tested as a full runner-only exit, since this codebase's own prior findings
— [[project_strat_runner_edge]], [[project_exit_structure_findings]] — hold
that fixed targets are close to a coin-flip and a runner trail is the actual
lever):

## Results

### Fixed-target exit (primary spec'd behavior)

3,396 resolved trades. **BROKEN.**

| | n | win rate | net P&L | expectancy | profit factor |
|---|---|---|---|---|---|
| Overall | 3,396 | 18.6% | -$22,599 | -$6.65 | 0.833 |
| First half (2024-07..2025-06) | 1,723 | 19.8% | -$9,471 | -$5.50 | 0.859 |
| Second half (2025-07..2026-06) | 1,673 | 17.5% | -$13,128 | -$7.85 | 0.808 |

By RR bucket (the actual root cause): high-RR trades (nominal RR > 8, i.e. a
tiny structural stop against a very distant single-level target — 885 of
3,396 resolved trades, 26%) have a **4.6% win rate** and drag the whole
sample deeply negative (net -$13,741 alone). Even the most conservative
RR<=2 bucket is still net negative (448 trades, 35.5% WR, -$2.02/trade
expectancy). No RR bucket is profitable under fixed-target resolution.

### Runner exit (activation 1.0R / trail 0.5R — same defaults as the two live
MNQ shadow lanes, PR #266/#267)

3,528 resolved trades, both halves individually positive:

| | n | win rate | net P&L | expectancy |
|---|---|---|---|---|
| Overall | 3,528 | 44.6% | +$1,616 | +$0.46 |
| First half | 1,797 | 45.2% | — | +$0.76 |
| Second half | 1,731 | 44.0% | — | +$0.15 |

This is walk-forward-consistent (both halves independently positive) — the
threshold for **PROMISING BUT UNPROVEN**. But the margin is razor-thin, and
it does not survive a modestly more conservative, still-realistic slippage
stress test:

| Adverse slippage on entry | net P&L | expectancy | profit factor |
|---|---|---|---|
| 1 tick (used above) | +$1,616 | +$0.46 | 1.017 |
| 2 ticks | **-$198** | **-$0.06** | 0.998 |
| 3 ticks | -$2,247 | -$0.64 | 0.978 |

A single extra tick of adverse fill on a market order confirming a break —
not an aggressive assumption for a fast-moving structural-level moment —
flips the sign. This is a fragile, not a real, edge.

## Rejection breakdown (2,241,840 bar-level candidates considered)

`NO_CONFIRMED_CLOSE` 1,099,472 · `NO_RETEST` 1,098,395 · `DUPLICATE_SETUP`
12,337 · `CONTEXT_OPPOSED` 9,372 · `STOP_TOO_WIDE` 9,051 · `NO_MAPPED_LEVEL`
5,541 · `RR_TOO_LOW` 3,993. (`SESSION_DISABLED`, `INVALID_STOP`,
`TARGET_TOO_CLOSE` did not fire meaningfully in this dataset — off_hours
bars are pre-filtered by the loader, not counted here.)

## Manual chart validation (today, 2026-07-13, real box data — not the
screenshot's pixel positions)

Reconstructed from the real 15-minute journal (`previous_day.low=29677.5`
i.e. PDL, `orb.high=29675.0`, `orb.low=29607.0`, `previous_day.high=30076.75`)
and the real recorded 5-minute bars (`logs/tf5m/bars_MNQ_2026-07-13.jsonl`,
11:40-12:50 UTC):

- **Did the break below PDL/HVI-cluster qualify?** Yes. At 12:30 UTC the bar
  closed at 29673.0, below PDL (29677.5) for the first time that session.
- **Was there a valid retest?** Yes. At 12:35 UTC (close 29675.0, low
  29655.25) the detector fires `SHORT break_and_retest @ PDL`: entry 29675.00,
  stop 29679.50 (4.5pt risk), target orb_low 29607.00 (68pt reward, RR
  15.1x) — squarely in the >8 RR bucket that the backtest shows loses ~95%
  of the time.
- **What happened to it?** Stopped out one bar later. At 12:40 UTC the bar's
  high reached 29694.75, above the 29679.50 stop — a real, small loss,
  exactly matching the operator's own description ("the small pop... caught
  0 percent of the short"). Price fell again after 12:50 in the underlying
  data the operator was watching; this position would not have participated
  in that continuation since it was already stopped out.
- **Did the morning bounce produce a valid long candidate, or only a
  countertrend bounce?** The detector's own `reclaim` condition (prior close
  <= PDL, current close > PDL) technically fires at 12:40 UTC, but
  `trend_direction=DOWN`/`trend_strength=STRONG` at that time classifies the
  context as `opposed` for a long — correctly rejected as a countertrend
  bounce-chase, not a qualifying long, under this module's own context
  filter.
- **Precise reason nothing else qualified:** every other level/direction/
  setup_type combination in this window failed `NO_CONFIRMED_CLOSE` or
  `NO_RETEST` — no other level was tested closely enough to trigger.

This is a genuine, concrete illustration of the exact failure mode the
backtest identifies: the setup family fires on real, correctly-timed
structural events, but the tiny-stop/distant-single-level-target geometry it
produces is empirically a net-negative trade design, not merely bad luck on
one morning.

## Prior art (independently corroborating, not cited after the fact to
rationalize this result — found while cross-checking today's real journal)

The box already runs a related, separately-built `wall_context`/
`range_signal` module (deployed, observe-only — [[project_rangebound_status]])
that computes SUPPLY_ZONE/DEMAND_ZONE/PDH/PDL/ORB/HOD/LOD/PWH/PWL walls and a
rejection/breakout `range_signal` off them. That lane's own dedicated study
([[project_range_signal_finding]]) reached, independently, the same
conclusion for MNQ: **REJECT — no edge**, lane closed, HOLD/OBSERVATION ONLY.
Two independently-built studies of closely related structural-level-fade
concepts both land on "no real edge for MNQ."

## Classification

**REJECTED.** This is not a candidate awaiting activation — it is a closed
research finding. Fixed-target exit: BROKEN, robustly negative, both halves,
every RR bucket. Runner exit: technically clears "PROMISING BUT UNPROVEN,
survives both halves" at an optimistic 1-tick slippage assumption, but does
not survive a 2-tick stress test — per the operator's own explicit gate
("only proceed... if replay... survives both halves with realistic fills"),
this does not clear the bar. Confirmed by manual reconstruction against real
2026-07-13 box data: the visually-obvious short would not have been captured
by this rule set — it fires, but is stopped out one bar later by the
morning bounce (see Manual chart validation above); the market's later
continuation lower would not have been captured by this specific setup,
because the position was already closed. **No shadow/live integration was
built, and none should be, from this design.** No config, drift-guard,
execution tracker, or `webhook/runner.py` changes were made. The module
lives under `research/`, not `context/`, specifically so its location does
not imply runtime-readiness.

**Do not build another version of this same level-fade design** (enter
immediately on a mapped-level break/retest with a very tight structural stop
against a distant next-level target) — that is the specific idea this study
rejects, not just this exact parameterization of it. The next attempt at
scoring the same motivating gap (overnight move / bounce / rejection) should
use a different setup family — see
`docs/mnq-5m-impulse-pullback-continuation-study-2026-07-13.md` for the
follow-up study.

## What would need to change before revisiting this design specifically

Kept for completeness, not as a build recommendation -- the operator's
direction is a different setup family (impulse/pullback/continuation, see
above), not a retuned version of this one:

1. A target-selection rule that isn't "whichever of 4 discrete levels happens
   to be next" — e.g. cap reward at some multiple of the stop distance, or
   require a minimum level density, so the >8 RR / 4.6%-win-rate tail (which
   alone accounts for the entire net loss) can't form.
2. Slippage-robust margin — the runner variant needs a wider positive margin
   than $0.46/trade net to survive normal fill-model uncertainty; right now
   one tick erases it.
3. Given the wall_context module already computes richer, price-derived
   SUPPLY_ZONE/DEMAND_ZONE/PWH/PWL levels live (unlike the raw payload
   passthrough), and the existing range_signal study already closed out
   MNQ as REJECT using those same richer levels, a third attempt at this
   general concept for MNQ would need a genuinely different setup design,
   not just more mapped levels, to be worth the engineering cost.
