# Pre-Registered Analysis Plan — Context-as-Permission-Layer Study

**Version:** 1.0 (2026-07-16)
**Status:** RESEARCH DOCUMENT ONLY. No runtime behavior, collectors, gates, strategy
permissions, fill models, stops, runners, session policy, broker routing, or deployment
state changes accompany this document or may be justified by it directly.
**Governing directive:** operator research directive of 2026-07-16 — prove whether market
context is the primary permission layer across strategies before proposing any gate.

This plan is written **before** the outcome-joined sample exists. Its purpose is to make
the analysis falsifiable: hypotheses, tests, thresholds, and kill criteria are fixed here,
in advance, so the study cannot become an after-the-fact explanation engine.

---

## 1. Primary objective

Determine whether market-context features measured **at signal time** discriminate between
candidate trades with positive and negative **net expectancy after realistic costs**, with
**controlled drawdown**, consistently **across strategy families** (ORB, VWAP, Strat) — such
that a shared context permission layer would improve all strategies simultaneously rather
than tuning any single one.

- **Expectancy unit:** R (risk-normalized: PnL ÷ initial stop distance in $), reported
  alongside $/trade. R-normalization allows MNQ/MES pooling.
- **Cost model:** the honest-cost convention ratified in the PR #287 strat tranche-2 review
  (per-side commission + modeled adverse slippage, fills at honest IOC/market prices — no
  legacy optimistic fill model). The same cost model is applied to every provenance stratum.
- **Drawdown control:** for any proposed gate, the equity curve of the "allowed" subset must
  have max drawdown ≤ the ungated baseline's max drawdown over the same period, and no
  single trade may account for >30% of the subset's total net PnL.

## 2. Data sources and join specification

