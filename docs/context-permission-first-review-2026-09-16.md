# Context-Permission-Layer Study — First Formal Review (2026-09-16)

**Status:** REVIEW RECORD ONLY (docs). No runtime behaviour, collector, gate, strategy,
fill model, session policy, routing, or deployment change accompanies this document or may
be justified by it.
**Plan under review:** `docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md`
(v1.0, the binding analysis contract). This is the first time that plan was executed; a
repository/branch/memory sweep found no earlier partial or full run.
**Verdict:** `AUDIT ONLY`. **0 of the 22 pre-committed tests reach gate candidacy.** No
context feature is authorised to become a gate. Kill criterion K1 is on track to fire at
the hard second review (2026-09-30) unless the remaining two weeks change the picture.
**Machine-readable results:** `docs/context-permission-first-review-2026-09-16-results.json`.

The operator ruling on this review (2026-09-16): the observation layer was built to test
whether market context is the missing permission layer; the first outcome-joined test says
almost none of it earned the right to become a gate. Do not add supply/demand or the other
context fields to the strategy gates on this evidence.

---

## 1. Data quality, join validation (§5) and gap ledger (§6)

**Sources (all read-only, snapshot of the VPS `logs/` directory taken 2026-09-16 22:38 UTC,
deployed release `8fd8b215063c-20260916-144801` verified via `/proc/<pid>/cwd`):**

| Item | Value |
|---|---|
| Journal days scanned | 2026-06-01 .. 2026-09-16 (93 files); context collectors exist from 2026-07-16 |
| Decision rows 07-16..09-16 (MNQ/MES) | 8,531; 8,459 carry `context.location_context` (99.2%); 8,459 match a `strategy_context_observations.jsonl` row |
| Candidates (`shadow_candidates` + `range_signal`) | 11,179 |
| Candidates with a `SHADOW_OUTCOME` row | 11,152 = **99.8% join integrity** (gate ≥90% passes); 0 duplicate `candidate_key`s among 13,396 SHADOW_OUTCOME rows |
| Excluded, counted | 2,973 NO_FILL/OPEN (non-terminal); 886 5m-timeframe rows (07-26..28, 5m bars that reached the 15m path); 84 rows with no location context |
| **S1 — shadow-resolved** | **7,236** |
| **S2 — PaperBroker** | **327** (`mnq_strat_22_reversal` 257, MES `trend_consolidation_break` 70; 5 unjoinable) |
| **S3 — Tradovate demo** | 2 ledger rows, no outcome — below the 15-row reporting floor, not reported |
| Joined total / days | **7,563** over 53 days |

The join key is the resolver's own `candidate_key`
(`lane|instrument|row_ts|strategy|direction|entry[|epoch|variant]`), replicated exactly. S2
rows join on `context.timestamp == entry_ts`.

**Distributions:** session asian 3,150 / london 2,596 / new_york 1,817; MNQ 3,971 / MES
3,592; LONG 3,999 / SHORT 3,564; family strat 3,592, strat-adjacent shadow
(`ema_pullback_trend`, `impulse_first_pullback_observed`, `trend_consolidation_break_observed`,
`transition_failed_breakdown_reclaim`) 2,036, `range_break_close` 1,558, orb 311, vwap 66.
Pooled baseline: mean **−0.168R**, median −1.02R, win rate 40.4%, −$66,424 net
(S1 −$64,921; S2 −$1,503 / −0.062R).

**Correlation caveat:** the 7,236 S1 rows sit on 4,816 bars (5,698 bar×direction pairs). The
permutation tests treat rows as independent and are therefore anti-conservative; a
one-per-(bar,direction) sensitivity is reported in §3.

**Field availability (§5, <60% = NOT_TESTABLE):** F1–F5, F9: 99–100%. F6 75.6%. F11 67.2%.
F10 60.4% (neutral alignment excluded by the contrast). **F7 54.0% — NOT_TESTABLE as
journaled.** F8 60.3% of the 2,052 TRENDING-at-signal rows (its eligible population).
**F12 0% — NOT_TESTABLE.**

