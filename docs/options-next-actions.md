# Options — Next Actions

_Priorities refreshed 2026-10-08; older checked items below are dated history, not fresh runtime proof. Operational checklist only. The authoritative options status remains `docs/options-current-state-handoff.md`. This file does not authorize strategy, cohort, collection, deployment or trading changes._

## Current focus — prove or reject the trading edge (2026-10-08)

**Main question:** Do current-cohort options setups have repeatable positive *option* P&L after executable entry/exit pricing and costs? **NOT PROVEN.** Do not equate successful code/CI, more signals or structural target hits with profitability.

- [ ] **1.** **Reconcile real paper closes first — #1071.** Through an *already authorized read-only path*, obtain a consistent provenance/hash-checked snapshot of `options_scanner.sqlite`, the 2026-10-07 EOD artifact and matching diagnostic/option-mark data. Confirm the effective scanner release, `OPTIONS_PAPER_V1` epoch start AND first shadow id, timestamps, ACTIVE lane, OPEN count, closed option P&L, and missing/unpriced results. Compare the operator-supplied 25 closed / -$1,716 lifetime snapshot with the raw ledger; do **not** assert it is current-epoch expectancy. **WAITING ON ACTUAL PAPER LEDGER/EOD DATA, not another general VPS audit; no root escalation or database writes.**
- [ ] **2.** **Explain losses using tools already in the repo.** After accounting passes, use `alert_ranker/v1_diagnostics.py` and contract-mark records for row-level setup, first-sight/trigger lag, remaining target vs stop, bid/ask spread, DTE, quote freshness, theta and pre-entry SPY/QQQ/GEX context when actually recorded. Distinguish structural WIN from positive `pnl_dollars`; use ASK-in/BID-out and label missing commissions/marks. Compare pre-entry conditions only, with missing fields marked UNKNOWN. Do not invent backfilled prices or completed trades.
- [ ] **3.** **Separate cohorts and define the next research question BEFORE scoring.** Keep ACTIVE actual paper results separate from COUNTERFACTUAL/filtered what-ifs and previous epochs. Preserve the already-closed 59-episode one-look (coverage gain only). Any new discriminating factor or floor-outcome test requires preregistration, a genuinely fresh untouched forward population, and an explicit operator approval; the `-02` floor trial remains DRAFT / NOT RUN with eligible start UNSET.
- [x] **4.** **#1177 Part 2 / PR #1198 — SOURCE MERGED** into `main` as `307fe56771031b44eeb8d0235224cf010616ce4a` (2026-10-08). Dedicated E1 canonical adapter and tests are present **in source only**. This does NOT prove an installed collector, raw tape/journal integrity on VPS, approved quote-age provenance, fitness readiness or a new evidence Day 1. Do not redo the merged implementation. #1154 remains parked pending the remaining source-to-runtime/producer/reconciliation gates.
- [ ] **5.** **Reuse #1071 carefully; do not rebuild it.** The epoch-P&L report and tests are in an old **OPEN / UNMERGED** PR #1071 (`0dca986`), not on current `main`; its Sept. 29 audit is a historical checkpoint, not proof of Oct. 8 data. Reconcile its diff against current source, get exact-head CI and independent review and separate operator merge GO before adopting. Never run historical scoring as a substitute for new forward evidence.
- [ ] **6.** **Cleanup after research/readiness, not instead of it.** Classify redundant or unused options utilities and instructions using actual callers and evidence preservation; #1167 remains audit-only. No speculative deletions or system changes.

**Parked safety work:** #1184 issue is CLOSED; source-only draft #1199 remains **UNMERGED**. It helps detect fake/replayed permission-to-trade records, but independently protected authority storage and verified human approvers do not exist in the proven runtime record. Do not add runtime approval wiring, order tickets or live execution. Further work waits until a real authority use case is explicitly approved.

**No mutation:** no collector repin, installation, journal rewrite, VPS deploy/restart, broker action, trial unsealing or new entry. OPTIONS HOLD · FUTURES HOLD.

## Now — no production mutation

### Setup-capture observer (late first-sight + SPX observation)

- [x] Source on `cursor/options-setup-capture-observer-0010`: oneshot JSONL collector, pre-open two-sided pending 2-2 WATCHING, SIP-lag, `MISSED_LATE`, `GAP_THROUGH_OPEN`, full-hour + stub watch, SPX INDEX 1m. Not scanner-embedded.
- [x] Oct. 5 SPY 9925/9933 documented as **not** a prospective catch. Structure key links both. Do not rewrite that history.
- [ ] Independent options diff review of the capture PR.
- [ ] Exact-head CI green.
- [ ] Operator GO required before merge.
- [ ] Separate operator GO required before installing `options-setup-capture.timer` (observer-only). Do not change live-trading/execution flags.
- [ ] Out of scope: webhook/alert ticker allowlist; confirm nginx auth on `/shadow-journal` after outside-IP 200s (2026-10-06).


### 2-1-2 floor-outcome forward study

