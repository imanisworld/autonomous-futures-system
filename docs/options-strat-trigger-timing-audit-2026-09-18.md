# Options Strat trigger-time audit — 2026-09-18

## Ruling

The preserved 30m coverage observer is valid for measuring what V1 could see under its
completed-bar + SIP-delay architecture, but it is **not a faithful strategy-entry clock**
for Strat setup qualification.

For Strat strategy evidence, setup formation and trigger detection must be separated:
a completed precursor freezes the trigger boundaries, then the first causal break of the
relevant boundary is the event time. Waiting for the breakout 30m candle to close changes
the entry population and can make otherwise valid signals appear late.

This work is evidence-only. No runtime, deployment, alerting, broker, DEMO, or live-trading
path is changed.

## External Strat rule check

Sources reviewed:

- https://thestrat.ai/docs/types-of-reversals/
- https://thestrat.ai/docs/3-1-2/
- https://thestrat.ai/docs/3-2/
- https://thestrat.ai/docs/2-2-reversal/
- https://thestrat.ai/docs/2-2-2-continuation/

The common execution rule is that the break is the confirmation. For 2-1-2 and 3-1-2,
the inside bar must already be closed, but the following breakout candle does not need to
close before the trigger exists. The 2-2 reversal family likewise triggers through the
prior 2's reversal-side extreme.

The reviewed 3-2 material goes further: 3-2 is itself actionable when price takes the
outside bar's high or low; it is not merely a developing state waiting for 3-2-2.

The reviewed 2-2-2 continuation material is materially different from how our coverage
table names it: a same-direction 2-2-2 is primarily trend/run context, and the preferred
trade entry is a lower-timeframe reversal that puts the next higher-timeframe 2 in force,
not buying the third higher-timeframe 2 after it closes.

## What the current options evidence lane does

`alert_ranker.coverage_observer`:

1. classifies the family only after the breakout 30m bar is complete;
2. computes alignment as of that breakout bar's close;
3. defines first sight as breakout-bar close + 960 seconds, rounded to the 5-minute
   scanner grid;
4. uses 5m data only afterward to price first sight / forward outcomes.

The existing outcome study already has a separate `mechanical` view that locates the
first 5m bar inside the breakout 30m candle that crossed the trigger. That is useful
evidence that the timing matters, but the current study deliberately does **not** re-family,
re-target, or re-align at that mechanical trigger. It therefore cannot by itself prove a
fully causal trigger-time strategy result.

## Family-by-family adjustment audit

| Family | Current treatment | Trigger-time treatment / issue |
|---|---|---|
| 2-1-2 continuation | classify after third 30m bar closes | arm on completed 1; trigger when its same-direction side breaks |
| 2-1-2 reversal | classify after third 30m bar closes | arm on completed 1; opposite-side break immediately identifies reversal |
| 3-1-2 | one combined `STRAT_312` family after close | arm on completed 1; trigger on its break; continuation vs reversal is determined by the 2 relative to the 3's color |
| 2-2 reversal / shotgun family | final directional bar classified after close | arm the prior 2; reversal-side extreme is the trigger |
| 1-2-2 | final reversal 2 classified after close | arm the first 2 after the 1; opposite break triggers; same-direction first break precludes a clean 1-2-2 reversal |
| 3-2-2 reversal | final reversal 2 classified after close | same 2-2 reversal timing, with the outside bar supplying larger context/magnitude |
| 3-2-2 continuation | currently studied as a separate continuation family | needs separate source/rule proof before treating it as an approved standalone setup |
| 3-2 | treated as developing/WATCH in the daily options detector | reviewed Strat source says 3-2 itself is actionable on the outside-bar boundary break |
| 2-2-2 continuation | treated as a structural family | reviewed Strat source treats this mainly as trend/run context; entry should come from a lower-timeframe signal, not the third bar's close |
| reclaim / PDH-PDL / VWAP / break-retest | not implemented in cov-v0.1 | when added, use an armed-level state machine and explicit event/confirmation time; never infer an entry from a later 30m close |

## Additional causal corrections implied by trigger-time entries

### 1. Market alignment must be sampled at trigger time

The old coverage observer measures SPY/QQQ + hourly/daily alignment at the **30m breakout
bar close**. If the actual trigger happened earlier inside that bar, that alignment can
contain information that was not available at entry.

A trigger-time backtest must freeze market context no later than the trigger timestamp.

### 2. Option selection evidence must move to the trigger timestamp

The decision-time option chain, underlying price, delta/open interest, spread, and
executable bid/ask evidence must correspond to the trigger event, not the delayed
first-sight timestamp.

This means the historical 212R acquisition timestamps should not be expanded into a
large paid data pull until the trigger-time clock is frozen. The prior first-sight sample
remains valid evidence about the old scanner, but it is not the final strategy-entry clock.

### 3. Do not hindsight-delete a valid break because the 30m bar later becomes a 3

A bar can first break one side (a live 2 trigger) and later take the opposite side,
finishing as a 3. Trigger-time evidence must preserve the first break and separately
record the later failure/outside transition. If both sides are first observed inside the
same lower-timeframe bar and ordering cannot be proven, mark it ambiguous.

### 4. Family-specific stop/target rules need a separate audit

The trigger timing correction does not automatically validate the generic target geometry
or one universal invalidation formula. In particular, the reviewed Strat material gives
2-1-2 / 3-1-2 the other side of the inside bar as the stop reference, while 2-2-family
reversals use different stop language. Do not silently reuse one formula across every
family.

## Implementation started

New pure module: `alert_ranker/trigger_time.py`

Identity: `OPTIONS_STRAT_TRIGGER_TIMING / trigger-v0.1`

It currently:

- arms 2-1-2, 3-1-2, 2-2-2 precursor, 3-2-2 precursor, 1-2-2 precursor, and 3-2;
- fails closed on same-direction 2-2-2 as run context rather than a direct entry, and on same-direction 3-2-2 continuation until separate setup proof exists;
- freezes the completed precursor's high/low before the watched 30m bar begins;
- resolves the first high/low break from caller-supplied lower-timeframe bars;
- refuses to invent ordering when both sides are crossed in one lower-timeframe bar;
- preserves a first trigger even if the 30m bar later becomes outside;
- cancels 1-2-2 reversal when the same-direction side breaks first;
- performs no I/O and has no runtime side effects.

## Next proof step

Build a trigger-time comparison on the already-frozen underlying corpus:

1. reconstruct each armed setup from completed 30m bars;
2. resolve first break from 5m bars;
3. compare trigger-time family/direction with the old close-classified family;
4. rebuild SPY/QQQ + hourly/daily context **as of trigger time**;
5. quantify family changes, latency, later-3 failures, ambiguous first-break bars, and
   mechanical-vs-delayed outcome deltas;
6. only then freeze the new decision timestamps for option-side quote acquisition.

Until that is done, no current family should be promoted merely because trigger-time
handling appears better. The result can improve or worsen once all gates are re-timed
causally.
