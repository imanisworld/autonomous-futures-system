# Capital-unconstrained strategy research — handoff (2026-10-11)

**Status: MIXED-PROVENANCE RESEARCH RECORD / SOURCE ONLY.** This page preserves the October 11 research report while separating repository-backed evidence from reported-only figures. The committed 5-trade NR7 diagnostic and Keltner result can be inspected on `main`; the Donchian figures and separate 4-trade one-position NR7 figures have no committed result artifact on current `main` and therefore remain **reported-only, not repository evidence**. The raw MNQ corpus was not rerun here. No strategy is promoted, and nothing in this record authorizes an order, risk-cap change, merge, deploy, or forward-outcome read.

## Decision and exact scope

Remove the historical **$1,500 account-size assumption from exploratory strategy screening only**. First measure a strategy's expectancy, execution feasibility, stability, and concentration; afterward calculate required capital with explicit gap/slippage and margin caveats. Do **not** change the real book's `120`-tick MNQ cap, its R:R floor, the DEMO's `300`-tick cap, or the isolated forward paper trial's `400`-tick stop cell.

Research MNQ history is limited to **pre-2026-06-29** observations. This record neither uses nor scores sealed forward results. Track B reported running `94a71bd` is left unchanged (runtime independently unverified).

## Candidate closure and provenance

| Candidate | Sample | Net / PF | Repository evidence status | Interpretation |
|---|---:|---:|---|---|
| Donchian daily 20/10 | 14 trades | +$767.28 / 1.044 | **REPORTED ONLY** — no committed result artifact found on current `main` | Reported reject: second half −$2,362.86, historical trade-equity DD $10,490.88, winner concentration and material underperformance vs B&H. Do not use as ledger/Inventory evidence without preserved provenance. |
| ChartArt NR7 + inside day (one-position executable) | 4 trades | −$1,072.42 / 0.065 | **REPORTED ONLY** — no committed 4-trade result artifact found on current `main` | Reported deprioritize: tiny sample, both halves negative. Keep separate from the canonical 5-trade diagnostic. |
| ChartArt NR7 + inside day (independent-signals diagnostic) | 5 trades | −$1,139.90 / 0.061 | **COMMITTED / RESEARCH_ONLY** — `nr7-five-valid-independent.json` + canonical ledger result | Exploratory diagnostic only. Its embedded positioning labels are internally inconsistent; do not treat it as the one-position executable result. |
| Vault Keltner *long reversal only* (60m RTH) | 61 trades | −$2,022.44 / 0.863 | **COMMITTED / RESEARCH_ONLY** — `keltner-kc-long-mnq.json` + canonical ledger result | Reject this frozen leg: both halves negative; DD $4,474.24. Other Keltner components were not tested. |

NR7 rules reported: strict NR7 among previous six days, inside day, bullish close, rising SMA14; next RTH open entry; stop at signal-day low; confirmed bullish-session exit at next RTH open; 15m stop-first and costs. Seven raw signals, two invalid stop-geometry cases. Reported maximum structural stop **$600.50** and maximum MAE **$989.50** need trade-by-trade attribution before anyone interprets exposure; they are not evidence of a capped worst possible loss.

Keltner rules reported: 20 EMA and 1.5 ATR channel, long reversal below lower band, middle-band exit, 1.5 ATR stop and 3 ATR target; causal next-bar execution. Other Keltner components were **not tested** and cannot inherit this verdict.

**Reproducibility boundary:** Current `main` contains the 5-trade NR7/Keltner scripts and preserved JSON outputs, so those bytes can be inspected without rerunning the studies. The canonical ledger result uses the **5-trade independent-per-signal NR7 diagnostic**; that file's embedded prose also says "non-overlapping", which is internally inconsistent with `position_model: independent_per_signal`. The separate 4-trade one-position result is reported in this handoff but has no committed result artifact on current `main`. Therefore the 5-trade result remains **independent-signals diagnostic**, while the 4-trade figure remains reported-only. Do not rewrite the preserved 5-trade artifact or its ledger hash.

**Repository chronology correction (append-only ledger preserved):** three October 11 ledger `recorded_at` values do not match the GitHub UTC commit times that actually added them. The six-micro `RUNNING` row says `2026-10-11T18:30:00Z`, while commit `e510dc44` was created at **2026-10-11T14:10:27Z**. NR7/Keltner `PLANNED` says `19:05:00Z`, while commit `75015af6` was created at **15:04:04Z**; `COMPLETED` says `19:10:00Z`, while commit `a10668e4` was created at **15:04:20Z**. The ledger is append-only, so those historical bytes are not rewritten. For repository chronology, use the GitHub commit times; the ledger timestamps are retained only as originally recorded metadata.

## Existing in-repo prospects (from canonical inventory / prereg)