- [x] #1115 merged as `65847295521be1ff0d6b9ef89c8fb8699aff7735`: original `-01` registration **DRAFT / PLANNED / NOT RUN**.
- [x] Frozen stopping monitor is hash-bound to sealed session snapshots, consecutive from the registered eligible start, with fail-closed identity/gate/session checks.
- [x] Independent review of the synthetic-only seal/scorer machinery (2026-10-04): contract matched; two seal fail-closed gaps found (`request_end < close` accepted; off-grid bars silently dropped).
- [x] Operator rulings 2026-10-05: broader floor-eligible companion; re-register `-02` to supersede `-01`; path-v0.2; refuse short `request_end` and off-grid bars; collector-side blind-window quarantine. **Not approval to collect or score.**
- [x] #1133 activation-only companion `E-2026-10-04-options-212c-preentry-factors-01` is **SUPERSEDED / NOT_RUN** (gate factors constant on activations).
- [ ] Merge the isolated preparation branch after CI/handoff are green. Do not deploy the collector change in the same step as merge unless the operator explicitly GOs a coverage-release deploy.
- [ ] Do **not** admit 2026-10-05. Eligible start stays UNSET until the approved capture path is on `main` **and deployed**. Never backfill.
- [ ] After merge + independent review, obtain a separate operator GO before adding provider fetch / real seal writer / timer integration.
- [ ] One-look scoring remains blocked until the stop condition fires and the scorer/metric adapter is separately approved. The adapter must verify manifest hashes and enforce `INSUFFICIENT SAMPLE` / `DESCRIPTIVE MEASUREMENT`. Stage B option fills remain **NOT EVALUATED**.


- [x] #1067 code complete: 66-symbol candidate universe + fail-closed capacity preflight.
- [x] #1067 exact-head CI green on `a4de6f986b4e19bca39e161248151f8e139eda0c`.
- [x] #1069 code complete: isolated SPX → SPXW paper lane, 0DTE/1+DTE cohorts, dedupe, resolver, P&L, risk cleanup.
- [x] #1069 exact-head CI green on `d7a546c789c6ff6e23713357feb2228442211965`.
- [x] #1071 code complete: read-only Epoch-3 / filter-reason audit.
- [x] #1071 exact-head CI green and independent diff review APPROVE on `0dca986edbb975e75c7c086c8b53f3361fdc4c4f`.
- [x] Production remains 20 symbols; `OPTIONS_PAPER_V1`; advisory/read-only; SPXW OFF.
- [x] #1077 merged as `8c4e2e472bd7926b46bcb83af1c7625117d3e769`; display-only Signa-v2 surfaces, no strategy/risk/order behavior change.
- [x] #1077 is **not deployed** to the options scanner. Its deploy remains a separate operator GO and is not required for #1067/#1069 evidence gates.

## Local / real-provider validation

- [x] **#1071 — Epoch-3 audit COMPLETE**
  - Verified read-only DB copy: 9,734 journal rows; latest `2026-09-29T19:46:18Z`; 337 Epoch-3 rows.
  - ACTIVE: 7 rows; 5 priced closed; 1 winner / 4 losers; 2 open; P&L `-$247.00`; structural target/stop `1/4`; 0 entry-consumed non-outcomes.
  - COUNTERFACTUAL: 330 observations, kept separate from ACTIVE and grouped by exact `counterfactual_filter_reason`.
  - Output preserved at `logs/validation-20260929/options_epoch3_audit.json`.
  - This is a cohort measurement, not proof of expectancy; do not tune V1 from five priced closes.

- [x] **#1067 — 66-symbol RTH capacity = PROVEN / PASS**
  - Exact head `fd2809061961578f3b280eff3ab664d710099d03`; exact-head CI green.
  - Real-provider RTH result: 66/66 in 136.834056s of 300s.
  - Critical data failures, 429/rate-limit failures, timeouts, and missing/stale causal-bar failures: 0.
  - Production watchlist stayed 20. No deploy. `storage_writes=0`. `alerts_sent=0`.
  - This PASS does not authorize merge, deploy, or a 66-symbol production expansion.

- [x] **#1069 — SPXW 0DTE provider gate = PROVEN / PASS; lane still OFF**
  - Exact head: `d7a546c789c6ff6e23713357feb2228442211965`; CI green; independent diff review found no blocking issue.
  - Root cause fixed: SPX index data uses `SPX` / `INDEX`; SPXW expirations/chains are requested from the SPX index chain and filtered to OCC root `SPXW`; equity requests stay `EQUITY`.
  - Real provider preflight PASS: SPX price present; 40 eligible expirations; tested chain 599 calls / 599 puts; 1+DTE present.
  - Lifecycle regression: 27/27 passed.
  - Lane stayed OFF; no journal, Discord, broker, deploy, or live-order side effects.
  - Refreshed RTH SPXW proof on exact head `a265fe680b9b7e78d321a4a35f931bc4199aa22e` = **PROVEN / PASS**: `has_0dte=true`, `has_1_plus=true`, 0DTE chain 2026-10-02 with 301 calls / 301 puts. Exact-head CI green.
  - Lane remained OFF. No deploy, journal, or Discord side effects.
  - This PASS does not authorize merge, deploy, or SPXW enablement.

