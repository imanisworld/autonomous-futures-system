# Options P2 backtest reconciliation — 2026-09-18

## Purpose

This is a current-state reconciliation only. It exists to prevent rebuilding options Backtest→DEMO infrastructure that is already on `main`.

It does **not** modify the preserved #653 evidence package, does not activate DEMO/paper automation, and does not authorize live trading.

## Current baseline

- current repository `main`: `c7dc6c5de3ddfde0dd0876bf86b28531f976c46b` (#750), after #744/#747 and the separate futures-only #749/#751/#752 changes.
- PR #706 merged: parameterized base-vs-adverse slippage stress runner.
- PR #710 merged: append-only prospective decision-time selector evidence capture.
- PR #712 merged: exact replay of the actual `OPTIONS_PAPER_V1` production selector from retained evidence.
- #715 removes `options_manager`/canonical-selector imports from the active evidence capture path while retaining the same production replay proof.
- The options scanner now runs its service-specific immutable selector-evidence release `58f1c50583d8bb747c0b221eabb75af376b10ecc`, smoke-proven advisory-only with Public read-only data, `order_supported=false`, account endpoints forbidden, and no broker/order activation.
- #730/#739 build the dedicated observation-only prospective 212R collector and correct it to an exact active-WATCHING SIP first-break clock; it remains **not deployed or scheduled**.
- #741 fails that collector closed when sufficiently recent consolidated SIP is unavailable; #742 proves IEX is not source-equivalent on the frozen 81 and therefore is not an admissible silent fallback.
- #744 corrects the historical-source audit: exact causal underlying trigger time/price is proven, while historical decision-time Delta and contract-level open interest remain the blocker to exact historical option-selector replay.
- #747 completes a controlled target-geometry test on the frozen clean V1-EPOCH-2 AHEAD cohort; wider targets improve forced-horizon P&L only slightly and all tested variants remain negative.
- #750 completes a Daily/4H_RTH hold-horizon compatibility test; Daily improves in a tiny/non-independent subset while 4H_RTH worsens materially, so no blanket higher-timeframe hold extension is supported.
- #749/#751/#752 are futures-lane observer/backtest-proof changes and do not alter options execution, selector authority, risk policy, or 212R qualification.

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

Massive historical option bid/ask is available. #733 proves the exact causal underlying SIP trigger-cross timestamp/price for **81/81** frozen rows with exact frozen 5-minute OHLC reconstruction and byte-identical repeat evidence, and #738 proves that causal price can traverse the production `context.price -> normalized price -> OPTIONS_PAPER_V1` path with retained-input replay parity.

A complete historical selector row still lacks proven decision-time **Delta** and contract-level **open interest** for the frozen population. Do not synthesize those values, model-derive them as if they were provider bytes, or substitute current snapshots.

Therefore a full frozen historical `option_quotes_manifest.json` / exact selector-fill replay for all 81 212R decisions is not yet honest evidence.

### Item 2B — executable-fill reconstruction

**Mechanics implementation: COMPLETE.**

No-fill, gap-stop, same-bar pessimism, canonical entry/exit fill parity, costs, and the stress runner now exist.

**Evidence status: NOT COMPLETE.**

The acceptance fields `slippage_stress_pre_registered` and `slippage_stress_pass` require an actual frozen policy and aggregate result. The newly merged runner intentionally chooses no policy values. Without a complete historical option-fill population, there is no honest aggregate historical stress result to claim.

### Item 3 — risk-policy cleanup

**Implementation/proof status: COMPLETE in code/tests.**

Premium-stop risk, no-averaging, aggregate-risk enforcement, provenance validation, and replay/forward risk parity are implemented.

A future evidence packet still has to reference the exact frozen runtime budget artifact rather than merely setting booleans.

## Trigger-time and geometry boundary — #717 through #738

The old completed-bar/first-sight clock remains valid for evaluating what V1 observed, but it is not the 212R strategy-entry clock.

The corrected evidence chain is now frozen in stages:

1. #721/#722 reproduce the exact frozen trigger-time population from completed 30m precursors and causal lower-timeframe breaks;
2. for the original frozen **81** 212R rows, family, direction, trigger, and invalidation are preserved **81/81** at the causal first-break boundary;
3. #724/#726 bind source-defined 212R geometry to those same frozen rows with **81/81 stop parity**; median source magnitude is **0.3333R**, **67/81** are below 1R, and **3/81** have source magnitude already consumed at entry;
4. #733 resolves the exact first price-forming consolidated-SIP trade strictly through each trigger for **81/81** rows, preserving nanosecond timestamp and causal crossing price while reproducing the frozen 5-minute OHLC exactly;
5. #738 proves on the frozen AMZN case that the causal trigger price can flow through the production `context.price -> normalized price -> OPTIONS_PAPER_V1` selector path with replay parity.

The old >=1R target floor is therefore not source-equivalent 212R magnitude; it is a separate management hypothesis. Historical option-side acquisition must use the corrected exact trigger boundary, but exact option selection remains blocked on historical Delta/OI provenance.

## Controlled management/horizon evidence added after the trigger correction

Two controlled clean-shadow studies on current `main` narrow management hypotheses without qualifying 212R:

- **#747 target geometry:** on the exact 47 clean V1-EPOCH-2 `AHEAD` rows, forced-horizon P&L is **-$840** at recorded `target_1`, **-$783** at fixed 1.5R, and **-$748** at fixed 2R. Wider targets help slightly but all tested variants remain negative, with censoring increasing as targets widen. This weakens a target-width-alone explanation for that mixed cohort; it is **not** a 212R-specific expectancy test.
- **#750 hold horizon:** on 24 clean Daily/`4H_RTH` rows, extending the hold by one RTH session changes combined P&L only **-$571 -> -$539**. Daily changes **-$358 -> +$137** on 10 rows / 7 structural keys, while `4H_RTH` changes **-$213 -> -$676** on 14 rows. This supports no blanket higher-timeframe extension; the Daily result is suggestive only and too small/non-independent for a rule change.

Neither study changes V1 policy, resolves 212R option-side data provenance, or authorizes scanner/collector deployment.

## What actually remains for 212R

These are strategy/evidence problems, not missing shared infrastructure:

- classification remains `WAIT`;
- exact underlying trigger time/price is frozen for the historical 81, but full historical option-selector replay is still blocked by missing causal decision-time **Delta** and contract-level **OI**;
- source-defined 212R magnitude is frozen, but final option target/runner management policy is unresolved because the existing >=1R floor is a separate management hypothesis; #747 weakens target-width-alone as an explanation in the mixed clean-shadow cohort but does not answer 212R-specific expectancy;
- no complete 212R replay/forward strategy formula parity packet exists under the corrected trigger-time + geometry + option-data boundary;
- collector v0.3 is merged and observation-only, but #741 proves the configured Alpaca entitlement cannot access sufficiently recent consolidated SIP during RTH, and #742 proves IEX is not source-equivalent on the frozen 81;
- therefore no trustworthy prospective `ARMED -> exact SIP cross -> option_evidence_usable` 212R row has been collected under the corrected lane;
- no required resolved option-fill population under corrected timing + geometry;
- no approved/pre-registered numeric slippage percentage or aggregate slippage-stress qualification pass;
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
- historical 81-point exact underlying trigger replay: proven; exact option selector replay remains DATA BLOCKED on historical Delta/OI;
- dedicated 212R prospective collector: built and fail-closed, but **HOLD** under current recent-SIP entitlement; IEX fallback rejected by #742;
- #747/#750 controlled studies narrow target/horizon hypotheses but do not establish 212R option expectancy and do not authorize V1 tuning;
- 212R strategy qualification: WAIT;
- next useful evidence comes from resolving the real-time consolidated-SIP source gate or obtaining an explicitly approved historical Delta/OI source, then collecting/validating option-side evidence—not another speculative infrastructure rewrite.