- **MNQ 4HR re-trigger** remains the strongest *historical* lead, **PROMISING BUT UNPROVEN**. The preregistered 400-tick/$200 forward paper cell's consumed benchmark reports 36 trades, +$3,076.72, PF 3.125, both historical halves positive, and **66.8%** of profit from its top three months. Real 1m pre-armed signals and 5m IOC model are distinct; parity and forward proof are pending. Forward eligible start: **2026-10-12**; prescribed first look at 40 qualifying fills or the trial's timeout. See `docs/prereg-4hr-mnq-400-forward-2026-10-09.md`.
- **60M 3-2-2 First Live** corrected historical prearmed cell: 32 resolved, all winners, +$2,471.64 at 3 ticks; **0 prospective natural-1m arms/touches**. Unusually perfect historical resolves are a risk flag, not evidence of certainty. Existing separate collection rules stand.
- **12HR Miyagi**: MNQ n=8, +$425.33, PF ~2.32 historically; sample too small, research detector only, not executable.
- **ORB/VWAP, MES 4HR, failed-breakdown and earlier rejected sweeps**: remain rejected on weak actual economics and/or execution. Lifting a research capital assumption does not overturn those verdicts.

## Polygon/Massive subscription boundary — October 18, 2026

**Operator constraint:** Existing Polygon/Massive subscription access is planned to end **2026-10-18**. **Do not recommend renewing it, adding a new provider, or paying to run the optional futures-carry experiment.** This handoff does not change or reverse the user's planned cancellation.

**Existing active dependency identified from committed code:** `research/six_micro_daily_tsmom_collect.py` imports `PolygonFuturesClient` and calls `fetch_bars` on every scheduled collection. `docs/agent-work-state.md` reports the six-micro daily timer installed and enabled on the VPS on 2026-10-11. Its continuation **after access expires** is unverified and should be treated as **DATA BLOCKED unless a proven, cost-neutral replacement is arranged**. Do not claim that the six-micro collector is provider-independent. Do not alter or stop the collector without separate operator approval. The primary 4HR Track B path is separate from this Polygon-based collector.

Other historical Polygon readers (for example `research/mgc_4h_wide_forward.py`) must be evaluated individually if used later; any blinded or sealed prospective trial must retain its strict evidence and first-look rules. **Do not inspect blinded results, purchase data, change datasets in place, or retroactively rewrite the experimental population**.

For now the futures-carry proposal is **DEFERRED**. Do not begin MCL history downloads or a carry backtest as justification to retain Polygon.

## Next distinct direction — futures curve/carry: data gate before outcomes

**Proposed hypothesis, not a completed test:** commodity futures front-vs-deferred term structure may forecast subsequent risk-adjusted returns. This is different from daily-bar pattern and indicator families; it is not inherently profitable, and seasonal commodity curves demand special handling.

The repository already offers `sources/polygon_client.py::PolygonFuturesClient.fetch_bars(ticker, start, end)`, which fetches **specific dated contracts**. Existing `docs/cross-instrument-portability-phase2.md` explicitly records that `MGC/MCL/MBT` *continuous* roll schedules are unavailable and that existing continuous bars cannot certify contract-month provenance. `docs/mcl-corrected-roll-rerun-2026-09-23.md` documents MCL-specific historical expiry/roll research; it does not itself establish a dated **front and deferred pair on each decision date**.

**A legitimate first experiment is blocked until all these are verified using existing subscriptions/data:**
1. For **MCL**, obtain genuinely simultaneous, timestamp-aligned near and next-maturity **dated-contract** prices over a meaningful predeclared historical interval (and verify dates, expiry, completeness, liquidity).
2. Ensure the front/next curves are known **at signal time**; no back-adjusted continuous series may be substituted for either curve leg.
3. Freeze one economically defined signal, holding/roll method, commissions/spread/slippage, risk exposure, and chronological evaluation design **before** outcome inspection.
4. Keep this independent of the MNQ seal and any protected experimental windows. No additional paid data provider or API spend is approved.

**Present disposition: DATA ELIGIBILITY UNVERIFIED / DO NOT RUN.** A provider method that can request dated bars is not proof the account has the complete historical pairs. CME publishes daily settlements, but bulk historical data/licensing and actual dated-contract coverage must be verified rather than assumed.

## Next actions

1. No additional source-preservation PR is needed: the NR7/Keltner scripts and JSON outputs are already on `main`. Any later QA should verify source/result linkage and hashes without rerunning or resampling protected dates.
2. **Before the October 18 Polygon expiration:** make a read-only inventory of actual on-disk Polygon datasets and the scheduled six-micro collector's dependency. Identify whether historical data already covers the required dates and whether a genuinely cost-neutral replacement is available; do not change the installed timer, deploy, or access sealed outcomes without separate operator approval.
3. **Defer futures-carry preflight** rather than initiate fresh Polygon usage. If Polygon-dependent collection cannot continue after expiration, report the expected loss of future six-micro coverage explicitly and seek an operator decision to pause that distinct trial. Preserve the primary 4HR forward path and its registered rules.

**No extra cost, no capital transfer, no trading, no config alteration, and no deployment implied by this PR.**