## After the proven RTH checks

- [x] #1067 66-symbol capacity PASS on `fd2809061961578f3b280eff3ab664d710099d03`. Not deployed.
- [x] SPXW 0DTE appeared during RTH on `a265fe680b9b7e78d321a4a35f931bc4199aa22e` with the lane OFF.
- [ ] Review all evidence together; do not infer profitability from code/CI success.
- [ ] Decide merge/deploy posture for #1067/#1069 and close out #1071.
- [ ] Obtain explicit operator approval before any production deployment, watchlist expansion, or SPXW enablement.
- [ ] If 20 → 66 is activated, record the new evidence-cohort boundary only after the first clean RTH cycle; do not pre-write `V1-EPOCH-4`.
- [ ] If SPXW is enabled, keep its evidence separate from equity `OPTIONS_PAPER_V1`; preserve 0DTE and 1+DTE as separate cohorts.
- [ ] Update `docs/options-current-state-handoff.md` with exact runtime/release proof after activation.

## Frozen 59-episode experiment — one look preserved; closed after this preservation merges

- [x] #1111 merged as `d238ea4aee2f2e9f7dae0b09e90cbb65f1e4070c`. The spec on `main` is `APPROVED` by Operator at `2026-10-02T16:05:00Z`.
- [x] The single one-look ran once. Result: **SUPPORTED BY THIS EXPERIMENT / coverage only / not edge.** `floor_ge1r` 2 activations versus `nearest_v1` 0 on the frozen 59. Disposition `RESEARCH_ONLY`. Do not rerun.
- [x] Evidence bytes preserved. `runner_report.json` SHA-256 `0d47bf46fd62748e9e6b67a248d2ef6ef76aad072e6ad1d2fd43192f7f3343e8`. That digest is the manifest `results_sha256`.
- [x] The measurement is coverage/activation only. It does not establish profitable trades, expectancy, strategy promotion, deployment, or execution authority.

## Forward floor-activation outcome draft — not approved, not run

- [x] No untouched retrospective holdout. Sessions through 2026-10-05 are ineligible and are never backfilled.
- [x] `-01` (`E-2026-10-02-options-212c-floor-outcome-01` / `T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01`) is **SUPERSEDED / NOT_RUN**. It was never approved, collected, or scored.
- [x] `#1133` `E-2026-10-04-options-212c-preentry-factors-01` is **SUPERSEDED / NOT_RUN** (activation-only).
- [x] Successor DRAFT prereg `docs/prereg-options-212c-floor-outcome-2026-10-04.md` and spec `E-2026-10-04-options-212c-floor-outcome-02` (`status=DRAFT`). Ledger event `PLANNED`. Eligible start UNSET.
- [x] Companion DRAFT `E-2026-10-04-options-212c-floor-factor-01` over the floor-eligible population (`MARKET_ALIGNMENT_REJECTED` + `WOULD_OTHERWISE_QUALIFY`). Descriptive / hypothesis-generating only.
- [ ] Do not approve `-02` or the companion, do not write a real seal, and do not collect/score until the capture path is on `main` and deployed.
- [ ] Stage B stays **NOT EVALUATED** until a causal historical chain source exists. Do not backfill current quotes.

## Deferred — approval security (not today's priority)

- [ ] **#1184 CLOSED / draft #1199 OPEN — PARK further infrastructure.** Offline defensive tests/source exist in draft #1199; full CI previously passed, but source merge still requires independent Grok review and explicit operator GO. Revisit protected storage and approved human identities only when real trade-approval persistence or use is proposed. Nothing is deployed or authorized to trade.

## Cleanup / non-blocking

- [ ] Decide whether/when to deploy #1077's display-only Signa-v2 surfaces. Separate operator GO; do not combine it with #1067 universe expansion or SPXW enablement.
- [ ] Resolve the options-scanner memory-cap decision recorded in `docs/agent-work-state.md` / `docs/futures-operator-todo.md`. Operational capacity only; not permission to alter strategy rules.

- [ ] Decide old draft #1026: **keep/install later** or **close as obsolete**. It does not block current validation.
- [ ] After the active PRs are merged/closed, delete only branches proven safely contained or intentionally obsolete; preserve archive/research/release branches unless explicitly reviewed.
- [ ] Do not reopen completed target-geometry, timing, 1-2-2 repair, or other historical studies merely for cleanup.

## Evidence-collection phase

Once the validation/activation gates are complete:

- [ ] Stop changing strategy rules without a new pre-registered question.
- [ ] Continue natural paper collection.
- [ ] Judge `OPTIONS_PAPER_V1` using clean current-cohort evidence, not blended historical totals.
- [ ] Judge SPXW 0DTE and 1+DTE separately.
- [ ] Wait for enough independent observations before changing filters or contract policy.

**Stop rule:** if a required provider/runtime proof is missing or fails, stop at that gate. No proof, no activation.
