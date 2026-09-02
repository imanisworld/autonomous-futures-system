# TheStrat Semantic-Fidelity Audit — 2026-09-02

## Verdict

**HOLD canonical conclusions; preserve implementation evidence.**

The repo has useful evidence for the exact Strat variants it implemented, but that evidence must not automatically be read as a verdict on canonical TheStrat concepts when the repo adds its own target, entry timing, context, or execution conventions.

This document is a classification/scope correction only. It changes no strategy, risk, broker, fill, config, or deployment behavior.

## Comparison basis

This audit compares the current repo to the operator-supplied TheStrat reference material used in the 2026-09-01 review. That material defines the candle scenarios and combo identities below, plus a workflow of identifying the exact combo, checking timeframe continuity/context, defining magnitude from actionable structure, waiting for the trigger, and defining risk/invalidation.

This is not a claim that one screenshot or guide is a complete universal machine specification for TheStrat. Where the reference material does not provide a deterministic executable rule, this audit leaves the item unproven rather than inventing one.

## Scenario identity

Repo bar classification matches the supplied scenario vocabulary:

- `1` = inside bar
- `2U` = took the prior high only
- `2D` = took the prior low only
- `3` = took both prior high and low

## Combo fidelity matrix

| TheStrat concept | Supplied sequence | Current repo identity | Fidelity / evidence scope |
|---|---|---|---|
| 2-2 reversal | `2D -> 2U` bullish / `2U -> 2D` bearish | `strat_22_reversal` | **Sequence match.** Any performance verdict still applies only to the repo's executable/shadow entry, target, fill, and context rules. |
| 2-2 continuation | `2U -> 2U` / `2D -> 2D` | `strat_22_continuation` | **Sequence match.** Same implementation-scope rule. |
| 2-1-2 continuation | `2U -> 1 -> 2U` / `2D -> 1 -> 2D` | `strat_212` | **Sequence match.** The causal executable implementation in `strategy/strat_212_122.py` uses a repo-defined fixed 2R target. Its evidence is evidence for that implementation, not a universal target rule. |
| 2-1-2 reversal | `2D -> 1 -> 2U` / `2U -> 1 -> 2D` | `strat_212_reversal` | **Identity fixed in PR #423.** Before #423 these rows fell through to `strat_inside_break`; therefore older evidence under the generic label is not a clean canonical 2-1-2-reversal study. The new identity is classified and fails closed; it is not yet a separately proven executable strategy. |
| 1-2-2 reversal | `1 -> 2D -> 2U` / `1 -> 2U -> 2D` | `strat_122` | **Sequence match.** The causal executable implementation uses the same explicit VP fixed-2R convention; current evidence is implementation-specific. |
| 3-1-2 | `3 -> 1 -> 2U/2D` | `strat_312` | **Sequence match.** Generic shadow/evidence variants use repo-defined bracket rules; do not universalize their result. |
| 3-2-2 reversal | `3 -> 2D -> 2U` / `3 -> 2U -> 2D` | `strat_322_reversal` plus a separate specialized `strat_322_first_live` strategy | **Sequence match for the generic family, but the 60M First Live strategy is specialized.** Its 7AM/8AM/9AM/10-11AM mechanics and system-risk verdict must not be presented as a verdict on generic 3-2-2. |
| 3-2 | `3 -> 2U/2D` | `strat_outside_continuation`; evidence lane `strat_32` | **Concept match, custom repo name.** Evidence remains scoped to repo execution conventions. |

## Target / magnitude scope

`strategy/strat_212_122.py` explicitly documents its target as a **fixed 2R VP implementation convention, not canonical Strat doctrine**. That distinction is binding for evidence interpretation.

The supplied reference material describes magnitude in terms of an actionable prior high/low or structural level, but it does not provide one universal deterministic target formula for every combo. Other repo Strat observers also use implementation-defined fixed-R brackets.

Therefore:

- a negative result can prove **the tested repo target/execution variant** is broken;
- it does **not** by itself prove the underlying candle sequence has no edge under a different, correctly specified magnitude rule;
- no canonical target rule should be added until an objective source/spec is locked and replay/live parity can be tested.

## FTFC / timeframe context

The repo can represent higher-timeframe alignment and can enforce strict directional alignment in selected deployment postures, but historical/research evidence has not uniformly required it.

The supplied material says to check Full Timeframe Continuity / relevant timeframe alignment and indicates that additional aligned timeframes strengthen the setup. It does not, by itself, establish that FTFC must be a universal hard gate for every combo.

Classification: **context available, universal hard-gate requirement NOT PROVEN.** Do not retroactively label older samples invalid solely because FTFC was not always mandatory, and do not add a new hard gate without evidence.

## Entry / trigger scope

Canonical directional scenario confirmation occurs when price takes the relevant prior high (`2U`) or prior low (`2D`). Some repo evidence paths evaluate a candidate after the signal bar has already been classified and then resolve a repo-defined bracket against subsequent bars. Paper/evidence paths may also use close/market-style entry conventions depending on the lane.

Accordingly, those results are valid for the **measured repo entry model**, but they are not automatically a clean test of a literal first-touch canonical trigger.

## Evidence reinterpretation rule

Use this wording going forward:

> **Current repo implementation = [VERDICT]. Canonical TheStrat concept = [PROVEN / NOT YET PROVEN separately].**

Do not use a repo implementation verdict as a universal statement about canonical TheStrat when any of these differ:

- combo identity
- magnitude/target construction
- entry timing
- timeframe-context requirements
- stop/invalidation rule
- fill model
- session filters

## Existing evidence remains valid

Do **not** discard or rewrite historical results. They still answer the question actually tested: how the repo's then-current implementation performed.

Specific scope corrections:

- `strat_212` evidence covers **2-1-2 continuation**, not 2-1-2 reversal.
- pre-PR-#423 generic `strat_inside_break` evidence may contain rows that would now classify as `strat_212_reversal`; do not treat that population as a clean standalone canonical reversal study.
- `strat_122` evidence covers the repo's causal 1-2-2 implementation with its explicit 2R convention.
- generic `strat_322_reversal` and specialized `60M 3-2-2 First Live` are separate evidence families.
- any BROKEN/WAIT/PROMISING verdict remains binding for the implementation/evidence family that produced it unless a new, preregistered variant is separately tested.

## Current safe posture

1. Preserve all existing evidence and verdicts at their implementation scope.
2. Do not enable or create a new `strat_212_reversal` execution path merely because classification is now correct.
3. Do not replace fixed-2R targets with guessed structural targets.
4. Do not force FTFC into every Strat setup without a locked rule and evidence.
5. If a canonical variant is pursued, write the exact trigger/entry/stop/magnitude/timeframe-context spec first, then use identical replay/live logic and realistic fills.

## Classification

**Current repo Strat evidence: VALID FOR THE IMPLEMENTATIONS TESTED.**

**Canonical TheStrat family as a whole: NOT YET PROVEN by this repo.**
