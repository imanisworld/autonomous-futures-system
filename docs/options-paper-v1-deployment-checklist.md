# Options Paper V1 — Deployment / Evidence Start Gate

This is the operational gate for starting the first `OPTIONS_PAPER_V1` evidence epoch. Current-state authority is `docs/options-current-state-handoff.md`.

Options-ready baseline: **`9d008a0dcdcb69270d80b663c678b4522f27ebb6`** with **4,921 passed / 7 skipped / 2 warnings**. If `main` advances before deployment, verify the deployed commit still contains this options baseline and has green CI.

**Current state: deployment not yet proven; evidence epoch not started.**

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

The box must have valid read-only option-market-data credentials/account pin and valid Alpaca data/calendar credentials for causal SIP bar context. Secrets stay on the box.

Scheduled V1 collection must fail closed when required causal/contract data or policy pins are missing.

Old PR #446 / `claude/options-data-health` is **not** a prerequisite for this V1 deployment. Do not revive the old companion/data-health path to satisfy this checklist; fix any newly proven provider defect in the active `alert_ranker` path.

## Frozen V1 policy to verify

- max planned risk per ACTIVE paper trade: **$300**
- max aggregate ACTIVE planned risk: **$1,000**
- no hard position-count cap
- 45+ DTE preferred
- 14–44 DTE allowed with `DTE_EXCEPTION`
- <14 DTE excluded
- ask entry
- 25% adverse premium stop
- underlying invalidation required
- Signa observational only
- GEX optional
- low liquidity / wide spread / missing critical quote data fails closed

Do not use generic/manual exceptions to inject <14 DTE rows into V1.

## Populations expected

ACTIVE:

- 30m 2-1-2 continuation
- Daily 2-1-2 continuation
- Daily 2-2-2 continuation / reversal
- Daily 3-2-2 continuation / reversal
- Daily 3-2 developing — WATCH only

COUNTERFACTUAL / observation:

- mechanically valid 30m/Daily signals rejected by market/target filters
- 1H Strat observations
- `4H_RTH` Strat observations built from session-anchored causal 30m bars

Counterfactual rows must never alert, reserve ACTIVE risk, or enter ACTIVE dashboard P&L.

## Market-hours smoke proof

Do not start the evidence epoch until one normal-session smoke proves all of the following on the deployed release:

1. deployed SHA is recorded and matches the intended reviewed release;
2. scheduled scan runs with V1 preflight satisfied;
3. fresh causal 30m bars and SPY/QQQ context are present;
4. setup/timeframe identity is correct;
5. selected expiration is sane and follows 45+ / 14–44 policy;
6. selected strike/right exists in the returned chain;
7. bid/ask, spread, volume, and open interest pass quality checks;
8. planned risk math and ACTIVE aggregate risk are correct;
9. ACTIVE and COUNTERFACTUAL rows are distinct and cannot dedupe each other;
10. COUNTERFACTUAL rows do not consume ACTIVE risk;
11. entry contract mark is written to SQLite;
12. the exact same option symbol is re-quoted on the next resolver cycle;
13. ACTIVE dashboard summary excludes counterfactual outcomes;
14. Discord sends/suppresses according to the existing trade-proof gate;
15. no order/broker path is reachable from the scanner;
16. an `options_v1_diagnostic_snapshots` ENTRY row is written;
17. the next resolver cycle writes a diagnostic MARK/RESOLUTION snapshot with underlying price + latest option quote;
18. entry trigger + underlying entry price are present so entry extension is computable;
19. quote timestamp/age and actual Greeks/IV presence or absence are visible;
20. ambiguity/path metadata is preserved rather than guessed.

## Read-only smoke reports

### Bankroll / drawdown / swing

```bash
python -m alert_ranker.account_equity \
  --db logs/options_scanner.sqlite \
  --balances 1500,2500,5000 \
  --capital-ceiling 5000
```

Confirm all three scenarios read the same ACTIVE V1 rows without changing collector state and report:

- realized + unrealized equity;
- peak-to-trough drawdown $/%;
- capital deployed + planned risk;
- capital-only blocked trades;
- minimum observed cash needed to fund the sequence;
- overnight holds / position-nights / max overnight positions / observed overnight premium gaps.

The **$5,000 value is an allocation ceiling, not an acceptable drawdown**.

### Strategy diagnostics

```bash
python -m alert_ranker.v1_diagnostics logs/options_scanner.sqlite \
  --output logs/options_v1_diagnostics.json
```

Confirm the report reads the same smoke data without mutation and exposes:

- option + underlying MAE/MFE;
- directional entry extension;
- quote age + observation gaps;
- missing Greeks/IV + provider errors;
- ambiguity flags;
- evidence quality (`HIGH/MEDIUM/LOW`);
- ACTIVE vs COUNTERFACTUAL identity;
- setup/timeframe sample counts and uncertainty ranges;
- all three friction views: recorded executable, fee stress, fee + 1c/share slippage stress.

## Evidence epoch start

Only after the smoke and both reports pass:

1. record deployed commit SHA;
2. record smoke timestamp;
3. mark that pair as the V1 evidence epoch;
4. collect natural candidates without changing setup, DTE, stop, target, filter, or risk rules;
5. any later rule change creates a new policy/evidence cohort.

**No proof, no trade.**
