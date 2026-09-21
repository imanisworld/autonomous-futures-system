# Futures setup coverage / paper-equivalence plan — 2026-09-21

## Verdict

**AUDIT ONLY / HOLD UNTIL OPTIONS PASS COMPLETES / NO NEW RUNTIME AUTHORITY.**

Operator requirement: the current options setup-coverage + historical-fixture +
internal-paper/sandbox-mirror work is not options-only. After that pass is
complete, run the same evidence discipline across futures.

This document records the futures follow-on now so it is not lost or
reinterpreted later. It authorizes no strategy change, no deploy, no new
collector, no broker order, and no live execution.

Core rule remains:

> No proof, no run.

## Sequencing

Do not start a broad futures rebuild while the options pass is still open.

Futures follow-on begins only after the options work has reached this state:

1. non-Strat observer definitions are frozen and merged;
2. logged options fixtures are cross-checked without hindsight inference;
3. the internal options forward-paper path is proven;
4. Webull sandbox entry/close lifecycle is proven or remains explicitly
   blocked;
5. no unresolved options evidence defect could contaminate the methodology.

The futures pass may reuse the methodology, not blindly copy options code.

## Futures broker / paper equivalence

For futures, the external paper/demo equivalent is **Tradovate DEMO**, not
Webull.

Required direction:

```
futures signal / detector
→ internal PaperBroker or isolated paper ledger
→ optional Tradovate DEMO evidence mirror
→ reconciliation
```

Invalid direction:

```
Tradovate DEMO fill → strategy authority
Tradovate state → detector authority
paper/demo P&L → live approval
```

Live futures remains disabled.

## Scope

Start with the existing futures universe and evidence source of truth:

- MNQ first;
- MES second where the strategy already supports it;
- MGC / MCL only after MNQ/MES methodology is proven;
- do not expand instrument coverage merely to increase sample size.

Source-of-truth inputs:

- `docs/strategy-rules/Strategy_Inventory.md`;
- canonical strategy-rule docs;
- detector implementations;
- replay artifacts;
- internal paper/shadow ledgers;
- futures journal rows;
- existing manually documented examples / fixture evidence where real
  provenance exists.

## What "same thing" means for futures

The pass has four separate questions. They must not be collapsed.

### 1. Coverage / setup detection

For every mechanically specified futures setup family, answer:

- can the detector reproduce the setup causally from bars available at that
  time?
- are live and replay formulas identical?
- does an existing logged/manual example match the detector without inferring
  the setup backward from the outcome?
- which setups are not testable because the original plan, timing, level or
  rule is missing?

A logged trade is not automatically a fixture.

If the old record does not prove the intended setup, mark it
`UNTESTABLE` / `DATA_BLOCKED`.

### 2. Historical evidence

For families with reproducible rules, use the existing standardized futures
evidence contract:

- honest IOC / fill model;
- same trigger timing as runtime;
- stop-before-target on ambiguous same-bar path;
- commissions and slippage;
- walk-forward halves;
- session segmentation;
- drawdown;
- concentration;
- minimum sample;
- no lookahead;
- no outcome-selected population.

Do not rerun already-closed negative studies merely to search for a better
answer. Reuse canonical evidence where the population and formulas are still
valid.

### 3. Internal paper / shadow

A setup may enter forward paper only when:

- the setup identity is frozen;
- detector timing is causal;
- stop is explicit;
- target / exit rule is explicit;
- position size is capped;
- max trades/day is enforced;
- session/news filters are defined;
- the exact internal paper lane cannot reach live execution;
- journal records setup, signal state, entry, stop, target, fill assumptions,
  exit and outcome;
- duplicate/re-entry behavior is defined.

The system is allowed to miss trades.

It is not allowed to create a paper fill from an underspecified setup.

### 4. Tradovate DEMO mirror

Only internally approved paper candidates may be considered for DEMO mirroring.

The DEMO route must prove:

- `TRADOVATE_ENV=demo`;
- live trading disabled;
- exact ticker routing;
- 1-contract hard cap unless a strategy-specific paper contract explicitly
  says otherwise;
