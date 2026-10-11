# Options edge discovery — 2026-10-11 closeout

**Verdict: NO PROVEN OPTIONS EDGE. NO STRATEGY APPROVED FOR OPTION TRADING.** This closes the 66-symbol, 5-minute intraday discovery round. It does not open another test, tune any thresholds, approve execution, or change the existing 1-2-2 collector.

## Evidence and scope

**Operator-supplied Cursor results; not independently re-executed by this documentation update.** The quoted data are from the local/gitignored `data/options_research_polygon_5m_20260401_20260908/` Polygon OHLC files and the local result `docs/research-notes/options-polygon-edge-2026-10-11/results.json` (not found on GitHub `main` at this update). Reported validation: 66 symbols, 110 NYSE sessions from 2026-04-01 through 2026-09-08, manifest hashes matched, zero bars on/after 2026-09-09. Sealed September 9–15 data were excluded. This is **underlying-price research**, not option-fill P&L, and the April–September history has already been examined (including an Alpaca screen). The late-August slice is **not an untouched holdout**.

| Family | Operator verdict | Setups taken / population | Mean R per setup | Same-stop prior-entry baseline | 2026-08-14–09-08 slice |
|---|---|---:|---:|---:|---:|
| Higher-timeframe pullback | HOLD RESEARCH / no promotion or retuning | 1,029 / 7,171 | +0.003R | −0.205R | −0.011R |
| Failed-break reclaim | REJECT this formulation | 6,950 / 11,850 | −0.016R | −0.140R | −0.013R |
| Compression then expansion | HOLD RESEARCH / no promotion or retuning | 2,659 / 7,260 | +0.001R | −0.045R | −0.043R |

Taken-trade mean R: +0.024R, −0.027R, +0.003R respectively. These tiny or negative pre-option results, negative later-period slices for the two holds, and roughly half-positive / half-negative symbols justify **no options-profitability trial** for any of these families. The baseline's prior-bar entry loses money; outperforming it mainly by skipping trades is **not proof of positive net expectancy**. No claim of statistically independent validation or positive options P&L follows.

**Existing SIP research, separate population:** PR #1222 (`docs/research-notes/options-underlying-edge-2026-10-11/`) preserved an Alpaca SIP screen with different scoring units/risk geometry and an earlier operator disposition. Do not merge its conditional 1R numbers with these Polygon 5-minute results or let the earlier operator label override this later round-closure decision. Preserve both as distinct historical evidence.

## 1-2-2 — separate passive forward measurement

Operator-supplied `122-IEX-E1` journal: 1,504 rows, 443 resolutions and **443 completed separate reconciliation records**, despite original append-only resolution rows retaining literal `PENDING_DELAYED_SIP`. Reconciliation categories: 135 confirmed same reversal; 306 no provisional (221 continuation-first, 85 no break); 2 negative-latency/inconsistent. **101** reversal option *entry snapshots*: 99 SIP-confirmed, 2 inconsistent. **Zero recorded exit bids and zero option P&L**. All 101 had reported V1 liquidity/contract-quality eligibility, but only 58/101 met a modeled $300 planned-loss cap under the 25%-premium-stop *entry-cost identity*; 43 did not. This is **not a realized trade result**.

The existing collector was reported unchanged. Historical 101 entry snapshots remain **unscored**: do not backfill a stop, time exit, target, or option bid. Any proposed future bid-path/exit measurement must freeze **actionable post-confirmation entry** and exit rules before new captures, preserve chronology, and receive separate operator approval before implementation. No forward order/strategy authority.

## Other options research — closed or observational

- Existing `OPTIONS_PAPER_V1` paper cohort: operator-supplied **26 closed ACTIVE, net −$1,536 ask-entry/bid-exit excluding commissions**. Do not confuse structural WIN with dollar gains or mix ACTIVE with COUNTERFACTUAL.
- Signa v2 did collect. 17 of the 26 closed ACTIVE rows had eligible pre-entry v2 signal data; a predeclared aligned/A–B/confidence>=70 filter kept **one −$265 losing trade** and excluded the four dollar winners within the matched 17. Too small for a universal claim. **No Signa trade gate**; observational context only.
- 2-1-2 reversal first-sight prospective excess did not persist; 2-1-2 continuation geometry was sealed/coverage-only; daily 2-2-2 and outside-bar forms were closed; scanner timing variants v1–v3 retired. Do not rerun without a distinct, pre-registered question.

## Next research question — not started or approved

**Open hypothesis:** Can a *multi-day / multi-week equity directional-persistence* entry create enough **net long-option payoff**, after executable quotes, spread, carry/decay, IV changes, and exit costs, to justify a trade?

This has **no completed options test** and **no approved experiment or collection change**. Do not silently reuse the separate futures six-micro daily time-series momentum rule (60-session signal held until flip/20 sessions; scheduled forward scoring from 2026-10-12). The futures rule is a different instrument, population, and execution model. Previously failed equity-index overnight/20-day filter and month-end/cross-market/VIX tests remain historical exclusions.

The present 110-session options OHLC window is reported too short to resolve the multi-day or multi-week question with appropriately independent signal warmup and untouched validation; check independent data and prior registry before **proposing** a bounded preregistration. Do not create a backtest, collector, or data purchase under this documentation change.

**Decision:** Stop tuning the closed intraday families. Allow existing 1-2-2 observation to continue unchanged. No new options trade, options test, runtime mutation, deployment, or live/paper broker order is approved.

## Source-status discipline

This closeout records the user's 2026-10-11 Cursor research results, which were pasted into the conversation. The local canvas and gitignored price archive were not available to the GitHub connector for independent measurement or hashing. Until those underlying artifacts are independently available, label detailed numbers **operator-supplied**; this document does not assert an independent reproduction.