**Cross-source consistency (§5):** impulse "late entry" disagreement between the location
collector and the observer = **12.1%** (>10% threshold; the two v1 heuristics are different
definitions — the collector wins per the plan and the conflict is recorded here). The
observer's `overnight_range_location` is never available (payload `overnight_high/low` are
null on every alert); the collector's bar-derived ONH/ONL is the only working source. The
two pair-agreement fields (regime label vs close direction) measure different constructs
and disagree 51% where both exist.

**Gap ledger (15m bars, 2026-06-05..09-16):** MNQ 123 gaps / 5,220 min (post-07-16: 34 gaps,
2,475 min, 11 of them ≥45 min); MES 155 / 5,820 (post-07-16: 31 gaps, 2,265 min, 7 ≥45 min).
Outcome-window `feed_gap_contaminated`: **2.7%** of joined rows. Context-window
contamination: 4h impulse window 4.7%; overnight window 10.8%; **1H-zone lookback 55%,
4H-zone lookback 78%**. Under a strict reading of §6 the zone features (F2/F3/F4/F10/F11)
are >15% context-contaminated — this is the §6 headline. It is a plan-design mismatch (5–10
day lookbacks against any gap, most of which are one or two bars), not a feed failure;
excluding feed-gap-contaminated rows moves no effect by more than 0.03R.

**MAE (both conventions, analysis layer — SHADOW_OUTCOME rows carry no MAE):** S1 raw-bar
42.7 pts / execution-capped 30.5 pts; S2 46.1 / 35.5.

## 2. Context usage matrix

Risk engine consumes no context feature. "Decision" means the DecisionEngine can act on it.

| Item | Computed | Live on box | Historical | Journaled | Decision | Class |
|---|---|---|---|---|---|---|
| Middle-of-range / edge, 1H & 4H zone relation, freshness/tests | `context/location_context.py` | yes | 07-16+ only (replay never computes it) | `context.location_context` | no | deliberately non-authoritative |
| Direction/zone alignment, opposing zone, target blocked | `candidate_location()` | yes | 07-16+ | `candidate_audit[].location`, `shadow_candidates[].location` (**not** on `range_signal` candidates) | no | deliberately non-authoritative |
| PDH/PDL/prev close | collector levels + payload | yes | payload 06-05+, collector 07-16+ | yes | only via `pdh_reclaim`/`pdl_reclaim` setups, none executable since #517 | can block only if a family is re-enabled |
| Prev-week H/L, HOD/LOD | payload → `wall_context`, observer | yes | 06-05+ | yes | no | observation-only |
| Overnight H/L | collector (bars) | yes | 07-16+ | yes | no | observation-only; observer copy dead |
| VWAP | payload | yes | 06-05+ | yes | yes, inside families that are all observation-only now | configurable |
| Impulse phase / late entry | collector + observer (different heuristics) | yes | 07-16+ | yes | no | observation-only |
| MNQ↔MES agreement | collector `other_instrument`/`regime_agreement`; observer close-direction | **degraded** (see §7 defect 1) | 07-16+ | yes | no | partially dead |
| Trend persistence | offline `regime_persistence()` only, never at signal time by design | n/a | 07-16+ | offline only | no | cannot be a gate at signal time |
| Structural regime | `context/structural_regime.py`, `gate_authoritative=False` | yes | 07-16+ | yes | no | observation-only; INSUFFICIENT_DATA on 46% of joined rows |
| GEX | `evaluate_gex` can RED_LIGHT when data exists | observe enabled, every field null | none | nulls | dormant | no data has ever arrived |
| Signa | `evaluate_signa`; blocks only if `signa_gate_enforced` (off) | grades present | 07-16+ | yes | configurable, off | observation-only |
| Deployed TRENDING gate (B2) | `MARKET_CONDITION_NOT_TRADABLE` / `NOT_TRENDING` | yes | 06-05+ | yes | **yes** | the only context that blocks |

## 3. The 22 confirmatory results

