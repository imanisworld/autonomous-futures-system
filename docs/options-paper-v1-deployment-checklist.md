# Options Paper V1 — Deployment / Evidence Start Gate

This is the operational gate for starting the first `OPTIONS_PAPER_V1` evidence epoch. Current-state authority is `docs/options-current-state-handoff.md`.

Options-ready baseline: **`9d008a0dcdcb69270d80b663c678b4522f27ebb6`** with **4,921 passed / 7 skipped / 2 warnings**. If `main` advances before deployment, verify the deployed commit still contains this options baseline and has green CI.

**Current state (2026-09-18): original V1 deployment gate is complete and evidence epochs 1 and 2 are recorded. The active options scanner is now independently pinned to service-specific release `3b9770d8fed4ad1825cc325bab536ffea618a94e`. Options entry/target/risk behaviour remains frozen; do not infer that repository `main` is deployed.**

| | |
|---|---|
| Deployed SHA | `899a524aad82a66c80ba832ea5601430bdac7355` (live 2026-09-15T00:32:37Z) |
| Smoke passed | 2026-09-15T16:50:00Z (first live exercise of the entry rules verified) |
| **Evidence epoch** | **`899a524` + 2026-09-15T16:50:00Z** — cohort `V1-EPOCH-1` |
| Cohort boundary | `scans.id >= 20972`, `options_shadow_journal.id >= 9192`; earlier rows are pre-epoch telemetry only |
| Machine-readable | `docs/options_v1_evidence_epoch.json` |

Rules in force for this cohort: late-entry guard (#570), Daily-lane entry timing + 1R target floor (#571), ENTRY_LATE episode block with counterfactual preservation (#575). Any change to them starts a new cohort.

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

## Prospective selector-evidence v3 deployment — prepared, not deployed

This is a separate evidence-only service update; it is **not** a new strategy/policy cohort.

Prepared exact candidate: `5c14577cf2b71270bd714ee5b3b783bab4d7b12f` on `release/options-selector-evidence-v3` (tracked by #716).

Exact delta from active options-scanner release `3b9770d8fed4ad1825cc325bab536ffea618a94e`:

- runtime: `alert_ranker/options_selector_evidence.py`, `alert_ranker/options_production_selector_replay.py`, evidence-only hooks in `alert_ranker/scanner_legacy.py`, append-only evidence storage in `alert_ranker/storage.py`;
- tests: `tests/test_options_selector_evidence.py`, `tests/test_options_paper_v1.py`.

No change to `alert_ranker/paper_v1.py`, market-data policy, setup rules, DTE/contract ranking, risk caps, broker/order code, `.env`, or futures runtime. Candidate proof before deployment: 31 targeted tests passed; isolated live SPY probe retained 282/282 bid timestamps, ask timestamps, OI, delta and IV; production selection and retained-input production replay matched with `production_replay_parity=true`.

Before promotion, actual-box proof must confirm the currently pinned options release/integrity, advisory-only/read-only posture, `order_supported=false`, account endpoints forbidden, production SQLite path/continuity, current aggregate planned risk, and the exact six-file candidate allowlist. Do not restart the futures bot. Use a service-specific restart window.

After promotion, prove options-scanner cwd/systemd pin equals the exact candidate release, health/posture is unchanged, production DB continuity is preserved, `options_selector_evidence` exists, and there is no traceback/order/broker activity. The first **natural** candidate must create a selector-evidence row with `production_replay_parity=true`; missing required evidence or replay mismatch must fail closed as `DATA_BLOCKED`. Rollback target is `3b9770d8fed4ad1825cc325bab536ffea618a94e`.

This deployment starts prospective selector provenance only. It does not qualify a strategy.

## Trigger-time research boundary — #717

The completed-30m-bar observer remains valid for measuring what the current scanner could see, but #717 establishes that it is not the final Strat strategy-entry clock. For strategy backtesting, a completed precursor must freeze its boundaries and the first causal lower-timeframe boundary break is the trigger event.

Therefore do **not** expand historical option-side acquisition from the old delayed first-sight timestamps. First run the trigger-time comparison on the frozen underlying corpus, rebuild market/HTF context as of trigger time, quantify family/timing/ambiguity changes, and freeze the resulting decision timestamps. #717 is research/advisory only and changes no deployed scanner behavior.

## Evidence epoch start

Only after the smoke and both reports pass:

1. record deployed commit SHA;
2. record smoke timestamp;
3. mark that pair as the V1 evidence epoch;
4. collect natural candidates without changing setup, DTE, stop, target, filter, or risk rules;
5. any later rule change creates a new policy/evidence cohort.

**No proof, no trade.**
