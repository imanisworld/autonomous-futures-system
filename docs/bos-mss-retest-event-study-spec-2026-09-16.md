# BOS/MSS → first-retest event study specification (2026-09-16)

## Verdict

**RESEARCH ONLY / EVENT STUDY ONLY.** This is not a strategy, not a promotion study, and not runtime wiring.

The purpose is to answer one narrow question raised by the 2026-09-16 missed-opportunity audit:

> Does the repository's own causal swing-structure signal (BOS/MSS), and the first retest of the broken swing, show directional follow-through that could justify a later strategy-design pass?

No trade bracket, P&L, risk rule, broker route, Pine change, environment change, deployment, alert route, or execution eligibility is created here.

## Why this is not duplicate work

The existing July structural study (`research/mnq_structural_level_5m.py`, PR #271) tested **mapped-level** behavior: PDH/PDL/ORB and other carried levels, with reclaim/rejection/break-and-retest logic. It was rejected after fixed-target results were robustly negative and the runner variant failed a two-tick slippage stress test.

`context/range_signal.py` also studies **wall/range** breaks and retests. It does not use swing-sequence state as the event origin.

The existing RiskSentinel BOS/MSS logic is different:

- confirm a swing pivot only after the configured right-side bars have closed;
- remember the last confirmed swing high and low;
- a close through the prior swing in the **same direction** as the previous break is BOS;
- a close through the prior swing in the **opposite direction** is MSS.

Repository search found BOS/MSS fields used as context/gates, especially in VWAP logic, but no dedicated BOS/MSS outcome study and no standalone first-retest study sourced from those swing breaks.

## CHoCH boundary

This study does **not** claim to implement CHoCH.

The repository has no canonical CHoCH definition, and the chart labels that motivated this audit came from an external indicator whose exact proprietary logic is not the system's source of truth. MSS is the repository's existing transparent structure-shift label. A later CHoCH study would require a separately specified, non-lookahead definition and a proof that it is materially distinct from MSS.

## Instrument order

### Phase 1 — MNQ

MNQ only. It has the deepest relevant 5-minute replay corpus and the prior structural studies, making it the correct instrument for proving whether this event family is genuinely new and mechanically sound.

### Phase 2 — MES portability

MES is permitted by the research runner but must not be interpreted until MNQ Phase 1 is mechanically valid and not obviously negative/useless. MES is a portability check, not a pooled sample.

### Out of scope

M2K, MGC, MCL, MBT and all options instruments. No expansion until MNQ/MES evidence warrants it.

## Pre-registered event definition

Source bars: complete 5-minute bars aggregated into exact 15-minute triples. Incomplete 15-minute buckets are skipped; they are never padded or inferred. Duplicate 5-minute timestamps fail closed.

Swing length: **7**, matching the current `RiskSentinel - Full Context` default (`i_bos_swing = 7`). This pass does not tune the swing length.

Pivot confirmation is causal: a candidate pivot is not recognized until all seven right-side 15-minute bars have closed. Equal-extreme ties are ignored rather than guessing TradingView tie behavior.

Break state:

- first confirmed directional break establishes state but is not scored;
- same-direction next break = **BOS**;
- opposite-direction next break = **MSS**;
- break requires a confirmed 15-minute close through the last confirmed swing and the prior 15-minute close at/on the other side of the level.

Event availability is the **15-minute bar close**, never the bar open/start timestamp.

## First retest definition

Starting only after the BOS/MSS event is known, examine subsequent 5-minute bars for up to 120 minutes.

The **first** bar whose range touches the broken swing level decides the retest classification:

- LONG event: touch plus 5-minute close above the broken level = `RETEST_HOLD`;
- SHORT event: touch plus 5-minute close below the broken level = `RETEST_HOLD`;
- first touch that closes on the wrong side = `RETEST_FAIL`;
- no touch inside 120 minutes = `NO_RETEST`.

A later successful retest cannot replace an earlier failed first touch. This prevents hindsight substitution.

## Measurements

This pass measures price behavior only at fixed horizons of **15, 30, 60, and 120 minutes** after:

1. the BOS/MSS event close; and
2. the first `RETEST_HOLD` close.

For each horizon:

- signed close movement in the event direction;
- maximum favorable excursion (MFE), points;
- maximum adverse excursion (MAE), points;
- percent with positive directional close;
- percent where MFE > MAE;
- mean and median signed close move;
- median MFE / MAE.

Results are split at minimum by BOS vs MSS and LONG vs SHORT counts. No P&L is computed because there is intentionally no entry/stop/target strategy yet.

## Reproducibility

The runner:

- reads JSONL only;
- records SHA-256 for every input file used;
- writes deterministic event JSONL plus summary and manifest;
- rejects malformed/non-object rows;
- records the number of complete 15-minute bars and incomplete buckets skipped;
- labels MNQ as Phase 1 and MES as Phase 2.

## Safety boundary

`research/bos_mss_retest_event_study.py` imports no execution, risk, webhook, broker, HTTP, or Tradovate surface. `scripts/bos_mss_retest_event_study.py` performs local file I/O only.

No existing runtime file is modified by this research branch.

## Decision gate after MNQ Phase 1

Do **not** build a trade strategy merely because events exist or because one subgroup looks attractive.

The next decision is only whether the event family merits bracket research. At minimum the event/retest behavior must be reproducible, causal, non-trivial in sample size, and show consistent directional information rather than a one-day or one-tail artifact. If it does not, classify the family **BROKEN / WAIT** and stop. If it does, a separate pre-registered bracket/fill study is required before any shadow or paper execution work.