| Source | File / field | Role |
|---|---|---|
| Location collector (bar-level) | journal rows → `context.location_context` (levels pdh/pdl/prev_open/prev_close/onh/onl/pmh/pml; zones 1h/4h relation + freshness; middle_of_range; nearest_key_level; impulse; other_instrument regime; regime_agreement; mtr_15m_points) | features |
| Candidate location blocks | `candidate_audit[].location`, `shadow_candidates[].location` (alignment per TF + overall; opposing zone; room_to_opposing_points; target_blocked_by_opposing_zone) | features |
| Strategy-context observer | `logs/strategy_context_observations.jsonl` (trend_persistence, mnq_mes_agreement, overnight_range_location, supply_demand_confluence, key_level_confluence, impulse_state, gex, structural_regime) | features (secondary source; on conflict the location collector wins and the conflict is counted in §5) |
| Offline persistence helper | `regime_persistence()` over bar history (+15/+30/+60 min) | feature F8 (offline-only; never available at signal time to the runner — analysis-layer only) |
| Outcomes | SHADOW_OUTCOME rows (causal resolver), PaperBroker paper trades, Tradovate demo fills (journal `TRADE` = confirmed execution per PR #254 semantics), morning-hold `SHADOW_NO_ORDER` suppressions resolved as shadow counterfactuals | labels |

**Join key:** (instrument, strategy family, candidate/journal timestamp). One joined row =
one candidate with (a) a complete context snapshot at signal time and (b) a resolved
outcome. Rows failing either side are counted, not silently dropped (§5).

**MAE convention (binding, per operator ruling 2026-07-16):** all aggregates report BOTH
`raw_bar_mae` and `execution_capped_mae` (STOP_HIT MAE capped at stop distance + modeled
adverse slippage). Analysis-layer only; no collector rows are rewritten.

## 3. Outcome-provenance stratification (binding)

Every statistic in this study is computed **per stratum** and pooled only with stratum
labels attached:

- **S1 — shadow-resolved** (modeled fills via the causal shadow resolver)
- **S2 — PaperBroker** (simulated fills at live prices; known ~6.5-pt detachment precedent)
- **S3 — Tradovate demo** (real fills, real broker lifecycle)

**Rule (operator-required): no feature may become a gate based only on shadow outcomes
(S1).** Gate candidacy requires consistent sign in ≥2 strata, at least one of which is S2
or S3. Precedent: the 622-day backtest edge was a fill-model artifact; a context feature
that "works" only in modeled fills is a conclusion about the simulator.

## 4. Purpose of the first 50–100 joined observations (binding scope limit)

The first 50–100 joined observations are for:

1. **Data-quality validation** — field completeness, value sanity, timestamp integrity.
2. **Join validation** — what fraction of candidates successfully join to outcomes; where
   and why joins fail.
3. **Hypothesis feasibility checks** — do the pre-committed test cells actually populate?
4. **Preliminary effect-size estimates** — direction and rough magnitude only.

**They are not sufficient by themselves to authorize a hard gate.** No gate proposal may
cite the first-review sample alone as confirmatory evidence.

## 5. Data-quality and join-validation gates (must pass before any hypothesis test)

- **Join integrity ≥90%:** ≥90% of resolved candidates must join to a complete context
  snapshot. Below 90%: fix at the analysis layer if possible; if the root cause requires a
  mid-sample collector change, see kill criterion K2.
- **Field completeness:** per-feature availability is tabulated. A feature with <60%
  availability in the joined sample is NOT_TESTABLE at that review (recorded, not dropped
  from the plan).
- **Cross-source consistency:** where the location collector and the observer measure the
  same construct (overnight location, impulse, pair agreement), disagreement rate is
  reported; >10% disagreement on any construct blocks tests using that construct until
  explained.
- **Duplicate detection:** duplicate candidate rows (same key) must be 0 after
  deduplication rules are documented.

## 6. Missing-bar and feed-gap treatment (binding)

Feed outages are **not random** — they plausibly correlate with volatility (2026-07-16
TradingView outage precedent). Therefore:

- A **gap ledger** is built offline from bar history (expected 15m grid vs present bars)
  for the full collection period. Gaps are first-class rows in the evidence, not absences.
- Any joined observation whose **outcome-resolution window overlaps a feed gap** is flagged
  `feed_gap_contaminated` and excluded from confirmatory tests (counted and reported).
- Any observation whose **context lookback** (zones, overnight range, persistence windows)
  overlaps a gap gets `context_gap_contaminated`; excluded from tests touching the affected
  features.
- The review reports total gap minutes, gap count, and the fraction of observations
  excluded. If >15% of observations are gap-contaminated, that is itself a headline finding
  (data pipeline before data conclusions).

## 7. Pre-committed features (12 univariate tests)

Session is deliberately **not** a feature — it is a control (§9). Each feature below is
exactly one confirmatory test, with its binary/categorical contrast fixed now.

| ID | Feature | Source field | Confirmatory contrast |
|---|---|---|---|
| F1 | Middle of range | `location_context.middle_of_range` | middle=True vs False |
| F2 | 1H zone relation | `zones.1h.relation` | inside/approaching a zone vs middle |
| F3 | 4H zone relation | `zones.4h.relation` | inside/approaching a zone vs middle |
| F4 | 4H zone freshness | nearest 4H zone tests count | fresh (0 tests) vs tested (≥1) |
| F5 | Key-level proximity | `nearest_key_level` distance | ≤0.5×MTR ("at level") vs beyond |
| F6 | Overnight range location | overnight H/L vs close | inside range vs at/beyond an extreme (within 0.5×MTR of ONH/ONL or outside) |
| F7 | Cross-instrument regime agreement | `other_instrument.market_condition` + `regime_agreement` | other instrument TRENDING & agreeing vs not |
| F8 | Regime persistence (offline) | `regime_persistence()` +30 min | TRENDING persistent at +30min vs transient (label decayed) |
| F9 | Impulse phase | `location_context.impulse` | pre/developing impulse vs late_entry |
| F10 | Direction alignment | candidate `location.alignment` overall | aligned vs conflicting |
| F11 | Target blocked | `location.target_blocked_by_opposing_zone` | blocked vs clear |
| F12 | GEX regime | `gex.gex_regime` (when available) | positive vs negative regime; expected NOT_TESTABLE early (availability tracked) |

## 8. Pre-committed hypotheses

Directional where a documented prior exists (source noted); two-sided otherwise. These are
the only confirmatory hypotheses; anything else found in the data is EXPLORATORY (§13).

- **H1 (F1, directional):** middle-of-range entries have lower net expectancy than
  edge/zone entries. *Prior: operator supply/demand framework; 07-16 morning audit (all 4
  losses mid-structure).*
- **H2 (F7, directional):** entries taken when the other instrument is not trending or
  disagrees have lower expectancy. *Prior: morning audit — other instrument never trending
  at signal in 4/4 losses.*
- **H3 (F8, directional):** transient TRENDING at signal (label decays within 30 min)
  predicts lower expectancy than persistent TRENDING. *Prior: morning audit — labels
  decayed within 30–60 min in the losing cluster.*
- **H4 (F9, directional):** late_entry impulse phase predicts lower expectancy.
  *Prior: doctrine + momentum-entry investigation (chasing re-anchored entries lost).*
- **H5 (F11, directional):** targets blocked by a fresh opposing zone predict lower
  expectancy and higher stop-before-target rate. *Prior: operator framework (room to run).*
- **H6 (F4, directional):** entries interacting with fresh zones outperform tested zones.
  *Prior: operator framework (freshness).*
- **H7 (F6, directional):** entries inside the overnight range underperform entries at or
  beyond its extremes. *Prior: morning audit (all losses inside overnight range).*
- **H8–H12 (F2, F3, F5, F10, F12, two-sided):** no committed direction.

## 9. Controls (binding)

Every confirmatory estimate is computed **with session and strategy-family controls**:

- **Session** (asian / london / new_york) — mandatory covariate and mandatory stratified
  reporting. A context feature that stops discriminating once session is controlled is a
  session finding, not a context finding (see K3).
- **Strategy family** (orb / vwap / strat) — mandatory covariate; the permission-layer claim
  requires the effect not be confined to one family.
- **Instrument** (MNQ / MES) and **direction** (long / short) — reported as strata;
  any effect confined to one instrument+direction cell is flagged.
- **Baseline-gate overlap:** each feature's incremental value is measured on top of what
  the deployed TRENDING regime gate already excludes (B2, §10).

## 10. Baseline comparators

- **B0 — unconditional:** per-strategy-family net expectancy after costs, no context. The
  floor any gate must improve.
- **B1 — session + strategy model:** expectancy predicted by session × family alone. Every
  candidate feature must add discrimination **beyond B1** (partial effect after controls).
- **B2 — deployed regime gate:** the TRENDING-label gate as currently in production.
  Features must add beyond what B2 already blocks; rediscovering B2 is not a finding.

## 11. Pre-committed interactions (exactly 10 — binding cap)

Univariate statistics are explicitly insufficient (operator directive). These 10 pairs are
the only confirmatory interactions; each is a 2×2 (or 2×3) expectancy table plus an
interaction contrast.

| ID | Interaction | Rationale (prior) |
|---|---|---|
| I1 | F8 persistence × session | Is transient-TRENDING harm concentrated in asian/london? This is the test that could eventually justify, refine, or retire the morning hold. |
| I2 | F7 pair agreement × session | Same structure as I1 for the second morning-audit finding. |
| I3 | F1 middle_of_range × F9 impulse | Late impulse + mid-range hypothesized worst cell (chasing into nothing). |
| I4 | F10 alignment × F4 freshness | Aligned entries at fresh zones hypothesized best cell. |
| I5 | F6 overnight position × direction | Breaking an overnight extreme vs fading it vs trading inside. |
| I6 | F11 target blocked × F5 at-level | Composite "room to run": at a level with a clear path vs blocked. |
| I7 | F8 persistence × F7 agreement | Do the two trend proxies add independently or measure the same thing? (collinearity check with teeth) |
| I8 | F1 middle_of_range × session | Is mid-range harm a london artifact? |
| I9 | F9 impulse × F8 persistence | Late entry into a persistent trend vs into a transient one. |
| I10 | F2 1H relation × F3 4H relation | Multi-timeframe zone confluence vs single-TF signal. |

**Confirmatory test family = 22 tests total (12 univariate + 10 interactions).** This count
is fixed; the multiple-comparison correction (§12) is computed over exactly this family.

## 12. Statistical method (binding)

- **Effect measure:** difference in mean net expectancy (R) between contrast groups, after
  costs, with `execution_capped_mae` convention; `raw_bar_mae` reported alongside.
- **Multiple-comparison correction:** Holm–Bonferroni at family-wise α = 0.05 over the 22
  pre-committed tests. p-values via permutation test (10,000 permutations, labels shuffled
  within session × strategy strata to respect the controls).
- **Walk-forward:** chronological split into halves — sign agreement in both halves is
  mandatory. If joined n ≥ 90: three contiguous folds; require sign agreement in ≥2 of 3
  folds and no fold showing an opposite effect worse than −0.05R.
- **Outlier rules:** no winsorizing, no trimming of losses. Every effect is reported three
  ways: full sample, excluding the top-1 absolute-PnL winner, excluding the top-5 absolute
  winners (project convention from the structural-level and strat reviews). An effect that
  requires the top-5 winners to exist is treated as not established.
- **Out-of-sample validation (separate, materially different period):** confirmatory
  in-sample data freezes at the first formal review. The OOS set is collected **after** the
  freeze, minimum 4 weeks, and must be *materially different*: it must contain at least one
  FOMC-meeting week or monthly OPEX week, **and** its daily realized-volatility median must
  differ by ≥25% (either direction) from the in-sample median. If the calendar cannot
  supply such a period by the deadline, OOS waits — no substitute window may be used, and
  no gate ships without it.
- **OOS pass rule:** sign agreement with in-sample plus ≥50% of the in-sample effect size.

## 13. Exploratory findings

Anything observed outside the 22 pre-committed tests — new features, new interactions,
post-hoc subgroups — is labeled EXPLORATORY. Exploratory findings cannot support gate
candidacy in this study. They may seed a version-2 pre-registration, where they become
confirmatory only against **new** data collected after that v2 plan is written.

## 14. Minimum sample sizes (binding; below minimum = NOT_TESTABLE, never "no effect")

| Test type | Minimum |
|---|---|
| Univariate (F1–F12) | ≥40 joined resolved outcomes total AND ≥15 per contrast level |
| Interaction (I1–I10) | ≥60 total AND ≥12 per cell of the 2×2 |
| Provenance stratum reported | ≥15 resolved outcomes in that stratum |
| Session-stratified estimate | ≥12 per session cell |

NOT_TESTABLE results are recorded with the observed cell counts and carry forward to the
next review; they are not evidence for or against the hypothesis.

## 15. Verdict thresholds

### 15.1 Success — gate-candidacy (ALL required)

A feature or interaction becomes a **gate candidate** only if ALL of the following hold:

1. Holm-adjusted p < 0.05 within the 22-test family.
2. Sign-consistent expectancy separation in both walk-forward halves (and the 3-fold rule
   if n ≥ 90).
3. Survives top-1 AND top-5 winner removal (effect ≥ 0.05R remaining after top-5 removal).
4. Effect size: ≥ +0.15R separation between "allowed" and "blocked" contrast groups, AND
   the blocked group's expectancy ≤ 0R after costs (a gate must block genuinely negative
   expectancy, not merely less-positive).
