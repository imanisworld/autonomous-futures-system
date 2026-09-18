# Options P2 backtest reconciliation — 2026-09-18

## Purpose

This is a current-state reconciliation only. It exists to prevent rebuilding options Backtest→DEMO infrastructure that is already on `main`.

It does **not** modify the preserved #653 evidence package, does not activate DEMO/paper automation, and does not authorize live trading.

## Current baseline

- repository `main` at this reconciliation: `4816aa4890266fa57f9afcf07bea7d299ff8fc53`.
- PR #706 merged: parameterized base-vs-adverse slippage stress runner.
- PR #710 merged: append-only prospective decision-time selector evidence capture.
- PR #712 merged: exact replay of the actual `OPTIONS_PAPER_V1` production selector from retained evidence.
- #715 removes `options_manager`/canonical-selector imports from the active evidence capture path while retaining production replay proof.
- Curated selector-evidence release **`58f1c50583d8bb747c0b221eabb75af376b10ecc`** is active on `options-scanner`; production selector behavior is unchanged, runtime evidence is v3/minimal, and the scanner remains advisory/Public read-only.
- #722 freezes the causal lower-timeframe trigger population; #724/#726 bind the frozen 81 to source-defined 212R geometry.
- #733 proves the exact first price-forming Alpaca SIP trade strictly through the trigger for 81/81 frozen rows with nanosecond timestamps and exact 5m OHLC reconstruction.
- #730 is merged as an observation-only prospective 212R trigger/selector-evidence collector. It is **not deployed or scheduled** and intentionally has no default capture-lag threshold.

## Market-hours proof captured 2026-09-18 15:24Z

Read-only Public probes were run against SPY and AMZN.

### Quote timestamp probe

Verdict: `PROVEN_FOR_CAPTURE`.

- SPY 2026-11-20 chain: 282 quoted contracts, 282 executable timestamps, 282 fresh, 0 missing, 0 stale, 0 future.
- AMZN 2026-11-20 chain: 114 quoted contracts, 114 executable timestamps, 114 fresh, 0 missing, 0 stale, 0 future.
- Frozen source identity: `public:/userapigateway/marketdata/{accountId}/option-chain`.
- Frozen quote-age limit: 900 seconds.

This proves the current capture, not every future response and not historical quote coverage.

### Selector capture and authority correction

The 15:24Z probe proved deterministic **canonical-selector** replay on identical bytes. It did **not** prove parity with the production scanner selector.

A post-#710 live SPY capture exposed that distinction: the production `OPTIONS_PAPER_V1` selector and the newer canonical/reference selector chose different contracts from the same 282-row chain. That was a real parity defect in the evidence claim, not a provider-data defect.

PR #712 resolved the authority boundary without changing production behavior:

- `OPTIONS_PAPER_V1` remains the production selector authority;
- retained evidence now hashes the exact production selector source;
- replay calls the same pure `choose_expiration()` + `choose_contract()` functions used by the scanner;
- the newer canonical selector is **offline reference/research only**, not production-equivalent evidence and not a runtime dependency;
- any future production-vs-replay divergence fails the evidence record closed as `DATA_BLOCKED / production_replay_mismatch`.

Live proof on minimal-runtime branch `476a6b3`, isolated temp SQLite DB, live SPY chain, no Discord send and no broker/order path:

- 282 chain rows captured;
- 282/282 bid timestamps;
- 282/282 ask timestamps;
- 282/282 open interest, delta and IV;
- production selection: `SPY261120C00775000`;
- production replay: `SPY261120C00775000`;
- production replay parity: `true`;
- runtime evidence modules import no `options_manager` package.

This proves the retained production inputs reproduce the production selector exactly for the observed capture while keeping canonical/reference analysis offline. It does not prove historical 212R coverage, every future provider response, or strategy edge.

## Do not rebuild these shared-infrastructure pieces

The following are already implemented and covered by current tests:

1. Frozen production `OPTIONS_PAPER_V1` selector plus exact retained-input replay.
2. Separate canonical/reference selector with frozen rule file; offline reference only unless a future approved cohort explicitly changes authority.
3. DTE, volume, open-interest, spread and delta filters.
4. No-hindsight future-quote exclusion.
5. Stale-quote exclusion before selector ranking.
6. Production scanner → retained evidence → production replay parity, fail-closed on mismatch.
7. Provider-chain → byte-stable production-selector evidence envelope; canonical/reference enrichment remains offline.
8. Timestamped quote retention with frozen source identity.
9. Fail-closed MISSING / STALE / FUTURE / INVALID / WIDE_SPREAD quote states.
10. Quote dataset manifest materialization and exact-byte verification.
11. Retained quote → selector replay parity.
12. Retained quote → canonical fill-consumer parity.
13. ASK-entry / BID-exit canonical fill path.
14. Explicit no-fill behavior.
15. Gap-through-stop handling.
16. Pessimistic same-bar stop/target handling.
17. Earliest post-trigger executable exit quote enforcement.
18. Fee/slippage input guards.
19. Base-vs-adverse slippage stress runner using the same canonical fill consumer.
20. Premium-stop planned-risk calculation.
21. Explicit no-averaging guard.
22. Aggregate-open-risk gate and runtime-budget provenance machinery.
23. Golden risk parity fixtures.

