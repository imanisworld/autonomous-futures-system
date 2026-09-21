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


## Detailed repo audit — pass 2

### Futures executable 2-1-2
`strategy/strat_212_122.py` is not a literal implementation of the full documented 2-1-2 family.

Verified behavior:
- It arms only when a completed inside bar follows a directional 2.
- For `strat_212`, direction is forced to match that first directional 2. Therefore this executable path is **continuation-only**.
- It watches exactly the next bar.
- Trigger is one tick beyond the inside-bar boundary.
- Stop is the opposite side of the inside bar.
- Target is hard-coded at 2R.
- Gap-aware causal fill logic and pessimistic same-bar stop-first handling are explicit and conservative.

Reference difference:
- documented 2-1-2 includes both continuation and reversal branches;
- documented reversal magnitude is the far extreme of the original directional 2;
- documented continuation objective is structural/measured-move, not fixed 2R;
- the current executable path has no documented FTFC state.

Classification: **PARTIAL / DIFFERENT**. Sequence timing is causal and useful; geometry and context are VP-specific.

### Options 2-1-2
`options_manager/strategies/strat_212.py` is also continuation-only. It delegates sequence identity to the shared classifier, but entry/invalidation/targets are caller-supplied or derived from the generic level finder. This is advisory-only and should not be described as a literal TheStrat 2-1-2 implementation without separately proving the structural target/context inputs.

Classification: **PARTIAL / DIFFERENT**.

### Existing trigger-time research
`alert_ranker/trigger_time.py` is much closer to the public methodology than the executable futures 2-1-2 path:
- precursor boundaries are frozen before the watched bar;
- the first lower-timeframe strict break is resolved causally;
- 2-1-2 continuation and reversal are distinguished by break direction relative to the parent 2;
- ambiguous same-lower-bar two-sided breaks fail to `AMBIGUOUS`.

`alert_ranker/trigger_geometry.py` already contains a source-oriented geometry layer:
- 2-1-2 reversal target = parent 2 far extreme;
- 2-1-2 continuation target deliberately unresolved;
- 3-1-2 reversal target = parent 3 far extreme;
- 3-2 direct setup = no own magnitude / HTF target required;
- unresolved stop rules are left unresolved instead of silently inheriting generic geometry.

This is important: the repo already contains building blocks for a literal reference study. We should reuse these pure research primitives where appropriate instead of creating a second competing definition.

### 60M 3-2-2 First Live
`strategy/strat_322_first_live.py` is a separate operator-specific 60M setup with exact clock windows:
- MNQ only;
- completed 7AM/8AM/9AM 60m bars;
- 8AM must be outside relative to 7AM;
- 9AM must be directional relative to 8AM;
- entry watches 10:00-11:00 ET at 5m granularity for the opposite 9AM boundary;
- stop = opposite side of 9AM bar;
- target = corresponding 8AM outside-bar extreme.

This path is causal at the implemented 5m resolution, but it is an operator-specific timed 3-2-2 variant. It should not be conflated with every generic public 3-2-2 setup.

Classification: **SEPARATE VARIANT / DO NOT REWRITE FROM THIS AUDIT**.

### Trend / FTFC
`context/trend.py` is explicitly an EMA-stack trend classifier shared by live and replay. That parity is good, but it is not TheStrat FTFC.

Therefore:
- do not rename or reinterpret the EMA-stack classifier as FTFC;
- a reference study must compute FTFC separately from aligned timeframe opens;
- first pass should report FTFC as a stratification variable rather than use it as an exclusion filter, so we can see whether it adds information without selection bias.

### Existing completed-audit boundary
This audit does not invalidate the existing 4HR, timed 3-2-2, Miyagi, Transition, ORB, or inverse-ORB work. Those are separate strategies/variants. The narrower correction is terminology and experimental scope: prior fixed-R Strat sequence tests are evidence about those **AFS implementations**, not automatically evidence about a literal public-methodology replication.

## Decision after pass 2

A new reference runner is justified, but it should be built from the existing causal observer primitives rather than modifying executable strategy modules.

First reference population: **2-1-2 reversal only**.

Reason:
1. objective sequence identity;
2. causal inside-bar trigger;
3. objective structural magnitude from the parent directional 2;
4. both LONG and SHORT mirrors;
5. no need to invent a continuation target before its rule is fully frozen.

The study must remain research-only and cannot promote a strategy directly.


## Reference-runner static audit — pass 3

Before any historical run, the first structural runner was audited against the frozen preregistration. Three outcome-sensitive defects were found and corrected while the study still has no historical results:

1. **Watch-window drift:** the first draft could keep a 2-1-2 precursor armed through all remaining 5-minute bars in the RTH session. A 2-1-2 requires the immediately following source bar. The runner now bounds trigger evidence to exactly the next 60-minute source-bar window and fails closed when that 5-minute window is incomplete.
2. **Post-trigger structural failure:** the first draft could continue crediting a later parent-magnitude hit after price had already broken the opposite inside-bar boundary. The runner now stops at the first post-trigger structural terminal event. If structural failure occurs before magnitude, magnitude is not credited. If failure and magnitude are first observed in the same 5-minute bar and order is unknowable, the resolution is marked ambiguous and magnitude is not credited.
3. **Excursion contamination:** the first draft measured MAE/MFE through every remaining watched bar even after magnitude was already reached. The runner now measures excursion only from trigger through the first terminal structural event, preventing post-resolution price action from changing the result.

A pure FTFC classifier has also been added for the documented UP/DOWN/CONFLICT rule, with missing required opens returning `UNAVAILABLE`. This does **not** prove the local replay corpus can reconstruct monthly/weekly/daily/current-60m opens; data plumbing remains unverified without the local corpus.

### Source-bar alignment boundary

The research runner currently constructs 60-minute bars as RTH session-aligned buckets anchored at 09:30 ET. The public reference does not establish that as the uniquely canonical futures alignment, while the existing timed 3-2-2 implementation uses separate clock-specific 7AM/8AM/9AM bars. The preregistration therefore now labels 09:30 RTH alignment as an explicit AFS research translation rather than public doctrine.

No historical result should be generalized to every 60-minute futures implementation without preserving that alignment label. Clock-aligned, ETH, 4HR, or Daily variants remain separate experiments.

### Current completion state

The structural code can be reviewed and unit-tested without local historical data. The study itself remains incomplete until:
- FTFC data capability is proven on the actual replay corpus;
- AFS EMA trend is recorded side-by-side on the same events;
- preregistered execution overlays are implemented only after the frozen structural population exists;
- the historical MNQ/MES run and output audit are completed.

No production strategy, risk, execution, broker, scanner, scheduler, or alert path is modified by this lane.
