# Options — Next Actions

_As of 2026-09-29. Operational checklist only. The authoritative options status remains `docs/options-current-state-handoff.md`. This file must not be used to redefine strategy status, cohort boundaries, or deployment authority._

## Now — no production mutation

- [x] #1067 code complete: 66-symbol candidate universe + fail-closed capacity preflight.
- [x] #1067 exact-head CI green on `a4de6f986b4e19bca39e161248151f8e139eda0c`.
- [x] #1069 code complete: isolated SPX → SPXW paper lane, 0DTE/1+DTE cohorts, dedupe, resolver, P&L, risk cleanup.
- [x] #1069 exact-head CI green on `5bcfb4dc1ba30ddfd71c746c68e4aefe7354fdef`.
- [x] #1071 code complete: read-only Epoch-3 / filter-reason audit.
- [x] #1071 exact-head CI green and independent diff review APPROVE on `0dca986edbb975e75c7c086c8b53f3361fdc4c4f`.
- [x] Production remains 20 symbols; `OPTIONS_PAPER_V1`; advisory/read-only; SPXW OFF.

## Local / real-provider validation

- [x] **#1071 — Epoch-3 audit COMPLETE**
  - Verified read-only DB copy: 9,734 journal rows; latest `2026-09-29T19:46:18Z`; 337 Epoch-3 rows.
  - ACTIVE: 7 rows; 5 priced closed; 1 winner / 4 losers; 2 open; P&L `-$247.00`; structural target/stop `1/4`; 0 entry-consumed non-outcomes.
  - COUNTERFACTUAL: 330 observations, kept separate from ACTIVE and grouped by exact `counterfactual_filter_reason`.
  - Output preserved at `logs/validation-20260929/options_epoch3_audit.json`.
  - This is a cohort measurement, not proof of expectancy; do not tune V1 from five priced closes.

- [ ] **#1067 — 66-symbol RTH capacity**
  - Run during RTH with real Public + Alpaca SIP/bar-context credentials.
  - PASS only if 66/66 completes inside the existing 5-minute cadence.
  - Require zero critical data failures, 429/rate-limit failures, timeouts, and missing/stale causal-bar failures.
  - Preserve measured runtime and the exact tested SHA.
  - Do not change the production watchlist on a failed or incomplete preflight.

- [ ] **#1069 — SPX/SPXW provider validation**
  - Keep `OPTIONS_SPXW_PAPER_LANE_ENABLED=false`.
  - Prove real SPX snapshot availability.
  - Prove SPXW expirations and option-chain retrieval.
  - Record whether 0DTE and 1+DTE are present.
  - Confirm no SPXW journal write, Discord side effect, broker route, or live execution occurs during provider preflight.
  - Rerun lifecycle tests after provider proof.

## After the three validation gates

- [ ] Review all evidence together; do not infer profitability from code/CI success.
- [ ] Decide whether #1067, #1069, and #1071 are merge-ready.
- [ ] Obtain explicit operator approval before any production deployment, watchlist expansion, or SPXW enablement.
- [ ] If 20 → 66 is activated, record the new evidence-cohort boundary only after the first clean RTH cycle; do not pre-write `V1-EPOCH-4`.
- [ ] If SPXW is enabled, keep its evidence separate from equity `OPTIONS_PAPER_V1`; preserve 0DTE and 1+DTE as separate cohorts.
- [ ] Update `docs/options-current-state-handoff.md` with exact runtime/release proof after activation.

## Cleanup / non-blocking

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
