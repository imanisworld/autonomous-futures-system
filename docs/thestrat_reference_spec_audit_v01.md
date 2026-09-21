# TheStrat reference-spec audit v0.1

**Status: RESEARCH / AUDIT ONLY. No execution authorization.**

Purpose: freeze the public TheStrat documentation as an external reference before changing AFS Strat logic or interpreting more historical Strat tests.

Sources reviewed on 2026-09-21:
- https://thestrat.ai/docs/the-3-universal-truths-that-govern-price/
- https://thestrat.ai/docs/inside-bar/
- https://thestrat.ai/docs/full-timeframe-continuity/
- https://thestrat.ai/docs/actionable-signals/
- https://thestrat.ai/docs/stop-losses/
- TheStrat docs index for the remaining named setup/context pages.

The docs are a methodology specification source, **not evidence of profitability**.

## Frozen reference rules

### Scenario identity
A bar is classified only by breaks of the prior bar:
- 1: takes neither prior high nor prior low.
- 2u: takes prior high only.
- 2d: takes prior low only.
- 3: takes both.
Equality does not count as a break. Once a live bar has taken both sides it is a 3.

### 2-1-2
The inside bar is equilibrium, not the signal. The signal is the break of the inside bar.
- Reversal: 2d-1-2u or 2u-1-2d.
- Reversal trigger: inside-bar extreme on the reversal side.
- Reversal magnitude: extreme of the first directional 2.
- Continuation: 2u-1-2u or 2d-1-2d.
- Continuation trigger: inside-bar extreme on the trend side.
- Continuation target: measured move based on the leg before the pause.

A generic fixed 2R target is therefore **not** a literal reference implementation of documented 2-1-2 target logic.

### FTFC
Standard FTFC is a state defined by the last sale relative to four opens:
- UP: last sale above monthly + weekly + daily + current 60-minute opens.
- DOWN: last sale below all four.
- anything else: conflict.

The docs also define horizon-specific 4-of-4 sets, including intraday 60m/30m/15m/5m-or-lower. FTFC is not an EMA-stack classification.

### Signal / magnitude
The docs define actionable signals through a structural trigger and structural magnitude. Reversal objectives are generally previous-range/pivot structure; continuation can use measured move logic. Magnitude fulfillment changes the state/exhaustion question.

### Stops / trade management
The docs do **not** specify one universal fixed stop.
Three placements are documented, selected by evidence:
- tightest: spread + 0.01 (equity-specific expression);
- tight: open of the entry candle;
- wide: reversal against on the entry timeframe.

Winner defense then depends on continuity/signals/exhaustion. This means an AFS test that forces one broad structural stop or a generic fixed-R stop is a test of that imposed geometry, not necessarily of the complete documented method.

For futures replication, equity-specific spread+$0.01 cannot be silently translated into ticks. It must be marked UNRESOLVED or separately preregistered.

## AFS comparison — verified repo facts

### MATCH
- `strategy/strat_classifier.py::classify_bar` uses strict prior-high/prior-low breaks, correctly distinguishing 1 / 2u / 2d / 3.
- It explicitly distinguishes 2-1-2 reversal from continuation.
- Direction on a resolved directional bar is derived from 2u vs 2d.

### PARTIAL / DIFFERENT
- `strategy/strat_classifier.py` is primarily a sequence classifier. It does not encode documented structural magnitude/target semantics.
- `alert_ranker/strat.py` maps sequence identity/bias for the options advisory scanner, but the named pattern result does not itself establish documented FTFC + magnitude + stop evidence.
- AFS `context/trend.py` defines trend via EMA9/EMA21/EMA55 stack ordering. That is a valid AFS trend model, but it is **not TheStrat FTFC**, whose documented definition is last sale relative to aligned timeframe opens.
- Existing fixed-R / broad-stop historical cells should therefore be described as **AFS Strat-inspired geometry tests** unless the individual study separately proves literal trigger, magnitude, FTFC, and stop semantics.

### NEEDS FURTHER REPO AUDIT
Before a literal reference runner is implemented, locate every futures Strat consumer and historical runner and map:
- trigger timestamp;
- whether live bars can mutate 1 -> 2 -> 3 causally;
- target/magnitude source;
- stop source;
- timeframe-continuity source;
- reversal vs continuation handling;
- first-live vs close-confirmed semantics.

Do not infer compliance from sequence names alone.

## Research decision

Do **not** modify scanner/execution logic from this document.

Next experiment should be a separate `strat_reference_v0.1` research lane, compared side-by-side with the current AFS interpretation on identical MNQ/MES sessions.

Initial reference candidate: **2-1-2 reversal**, because its public documentation gives an objective sequence, trigger, direction and magnitude. Any stop translation that is not directly specified for futures must be preregistered as an explicit experimental overlay rather than attributed to TheStrat.

Required comparison outputs:
1. identical candidate population before filters;
2. documented trigger timing;
3. documented structural magnitude hit rate/time-to-magnitude;
4. MAE/MFE before magnitude;
5. FTFC state at trigger, reported separately first;
6. current-AFS outcome on the same event IDs;
7. realistic fill/cost cells;
8. H1/H2 and instrument split;
9. no promotion from this experiment alone.

## Guardrail

The existing futures audit says completed strategy work should not be blindly redone and that changing timeframe is a new variant. This reference study therefore does not overwrite historical 4HR/3-2-2/Miyagi/Transition conclusions. It answers a narrower question: **were prior AFS Strat tests faithful to the now-frozen public methodology, and does a literal reference implementation produce materially different evidence?**
