# Options — Current State Handoff

_As of 2026-09-09. Do not recreate completed scanner/risk work, do not tune Policy V1 during collection, and do not treat a functioning scanner as proof of strategy edge._

## Verdict

**ADVISORY / PAPER EVIDENCE ONLY. PIPELINE BUILT. STRATEGY EDGE NOT YET PROVEN.**

Completed evidence plumbing now includes:

- PR #533 — independent multi-setup Strat collection;
- PR #536 — rejected-filter counterfactuals, 1H/4H_RTH observer cohorts, ambiguity telemetry, active/counterfactual separation, and fail-closed V1 runtime preflight;
- PR #540 — $1,500 / $2,500 / $5,000 bankroll, drawdown, and swing replay;
- PR #541 — append-only MAE/MFE, entry-extension, friction-stress, evidence-quality, and sample-confidence diagnostics.

No broker execution is added by this work.

## Frozen `OPTIONS_PAPER_V1` policy

- max planned risk per active paper trade: **$300**
- max aggregate active planned risk: **$1,000**
- no hard position-count cap; aggregate planned risk controls exposure
- preferred DTE: **45+**
- 14–44 DTE: allowed and tagged `DTE_EXCEPTION`
- <14 DTE / weeklies: excluded
- entry basis: ask
- premium stop: **25% adverse from entry premium**
- underlying invalidation required
- no averaging down
- Signa observational only
- GEX optional context
- missing critical setup/quote/risk data => `DATA_INVALID`

Any change to those rules creates a separate policy/evidence population.

## Active strategy populations

- **30m 2-1-2 continuation**
- **Daily 2-1-2 continuation**
- **Daily 2-2-2 continuation**
- **Daily 2-2-2 reversal**
- **Daily 3-2 developing** — WATCH only
- **Daily 3-2-2 continuation**
- **Daily 3-2-2 reversal**

Daily actionability remains mechanical: previous-candle high/low break, with the opposite side as underlying invalidation. Existing market-alignment proof remains the active promotion gate.

## Counterfactual / timeframe observation populations

Mechanically valid signals rejected by filters are preserved instead of disappearing. Counterfactual rows:

- never send Discord alerts;
- never consume the active $1,000 aggregate-risk budget;
- use the same real-chain contract quality where enough facts exist;
- re-quote the exact selected option contract;
- remain distinct from ACTIVE rows;
- are excluded from active dashboard P&L/win rate.

Observer populations include rejected 30m/Daily signals plus **1H** and explicit session-anchored **4H_RTH** Strat observations. `4H_RTH` is not presented as a native/vendor 4H candle.

## Bankroll / drawdown evaluation

The same ACTIVE V1 evidence is replayed through cash-only long-option account scenarios:

- **$1,500** — initial test bankroll
- **$2,500** — intermediate comparison
- **$5,000** — current maximum allocation ceiling if the demo proves itself

**$5,000 is an allocation ceiling, not an acceptable drawdown.**

The account replay uses ask entry, bid mark-to-market/exit, includes unrealized P&L, treats long options as cash-funded, records trades blocked by insufficient available cash, and excludes counterfactual rows from account P&L.

It measures ending equity, return, realized/unrealized P&L, peak-to-trough drawdown, lowest equity, time underwater, max capital deployed, max planned risk, max open positions, capital-only blocks, and minimum observed starting cash needed to fund every active entry.

Swing positions remain open risk until they close. Overnight metrics include held-over trades, position-nights, max simultaneous overnight positions, worst observed overnight premium gap, and unrealized P&L in the equity curve.

## Diagnostic evidence now collected

PR #541 adds a separate append-only diagnostics table and read-only report. It does **not** change which trades V1 takes.

For each V1 row it records/reports:

- **option MAE/MFE** from the executable bid path relative to ask entry;
- **underlying MAE/MFE** from scheduled underlying snapshots;
- **directional entry extension** beyond the mechanical Strat trigger;
- quote age and maximum observation gap;
- missing Greeks/IV, provider errors, sparse marks, stale quotes, and ambiguity flags;
- ACTIVE vs COUNTERFACTUAL lane identity;
- hold duration and event-risk state when known;
- evidence quality `HIGH / MEDIUM / LOW`.