Effect = mean net R (hypothesised-better group) − mean net R (other group). Net R = gross
ticks − 1 adverse tick per side − $1.48 round-turn, divided by initial stop risk (S2 uses
the journaled `net_dollars`). Permutation p: 10,000 shuffles within session × family strata,
one-sided in the pre-registered direction for directional tests. Holm–Bonferroni over the
fixed family of 22.

| ID | Contrast | n (T/F) | ΔR net | ΔR gross | Δ$ | p | Holm | WR T/F | halves | 3-fold | ex-top-5 | B1 partial | S1 / S2 | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | edge vs middle | 5,613/1,950 | +0.001 | +0.025 | +13 | 0.31 | 1.0 | .39/.43 | +/+ | +/+/+ | +0.004 | +0.02 | +0.00/+0.00 | NOT SUPPORTED |
| F2 | 1H inside/approaching vs middle | 4,760/2,803 | +0.018 | +0.037 | +13 | 0.60 | 1.0 | .40/.42 | +/+ | +/+/+ | +0.019 | +0.03 | +0.01/+0.18 | NOT SUPPORTED |
| F3 | 4H inside/approaching vs middle | 3,396/4,167 | −0.021 | +0.009 | +3 | 0.49 | 1.0 | .39/.42 | −/+ | −/−/+ | −0.021 | −0.01 | −0.00/−0.42 | NOT SUPPORTED |
| F4 | 4H fresh vs tested | 2,946/4,617 | +0.043 | +0.017 | +9 | 0.17 | 1.0 | .45/.38 | +/− | +/−/− | +0.043 | +0.03 | +0.04/+0.16 | NOT SUPPORTED (sign flip) |
| F5 | at key level vs beyond | 2,890/4,673 | +0.061 | +0.070 | +11 | 0.06 | 1.0 | .43/.39 | +/+ | +/+/+ | +0.063 | +0.05 | +0.06/+0.11 | NOT SUPPORTED |
| F6 | overnight extreme vs inside | 1,627/4,088 | +0.076 | +0.073 | +5 | 0.005 | 0.114 | .43/.38 | +/+ | −/+/− | +0.069 | +0.09 | +0.08/+0.03 | FAILED (3-fold rule; asian −0.17 vs london +0.20 / NY +0.23) |
| F7 | pair TRENDING & agreeing vs not | 534/3,547 | −0.189 | | | | | | | | | | | NOT TESTABLE (54% availability) |
| F8 | TRENDING persistent at +30m vs transient | 670/567 | +0.205 | +0.202 | +21 | 0.005 | 0.114 | .35/.28 | +/+ | +/+/+ | +0.206 | +0.21 | +0.23/−0.09 | NOT SUPPORTED at Holm; see repaired variant |
| F9 | pre/developing vs late entry | 7,016/547 | −0.027 | −0.002 | +12 | 0.91 | 1.0 | .41/.38 | +/− | −/+/− | −0.020 | −0.08 | −0.03/+0.08 | NOT SUPPORTED (wrong direction; 12% cross-source disagreement) |
| F10 | aligned vs against | 1,453/3,116 | −0.096 | −0.098 | −10 | 0.025 | 0.494 | .27/.31 | −/− | −/+/− | −0.092 | −0.09 | −0.10/−0.03 | NOT SUPPORTED — aligned entries are worse; asian / MNQ-short only |
| F11 | target clear vs blocked | 1,257/3,828 | −0.036 | −0.042 | −6 | 0.77 | 1.0 | .29/.30 | −/− | −/+/− | −0.033 | −0.03 | −0.03/−0.22 | NOT SUPPORTED (wrong direction) |
| F12 | GEX regime | 0/0 | | | | | | | | | | | | NOT TESTABLE (0% availability) |

**Interactions (I1–I10):**