## Shared-infrastructure status vs the acceptance spec

### Item 1 — mechanical selector

**Implementation/proof status: COMPLETE in code/tests for the production selector replay boundary.**

Authority is explicit: `OPTIONS_PAPER_V1` is production. The canonical selector is not a substitute for production replay because its ranking semantics differ. Evidence schema v3 preserves the exact production inputs and replay parity without importing the canonical selector at runtime; only the production replay parity field can support a production-equivalence claim.

The old #653 packet still says these fields are false because it predates the implementation. Do not treat those old false values as a request to rebuild the selector.

### Item 2 — timestamped quote retention

**Forward/current-capture implementation: COMPLETE.**

Current Public market-hours evidence proves executable bid/ask timestamps and source identity for the SPY/AMZN capture.

**Historical 212R dataset: PARTIALLY UNBLOCKED / still DATA BLOCKED for full selector replay.**

#733 proves the exact causal underlying crossing-trade timestamp/price for 81/81 frozen decisions using SHA-bound Alpaca SIP trades, strict-through trigger semantics, and exact frozen 5m OHLC reproduction. That removes the old “unknown trigger clock/price” blocker.

Massive historical option bid/ask is also available. What is still missing for a complete production-selector row is proven historical decision-time Delta, historical contract-level open interest, and a final replay packet proving that the causal trigger price is wired through the intended historical `context.price -> normalized price -> OPTIONS_PAPER_V1` path. Do not synthesize those values and do not substitute current snapshots.

Therefore a full frozen historical production-selector/fill manifest for all 81 212R decisions is not yet honest evidence.

### Item 2B — executable-fill reconstruction

**Mechanics implementation: COMPLETE.**

No-fill, gap-stop, same-bar pessimism, canonical entry/exit fill parity, costs, and the stress runner now exist.

**Evidence status: NOT COMPLETE.**

The acceptance fields `slippage_stress_pre_registered` and `slippage_stress_pass` require an actual frozen policy and aggregate result. The newly merged runner intentionally chooses no policy values. Without a complete historical option-fill population, there is no honest aggregate historical stress result to claim.

### Item 3 — risk-policy cleanup

**Implementation/proof status: COMPLETE in code/tests.**

Premium-stop risk, no-averaging, aggregate-risk enforcement, provenance validation, and replay/forward risk parity are implemented.

A future evidence packet still has to reference the exact frozen runtime budget artifact rather than merely setting booleans.

## Trigger-time and geometry boundary — #717 through #733

The old completed-bar/first-sight clock remains valid for evaluating what V1 observed, but it is not the strategy-entry clock for 212R qualification.

The corrected evidence chain is now frozen in stages:

1. #722 reproduces the exact frozen 81 at the causal first five-minute break with 81/81 family, direction, trigger and invalidation parity.
2. #724/#726 bind source-defined 212R geometry to those same frozen rows: 81/81 stop parity, median source magnitude 0.3333R, 67/81 below 1R, and 3/81 source magnitude consumed at entry.
3. #733 resolves the exact first price-forming SIP trade strictly through each trigger for 81/81 rows, preserving nanosecond timestamp and causal crossing price while reproducing the frozen 5m OHLC exactly.

The old >=1R target floor is not source-equivalent 212R magnitude; it is a separate management hypothesis. The exact trigger clock/price is therefore proven at the underlying level, but historical option selection remains blocked on unavailable decision-time analytics and final selector price-path parity.

## What actually remains for 212R

These are strategy/evidence problems, not missing shared infrastructure:

- classification remains `WAIT`;
- exact underlying trigger clock/price is proven, but historical option-selector replay still lacks decision-time Delta/OI and final `context.price -> normalized price -> OPTIONS_PAPER_V1` parity proof;
- source-defined 212R magnitude is frozen, but final option target/runner management policy is not;
- #730 prospective collector code is merged but not deployed; capture-lag threshold and timer cadence remain explicit policy decisions;
- no real prospective `ARMED -> TRIGGERED -> option_evidence_usable` row has been collected under #730;
- no required resolved option-fill population under corrected timing + geometry;
- no pre-registered aggregate slippage-stress qualification policy/pass;
- no positive after-cost option expectancy or net P&L proof;
- no completed untouched multi-month / chronological validation;
- prospective persistence has not met its pre-registered proof requirement;
- remaining 212R-specific golden fixtures are not complete.

## Ruling

Do **not** regenerate the preserved #653 packet by flipping its old booleans. It is baseline provenance.

Do **not** rebuild selector, quote-retention, fill-realism, or risk infrastructure that now exists on `main`.

For P2, the correct state is:

- shared infrastructure: substantially built;
- current forward quote capture: working;
- current merged-main production-selector retained-input replay: proven for the observed SPY capture;
- canonical/reference selector: deterministic but not production authority;
- historical 81-point exact selector replay: parked as DATA BLOCKED;
- 212R strategy qualification: WAIT;
- next useful evidence comes from prospective decision-time captures and later strategy-specific validation, not another speculative infrastructure rewrite.
