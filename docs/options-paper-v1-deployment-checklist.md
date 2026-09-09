# Options Paper V1 — Deployment / Evidence Start Gate

This checklist is the final gate before the first `OPTIONS_PAPER_V1` evidence row is counted. It is options-only and does not authorize broker execution.

## Required runtime mode

Set the scanner box explicitly for the frozen campaign:

```env
OPTIONS_SCANNER_ENABLED=true
OPTIONS_PAPER_V1_COLLECTION_ENABLED=true
OPTIONS_BAR_CONTEXT_ENABLED=true
OPTIONS_BAR_CONTEXT_FEED=sip
OPTIONS_BAR_CONTEXT_TIMEFRAME=30Min
OPTIONS_BAR_CONTEXT_LOOKBACK_DAYS=10
OPTIONS_SCANNER_INTERVAL_MINUTES=5
OPTIONS_MARKET_DATA_PROVIDER=public
OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS=1000
OPTIONS_MANAGER_RISK_MIN_DTE_DAYS=14
OPTIONS_COMPANION_ENABLED=false
```

The box must also have valid read-only Public market-data credentials/account pin and valid Alpaca data/calendar credentials for causal SIP bar context. Secrets stay in the box environment and never in git.

`OPTIONS_PAPER_V1_COLLECTION_ENABLED=true` turns on the scheduled V1 preflight. A scheduled run fails closed when bar context is disabled/unconfigured or contract market data is unconfigured; it must not silently collect a blind population.

## Frozen V1 policy

- max planned risk per active paper trade: **$300**
- max aggregate active planned risk: **$1,000**
- no position-count cap
- 45+ DTE preferred
- 14–44 DTE allowed with `DTE_EXCEPTION`
- <14 DTE excluded from the V1 collector
- entry basis: ask
- premium stop: 25% adverse from entry
- underlying invalidation required
- Signa observational only
- GEX optional context
- low liquidity / wide spread / missing critical quote data fails closed

The generic manual `options_manager` contract-quality surface is not the V1 evidence authority. V1 rows are admitted only by the scanner's `OPTIONS_PAPER_V1` policy; do not use a manual `dte_exceptional` override to inject <14 DTE rows into this campaign.

## Populations

### Active paper populations

- 30m 2-1-2 continuation
- Daily 2-1-2 continuation
- Daily 2-2-2 continuation / reversal
- Daily 3-2-2 continuation / reversal
- Daily 3-2 developing is WATCH-only

### Counterfactual / observation populations

These rows never alert and never consume the $1,000 active-risk budget:

- mechanically valid 30m/Daily signals rejected by market/target filters
- 1H Strat observations
- `4H_RTH` Strat observations, defined as regular-session blocks anchored at the session open; completed prior sessions retain the shortened final RTH block rather than pretending it is a native/vendor 4H candle

Every counterfactual row carries `paper_evidence_lane=COUNTERFACTUAL` and `risk_budget_consumed=false`. The normal dashboard summary excludes those rows from active win rate and P&L.

## Lifecycle / ambiguity rule

The exact selected option contract is re-quoted on the scheduled resolver cadence. Each resolution records that the path between snapshots is not directly observed.

If premium-stop and underlying-target conditions are both true on the same observed snapshot, the row is explicitly tagged:

- `resolution_ambiguity=AMBIGUOUS`
- `pessimistic_status=LOSS`
- `pessimistic_resolution_used=true`

It remains a pessimistic loss for accounting, but later analysis can include/exclude the ambiguous cohort explicitly instead of mistaking it for a clean path.

## Market-hours smoke proof

Do not start the evidence epoch until one normal-session smoke proves all of the following on the deployed release:

1. scheduled scan runs with V1 preflight satisfied;
2. fresh causal 30m bars and SPY/QQQ context are present;
3. setup/timeframe identity is correct;
4. selected expiration is sane and follows 45+ / 14–44 policy;
5. selected strike/right exists in the returned chain;
6. bid/ask, spread, volume and open interest pass quality checks;
7. planned risk math and active aggregate risk are correct;
8. active and counterfactual rows are distinct and cannot dedupe each other;
9. counterfactual rows do not consume aggregate active risk;
10. entry contract mark is written to SQLite;
11. the exact same option symbol is re-quoted on the next resolver cycle;
12. active dashboard summary excludes counterfactual outcomes;
13. Discord sends or suppresses exactly according to the existing trade-proof gate;
14. no order/broker path is reachable from the scanner.

After this passes, record the deployed commit SHA and smoke timestamp as the V1 evidence epoch. From that point forward, do not tune setup, DTE, stop, target, filter or risk rules inside the same population. A rule change creates a new policy version and evidence cohort.