| ID | Factors | n / min cell | Result | Verdict |
|---|---|---|---|---|
| I1 | F8 × session | 1,237 / 107 | persistence effect asian −0.02, london +0.48, NY +0.02 | NOT TESTABLE as journaled (F8 availability); repaired: London-only |
| I2 | F7 × session | 4,081 / 116 | agreeing-TRENDING pair worse in every session | NOT TESTABLE as journaled; repaired: opposite to prior |
| I3 | F1 × F9 | 7,563 / 227 | hypothesised worst cell (mid-range + late) = −0.05R, the best cell; DoD −0.24, p 0.05, Holm 1.0 | NOT SUPPORTED (prior inverted) |
| I4 | F10 × F4 | 4,569 / 450 | hypothesised best cell (aligned + fresh) = −0.32R, the worst cell; DoD −0.02, p 0.80 | NOT SUPPORTED (prior inverted) |
| I5 | F6 × direction | 5,715 / 700 | LONG at/beyond extreme +0.19R vs inside; SHORT −0.06 | NOT SUPPORTED (direction-confined; parent failed) |
| I6 | F11 × F5 | 5,085 / 459 | DoD +0.05, p 0.58 | NOT SUPPORTED |
| I7 | F8 × F7 | 287 / 63 | DoD +0.25, p 0.42 | NOT SUPPORTED; the two trend proxies do not add |
| I8 | F1 × session | 7,563 / 483 | asian +0.02, london −0.05, NY +0.04 | NOT SUPPORTED |
| I9 | F9 × F8 | 1,237 / 36 | DoD −0.29, p 0.19 | NOT SUPPORTED |
| I10 | F2 × F3 | 7,563 / 853 | DoD +0.01, p 0.84; MTF confluence cell −0.17R | NOT SUPPORTED |

**Sensitivities:** one-per-(bar,direction) dedupe (n 5,781): F6 +0.048R p 0.056; F10 −0.096R
p 0.049; F5 +0.055 p 0.14; F4 +0.026 p 0.41. Day-cluster sign consistency: F6 22 positive
days / 22 negative; F10 18 / 25.

### 3a. Analysis-layer repair of F7/F8 (reported separately; NOT confirmatory)

Defect 1 in §7 blinds the as-journaled F7 (for MNQ) and F8 (for MES). §5 permits an
analysis-layer fix: the same constructs were re-derived from `context.market_condition`.
Because these results were viewed on the same sample, §18 forbids promoting them to
confirmatory status; they are a feasibility observation and may only become confirmatory
in a v1.1/v2 pre-registration on **new** data.

| ID | n (T/F) | ΔR net | raw p | halves | 3-fold | ex-top-5 | B1 partial | S1 / S2 | session |
|---|---|---|---|---|---|---|---|---|---|
| F7r | 1,183/6,363 | −0.128 | 0.94 (opposite to H2) | −/− | +/−/− | −0.131 | −0.07 | −0.13/−0.10 | asian +0.02, london −0.17, NY −0.11 |
| F8r | 1,129/910 | +0.181 | 0.0012 | +0.21/+0.14 | +0.29/+0.06/+0.21 | +0.182 | +0.18 | **+0.20 / −0.06** | asian +0.01, **london +0.44**, NY −0.10 |