5. Incremental over B1: ≥ +0.10R partial separation after session + strategy controls, and
   adds beyond B2.
6. Consistent sign in ≥2 provenance strata, **at least one of which is S2 (PaperBroker) or
   S3 (Tradovate demo)** — never shadow-only.
7. Not confined to a single strategy family (directionally present in ≥2 families with
   n ≥ 12 each, or explicitly scoped and renamed as a family-specific finding, which is NOT
   a permission-layer result).
8. OOS pass per §12.
9. Drawdown control per §1.
10. Independent spot-check passed (§16).

Even a fully passing gate candidate ships first as **observe-only / shadow enforcement**
through at least one materially different market stretch before any enforcing deployment —
and enforcement deployment requires its own operator approval and its own PR. Nothing in
this document authorizes deployment.

### 15.2 Failure (any one is sufficient to reject the feature as a gate direction)

- Sign flip between walk-forward halves.
- Effect < 0.05R after top-5 winner removal.
- Effect confined to a single session, single provenance stratum, or single
  instrument+direction cell.
- Effect fully absorbed by B1 (session + strategy) controls.
- OOS sign reversal.

### 15.3 Inconclusive handling, extension limit, and hard deadline

- Mixed or NOT_TESTABLE results → **exactly one extension**: continue collection for
  +6 weeks OR until joined n doubles, whichever comes first. No second extension.
