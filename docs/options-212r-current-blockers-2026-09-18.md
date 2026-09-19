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

This proves the exact causal crossing-trade clock/price. #738 separately proves that the causal trigger price can flow through the intended `context.price -> normalized price -> OPTIONS_PAPER_V1` path with retained-input replay parity. The remaining historical selector blocker is causal decision-time option analytics (Delta and contract-level OI), not underlying trigger-price provenance.

### 7. Miss-allowed IEX provisional source policy

PR #756 completes the alternative-source study that #742 proposed but did not validate.

Outcome-independent frozen denominator:
- all structurally ARMED 212 watch windows: **183**;
- SIP authoritative: **91 reversal / 66 continuation / 26 no-break / 0 blocked**;
- IEX provisional reversal-first: **90**;
- delayed SIP confirmation: **89/90 = 98.9%**;
- SIP reversal recall: **89/91 = 97.8%**;
- false provisional: **1/90**, rejected because SIP continuation broke first;
- explicit IEX misses of SIP reversals: **2/91**;
- confirmed directions: **48 LONG / 41 SHORT**, spanning all five frozen sessions.

Confirmed IEX-minus-SIP timing remains materially different:
- median: **3.501s**;
- p95: **156.273s**;
- max: **569.811s**.

Ruling: `MISS_ALLOWED_RESEARCH_OBSERVER_FEASIBLE`, **not SIP-equivalent**. This validates a distinct research source policy only. It does not deploy a collector, choose cadence/lag, create option expectancy, or promote 212R.

## DATA BLOCKED

### Historical executable option replay

The frozen historical trigger population still lacks a proven causal source for every production-selector input at the trigger boundary, specifically:
- historical decision-time Delta;
- historical decision-time contract-level open interest.

The underlying trigger boundary is no longer part of this blocker: #733 freezes the exact SIP crossing timestamp/price for 81/81 rows, and #738 proves that causal price can traverse the production `context.price -> normalized price -> OPTIONS_PAPER_V1` path with retained-input replay parity.

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

The separate frozen **first-sight family-persistence** study has now reached its first pre-registered decision point: 47 prospective 212R episodes across 3 sessions, both directions, produced an ex-opening matched-baseline excess of **-4.2 pp**, so that lane classifies 212R as **NO LONGER SHOWING EXCESS**. This does not answer the corrected trigger-time option strategy, but it removes the earlier retrospective first-sight excess as supporting prospective evidence.

Still needed for the corrected 212R lane:
- prospective 212R observations under corrected trigger timing;
- prospective option-chain evidence captured at the trigger boundary;
- executable option marks through resolution;
- after-cost expectancy;
- concentration and drawdown;
- untouched chronological / multi-month validation;
- final strategy + selector + fill + risk replay/forward parity.

## Important operational distinction

Production-selector evidence capture is already deployed for the **current running V1 scanner**.

The dedicated 212R prospective collector is now also **built in code** (#730), but it is **not deployed or scheduled** and it does not make 212R a running strategy family. Its evidence journal is isolated from scanner trade/risk state.

Independent timing review found that collector v0.1 measured evidence lag from the completed five-minute trigger bar rather than from the actual strict-through crossing inside that bar. The frozen 81 showed that bar-close-only detection would inherit 11.716s to 299.821s of delay, with 169.751s median, before any network/chain latency.

Collector v0.3 corrects the lane by:
1. retaining the proven Public chart structure/arm logic;
2. while a pre-armed setup is still `WATCHING`, querying Alpaca consolidated SIP trades only from the frozen watch start through the current source-observation time;
3. resolving whichever frozen boundary broke first with the same strict price-forming trade semantics as #733, including exact nanosecond crossing time;
4. reusing the same 212 family classifier and canonical source-geometry function for the live first break;
5. measuring both pre-selector and final selector-capture lag from the exact SIP crossing timestamp;
6. allowing a first-bucket arm only when its recorded observation time is truly before the exact crossing;
7. preserving the canonical raw SIP trade window separately with SHA-256 provenance;
8. blocking selector evidence if SIP credentials, exact crossing proof, quote evidence, parity, freshness, or timing proof are missing;
9. keeping all output in isolated evidence storage with no alert, ACTIVE risk, broker/account, order, DEMO, or live route.

## Actual next gate

Collector v0.3 review/CI is complete: #730/#739 are merged, and #741 adds an explicit fail-closed preflight for the current Alpaca entitlement.

The source question is now split cleanly:

- **Exact-SIP lane:** still blocked prospectively because the configured Alpaca entitlement cannot query sufficiently recent consolidated SIP during RTH.
- **IEX-provisional lane:** the previously proposed alternative-source study is now complete in #756. On the outcome-independent 183-arm frozen population, IEX emitted 90 provisional reversals; delayed SIP confirmed 89, rejected 1 continuation-first false provisional, and IEX missed 2 of 91 SIP-authoritative reversals. Confirmed IEX timing remained materially different from SIP (median 3.501s late, p95 156.273s, max 569.811s), so IEX is **not** an exact-SIP substitute.

If the IEX-provisional source policy is pursued prospectively, the next gates are:

1. Explicitly authorize a **separately versioned observation-only IEX + delayed-SIP reconciliation release**. Do not mutate collector v0.3 by silently swapping its source.
2. Pre-register the allowed IEX-trigger-to-selector-evidence lag policy. #756 intentionally chose no production threshold.
3. Pre-register collector cadence. No timer value is authorized by the offline study.
4. Prove the service-specific route cannot mutate scanner/risk/broker/order state.
5. Obtain the first natural RTH `ARMED -> IEX provisional reversal -> decision-time selector evidence -> delayed SIP reconciliation` row on that exact release.
6. Keep confirmed IEX rows in a distinct source cohort; misses/rejections remain explicit and are never backfilled or rewritten to the earlier SIP timestamp.

The exact-SIP path remains separately available only after real-time SIP access is proven. #741 remains the fail-closed proof for that lane.

After an authorized prospective collector begins accumulating real rows, existing fill-realism and risk tooling can be used to calculate option-side evidence. Historical exact option replay still remains DATA BLOCKED on causal historical Delta and contract-level OI; #738 separately proves that the exact trigger price can traverse the production `context.price` selector path with replay parity.

## Current verdict

**212R: UNPROVEN / WAIT.**

No proof, no trade.