- no market/live fallback;
- entry order identity;
- protective exit lifecycle;
- cancel / reject / unknown-state handling;
- duplicate suppression;
- reconciliation against the internal paper record.

Internal paper remains the evidence of record.

## Existing futures work must be reused

This is not permission to rebuild lanes that already exist.

Known examples from the current inventory:

- 4HR broad control already has paper evidence and the only currently guarded
  Tradovate DEMO evidence route;
- 4HR 4H 2→2 continuation is observation metadata only;
- 3-2-2 First Live has a deployed observation-only 1m timing collector;
- wide-stop 4HR / 3-2-2 / Miyagi isolated paper ledgers already exist and have
  their own proof-critical epoch;
- Daily 2-2 completed-close paper identity is separate from the broken
  true-touch variant;
- Transition failed-breakdown remains passive collection / WAIT after failing
  required slippage robustness;
- inverse ORB is BROKEN and must not be revived from the retired detached-fill
  baseline;
- VWAP Hold / ORB close-confirmed families have negative honest-fill evidence;
- PDL Reclaim is undersampled research only;
- PDH Reclaim is retired.

The futures pass should identify **gaps**, not reset these verdicts.

## Required inventory output

Before any new futures test or lane is built, create one machine-readable
matrix with one row per strategy/setup identity.

Required fields:

```
strategy_id
instrument
timeframe
rule_source
detector_path
rules_complete
detector_built
manual_or_logged_fixture_status
fixture_source
replay_parity
trigger_timing_status
fill_model
walk_forward_status
slippage_status
sample_size
drawdown_status
current_verdict
internal_paper_status
tradovate_demo_status
live_status
next_missing_proof
do_not_retest_reason
```

This matrix is the futures equivalent of the options fixture/paper audit.

It must distinguish:

- proven missing evidence;
- untestable historical recall;
- existing positive evidence;
- existing negative evidence;
- forward evidence already collecting.

## Logged futures trades / setups

Where the system already has logged futures trades or operator-identified
setups, test them only when the original record establishes enough pre-trade
identity to do so.

For each usable logged case:

1. freeze the intended strategy family before opening outcome details;
2. freeze the relevant date/time/instrument;
3. regenerate detector state from causal market data;
4. compare signal identity, direction, planned entry, stop and target;
5. record match / mismatch / missing-data reason;
6. do not rewrite the strategy definition to rescue a mismatch.

If the setup cannot be identified from the original record, the correct result
is `UNTESTABLE`, not a guessed family.

## Order of work after options completes

1. Build the futures strategy/setup evidence matrix from current repo truth.
2. Identify every existing logged/manual futures fixture with real provenance.
3. Cross-check those fixtures against the canonical detectors.
4. Mark already-complete evidence cells as reused; do not duplicate them.
5. Run only missing historical tests for reproducible setups.
6. Route only qualifying setups to isolated internal paper/shadow collection.
7. Use existing Tradovate DEMO lanes where already proven.
8. Build a new DEMO mirror only if a qualifying setup lacks one and the need is
   proven.
9. Reconcile internal paper vs DEMO.
10. Review at pre-registered sample/calendar checkpoints.

## Hard blocks

Stop the futures pass if any of these occur:

- live/replay formula divergence;
- missing original setup identity;
- missing stop;
- missing exit rule;
- ambiguous instrument routing;
- paper/live mode ambiguity;
- strategy definition changes after outcomes are viewed;
- optimistic same-bar target-priority fill;
- unbounded duplicate/re-entry behavior;
- historical evidence cannot be reproduced;
- a BROKEN/RETIRED lane is being revived without a new pre-registered
  population.

## No live promotion from this pass

The output of this work can be:

- `VALIDATED`;
- `PAPER PROOF`;
- `PROMISING BUT UNPROVEN`;
- `WAIT`;
- `RESEARCH ONLY`;
- `BROKEN`;
- `RETIRE`.

It cannot itself authorize live trading.

Any future live decision remains a separate operator decision after sufficient
forward paper/DEMO evidence and a fresh execution-safety audit.

## Safe next step

Finish the options pass first.

Then create the futures evidence matrix before touching any new futures
strategy, replay, paper lane or broker path.