- **Hard second-review deadline: 2026-09-30.** Any test still inconclusive at the second
  review is treated as a failure **for gating purposes** — the feature remains
  observation-only journaling indefinitely, and may only re-enter via a v2 pre-registration
  on new data.

## 16. Independent spot-check requirement (binding, blocks any verdict)

Before any review verdict is delivered:

1. An independent party (Codex or the operator — not the analysis author) re-derives **≥3
   randomly selected joined rows** end-to-end from raw journals: context fields, outcome,
   costs, R computation. Selection is seeded deterministically by the review date string so
   the author cannot cherry-pick.
2. The same party independently recomputes **≥1 headline statistic** from raw evidence
   files (not from the author's intermediate tables).
3. Any discrepancy blocks the verdict until resolved and documented.

## 17. What would make us abandon this direction? (falsification criteria)

The context-permission-layer hypothesis is **abandoned** — not extended, not softened — if
any of the following occur:

- **K1 — Nothing discriminates:** at the hard second review (2026-09-30), zero of the 22
  pre-committed tests meet gate candidacy AND zero show a consistent-sign ≥0.10R separation
  in ≥2 provenance strata. Verdict: context does not discriminate at measurable scale;
  collection may continue as cheap journaling, but the "primary permission layer" research
  direction ends and no v2 pre-registration is written for the same hypothesis without new
  mechanistic evidence.
- **K2 — The data cannot be trusted:** join integrity <90% with a root cause that cannot be
  fixed at the analysis layer (i.e., would require mid-sample collector changes). One
  restart of collection is permitted after a reviewed collector fix; if the second sample
  also fails integrity, abandon until the pipeline is redesigned.
- **K3 — It's all session:** every surviving effect is fully absorbed by session + strategy
  controls. Verdict: the finding is "session policy," not a context layer — revisit session
  policy directly and abandon the layer framing.
- **K4 — It's the fill model:** any headline effect reverses sign between shadow (S1) and a
  real-fill stratum (S2/S3). Verdict: the "edge" is a simulation artifact (622-day
  precedent); abandon until execution-grade evidence exists.
- **K5 — OOS contradiction:** the OOS period sign-reverses the headline surviving features.
  Abandon; do not re-run against a friendlier OOS window.

## 18. Amendment policy

- This is v1.0. Amendments **before** the in-sample data freeze produce v1.x with a
  changelog and operator sign-off; tests already peeked at cannot be modified.
- After any data has been analyzed, no change may promote a test to confirmatory status;
  changes apply only to future v2 pre-registrations on new data.

## 19. Review report template (both reviews use it)

1. Data-quality + join-validation results (§5) and gap-ledger summary (§6).
2. Per-feature availability and NOT_TESTABLE table (§14).
3. The 22 confirmatory results: effect (R), both MAE conventions, full/top-1/top-5
   variants, per-stratum, per-session, Holm-adjusted p, walk-forward agreement.
4. Baseline comparisons B0/B1/B2.
5. Exploratory observations (clearly fenced, §13).
6. Spot-check attestation (§16).
7. Verdict per test: GATE_CANDIDATE / FAILED / NOT_TESTABLE / INCONCLUSIVE(+extension state).
8. Kill-criteria assessment (§17) — explicitly evaluated every review, pass or fail.
