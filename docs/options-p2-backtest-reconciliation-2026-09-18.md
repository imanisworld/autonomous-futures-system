# Options P2 backtest reconciliation — 2026-09-18

## Purpose

This is a current-state reconciliation only. It exists to prevent rebuilding options Backtest→DEMO infrastructure that is already on `main`.

It does **not** modify the preserved #653 evidence package, does not activate DEMO/paper automation, and does not authorize live trading.

## Current baseline

- `main`: `e465631ccf91fa6235b8a6ed5b3caeb05dc35562`
- PR #706 merged: parameterized base-vs-adverse slippage stress runner.
- Targeted shared-infrastructure regression on current `main`: **248 passed**.
- No deployment or restart was performed for this reconciliation.

## Market-hours proof captured 2026-09-18 15:24Z

Read-only Public probes were run against SPY and AMZN.

### Quote timestamp probe

Verdict: `PROVEN_FOR_CAPTURE`.

- SPY 2026-11-20 chain: 282 quoted contracts, 282 executable timestamps, 282 fresh, 0 missing, 0 stale, 0 future.
- AMZN 2026-11-20 chain: 114 quoted contracts, 114 executable timestamps, 114 fresh, 0 missing, 0 stale, 0 future.
- Frozen source identity: `public:/userapigateway/marketdata/{accountId}/option-chain`.
- Frozen quote-age limit: 900 seconds.

This proves the current capture, not every future response and not historical quote coverage.

### Selector parity probe

Verdict: `PROVEN_FOR_CAPTURE`.

For both SPY and AMZN, CALL and PUT:
- provider chain normalized into canonical serialized selector input;
- serialized input round-trip was byte-stable;
- replay/forward result bytes matched;
- the canonical selector produced a deterministic contract selection from the same bytes.

This proves current provider→selector parity for the capture. It is not a historical 212R backtest result.

## Do not rebuild these shared-infrastructure pieces

The following are already implemented and covered by current tests:

1. Mechanical contract selector with frozen rule file.
2. Deterministic expiration / strike / delta / moneyness ranking.
3. DTE, volume, open-interest, spread and delta filters.
4. No-hindsight future-quote exclusion.
5. Stale-quote exclusion before selector ranking.
6. Serialized replay/forward selector parity.
7. Provider-chain → canonical selector input bridge.
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

**Implementation/proof status: COMPLETE in code/tests.**

The old #653 packet still says these fields are false because it predates the implementation. Do not treat those old false values as a request to rebuild the selector.

### Item 2 — timestamped quote retention

**Forward/current-capture implementation: COMPLETE.**

Current Public market-hours evidence proves executable bid/ask timestamps and source identity for the SPY/AMZN capture.

**Historical 212R dataset: DATA BLOCKED.**

Massive historical option bid/ask is available, but a complete historical selector row still lacks proven decision-time historical delta/open interest and exact underlying-price provenance for the frozen population. Do not synthesize those values and do not substitute current snapshots.

Therefore a full frozen historical `option_quotes_manifest.json` for all 81 212R decisions is not yet honest evidence.

### Item 2B — executable-fill reconstruction

**Mechanics implementation: COMPLETE.**

No-fill, gap-stop, same-bar pessimism, canonical entry/exit fill parity, costs, and the stress runner now exist.

**Evidence status: NOT COMPLETE.**

The acceptance fields `slippage_stress_pre_registered` and `slippage_stress_pass` require an actual frozen policy and aggregate result. The newly merged runner intentionally chooses no policy values. Without a complete historical option-fill population, there is no honest aggregate historical stress result to claim.

### Item 3 — risk-policy cleanup

**Implementation/proof status: COMPLETE in code/tests.**

Premium-stop risk, no-averaging, aggregate-risk enforcement, provenance validation, and replay/forward risk parity are implemented.

A future evidence packet still has to reference the exact frozen runtime budget artifact rather than merely setting booleans.

## What actually remains for 212R

These are strategy/evidence problems, not missing shared infrastructure:

- classification remains `WAIT`;
- no frozen 212R target formula;
- no 212R replay/forward strategy formula parity;
- historical option selector replay is blocked by missing decision-time analytics/provenance;
- no required resolved option-fill population;
- no positive after-cost option expectancy or net P&L proof;
- no completed untouched multi-month / chronological validation;
- prospective persistence has not met its pre-registered proof requirement;
- remaining 212R-specific golden fixtures are not complete.

## Ruling

Do **not** regenerate the preserved #653 packet by flipping its old booleans. It is baseline provenance.

Do **not** rebuild selector, quote-retention, fill-realism, or risk infrastructure that now exists on `main`.

For P2, the correct state is:

- shared infrastructure: substantially built;
- current forward quote/selector proof: working;
- historical 81-point exact selector replay: parked as DATA BLOCKED;
- 212R strategy qualification: WAIT;
- next useful evidence comes from prospective decision-time captures and later strategy-specific validation, not another speculative infrastructure rewrite.
