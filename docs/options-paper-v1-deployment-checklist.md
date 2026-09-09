# Options Paper V1 — Deployment / Evidence Start Gate

This checklist is the final gate before the first `OPTIONS_PAPER_V1` evidence epoch is counted. It is options-only and does not authorize broker execution.

## Required runtime mode

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

The box must have valid read-only option-market-data credentials/account pin and valid Alpaca data/calendar credentials for causal SIP bar context. Secrets stay in the box environment.

Scheduled V1 collection fails closed when required causal/contract data or policy pins are missing.

## Frozen V1 policy

- max planned risk per active paper trade: **$300**
- max aggregate active planned risk: **$1,000**
- no position-count cap
- 45+ DTE preferred
- 14–44 DTE allowed with `DTE_EXCEPTION`
- <14 DTE excluded
- ask entry
- 25% adverse premium stop
- underlying invalidation required
- Signa observational only
- GEX optional context
- low liquidity / wide spread / missing critical quote data fails closed

Do not use a generic/manual `dte_exceptional` override to inject <14 DTE rows into this campaign.

## Active populations

- 30m 2-1-2 continuation
- Daily 2-1-2 continuation
- Daily 2-2-2 continuation / reversal
- Daily 3-2-2 continuation / reversal
- Daily 3-2 developing — WATCH only

## Counterfactual / observation populations

These rows never alert and never consume the active $1,000 risk budget:

- mechanically valid 30m/Daily signals rejected by market/target filters
- 1H Strat observations
- `4H_RTH` Strat observations built from session-anchored causal 30m bars

They remain separate from ACTIVE P&L and risk accounting.

## Bankroll / drawdown study

Replay the same ACTIVE V1 rows through cash-only long-option scenarios:

- **$1,500** starting balance
- **$2,500** starting balance
- **$5,000** starting balance / current maximum allocation ceiling

The $5,000 figure is **not** an acceptable drawdown.

Run:

```bash
python -m alert_ranker.account_equity \
  --db logs/options_scanner.sqlite \
  --balances 1500,2500,5000 \
  --capital-ceiling 5000
```

The report must use ask entry, bid mark-to-market/exit, include unrealized P&L, exclude counterfactual rows from account P&L, record capital-only blocks, and preserve overnight/swing exposure.

## Diagnostic evidence study

Run:

```bash
python -m alert_ranker.v1_diagnostics logs/options_scanner.sqlite \
  --output logs/options_v1_diagnostics.json
```

The diagnostic report must be read-only and must preserve:

- option MAE/MFE;
- underlying MAE/MFE;
- directional entry extension beyond the mechanical trigger;
- quote age and observation gaps;
- missing Greeks/IV and provider errors;
- resolution/path ambiguity;
- evidence quality (`HIGH/MEDIUM/LOW`);
- ACTIVE vs COUNTERFACTUAL identity;
- setup/timeframe sample counts and uncertainty ranges;
- recorded executable P&L plus analysis-only friction stresses.

Friction overlays are sensitivity tests only:

1. recorded ask-entry / bid-exit;
2. +$0.65 per contract per leg fee stress;
3. same fee stress + $0.01/share adverse slippage on both legs.

They never overwrite canonical V1 outcomes.

## Lifecycle / ambiguity rule

The exact selected option contract is re-quoted on the scheduled resolver cadence. Each resolution records that the path between snapshots is not directly observed.

If premium stop and underlying target are both true on the same observed snapshot, the row is explicitly tagged ambiguous and pessimistically counted as a loss while preserving the ambiguity metadata.

## Market-hours smoke proof

Do not start the evidence epoch until one normal-session smoke proves all of the following on the deployed release:

1. scheduled scan runs with V1 preflight satisfied;
2. fresh causal 30m bars and SPY/QQQ context are present;
3. setup/timeframe identity is correct;
4. selected expiration is sane and follows 45+ / 14–44 policy;
5. selected strike/right exists in the returned chain;
6. bid/ask, spread, volume, and open interest pass quality checks;
7. planned risk math and active aggregate risk are correct;
8. active and counterfactual rows are distinct and cannot dedupe each other;
9. counterfactual rows do not consume aggregate active risk;
10. entry contract mark is written to SQLite;
11. the exact same option symbol is re-quoted on the next resolver cycle;
12. active dashboard summary excludes counterfactual outcomes;
13. Discord sends/suppresses according to the existing trade-proof gate;
14. no order/broker path is reachable from the scanner;
15. an `options_v1_diagnostic_snapshots` ENTRY row is written for the shadow setup;
16. the next resolver cycle writes a diagnostic MARK/RESOLUTION snapshot with underlying price + latest option quote;
17. entry trigger and underlying entry price are present so entry extension is computable;
18. quote timestamp/age and Greeks/IV presence are visible in diagnostics;
19. `alert_ranker.account_equity` reads the smoke rows/marks without mutating collector state;
20. the $1,500 / $2,500 / $5,000 replay reports equity, drawdown, capital, risk, blocks, and swing metrics;
21. `alert_ranker.v1_diagnostics` reads the same smoke data without mutating collector state;
22. the diagnostic report shows MAE/MFE, entry extension, quality flags, and all three friction views.

After this passes, record the deployed commit SHA and smoke timestamp as the V1 evidence epoch. From that point forward, do not tune setup, DTE, stop, target, filter, or risk rules inside the same population. A rule change creates a new policy version and evidence cohort.