F8r is the only statistically strong result and still fails gate candidacy on three
independent §15 rules: the sign reverses in the only real-fill stratum (S2, n 72/58 — small,
but it is the rule and it is K4's trigger); the effect is confined to London; and the
"allowed" group is still −0.145R after costs. It is also structurally not a gate: regime
persistence at +30 minutes does not exist at signal time (the plan itself labels F8
offline-only). Descriptively: the Pine TRENDING label decays within 30 minutes on ~45% of
TRENDING signals, and those trades lose ~0.18R more — a label-quality diagnostic.

## 4. Baseline comparisons

- **B0** per family (net R): strat −0.20, strat-adjacent −0.18, range −0.03, orb −0.30, vwap −0.75.
- **B1** session × family: every cell negative (−0.02 to −1.2).
- **B2** by Pine label: TRENDING −0.10R, RANGE_BOUND −0.10, CHOPPY −0.10, DEAD −0.15,
  label-null −0.27. The deployed gate does not itself separate expectancy in the shadow
  population, and no feature adds ≥0.10R beyond B1 except F8r (with the failures above).

## 5. Exploratory observations (fenced, §13 — not gate evidence)

- Two operator-framework priors point the wrong way in this sample: aligned entries lose
  more than conflicting ones, and aligned + fresh is the worst 2×2 cell.
- `range_break_close` candidates win 83% of resolved rows at an R:R near 0.2 and net
  −0.03R — bracket geometry, not edge.
- Structural regime is INSUFFICIENT_DATA on 46% of joined rows (design: the 45-minute
  contiguity rule resets at the 17:00–18:00 ET break).

## 6. Spot-check attestation (§16)

Not satisfiable by the analysis author. Deterministically seeded rows for an independent
party (`random.seed("2026-09-16")`, indices 5809, 4169, 343 of the joined table in the
review scratchpad) and the headline statistic to re-derive (F8r, +0.181R, 1,129/910) were
supplied to the operator with every raw field, key and R computation. **No verdict in this
document is final until that spot-check is recorded.**

## 7. Problems found

**Data-pipeline defects (none authorised for change before 2026-09-30):**

1. **MES top-level `market_condition` has been `None` on every 15m journal row since
   2026-07-28 23:45 UTC.** The #376 universe short-circuit in
   `strategy/signal_engine.py` returns before the decision carries the label; the payload
   label survives only in `context.market_condition` / `location_context.regime_at_signal`.
   `read_other_instrument_regime()` (live) therefore returns null for every MNQ bar (F7
   blind for MNQ) and `regime_persistence()` (offline) is blind for MES (F8 undefined for
   MES). This is why F7 is NOT_TESTABLE as journaled. The fix is a runner/journal field, not
   collector logic — post-freeze queue.
2. The observer's `overnight_range_location` is dead by construction (payload
   `overnight_high/low` null on every alert).
3. 886 5m-timeframe rows (07-26..28) reached the 15m decision/observer/resolver path;
   `timeframe_minutes` distinguishes them, exclusion is analysis-layer.
4. `range_signal` candidates never receive a per-candidate `location` block
   (`_annotate_candidate_locations` covers only `candidate_audit` and `shadow_candidates`),
   so F10/F11 are unavailable for 1,558 rows.
5. Impulse-phase construct disagreement 12.1% between the two collectors.
6. The isolated MES 1-2-2 lane writes its own `strategy_context_observations.jsonl` and
   cross-instrument files under `hypothetical_ledger/mes_122_1500/` (a `log_dir` isolation
   side-effect); a census or join must not double-count it.

**Strategy findings (not defects):** the shadow population is net negative in every family,
session and stratum; no subgroup is good enough to name.

## 8. Verdict per test and kill-criteria assessment (§17)

- **Per test:** F6 FAILED; F7, F12 NOT_TESTABLE; every other univariate and every
  interaction NOT SUPPORTED. Zero GATE_CANDIDATE.
- **K1 (nothing discriminates):** 0/22 gate candidates and 0 features with consistent-sign
  ≥0.10R separation in ≥2 provenance strata (F8r +0.20/−0.06; F2 +0.01/+0.18; F4 +0.04/+0.16;
  F5 +0.06/+0.11). If the 2026-09-30 review looks like this, K1 fires and the "context as
  primary permission layer" direction ends per the plan.
- **K2:** join integrity 99.8% — not triggered.
- **K3:** nothing survives to be absorbed by session/strategy controls.
- **K4 (fill model):** the one headline effect (F8r) reverses sign S1→S2 — triggered for
  that feature.
- **K5:** OOS not yet applicable.
- **§15.3:** this review is inconclusive for F7/F8/F12 (NOT_TESTABLE) and a failure for
  everything else. The single permitted extension runs to the hard deadline of
  **2026-09-30**; anything still inconclusive then is a failure for gating purposes.

## 9. What was ruled (operator, 2026-09-16)

- Do not add supply/demand or any other context field to the strategy gates on this
  evidence.
- This document is the first formal review record; nothing runtime changes.
- After the 09-30 freeze, the first code fix should be defect 1 (MES `market_condition`
  plumbing), followed by fresh prospective data. The repaired F7r/F8r numbers were viewed on
  this sample and are not clean confirmation.
