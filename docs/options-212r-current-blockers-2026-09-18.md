# 212R options — current blocker sheet — 2026-09-18

## Purpose

This is the current authority for the 30m `STRAT_212_REVERSAL` research lane after the trigger-time and family-geometry corrections. It exists to stop old gate packets and stale handoffs from sending work back toward already-built infrastructure.

It does **not** authorize DEMO activation, broker submission, or live trading.

## Evidence identity

Frozen underlying observer:
- version: `cov-v0.1`
- SHA-256: `edba1ab55e859357f38db843c48b9942e685c1b148db82e2cefafaca632df286`

Causal trigger-bar refetch:
- snapshot: `OPTIONS_TRIGGER_BAR_SNAPSHOT / trigger-bars-v0.1`
- primary-20, 2026-09-09 through 2026-09-15
- Alpaca consolidated SIP 30m + 5m
- manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`

## PROVEN / DONE

### 1. Shared options infrastructure

Do not rebuild:
- production selector replay boundary;
- timestamped quote retention and provenance;
- stale/future/missing/wide-spread fail-closed handling;
- quote-dataset manifest verification;
- ASK-entry / BID-exit fill consumer;
- no-fill, gap-stop, same-bar pessimism;
- fee/slippage input guards and parameterized stress runner;
- premium-stop risk math;
- no-averaging guard;
- aggregate-open-risk gate + provenance;
- replay/forward risk parity.

### 2. Production selector authority

`OPTIONS_PAPER_V1` in `alert_ranker.paper_v1` is the production selector authority.

The canonical selector under `options_manager.contracts` is deterministic reference/research infrastructure only. It is not production authority.

Prospective production-selector evidence/replay has already been built (#710/#712/#715) and proven on a live SPY capture:
- 282 chain rows;
- 282/282 bid timestamps;
- 282/282 ask timestamps;
- 282/282 OI, delta, IV;
- production choice == retained-input production replay;
- `production_replay_parity=true`.

The minimal service-specific selector-evidence release is deployed on `options-scanner` as `58f1c50583d8bb747c0b221eabb75af376b10ecc`. It remains advisory/Public read-only and does not activate 212R as a running strategy family.

### 3. Causal 212R trigger clock

The old completed-30m + delayed first-sight clock is not the final Strat strategy-entry clock.

For the exact frozen 81-row 212R population:
- 81/81 recovered at the causal first lower-timeframe break;
- 81/81 same 212R family;
- 81/81 same direction;
- 81/81 exact trigger level;
- 81/81 exact invalidation level;
- 0 ambiguous.

The trigger-time model also exposed 8 additional 212R first breaks that were absent from the close-classified population because those 30m bars later became outside bars. Those 8 remain a separate descriptive population and are not silently added to the frozen 81.

### 4. 212R source geometry

PR #724 + #726 bind the geometry result to both frozen evidence hashes.

Frozen 81:
- source stop matches retained invalidation: 81/81;
- wrong-side source targets: 0;
- source magnitude already consumed at trigger: 3;
- median source magnitude: 0.3333R;
- source magnitude below 1R: 67/81.

Source-path outcome labels, underlying only:
- target first: 60;
- stop first: 13;
- unresolved at close: 5;
- target consumed at entry: 3.

The old >=1R target-floor rule is **not** source-equivalent 212R geometry:
- match source magnitude: 8;
- before source magnitude: 5;
- beyond source magnitude: 57;
- generic invalid: 11.

Therefore source magnitude and any farther runner target must remain separate concepts.

### 5. Trigger-time market-context comparison

The exact frozen 81 were independently replayed from the same SHA-bound SIP snapshot at the causal trigger clock.

Full alignment pass counts:
- old 30m-close alignment: **1/81**;
- completed-only trigger-time alignment: **1/81**;
- developing-HTF trigger-time alignment: **0/81**.

Cross-table:
- 80/81 fail all three variants;
- 1/81 passes old-close + completed-only and fails developing-HTF.

The single old/completed pass is GE SHORT on 2026-09-15 at the 17:00Z watched bar.

Completed-only trigger-time failure counts:
- hourly: 76;
- SPY trend: 66;
- QQQ trend: 55;
- prior daily: 45.

Developing-HTF trigger-time failure counts:
- hourly: 81;
- SPY trend: 66;
- developing daily: 61;
- QQQ trend: 55.

This proves full alignment is an extremely restrictive independent filter on this population. It does **not** prove that relaxing alignment improves option expectancy.

### 6. Exact underlying trigger-cross trade clock

A SHA-bound historical Alpaca SIP trade audit now resolves the exact first price-forming trade strictly through the frozen trigger for **81/81** rows.

Proof gates:
- frozen observer SHA verified before population binding;
- frozen trigger snapshot SHA verified before replay;
- only the already-proven five-minute first-cross bucket is queried;
- unknown SIP tapes or trade-condition codes fail closed;
- accepted price-forming trades reproduce frozen five-minute **OHLC exactly for 81/81 rows**;
- trigger semantics are strict (`>` for LONG, `<` for SHORT), so equality prints do not start the clock;
- all 81 rows resolve to nanosecond SIP timestamps and causal crossing-trade prices.

Compact evidence:
- manifest SHA-256: `29a1ecdcc052348708583818da1334f098af9425da69d9612f0c6e0b0003e103`;
- events SHA-256: `58718220328645e7f87aaa6826fd20fadf9f45e75a36ed920b4242635c9d42f6`;
- raw SIP trade rows retained: **353,456** across 81 windows;
- first-cross offset from five-minute bucket start: min **0.178917261s**, median **130.249476936s**, max **288.283787331s**.

This proves the exact causal crossing-trade clock/price. It does not by itself prove historical option-selector parity; the replay packet must still show that this causal price is fed through the intended `context.price -> normalized price -> OPTIONS_PAPER_V1` path.

## DATA BLOCKED

### Historical executable option replay

The frozen historical trigger population still lacks a proven causal source for every production-selector input at the trigger boundary, especially:
- historical decision-time Delta;
- historical decision-time contract-level open interest;
- a final replay packet proving the exact trigger-cross trade price is wired through the same historical `context.price -> normalized price -> OPTIONS_PAPER_V1` semantic path used for the corrected 212R replay.

Massive historical bid/ask is available, but current/future snapshots must not be used to back-fill historical analytics.

Therefore:
- no exact historical production-selector replay over the frozen 81;
- no honest historical option fill population;
- no historical after-cost option expectancy;
- no historical option drawdown or stress result.

Keep this **DATA BLOCKED** rather than synthesizing inputs.

## NEEDS POLICY DECISION

### A. Market context

Do not silently freeze old-close, completed-only, or developing-HTF full alignment as the 212R hard gate.

Reason: the corrected trigger-time population provides effectively no sample under full alignment (1/81 or 0/81), and no option expectancy comparison exists to prove that this filtering improves results.

A future rule must be explicitly chosen and pre-registered. Until then, market context should remain a measured field, not an invented proof claim.

### B. 212R target management

The source-defined base geometry is now clear enough to separate mechanically:
- entry: causal break of the inside-bar boundary;
- invalidation: opposite side of the inside bar;
- source magnitude / exhaustion target: far extreme of the preceding 2;
- farther structural runner: separate management hypothesis only.

The 3 `TARGET_CONSUMED_AT_ENTRY` rows have no remaining source magnitude at the trigger and must not be counted as ordinary target wins.

A future option test must state whether the source magnitude is T1, whether a runner exists, and how the runner is managed. It may not replace the source magnitude with the old >=1R floor and still call the test source-equivalent 212R.

### C. Cost/stress policy

The stress engine exists, but the numeric qualification policy still needs a frozen/pre-registered value set and aggregate pass threshold. The test fixtures' example percentages are not policy.

## NEEDS MORE EVIDENCE

- prospective 212R observations under corrected trigger timing;
- prospective option-chain evidence captured at the trigger boundary;
- executable option marks through resolution;
- after-cost expectancy;
- concentration and drawdown;
- untouched chronological / multi-month validation;
- final strategy + selector + fill + risk replay/forward parity.

## Important operational distinction

Production-selector evidence capture is already deployed for the **current running V1 scanner**.

It is useful, but it is **not sufficient to collect 212R option evidence**, because `alert_ranker.trigger_time` remains research/offline and 212R is not a running production strategy family.

The repository currently has no runtime/advisory path that watches a 212R precursor, detects the causal lower-timeframe break, and captures the production-selector option-chain evidence at that trigger while remaining observation-only.

## Actual next build

The next missing Phase-1 component for 212R is an **observation-only prospective trigger-evidence collector**.

It should:
1. arm completed 30m 2-1-2 reversal precursors using the proven trigger-time model;
2. detect the first causal lower-timeframe break without waiting for the 30m bar close;
3. preserve source entry, inside-bar invalidation, and source magnitude;
4. record SPY/QQQ + HTF context as fields without using an unapproved alignment variant to promote the signal;
5. fetch/read only the option-chain data required by the existing production selector;
6. retain underlying quote provenance, full chain fields, bid/ask side timestamps, volume, OI, delta, IV, selected contract, selector source hash, and production replay parity;
7. write to isolated evidence storage;
8. never alert as a trade, reserve ACTIVE risk, submit an order, or reach a broker path;
9. fail closed on missing trigger, missing invalidation, missing quote evidence, selector mismatch, or stale data.

Only after that lane accumulates real prospective rows should existing fill-realism and risk tooling be used to calculate option-side expectancy.

## Current verdict

**212R: PROMISING BUT UNPROVEN / WAIT.**

No proof, no trade.
