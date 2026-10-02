# Options — Next Actions

_As of 2026-10-02. Operational checklist only. The authoritative options status remains `docs/options-current-state-handoff.md`. This file must not be used to redefine strategy status, cohort boundaries, or deployment authority._

## Now — no production mutation

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

## Forward floor-activation outcome draft — merged as DRAFT, not run

- [x] No untouched retrospective holdout. Sessions through 2026-10-02 are ineligible.
- [x] DRAFT prereg `docs/prereg-options-212c-floor-outcome-2026-10-02.md` and spec `E-2026-10-02-options-212c-floor-outcome-01` (`status=DRAFT`). Ledger event `PLANNED`.
- [x] Independent re-review approved PR #1115 for merge as DRAFT registration only. Merge `65847295521be1ff0d6b9ef89c8fb8699aff7735`, approved head `4631812866cbbaff2077d3dece92cb8b69eae250`.
- [ ] Do not start this trial's 2026-10-05 collection. First implement and independently validate the prospective seal-capture path against the frozen contract.
- [ ] Stage B stays **NOT EVALUATED** until a causal historical chain source exists. Do not backfill current quotes.

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