Entry extension is signed and directional:

- LONG = `entry_underlying - trigger`
- SHORT = `trigger - entry_underlying`

Positive means the recorded entry was already beyond the mechanical trigger.

The diagnostic report also applies analysis-only friction overlays to the same recorded trade:

1. `RECORDED_EXECUTABLE` — ask entry / bid exit, no added fee;
2. `FEE_STRESS_065` — subtract $0.65 per contract per leg;
3. `FEE_065_PLUS_1C_SLIPPAGE` — same fee stress plus $0.01/share adverse slippage on both legs.

The stress scenarios are sensitivity assumptions, not claims about the actual broker fee schedule, and never overwrite canonical V1 outcomes.

Per setup/timeframe/lane summary includes sample count, win rate + 95% Wilson interval, expectancy + approximate 95% interval, profit factor, sequential closed-trade drawdown, friction totals, evidence-quality counts, and median MAE/MFE/entry extension.

Sample labels are descriptive only:

- `<20` priced closed rows: `INSUFFICIENT`
- `20–49`: `EARLY`
- `50+`: `REVIEWABLE`

`REVIEWABLE` is not the same as proven.

Full definitions and CLI: `docs/options-v1-diagnostics.md`.

## Strategy isolation protocol

After enough trustworthy rows exist, isolate every strategy with the same questions:

1. **Signal / timeframe** — does the pattern itself predict direction; 30m vs 1H vs 4H_RTH vs Daily where comparable?
2. **Entry** — are fills too early, too late, or extended beyond trigger?
3. **Stop** — is the 25% premium stop inside normal MAE?
4. **Target / exit** — does MFE show winners are held too long or targets mismatched to timeframe?
5. **Filters** — compare ACTIVE survivors against COUNTERFACTUAL rejected signals.
6. **Execution realism** — ask/bid path, spreads, fee/slippage stress, ambiguity, and account-risk constraints.

Do not tune these dimensions from a handful of observations. Diagnose first; any rule change becomes a separate evidence cohort.

## Lifecycle / execution evidence

The exact selected option contract is re-quoted on the scheduled resolver cadence. Intra-interval path is explicitly treated as unobserved.

If premium stop and underlying target are both true on the same observed snapshot, accounting remains pessimistic but the row is tagged:

- `resolution_ambiguity=AMBIGUOUS`
- `pessimistic_status=LOSS`
- `pessimistic_resolution_used=true`

Later studies can include/exclude that cohort explicitly.

## What is already done — do not redo

- scanner/advisory plumbing and SQLite persistence
- real-chain contract selection
- 2099/far-future expiration sanity protection
- frozen premium-stop and active aggregate-risk accounting
- causal setup proof and fail-closed alerting
- independent Daily multi-setup collection
- counterfactual/filter evidence plumbing
- 1H / 4H_RTH observer cohorts
- active/counterfactual risk and dashboard separation
- snapshot-path ambiguity telemetry
- V1 scheduled runtime preflight
- $1,500 / $2,500 / $5,000 bankroll + drawdown + swing replay
- MAE/MFE + entry extension + friction stress + evidence-quality diagnostics

Missing strategy evidence is now primarily a **collection problem**, not a reason to keep adding strategy rules before the first epoch.

## Smallest safe next step

Use `docs/options-paper-v1-deployment-checklist.md`.

1. Deploy reviewed current `main` through the normal options deployment path.
2. Set the V1 runtime pins, including causal context, $1,000 manager aggregate-risk pin, 14-day minimum DTE, and old companion disabled.
3. Run one market-hours smoke proving active/counterfactual identity, real contract selection/re-quotes, risk separation, ambiguity telemetry, diagnostics snapshots, Discord behavior, and no order path.
4. Run both read-only reports against the smoke DB:
   - `alert_ranker.account_equity`
   - `alert_ranker.v1_diagnostics`
5. Record deployed SHA + smoke timestamp as the V1 evidence epoch.
6. Collect natural candidates without tuning V1.
7. Review strategy decomposition and bankroll/drawdown only after the sample is trustworthy enough.

**No optimization during collection. No proof, no trade.**
