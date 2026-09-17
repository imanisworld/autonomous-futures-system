# Pre-Registration — Dynamic Structural-Level Attribution Study

**Version:** 1.5 (2026-09-17), frozen at the commit that introduces this version.
Changelog v1.4 → v1.5 (operator instruction 2026-09-17: "add M2K first as a v1.5 amendment";
MGC / MCL / MBT are deferred to later tranche-2 amendments because they need product-specific
session / RTH-open / daily-roll level definitions and non-quarterly contract-roll schedules;
no outcome was read; evidence in `docs/structural-level-v15-m2k-2026-09-17.md`): (1) **M2K
(Micro Russell 2000) is added as a third instrument** to P-REPLAY (`data/replay_polygon_v2/M2K`,
2024-10-01 → 2026-06-26, the v1.4 window, pinned builder, quarterly roll) and to
P-OOS-PROSPECTIVE (from the cross-instrument observation epoch `2026-09-16T12:17:19Z`, Z6). M2K
has **no P-LIVE calibration stratum** (no history before the epoch) and no P-OOS-MES-style
holdout. (2) **M2K's live candidate source is the cross-instrument observation lane**
(`webhook/observation_transport.observe_collection_only_alert` →
`execution/cross_instrument_observation.observe_bar`, evidence file
`logs/cross_instrument_observation_v1.jsonl`), not the runner journal. It calls the same
`strategy.shadow_setups.evaluate_shadow_setups` as runner and replay with canonical VWAP
observers off and Pine advisory brackets stripped; CANDIDATE rows (`structural_outcome`,
bracket authoritative) and SIGNAL rows (`signal_metrics`, bracket recorded but not authoritative
and never resolved by the lane) are both firings; the lane's own resolver (`_resolve_one`) is a
second implementation of the shadow resolver rules — **C20**, proven equivalent on 30,000
synthetic cases (P-R). Lane-only canonical families `strat_212` / `strat_122`
(`advance_strat_212_122`) are not `shadow_setups` families → `LANE_ONLY`, never pooled — **C21**.
(3) Level definitions (§3), events (§5), constants, hypotheses (§6), gates and kill criteria are
**unchanged**: M2K is an equity-index micro on the same CME Globex session map, 18:00 ET day
roll, 09:30 ET RTH / NY ORB and 03:00 ET London ORB; only the tick (0.10) differs. (4) §7
strata: instrument ∈ {MNQ, MES, M2K}; every pooled statement prints all three beside it. (5) New
prerequisites: **P3-M2K** bar-source levels parity (no journaled `location_context` copy exists
for a collection-only root, so the P3 gate is applied to admitted levels built from the box's
`bars_M2K` files vs the pinned Polygon corpus at the same B0, plus OHLC parity), **P5-M2K**
observation-lane firing/bracket parity vs replay under the frozen §4 gates of the P2 spec, and
**P-R** resolver equivalence. Preliminary results at n = 25 co-evaluated bars: firing Jaccard
1.000 and bracket 1.000 on every family that fired (28 co-fired candidates); NY ORB 19/19; OHLC
24/25 (one open two ticks apart) — **not a pass at that n**; rerun when ≥ 5 sessions of M2K
history exist. (6) Family expectations for M2K from the lane config: `strat_122_pullback`,
`strat_4hr_retrigger_observed` and `vwap_*` are not configured for M2K (absent by design).
(7) **X0 roll provenance (audit repair, same day; #622 §3 and amendment 1 #625 apply to M2K):**
a `contract_schedule` chain and a clean manifest are *not* roll provenance. Every M2K corpus
now carries an X0 proof (`scripts/structural_level_x0_roll_proof.py`): per-segment dated-contract
identity re-established from the provider, per-seam census, and live-feed reconciliation by
per-bar OHLC identification against the candidate dated contracts (the box's `M2K1!` bars prove
nothing by themselves). Results: `data/replay_polygon_v2/M2K` = `CONTRACT_IDENTITY_PROVEN`,
seams = `SCHEDULER_CONVENTION_ONLY` (`roll_days=8`, UTC-midnight seams; no M2K live feed existed
in the window to reconcile against — same provenance class as the admitted MNQ/MES v1.4
corpora); the September stitched parity corpus (`roll_days=3`, seam 2026-09-15T00:00Z) =
**`ROLL_PROVENANCE_UNKNOWN`** — the live feed's contract between 2026-09-14T22:00Z and the
first M2K live bar (2026-09-16T12:15Z) is unobservable for M2K (the MNQ/MES 22:00Z switch is
not M2K evidence), so that corpus is **not admitted** and P3-M2K/P5-M2K run on it are
non-confirmatory. The admitted September parity corpus is the **single dated contract
`M2KZ6`** (`data/replay_polygon_parity_m2kz6_2026_09_16/M2K`, builder `--contract`, no seam,
live bars identified as Z6 27/27 served) — not a new roll rule, the roll is simply outside the
window. **C23** records this. Relationship to `docs/prereg-cross-instrument-historical-expansion-2026-09-17.md`
(#622): M2K's population identity here is its own corpus and its own prospective stream
(strata {MNQ, MES, M2K}, never pooled); the MNQ/MES P-REPLAY / P-LIVE populations of v1.4 are
unchanged, and the expansion prereg's X0 / X1 / X2 gates are applied to M2K by this amendment.
Changelog v1.3 → v1.4 (amendment based **only** on the R1–R4 corpus/parity evidence in
`docs/structural-level-p2-parity-corpus-r4-2026-09-17.md`; no outcome was read; operator
rulings of 2026-09-17, both taken before R5 / any outcome): (1) **P-REPLAY window amended to
2024-10-01 → 2026-06-26** (Ruling 1, option A). Reason: the Polygon futures feed retains a
rolling ~2-year window (C17) — the U4 contracts return no bars and the series begins
2024-09-17 — so the v1.0 start (2024-07-01) is no longer fetchable from the provider; the
2024-09-17 → 2024-09-30 bars are the builder warm-up (EMA-200 and day-boundary fields
populated from the first in-window bar), the corpus is `data/replay_polygon_v2/{MNQ,MES}`
built by the pinned `scripts/polygon_to_replay.py` (emits `london_orb_*`; the old
`data/replay_polygon` files are retired), single provider vintage by design (the hybrid
option that would have mixed the preserved June-2026 fetch with today's was rejected because
the overlap already shows 8 revised and 138 backfilled bars, C18). The ~11 % shorter in-sample
window is accepted for documented provider-retention reasons, not for any result. (2) **§2.3
family compatibility (P5) is now filled from the parity corpus (R4)**: `ema_pullback_trend`
passed the firing gate (Jaccard 0.995) but **failed the frozen bracket gate** (93.88 % of
co-fired rows with all three legs within one tick; target leg) → it is **REPLAY_ONLY +
LIVE_ONLY strata, never pooled** (Ruling 2; no tolerance widening, no change to the 2.2×
target, no rounding change, no row removal; the `BRACKET_CONFLICT` finding stands). `BOTH`
(pooled-eligible): `strat_22_continuation_observed`, `strat_22_reversal_observed`,
`strat_312_observed`, `strat_322_reversal_observed`, `strat_122_observed`,
`strat_122_pullback` (MES cell `NOT_TESTABLE` on P-LIVE), `strat_4hr_retrigger_observed`,
`orb_false_break_fade`, `impulse_first_pullback_observed`, `trend_consolidation_break_observed`.
`NOT_TESTABLE`: `transition_failed_breakdown_reclaim`. `LIVE_ONLY`: `vwap_hold_observed`,
`vwap_rejection_observed`, `range_break_close`. `DEAD`: `ovn_high/low_sweep_reclaim`, `gap_fill`.
Level and event definitions, constants, hypotheses, strata, gates and kill criteria are
unchanged from v1.3.
Changelog v1.2 → v1.3 (amendment based **only** on the P3 levels-only parity evidence in
`docs/structural-level-parity-p3-2026-09-16.md`; no outcome was read; operator rulings of
2026-09-17): (1) the P3 parity denominator for a level excludes rows already
`context_gap_contaminated` for that level under §9.4 — exclusions stay counted and reported;
PWH/PWL remain admitted, bar-derived (Pine `wall_context` is a parity source, never the feature
source); (2) **VWAP is `NOT_ADMITTED` for tranche 1** — the frozen §3.7 formula diverges from Pine
on a complete-data holiday session (C14) and moves by more than one tick on a single missing bar
(C16); it stays computed and parity-reported as a diagnostic, and is removed from H5 and every
confirmatory level set (no tolerance change, no holiday rule invented from one case); (3) the
previous-day close is `PDC_BAR` (last 15m close of the prior CME trading day, the collector/bar
construct); Pine daily `previous_day.close` is a separate, non-admitted construct (C15), and the
claim that Pine previous-day H/L/C and the collector copies are identical is withdrawn — the study
uses the bar-derived PDH/PDL/PDC_BAR, which reproduced the collector at 100%; (4) C14 is queued
as a post-2026-09-30 live/replay parity **blocker** for `vwap_*` evidence.
**Outcome-provenance statement:** no outcome analysis of the hypotheses defined here (15m
conditioning of existing strategy candidates with unchanged brackets, H1–H6) was run while
drafting v1.0, v1.1 or v1.2. Previously viewed evidence that informed the drafting is
disclosed and fenced as prior information: the closed context-permission review (F1–F12,
I1–I10 on live outcomes 2026-07-16..09-16) and the 2026-07-13 MNQ 5m structural-level study
(reclaim / failed-breakdown / rejection / break-and-retest on PDH/PDL/ORB as a standalone
strategy, 2024-07..2026-06). Nothing else has been read.
Changelog v1.1 → v1.2 (second operator review, definition-level, pre-freeze): (1) §5.1
deterministic candidate-level reduction rule for H1–H4 (nearest-to-entry anchor per
hypothesis level set, fixed tie precedence; no "any level that makes T true"); (2) H2
break-age confound removed — F is an age-matched accepted-but-not-retested break, immediate
chase is descriptive only; (3) H5 anchor must lie within the frozen ≤ 0.5 × MTR15 relevance
band else `NOT_APPLICABLE`; (4) provenance wording corrected (the 07-13 study computed these
event concepts before; what is new is 15m candidate-conditioning with fixed brackets) and
P3's ORB parity denominator excludes `NOT_AVAILABLE` session-days.
Changelog v1.0 → v1.1 (operator review of PR #616, all
definition-level, pre-freeze): (1) missing opening-range bar → ORB `NOT_AVAILABLE`, the
`orb_start_shifted` substitute is removed; (2) deterministic anchor for E8/H5 `CLUSTER`;
(3) duplicate `candidate_key` handling fails closed on conflict; (4) H4/H6 clean confirmation
is prospective-only — `P-OOS-MES` is not pristine for them; (5) primary prospective OOS is
calendar/sample-size based and unconditional, volatility is descriptive / a labelled stress
check only; (6) one parity tolerance (one tick) for every admitted level including VWAP.
**Status:** RESEARCH DOCUMENT ONLY — AUDIT / RESEARCH / PAPER. Nothing here changes, or may be
cited to change, runtime behaviour, strategy, risk, execution, broker routing, `.env`,
services, Pine, collectors, gates, session permissions or deployment. **No proof, no gate. No
proof, no run.** The analysis this document specifies has **not been run**; no outcome has
been looked at while writing it.
**Relationship to the prior study:** `docs/prereg-context-permission-layer-analysis-plan-2026-07-16.md`
(v1.0) and its first review `docs/context-permission-first-review-2026-09-16.md` are
**complete and closed** (0/22 gate candidates). They are not re-run or re-interpreted here.
This is a different question about different constructs; where a construct overlaps one
already viewed on the 2026-07-16..09-16 journal sample, §11 says what that sample may be
used for.

---

## 1. Exact research question

**Primary (trading-filter) question.** For every eligible candidate produced by an
*existing* strategy detector — losers included — does a mechanically-defined,
signal-time-causal **price behaviour at a dynamically valid structural level** change the
candidate's **executable net expectancy** (net R after the pinned cost model, same
entry/stop/target/exit/fill/session policy as the candidate itself), within pre-specified
strata of instrument × session × direction × strategy family × outcome provenance?

**Secondary (price-response) question, reported separately and never merged with the
first.** Does the same structural event predict **subsequent price behaviour** at the level
(signed forward excursion at fixed horizons; "reaction" hit-rate in scale-free units),
independent of any strategy bracket?

The two questions are answered on the same population with the same feature values but
different outcome variables (§8). A level may predict a bounce and still fail to improve the
trade; this document requires both answers to be stated.

The model being tested is
`setup + level identity + reaction type + session + level test-count + level age +
directional context + room to next structure → outcome`, **not** `setup + "near demand"
→ outcome`. Coarse proximity/inside states were the closed study's subject.

**What this study is not.** It is not a detector, not a bracket redesign, and not a new
setup family. The 2026-07-13 structural-level 5m study
(`docs/mnq-structural-level-5m-study-2026-07-13.md`, `research/mnq_structural_level_5m.py`,
REJECTED) tested *trading the level events themselves* with tight structural stops and
next-level targets and was rejected on both exit variants. Nothing in this plan may
re-create that design: the events below are **conditioning features on existing
candidates**, and the candidate's own bracket is held fixed (§8, §9). Kill criterion K6
(§12) exists specifically to stop a "the level predicts price, so build a level trade"
drift.

## 2. Evidence / corpus inventory and availability matrix

Two populations, one offline feature builder (§14 P1) computing every level and event from
OHLCV bar history with identical code on both. Journaled context is used only for parity
proof and stratification, never as the feature source (see conflicts C1, C2, C3 in §15).

### 2.1 Populations

| Population | Source | Window | Instruments | Bar grid | Candidate generator | Outcome provenance | Role |
|---|---|---|---|---|---|---|---|
| **P-REPLAY** (confirmatory in-sample) | `data/replay_polygon_v2/{MNQ,MES,M2K}` (M2K added v1.5) (Polygon 15m, raw front-month, pinned `scripts/polygon_to_replay.py` via `scripts/structural_level_corpus_build.py`, `MANIFEST.json` per corpus; **v1.4**: replaces the retired `data/replay_polygon` files) | **2024-10-01 → 2026-06-26** (v1.4, Ruling 1; warm-up 2024-09-17 → 09-30 fetched, not in-window; v1.0–v1.3 said 2024-07-01, no longer fetchable — C17) | MNQ, MES | 15m | `replay/replay_engine.py` shadow-candidate path at a pinned SHA, ungated (`shadow_candidates`), families per §2.3 | S1-R: replay shadow resolver (`strategy/shadow_setups.resolve_shadow_candidate`, pessimistic both-hit, resting-entry fill) | primary confirmatory sample; 3 chronological folds |
| **P-LIVE** (calibration + provenance stratum) | VPS `logs/` read-only snapshot 2026-09-16 22:38Z (journal `journal_*.jsonl`, `bars_{MNQ,MES}_*.jsonl` 15m from 2026-06-05, `strategy_context_observations.jsonl`, PaperBroker evidence files) | 2026-07-16 → **2026-09-14T22:00Z** (contract-roll cut, §9) | MNQ, MES | 15m | live runner `shadow_candidates` + `range_signal` (the closed study's population, same `candidate_key` join) | S1: live `SHADOW_OUTCOME`; S2: PaperBroker (`mnq_strat_22_reversal`, `mes_trend_consolidation_break`); S3: Tradovate demo (n=2, below floor) | feature-definition calibration; live-vs-offline parity; S1/S2 fill-model check; **confirmatory only for constructs not viewed in the closed study** (§11) |
| **P-OOS-MES** (pre-registered holdout, **H1/H2/H3/H5 only**) | `data/replay_polygon/MES_oos_2026-07-24_2026-09-08` (40 files) | 2026-07-24 → 2026-09-08 | MES | 15m | replay, same SHA | S1-R | never scored with any structural event; used once, after IS results are frozen. **Not pristine for H4/H6**: the closed study viewed F4/F11 (fresh-vs-tested, target-blocked) on live outcomes over this same market period, so H4/H6 results here are EXPLORATORY (§11) |
| **P-OOS-PROSPECTIVE** | VPS journal + bar history collected **after 2026-09-17 00:00Z** (Z6 contracts); **M2K (v1.5):** `logs/cross_instrument_observation_v1.jsonl` CANDIDATE/SIGNAL rows + `logs/bars_M2K_*.jsonl` from the observation epoch `2026-09-16T12:17:19Z` | 2026-09-17 → ≥ 2026-10-31 and ≥ 50% of fold-3 rows per instrument, unconditional once complete (§11) | MNQ, MES, **M2K** | 15m | live runner (MNQ/MES); cross-instrument observation lane (M2K, same `evaluate_shadow_setups`) | S1, S2 | prospective OOS; the only route from "supported" to "v2 shadow-observation spec" |

Not admitted: `data/replay_polygon_5m/*` and the 5m-native families (4HR Re-Trigger, 60M
3-2-2, MES 1-2-2 5m lane) — different bar grid, different resolver
(`execution/forward_evidence_campaign.py`), and the level features below are defined on the
15m grid. A 5m tranche would be a separate pre-registration.

### 2.2 Level availability matrix

"Reconstructable" = computable from OHLCV bar history alone with the frozen definition in
§3, causally. "Journaled" = a live copy exists for parity proof (P-LIVE availability from
the 12,681 15m MNQ/MES decision rows 2026-06-01..09-16 in the snapshot; percentages are
of rows in the window where the source existed).

| Level | Reconstructable from OHLCV | Journaled live copy (parity source) | P-REPLAY | P-LIVE | Admitted to tranche 1 |
|---|---|---|---|---|---|
| PDH / PDL / prior close (`PDC_BAR`) | yes (CME trading day, §3.1) | `location_context.levels.pdh/pdl/prev_close` 07-16+ (the parity source; **P3: 100%**). Pine `context.previous_day.high/low/close` is a *different* copy (P3: 90.6% / 93.7% / **1.3%** agreement) and is not a parity source (C15) | yes | yes | **yes** |
| PWH / PWL | yes (CME trading week, §3.2) | `wall_context` PWH/PWL 75.8% of rows 06-29+ (Pine weekly) | yes (needs ≥1 full prior week of warm-up) | yes | **yes** |
| ONH / ONL | yes (§3.3) | `location_context.levels.onh/onl` (44% of rows — only rows after the 18:00 ET reopen have one) | yes | yes | **yes, RTH-only evaluation** |
| PMH / PML | yes (§3.4) | `location_context.levels.pmh/pml` 33.6% | yes | yes | no — exploratory (subset of the overnight range; RTH-only; small) |
| Running HOD / LOD | yes (§3.5) | `wall_context` HOD/LOD 75.8% (Pine, trading-day reset) | yes | yes | no — exploratory (≡ ONH/ONL before 09:30 ET; touching the running extreme is tautological) |
| NY ORB high/low | yes (§3.6) | `context.orb` when `session == new_york` (31.7% of rows, 06-03+) | re-derived (candle `orb_*` field is stale outside NY — C2) | yes | **yes** |
| London ORB high/low | yes (§3.6) | `context.orb` when `session == london` (28.3%) | re-derived (older corpora lack `london_orb_*` — C2) | yes | **yes** |
| VWAP (CME-day anchored) | yes (§3.7) | `context.vwap.value` 100% — **P3: 89.0%**, diverges from Pine on a complete-data holiday session (C14) and on any missing bar (C16) | candle `vwap` (same 18:00 convention — shares C14) | yes | **NO — `NOT_ADMITTED` tranche 1 (v1.3)**; computed and parity-reported as a diagnostic only |
| `location_context` 1H supply/demand zones | yes — pure `aggregate`/`detect_zones`/`nearest_zones` (§4.1) | `location_context.zones.1h` 59.6% (07-16+) | yes (replay never computed it live; offline only) | yes | **yes** |
| `location_context` 4H zones | yes (§4.1) | `location_context.zones.4h` 59.7% | yes | yes | **yes** |
| Pine pivot supply/demand (`risksentinel_context.pine`) | in principle (ta.pivothigh/low 7/7 + pivot-bar body, §4.2) — **parity unproven**, deployed `i_bos_swing` input unverified (C11) | `wall_context` SUPPLY_ZONE/DEMAND_ZONE 75.8%; observer `supply_demand_confluence` 100% of 15m observer rows | only via reconstruction | yes | no — tranche 2 after P4 parity passes |
| BOS / MSS | in principle (same pivots) | **not journaled** (`_market_state_context` omits `bos_direction`/`mss_direction`) | reconstruction only | no | no — exploratory |
| FVG | no existing definition in repo | none | — | — | no — not admitted; defined nowhere, would be a new construct |
| Asian / Globex opening range | no existing definition | none | — | — | no — new construct; §3.6 gives a label-only definition so it cannot be confused with NY/London ORB |
| GEX / Signa / options walls | not price-derived | GEX all-null; Signa grades only | none | no history | no — never fabricated |

### 2.3 Candidate family compatibility (prerequisite P5 must fill this in before analysis)

A family is admitted to P-REPLAY only if the **same detector code path** produces its
candidates and brackets in replay and live at the pinned SHA. Expected classification
(to be proven, not assumed):

- Expected both: `orb_breakout`, `orb_reclaim`, `orb_rejection`, `vwap_reclaim`,
  `vwap_hold`, `vwap_rejection`, `pdh_reclaim`, `pdl_reclaim`, Strat 15m families
  (`strat_212`, `strat_122`, `strat_inside_break`, `strat_outside_continuation`,
  `strat_22_*`, `strat_32`, `strat_322` 15m), `continuation_pullback`.
- Expected live-only: `range_signal` (bracket built from `wall_context`, which needs Pine
  S/D and carries C1), `ema_pullback_trend`, `impulse_first_pullback_observed`,
  `trend_consolidation_break(_observed)`, `transition_failed_breakdown_reclaim` unless
  P5 shows the replay engine emits them identically.
- A live-only family is reported in P-LIVE strata only and is labelled `LIVE_ONLY`; it
  cannot reach the pooled confirmatory statistic.
- **P5 result (v1.4, from the parity corpus, `docs/structural-level-p2-parity-corpus-r4-2026-09-17.md`):**
  the expectations above are superseded by measurement. `BOTH` = all `strat_*_observed`
  families, `strat_122_pullback`, `strat_4hr_retrigger_observed`, `orb_false_break_fade`,
  `impulse_first_pullback_observed`, `trend_consolidation_break_observed` (firing Jaccard
  0.937–1.000, bracket ≥ 0.999). **`ema_pullback_trend` = `REPLAY_ONLY` + `LIVE_ONLY`
  strata** (firing 0.995 passed; bracket 93.88 % < 98 % failed — target leg; Ruling 2).
  `NOT_TESTABLE` = `transition_failed_breakdown_reclaim`. `LIVE_ONLY` = `vwap_*_observed`,
  `range_break_close`. `DEAD` = `ovn_*_sweep_reclaim`, `gap_fill`. The `orb_breakout` /
  `orb_reclaim` / `pdh_reclaim` / `continuation_pullback` names listed as "expected both"
  are DecisionEngine concepts, not `shadow_candidates` families, and are out of population.
- **M2K (v1.5, P5-M2K to confirm):** expected `BOTH` for `strat_22_continuation_observed`,
  `strat_22_reversal_observed`, `strat_312_observed`, `strat_322_reversal_observed`,
  `impulse_first_pullback_observed`, `trend_consolidation_break_observed`,
  `orb_false_break_fade`, `ema_pullback_trend` (all confirmed 1.000/1.000 on the first 25
  co-evaluated bars — preliminary); `LANE_ONLY` = `strat_212`, `strat_122` (C21);
  `NOT_TESTABLE` = `transition_failed_breakdown_reclaim`; absent by lane config =
  `strat_122_pullback`, `strat_4hr_retrigger_observed`, `vwap_*`; `DEAD` = `ovn_*`, `gap_fill`.
  For M2K, `ema_pullback_trend` is judged on its own P5-M2K bracket parity (Ruling 2 was an
  MNQ/MES measurement) — until that gate is met at adequate n it is treated exactly as for
  MNQ/MES: `REPLAY_ONLY` + `LIVE_ONLY` strata.

### 2.4 Bar-history coverage and gaps

- P-LIVE 15m bars: `bars_{MNQ,MES}_2026-06-05..09-16` (89 files each). The closed study's
  gap ledger stands (MNQ 34 gaps / 2,475 min post-07-16, 11 ≥45 min; MES 31 / 2,265 min,
  7 ≥45 min). Rule for this study in §9.
- P-REPLAY: Polygon aggregates; a gap ledger on the same 15m-grid rule is built (P6) and
  expected near-empty; holidays and the 17:00–18:00 ET halt are not gaps.

## 3. Exact level definitions, reset and session semantics (frozen)

Notation: bars are 15m and **labelled by open time**; `B0` is the decision bar whose close
produces the candidate; `B−k` is k bars earlier. Signal time = close of `B0`. Every feature
may use bars with open ≤ `B0.open` (so `B0` itself, already closed, and all prior bars) and
nothing later. Sessions are `webhook.state_builder.detect_session` (asian 18:00–02:59 ET,
london 03:00–09:29, new_york 09:30–16:59, off_hours 17:00–17:59). CME trading day =
`(ET time + 6h).date()` (roll at 18:00 ET) — `context/location_context._trading_day`.

**Scale unit.** `MTR15` = median true range of the last 64 closed 15m bars ending at `B0`
(inherited: `location_context._median_true_range(past[-64:])`). All distances are reported
in MTR15 units; point thresholds are never used in the confirmatory family.

| # | Level | Definition (frozen) | Formed / valid | Source-of-truth parity |
|---|---|---|---|---|
| 3.1 | **PDH / PDL / `PDC_BAR`** | high / low / **last 15m bar close** of all bars in the previous CME trading day (bar-derived; `PDC_BAR` is the collector's `prev_close`) | valid for the entire current trading day 18:00 → 17:00 ET; age = time since 17:00 ET prior day | `location_context._day_ranges` and `csv_to_replay.get_prev_day_stats` (same construct; **P3: 100%**). Pine `request.security("D", high[1]/low[1]/close[1])` is a **separate construct**: its `close[1]` is the 17:00 ET daily print, not the last 15m close (P3: 1.3% agreement, C15), and its H/L differ on outage/holiday days (P3: 90.6% / 93.7%). Pine previous-day fields are not admitted and not a parity source |
| 3.2 | **PWH / PWL** | high / low of all bars whose trading day falls in the previous Monday–Friday trading-week (Sunday 18:00 ET reopen belongs to Monday) | valid for the whole current trading week | Pine weekly `high[1]/low[1]`; **no replay copy exists** — parity proven against `wall_context` PWH/PWL on P-LIVE (P3) |
| 3.3 | **ONH / ONL** | high / low of current-trading-day bars with open in [18:00 ET, 09:30 ET) | forming during Asian/London (running); **frozen at 09:30 ET**; events evaluated only for `B0` in new_york | `location_context._day_ranges` overnight (identical); observer copy dead (C4) |
| 3.4 | PMH / PML | as 3.3 restricted to [04:00, 09:30) ET | RTH-only | `location_context` premarket; exploratory |
| 3.5 | HOD / LOD | running max/min of the current trading day up to `B0` | continuous; exploratory | Pine `ta.change(time("D"))` reset = 18:00 ET (Globex-inclusive, **not** RTH HOD/LOD); `polygon_to_replay` CME-day extremes — agree (C5) |
| 3.6 | **NY ORB** | high / low of bars with open in [09:30, 09:45) ET = the single 09:30 15m bar. If that bar is absent (feed gap, holiday, late open) the NY ORB for that trading day is **`NOT_AVAILABLE`** — no later bar is substituted, rows are counted under §9.9. (Pine and `polygon_to_replay` would start the range on the first observed in-session bar; that runtime behaviour is a known divergence from this definition and is *why* the study re-derives the ORB rather than reading it — a synthesized range is not the opening range.) | valid from the 09:45 bar to the first bar with open ≥ 17:00 ET; **no ORB exists outside 09:30–17:00** | Pine `i_orb_min=15`, `i_ny_session="0930-1200"`, expiry when leaving `"0930-1700"`; `state_builder` routes NY ORB only when `session == new_york`; `polygon_to_replay` first-bar-in-session reset — agree. Candle `orb_*` fields outside NY are stale and must not be read (C2) |
| 3.6 | **London ORB** | high / low of bars with open in [03:00, 03:15) ET = the single 03:00 15m bar; absent bar → `NOT_AVAILABLE` for that day, same rule as NY, no substitute | valid from 03:15 to the first bar with open ≥ 09:30 ET | Pine `i_london_session="0300-0830"`, expiry when leaving `"0300-0930"`; `state_builder` routes it only for `session == london` — agree; older corpora need re-derivation (C2) |
| 3.6 | Asian/Globex opening range | **NOT DEFINED AS A CANONICAL ORB.** If ever studied it is the research construct `GLOBEX_OR_15 := high/low of the 18:00 ET bar`, evaluated 18:15–02:59 ET only, and never called "ORB" | — | new construct; exploratory only; the canonical NY/London definitions are not altered |
| 3.7 | VWAP — **`NOT_ADMITTED` tranche 1 (v1.3), diagnostic only** | cumulative Σ(hlc3·volume)/Σvolume over bars of the current CME trading day, reset at the first bar with open ≥ 18:00 ET (kept as the diagnostic definition; not tuned) | continuous | `csv_to_replay.vwap_day_range` / `compute_vwap` and `polygon_to_replay` use this convention (sub-session resets were a replay bug, fixed, never to be resurrected). **P3 disproved parity with Pine `ta.vwap(hlc3)` as a general statement:** 100% on regular complete-data days, but Pine did not reset at the 18:00 ET reopen after the 2026-09-07 holiday session (C14) and one missing 15m bar moves the cumulative value by more than one tick (C16). No holiday-anchor rule is invented from one case; readmission needs a v2 definition with its own parity proof |

**Dynamic validity / state.** A level carries a state at `B0`: `intact` (no close through it
since it became valid), `broken` (a close through it has occurred; the break bar index is
kept), plus `test_count` and `age` (§5). Broken levels remain in the reference set (break →
retest events need them); "valid" means "in the current reference set", not "unbroken".

## 4. Supply/demand — two separate constructs, separate names

They are never merged, pooled, or used as fallbacks for one another.

### 4.1 `LC_ZONE` — `context/location_context.py` impulse-base zones (admitted)

Pure functions, pinned constants: 15m bars up to and including `B0` → `aggregate(bars, 60)`
/ `aggregate(bars, 240)` (epoch-aligned buckets), last 120 1H / 60 4H bars →
`detect_zones` (base bar immediately before an impulse bar with |body| ≥ 1.2 × median TR
of that timeframe; demand if the impulse is up, supply if down; `broken` once a later close
passes the far edge; `tests` = later bars overlapping the zone, skipping the impulse bar;
`fresh` = tests == 0) → `nearest_zones(price)`. Provenance: v1 observational heuristic,
07-16 (#291). Live journal copy: `context.location_context.zones` (59.7% of rows, 07-16+).
Live builder includes `B0` (bar is recorded before the context is built, `runner.py:780`
vs `:830`), so the offline builder must include `B0` too.

### 4.2 `PINE_SD` — `tradingview/risksentinel_context.pine` pivot zones (tranche 2 only)

Supply = most recent confirmed `ta.pivothigh(high, i_bos_swing, i_bos_swing)`: top = pivot
bar high, bottom = pivot bar body bottom, `wavg` = body midpoint; demand mirrored on
`ta.pivotlow`. Only the **latest** pivot of each kind exists; confirmation lag =
`i_bos_swing` bars (default 7; deployed value unverified — C11). No test count, no broken
flag; `wall_context.fresh` is always `True` because `zone_state` is never sent (C3);
observer `near_*` uses a 20%-of-zone-height margin. Historical values exist only on the
live journal (`wall_context` 06-29+, observer). Reconstruction is legitimate only if P4
proves ≥ 98% within-1-tick agreement of an offline `ta.pivothigh/low` re-implementation
against journaled `wall_context` bounds over 07-16..09-14 — until then PINE_SD is
**not admitted**.

## 5. Mechanical event / reaction definitions (all causal at `B0` close)

Let `ℓ` be a point level (for `LC_ZONE`, use the zone edge facing price: top for a zone
below price / bottom for a zone above, and "range overlaps ℓ" means overlaps
`[bottom, top]`). `τ = 0.25 × MTR15` (frozen touch tolerance for retest/proximity where a
tolerance is needed; rationale: a quarter of the median bar range; the closed study's
"approaching" band of 0.5 × MTR15 is kept for proximity). `tick` per
`config/futures_contracts`. Constants `K = 4` bars (1 h; sweep lookback), `R = 8` bars
(2 h; retest lookback, inherited from `IMPULSE_WINDOW_BARS_15M`), `N = 2` closes
(acceptance), `D_max = 1.0 × MTR15` (sweep depth cap: deeper = break, not sweep). All are
frozen here with rationale; none may be tuned after outcomes are seen.

Side convention: for a **LONG** candidate the "supportive" levels are those at or below
entry, the "opposing" are above; mirrored for SHORT. Events are computed per (level,
side); the hypothesis states which side is used.

### 5.1 Candidate-level reduction rule (binding; one label per candidate per hypothesis)

Several admitted supportive levels can qualify on the same candidate (e.g. a PDL
`WICK_REJECT`, a demand-zone `PROXIMITY_ONLY` and an ONL with two prior tests). Each
hypothesis is labelled from **exactly one** level, chosen mechanically before any event is
inspected. "Any level that makes T true" is forbidden.

- **Anchor `A_H`** for H1, H3, H4 (and E8/H5): the admitted supportive-side level **from
  that hypothesis's own level set** (§6, after own-level exclusion) with the smallest
  absolute distance from the candidate's **entry** (zones: distance to the facing edge).
  Ties at tick resolution resolve by the fixed precedence PWH/PWL > PDH/PDL > ONH/ONL >
  NY ORB > London ORB > `LC_ZONE` 4H > `LC_ZONE` 1H > `PDC_BAR` (higher timeframe first; VWAP is
  not admitted and never enters an anchor or cluster),
  then lower price for LONG / higher price for SHORT. The anchor is chosen from geometry
  only; it is never switched to whichever level happens to carry an event.
- H1, H3, H4 are then evaluated on `A_H` alone. If `A_H` satisfies neither the T nor the F
  condition of the hypothesis (e.g. it is farther than 0.5 × MTR15 from entry, or it has
  no touch/proximity/sweep), the candidate is `NOT_APPLICABLE` for that hypothesis and
  counted. Because T and F for H1/H3/H4 all require a touch or proximity event, this
  makes the relevance band `≤ 0.5 × MTR15` inherent for them.
- **Anchor `A_2`** for H2: an *eligible broken level* is a `MAJOR` level (trade-direction
  side, own-level exclusion applied) with a `BREAK` in the trade direction at `B−j`,
  `2 ≤ j ≤ R`, and no close back through it in `B−j+1..B0`. Among eligible broken levels
  the one with the smallest absolute distance from entry is `A_2` (same tie precedence).
  H2 is evaluated on `A_2` alone; a candidate with no eligible broken level is
  `NOT_APPLICABLE` for H2 and counted.
- H6 already reduces to one level by construction (the nearest opposing admitted level to
  entry; same tie precedence).
- E8/H5 uses `A_H` with the H5 level set, plus the relevance band in §6.

| Code | Event | Mechanical definition at `B0` | Bars allowed |
|---|---|---|---|
| E1 `TOUCH` | touch | `B0.low ≤ ℓ ≤ B0.high` | B0 |
| E2 `TEST_COUNT`, `FIRST_TOUCH`, `AGE` | prior tests / age | `TEST_COUNT` = number of bars from level-validity start to `B−1` whose range overlaps ℓ (for zones: overlaps the zone; the forming bar(s) of the level are excluded); `FIRST_TOUCH` = `TOUCH ∧ TEST_COUNT == 0`; `AGE` = CME-open hours between level formation (§3) and `B0.close` | ≤ B0 |
| E3a `WICK_REJECT` (same-bar sweep) | wick through, close back | supportive side, LONG: `B0.low ≤ ℓ − tick ∧ B0.close > ℓ ∧ (ℓ − B0.low) ≤ D_max`; SHORT mirrored. Equivalent to the 07-13 study's `failed_breakdown`/`failed_reclaim` (definition inherited, verdict on *trading it* not) | B0 |
| E3b `SWEEP_RECLAIM` (multi-bar) | sweep + close back through | supportive side, LONG: ∃ k ∈ [1, K] with `B−k.close < ℓ` (a close on the far side), `min(low over B−k..B0) ≥ ℓ − D_max`, every close `B−k..B−1 ≤ ℓ`, and `B0.close > ℓ` (first close back). Equivalent to the 07-13 `reclaim`. SHORT mirrored | B−K..B0 |
| E4 `BREAK` / `ACCEPT` | close-through / acceptance | `BREAK` at bar `Bj`: `Bj.close` beyond ℓ by ≥ 1 tick and `Bj−1.close` not beyond. `ACCEPT` = `N` consecutive closes beyond ℓ ending at B0 | ≤ B0 |
| E5a `BREAK_RETEST_HOLD` | break → retest → hold (role reversal) | a `BREAK` of ℓ in the trade direction at `B−j`, 2 ≤ j ≤ R; every close in `B−j+1..B−1` stays beyond ℓ; **no** bar in `B−j+1..B−1` came within τ of ℓ (so `B0` is the *first* retest); `B0` range comes within τ of ℓ from the far side (`B0.low ≤ ℓ + τ` for LONG) and `B0.close` stays beyond ℓ. Companion state `ACCEPT_NO_RETEST`: same break-age window and held closes, and no bar in `B−j+1..B0` within τ (H2's control) | B−R..B0 |
| E5b `BREAK_RETEST_REJECT` / `FAILED_BREAKOUT` | break → retest → reject | as E5a but `B0.close` back through ℓ (the retest fails). For the hypothesis it is the mirror-side supportive event: a failed breakout *above* an opposing level is `FAILED_BREAKOUT` for a SHORT | B−R..B0 |
| E6 `PROXIMITY_ONLY` | approach without touch | `0 < signed distance from B0.close to ℓ ≤ 0.5 × MTR15` and not `TOUCH` | B0 |
| E7 `DIST` | distance at signal | `(B0.close − ℓ) / MTR15`, signed so that positive = level on the supportive side | B0 |
| E8 `CLUSTER` | clustering / confluence | Anchor `A` = `A_H` of §5.1 computed over the H5 level set (nearest admitted supportive level to entry, fixed tie precedence, never switched to an event level). **Relevance band:** if the distance from entry to `A` exceeds 0.5 × MTR15, `CLUSTER` is `NOT_APPLICABLE` (structure that merely exists somewhere below/above the entry is not clustered structure *at* the setup). Otherwise `CLUSTER` = number of distinct admitted levels (anchor included) within ± 0.5 × MTR15 of `A`, after tautology removal: HOD≡ONH / LOD≡ONL before 09:30 ET count once; `PDC_BAR` counts; VWAP does **not** count (v1.3, not admitted); a `LC_ZONE` counts if the band intersects the zone; own-level exclusion (§6) applies | B0 |
| E9 `ROOM` | room to next opposing structure | distance from candidate **entry** to the nearest opposing admitted level (excluding the candidate's own level), divided by the candidate's stop distance → `ROOM_R`; `TARGET_REL` ∈ {`before`, `inside` (target within ± τ of the opposing level), `beyond`} | B0 |

**Sequence rule.** Every event above is defined so that its confirmation bar is `B0` — the
same bar whose close produces the candidate. An event whose confirmation would require
`B+1` or later (e.g. "the reclaim held for two more bars") is not a signal-time feature; it
may appear only in the price-response outcome (§8.2) as a *response*, never as a
conditioning variable.

## 6. Small confirmatory hypothesis family (6 trading-filter tests; the family for Holm)

Each hypothesis is one pre-committed contrast on the same candidate row, reported twice:
**TF** (trading-filter, §8.1) and **PR** (price-response, §8.2). Holm–Bonferroni α = 0.05
is applied **over the 6 TF tests**; the 6 PR tests form their own Holm-6 family. Directions
are frozen; "better" means higher mean net R (TF) / larger favourable excursion (PR).

**Own-level exclusion (binding).** A candidate's family-defining level is removed from its
feature set: ORB for `orb_*`, VWAP for `vwap_*`, PDH/PDL for `pdh_/pdl_reclaim`, the
`range_signal` wall for `range_signal`. Without this, H1–H3 restate the detector.

| ID | Hypothesis | Contrast (T vs F) | Level set | Direction |
|---|---|---|---|---|
| **H1** | Sweep → reclaim of a major level in the trade direction improves the candidate | On `A_H` (§5.1, H1 level set). T: `E3a ∨ E3b` on `A_H` within K bars; F: `TOUCH` of `A_H` at B0 without E3a/E3b (plain touch or `BREAK` through it); neither → `NOT_APPLICABLE` | `MAJOR` = {PDH, PDL, PWH, PWL, ONH, ONL (RTH only), NY ORB H/L, London ORB H/L} | T better |
| **H2** | Break → retest → hold (role reversal) beats an age-matched accepted break that has not retested | On `A_2` (§5.1), break age `j ∈ [2, R]`. T: `E5a` — every close since the break stayed beyond ℓ, **no** bar in `B−j+1..B−1` came within τ of ℓ, and `B0` is the first bar that comes within τ and closes beyond ℓ (retest-hold). F: same break age window, every close since the break stayed beyond ℓ, and **no** bar in `B−j+1..B0` has come within τ of ℓ (accepted, not yet retested). Candidates whose level already retested at an earlier bar (`B−m`, m < j) are neither T nor F → `NOT_APPLICABLE`, counted. **Age matching (binding):** the effect is the stratified difference across frozen break-age bins {2–3, 4–5, 6–8} bars (equal-weight across bins with n ≥ 12; a bin below that is dropped and counted); permutation shuffles labels within age bin × instrument × session × family. Immediate-chase breaks (`j ∈ {0, 1}`) are a **descriptive third group only** (exploratory, §13), never the control | `MAJOR` | T better |
| **H3** | Actual rejection beats mere proximity | On `A_H` (§5.1, H3 level set). T: `E3a` (wick-reject) on `A_H`; F: `E6 PROXIMITY_ONLY` to `A_H`; a plain touch that closes through `A_H` is neither → `NOT_APPLICABLE` (descriptive third group, §13) | `MAJOR ∪ LC_ZONE` (zone edge) | T better |
| **H4** | Test count and age matter *separately* | On `A_H` (§5.1, H4 level set), among candidates with `TOUCH` or `PROXIMITY_ONLY` on `A_H`: ordered `TEST_COUNT` bins {0, 1–2, ≥3} and, separately, `AGE` tertiles fixed from P-REPLAY fold 1 before any outcome is read; test = permutation Spearman trend of net R across bins (one test each; H4 counts as one Holm entry using the smaller of the two p-values, Bonferroni-2 inside) | `MAJOR ∪ LC_ZONE` | two-sided (the closed study's F4 flipped sign) |
| **H5** | Structural clustering improves the candidate | Anchor `A` must lie within **≤ 0.5 × MTR15 of entry** (E8 relevance band) or the row is `NOT_APPLICABLE`, counted. T: `CLUSTER ≥ 2`; F: `CLUSTER == 1` (only the anchor in band) | `MAJOR ∪ LC_ZONE ∪ {PDC_BAR}` (VWAP removed in v1.3 — not admitted) | T better |
| **H6** | Room to the next opposing structure relative to the planned target | T: `TARGET_REL == before` (target reached before the nearest opposing level); F: `TARGET_REL == beyond` | opposing side of `MAJOR ∪ LC_ZONE` | T better |

H4 and H6 overlap constructs already viewed on P-LIVE (F4 fresh-vs-tested; F11
target-blocked). §11 restricts them to exploratory status on P-LIVE; they are confirmatory
on P-REPLAY (in-sample) and P-OOS-PROSPECTIVE only; P-OOS-MES is not pristine for them
either (same market period). H1, H2, H3, H5 use event *concepts* that the 2026-07-13 MNQ
5m study did compute (as a standalone 5m strategy with its own bracket, PDH/PDL/ORB only);
what is new — and what has never been outcome-analysed — is their use as 15m conditioning
features on existing strategy candidates with unchanged brackets, on the level sets above.

No interaction is confirmatory in tranche 1. Session, family, instrument, direction and
provenance are **controls and mandatory strata** (§7), not features.

## 7. Stratification plan (binding; strata first, pooled last)

Every TF and PR estimate is computed and printed per stratum **before** any pooled number:

1. instrument: MNQ | MES | M2K (v1.5; never a pooled statistic without every instrument stratum beside it);
2. session at `B0`: asian | london | new_york;
3. direction: LONG | SHORT;
4. strategy family: orb | vwap | pdh_pdl | strat | continuation | live-only families
   (each labelled) — `scripts/context_permission_prereg_review.family_of` is the mapping;
5. provenance / fill model: S1-R (replay resolver) | S1 (live resolver) | S2 (PaperBroker)
   | S3 (Tradovate demo, reported only if ≥ 15 rows).

Additional mandatory cuts: `regime` at signal (Pine `market_condition` via
`context.market_condition` for MES — C8) and `level identity` (which level produced the
event) as descriptive tables.

Minimum cell sizes (below = `NOT_TESTABLE`, never "no effect"): a TF/PR contrast needs
≥ 40 rows total and ≥ 15 per contrast level; a reported stratum needs ≥ 30 rows; an
H4 trend needs ≥ 12 per bin. Pooled statistics are reported only with the per-stratum
table above them and carry a `strata_consistent` flag = sign agreement in ≥ 2 of the
instrument × session cells with n ≥ 30.

## 8. Outcome and execution definitions

### 8.1 Trading-filter outcome (TF)

- Row = one candidate (`candidate_key`) with a terminal outcome (`WIN`/`LOSS`). `NO_FILL`
  and `OPEN` are excluded and counted; `NO_FILL` rate is reported per contrast (a feature
  that changes fillability is a finding about fillability, reported as such).
- Net R = (gross ticks × tick value − 2 adverse ticks round-turn × tick value − $1.48) ÷
  (stop distance in ticks × tick value), exactly the closed study's pinned convention
  (`resolved_economics`); S2 rows use the lane's journaled `net_dollars`.
- Held constant across T and F for every candidate: entry, stop, target, exit mode (static
  bracket as journaled), session policy, fill model (resting entry, pessimistic both-hit,
  fill-bar target-only touch ignored — `resolve_shadow_candidate`), slippage, commission.
  **The study never edits a bracket.** If a family's live and replay bracket formulas differ
  at the pinned SHA (P5), that family is `LIVE_ONLY`/`REPLAY_ONLY` and cannot reach a
  pooled or promotion-quality statement.
- Both MAE conventions (raw-bar, execution-capped) are reported per contrast.

### 8.2 Price-response outcome (PR)

Independent of the bracket. For the event's implied direction (up for a supportive-side
event of a LONG, etc.):

- `EXC_h` = signed excursion `(close(B0+h) − B0.close) / MTR15` at h ∈ {4, 8, 16} bars
  (1 h, 2 h, 4 h), capped at the trading-day end (rows without h bars in-day are excluded
  and counted).
- `MFE_16`, `MAE_16` in MTR15 units over the next 16 bars.
- `REACT_1` = 1 if price reaches `+1.0 × MTR15` before `−1.0 × MTR15` (same-bar ambiguity
  resolved against the event, i.e. adverse-first), else 0.

The PR answer is stated as "the event does / does not predict price behaviour at
horizon h"; it is never converted into a trade statement.

## 9. Data-quality and exclusion rules (all exclusions counted, none silent)

1. **Integrity gates (from the closed study, reused):** candidate→outcome join ≥ 90%;
   duplicate `candidate_key` handling is **fail-closed**: rows sharing a key whose
   compared payload (instrument, bar ts, strategy, direction, entry, stop, target, outcome
   result, pnl_ticks, resolved_at) is byte-identical collapse to one row and the collapse
   is counted; rows sharing a key that **differ** in any compared field are
   `CONFLICTING_DUPLICATE` — the affected (population × family) is BLOCKED until the
   provenance is explained in writing, and the block is reported. No "first row wins".
   Observer/journal duplicate keys are counted the same way.
2. **Timeframe:** rows with `timeframe_minutes == 5` on the 15m path (07-26..28) excluded.
3. **Missing fields:** a level that is unavailable at `B0` (e.g. no PWH because the corpus
   has < 1 prior week; no ORB outside its window) makes the row `NOT_APPLICABLE` for that
   level, not `False`. A hypothesis is `NOT_TESTABLE` in a stratum if < 60% of rows carry
   the needed level.
4. **Feed gaps (P-LIVE):** outcome window overlapping any gap → `feed_gap_contaminated`,
   excluded. Context lookback: a row is `context_gap_contaminated` for a level only if a
   gap **≥ 45 min (≥ 3 bars)** falls inside that level's formation/lookback window
   (informed by the closed study's finding that one-bar gaps in 5–10-day zone lookbacks
   moved no effect by > 0.03R; frozen here, before any structural outcome is read).
5. **Contract roll (binding, per the 2026-09-15 roll ruling):**
   - P-LIVE ends at **2026-09-14T22:00Z**; 09-15/09-16 rows are excluded (Z6 vs U6-derived
     levels). P-OOS-PROSPECTIVE starts 2026-09-17 on Z6.
   - P-REPLAY: roll dates are **extracted** from the corpus (session-open price gap with
     the 8-days-pre-expiry convention, e.g. 2026-06-11 +277.5) into a roll ledger (P6), not
     assumed. Per-feature contamination windows: PDH/PDL/PDC, ONH/ONL, VWAP, ORB, HOD/LOD
     → the roll trading day and the next; PWH/PWL → the roll week and the following week;
     `LC_ZONE` 1H → 10 trading days after the roll; 4H → 10 trading days (lookback 60 4H
     bars ≈ 10 days). Contaminated rows are excluded for that feature and counted.
6. **Known corrupted periods:** the 2026-07-16 TradingView outage day, the 2026-09-14
   401/outage window, and any day in the P-LIVE gap ledger with ≥ 90 missing minutes are
   listed and their rows flagged; the 2026-07-26..28 5m contamination (item 2).
7. **Fill provenance:** every row carries `provenance ∈ {S1-R, S1, S2, S3}` and the
   resolver version; rows are never pooled across provenance without the stratum table.
8. **Ambiguity pessimism:** same-bar stop/target → stop; fill-bar target-only → ignored
   (resolver rule); PR `REACT_1` adverse-first.
9. **Missing opening-range bar:** when the 09:30 (NY) or 03:00 (London) 15m bar is absent
   the ORB is `NOT_AVAILABLE` for that session-day; every hypothesis that needs it treats
   the row as `NOT_APPLICABLE` for that level (item 3) and the count of affected
   session-days is reported per population. Missing data never synthesizes a replacement
   range. If > 5% of the session-days in a stratum lack the bar, that stratum's ORB events
   are `NOT_TESTABLE`.

## 10. Statistical and robustness rules (binding)

- Effect = mean net R (T) − mean net R (F) after costs; PR effect = mean `EXC_h`
  difference and `REACT_1` rate difference.
- p-values: permutation (10,000 shuffles) of the feature label **within instrument ×
  session × family strata**, one-sided in the frozen direction, two-sided for H4; seed
  pinned in the output.
- Multiplicity: Holm–Bonferroni over the 6 TF tests; separately over the 6 PR tests. Any
  additional cut is exploratory (§13).
- Walk-forward: P-REPLAY split into 3 contiguous chronological folds (fold boundaries at
  the 1/3 and 2/3 row quantiles of `B0` time, fixed before outcomes are read); sign
  agreement in ≥ 2 of 3 and no fold worse than −0.05R against the frozen direction.
- Dependence: (a) one-per-(bar, direction) dedupe sensitivity; (b) day-block bootstrap
  (2,000 resamples of trading days) for the CI of every effect; (c) day-level sign
  consistency count. The row-level permutation p is labelled anti-conservative when
  rows/bars < 1.5.
- Outliers: no winsorising; each effect reported full, ex-top-1 and ex-top-5 absolute-PnL
  rows; an effect needing the top-5 rows is "not established".
- "Supported" (per hypothesis, in-sample) requires **all** of: Holm p < 0.05; walk-forward
  rule; ex-top-5 effect ≥ 0.05R; effect ≥ 0.10R; `strata_consistent`; not confined to one
  family; incremental ≥ 0.10R after a session × family fixed-effect adjustment (the
  closed study's B1 partial).
- **Gate candidacy is not an outcome of this study.** A supported hypothesis produces at
  most a v2 *shadow-observation* specification for prospective collection; enforcement
  needs its own pre-registration, operator approval and PR.

## 11. OOS / freeze policy

- Definitions, constants, hypotheses, strata and rules freeze at this file's commit SHA.
  Amendments before the P-REPLAY run produce v1.x with a changelog and operator sign-off;
  after any outcome is read, changes apply only to a v2 on new data.
- **Order of operations:** P1–P7 prerequisites → P-REPLAY folds (in-sample; results
  frozen in a results JSON) → P-OOS-MES once → P-LIVE (as calibration/provenance stratum)
  → P-OOS-PROSPECTIVE.
- **Primary prospective OOS window (calendar / sample-size, unconditional):** all
  MNQ/MES journal rows with `B0` from **2026-09-17 00:00Z** through the later of
  **2026-10-31 23:59Z** (≥ 6 calendar weeks, which by the fixed calendar contains the
  2026-10-16 monthly OPEX and the 2026-10-27/28 FOMC meeting) and the first date on which
  the prospective terminal-outcome row count reaches **≥ 50% of the P-REPLAY fold-3 row
  count** for each instrument. Once that window is complete it is **evaluated
  unconditionally** — no characteristic observed during collection (volatility, regime
  mix, trend, gap count) may decide whether it counts. Its daily realised-volatility
  median relative to fold 3 is **reported descriptively**. A separately labelled
  **volatility-stress check** (`OOS_VOLSTRESS`, non-primary) may additionally report the
  prospective rows split at the fold-3 realised-volatility median; it cannot rescue or
  overturn the primary OOS verdict. No substitute window.
- **Prior-viewing status per hypothesis (binding):**
  - **H1, H2, H3, H5** — the underlying event concepts (reclaim, failed breakdown,
    rejection, break-and-retest) were computed by the 2026-07-13 MNQ 5m structural-level
    study on PDH/PDL/ORB over 2024-07..2026-06, as a standalone 5m strategy with its own
    bracket, and that study's outcomes are known. The **candidate-conditioning hypotheses**
    here (15m grid, existing candidates, unchanged brackets, MAJOR/LC_ZONE level sets,
    the §5 event definitions with MTR15 normalisation) have never been outcome-analysed.
    The 07-13 result is disclosed as prior information and is the reason K6 exists.
    Confirmatory on P-REPLAY (in-sample) and on **P-OOS-MES** and **P-OOS-PROSPECTIVE**;
    may also be reported on P-LIVE as a provenance stratum (S1/S2).
  - **H4, H6** — overlap F4 (fresh vs tested) and F11 (target blocked), which the closed
    study viewed on live outcomes for 2026-07-16..09-16. That viewing covers the market
    period of both P-LIVE and P-OOS-MES (07-24..09-08). Therefore H4/H6 are confirmatory
    on **P-REPLAY only** (in-sample, 2024-07..2026-06), **EXPLORATORY on P-LIVE and on
    P-OOS-MES regardless of result**, and their only clean confirmation is
    **P-OOS-PROSPECTIVE** (post-freeze data). A "supported" H4/H6 therefore cannot exist
    before the prospective window completes.
- OOS pass: sign agreement with in-sample and ≥ 50% of the in-sample effect; OOS sign
  reversal = K5.

## 12. Kill criteria (evaluated at every review, pass or fail)

- **K1 — nothing discriminates:** no hypothesis is "supported" in P-REPLAY, or none shows a
  consistent-sign ≥ 0.10R separation in ≥ 2 provenance strata → close the dynamic-level
  direction; no v1.1 on the same constructs without new mechanistic evidence.
- **K2 — feature parity fails:** P3/P4 parity < 98% within **one tick** (the single frozen
  tolerance for every admitted level) on any admitted level over its **eligible rows** (rows
  not `context_gap_contaminated` for that level under §9.4; exclusions counted and reported —
  v1.3),
  or replay/live bracket formulas differ for a family that carries > 25% of rows →
  analysis BLOCKED for that level/family until the definition is reconciled (not in this
  task).
- **K3 — it's all session/family:** every surviving effect is absorbed by the session ×
  family adjustment → session-policy finding, not a level finding.
- **K4 — it's the fill model:** a headline TF effect reverses sign between S1-R/S1 and S2
  → simulation artefact; nothing proceeds to prospective collection on that hypothesis.
- **K5 — OOS contradiction:** the applicable clean OOS (P-OOS-MES and P-OOS-PROSPECTIVE for
  H1/H2/H3/H5; P-OOS-PROSPECTIVE only for H4/H6) sign-reverses a supported hypothesis →
  abandon it; no re-run against a friendlier window and no substitution of the
  `OOS_VOLSTRESS` split for the primary window.
- **K6 — price-response-only:** PR supported but TF not, across all six → record "levels
  predict price, not these trades" and **close**; do not redesign brackets to capture it
  (that is the rejected 07-13 design).
- **K7 — population integrity:** join < 90% or roll-ledger extraction fails to reproduce
  the known 2026-06-11 gap → BLOCKED.

## 13. Exploratory-only variables (not admitted to the confirmatory family)

Reported, if at all, under an `EXPLORATORY` fence; never Holm-corrected into the family;
never gate evidence: PMH/PML events; running HOD/LOD events; VWAP in any role (not admitted
from v1.3 — diagnostic parity reporting only, no PR use, no cluster membership); Pine
`previous_day.high/low/close` (C15); `PINE_SD` (until P4);
BOS/MSS reconstructions; any FVG definition; `GLOBEX_OR_15`; `wall_context.wall_alignment`
tags and their 0.2/0.3/0.5% thresholds; `confluence_scorer` composite score and its point
weights (unvalidated — no outcome validation exists in the repo; the score is also a
ranking/gate input, so the *selected* setup population is shaped by it, which is why the
study uses the full ungated candidate set); observer `supply_demand_confluence` /
`key_level_confluence` (different "near" definitions); impulse phase (two disagreeing
heuristics); structural regime (46% INSUFFICIENT_DATA); sweep depth, wick ratio, retest
count, acceptance length (`N`), event recency within `K` — any threshold variation of
§5; the descriptive third groups of H2 (immediate-chase breaks, `j ∈ {0,1}`) and H3
(touch-and-close-through); `E5b` break-retest-reject as a feature; interactions among H1–H6;
any level-identity subgroup ("only PDL works").

## 14. Implementation / data prerequisites (none executed in this task)

| # | Prerequisite | Output | Touches runtime? |
|---|---|---|---|
| P1 | Pure offline feature builder `research/structural_level_features.py`: levels §3, `LC_ZONE` §4.1 via the pure `location_context` functions, events §5, from a list of 15m bars ending at `B0`; no imports from webhook/strategy/execution/journal | module + unit tests on synthetic bars (one test per event) | no |
| P2 | Candidate regeneration spec for P-REPLAY: pinned SHA, `enabled_concepts` list, shadow-candidate output with brackets and resolver outcomes, per family; family compatibility matrix (P5) filled from the run manifest | run manifest + candidate JSONL (not run here) | no (replay is offline) |
| P3 | Parity proof on P-LIVE 07-16..09-14: offline PDH/PDL/PDC/ONH/ONL/PMH/PML vs `location_context.levels`; NY/London ORB vs `context.orb` (NY/London rows only, and **only session-days where the canonical 09:30 / 03:00 bar exists** — `NOT_AVAILABLE` days are excluded from the ≥ 98% denominator and reported separately, because runtime intentionally falls back to the first observed in-session bar on those days and that divergence is by design, not a parity failure); VWAP vs `context.vwap.value` (**diagnostic only from v1.3** — reported, not in the pass/fail family); PWH/PWL/HOD/LOD vs `wall_context` (Pine copies are the parity *source*, never the feature source); 1H/4H zones vs `location_context.zones` (edges within 1 tick, same `tests`/`broken`). **Denominator (v1.3):** for every admitted level, rows already `context_gap_contaminated` for that level under §9.4 are excluded from the ≥ 98% denominator and reported as excluded with their own agreement rate. Thresholds ≥ 98% within one tick. **Status: RUN — see `docs/structural-level-parity-p3-2026-09-16.md`** | parity report | no |
| P4 | `PINE_SD` reconstruction parity (tranche-2 admission test) incl. determining the deployed `i_bos_swing` | parity report | no |
| P5 | Live vs replay bracket-formula parity per family at the pinned SHA (entry/stop/target arithmetic, tick constants) | compatibility matrix (§2.3) | no |
| P6 | Roll ledger for P-REPLAY (dates, gap sizes) and gap ledger on the 15m grid; P-LIVE ledger reused | ledgers | no |
| P7 | MNQ OOS corpus 2026-07-24 → 2026-09-14 via `polygon_to_replay` (data fetch only) so P-OOS is not MES-only; and the P-OOS-PROSPECTIVE collection is just the existing journal (no collector change) | corpus + manifest | no |
| P8 | Independent spot-check protocol: ≥ 3 seeded rows re-derived end-to-end (levels, events, outcome, R) by a party other than the analysis author; ≥ 1 headline statistic recomputed from raw files | attestation | no |
| P3-M2K (v1.5) | Bar-source levels parity for M2K: admitted levels from `logs/bars_M2K_*.jsonl` vs the X0-admitted Polygon corpus (single contract `M2KZ6` for September 2026) at every common B0, one tick, ≥ 98% eligible rows, plus OHLC parity (`scripts/structural_level_bar_source_parity.py`); rerun until ≥ 5 sessions of live history exist | report JSON | no |
| P5-M2K (v1.5) | Observation-lane vs replay firing/bracket parity for M2K under the P2 spec §4 gates (`scripts/structural_level_p2_parity.py --live-source observation`), replay on an X0-admitted corpus only | report JSON | no |
| P-R (v1.5) | Resolver equivalence, synthetic only: `resolve_shadow_candidate` vs `cross_instrument_observation._resolve_one` (`scripts/structural_level_resolver_equivalence.py`) — never run on real candidates | report JSON | no |
| X0-M2K (v1.5, #622 §3 / #625) | Dated-contract identity + seam provenance per corpus (`scripts/structural_level_x0_roll_proof.py`): every segment re-fetched as its dated contract, seam census, live-feed per-bar contract identification; a corpus compared with a live feed is admitted only when `roll_provenance ∈ {PROVEN}` (no seam in window, or every seam `FEED_CONFIRMED`); P-REPLAY corpora record `SCHEDULER_CONVENTION_ONLY` | report JSON per corpus | no |

## 15. Conflicts between current repo definitions that must be resolved before analysis

Reported, not fixed (freeze until 2026-09-30; all are post-freeze queue items unless they
can be handled entirely in the offline builder):

- **C1 — `wall_context` ORB placeholder leakage.** When no ORB is defined,
  `state_builder` sets `orb.high/low` to the current bar's high/low as placeholders;
  `build_wall_context` emits them as `ORB_HIGH`/`ORB_LOW` walls (578 Asian rows since
  2026-09-04 carry ORB walls with `status == undefined`), so nearest-wall distances and
  `wall_alignment` (`BREAKING_WALL` 66%, `PIN_RISK` 22% in Aug–Sep) are contaminated.
  Before 09-04, Asian rows carried the previous day's NY ORB (≈ 4,360 rows). **Handling:**
  the study never reads ORB from `wall_context`; ORB is re-derived (§3.6).
- **C2 — Stale ORB in replay candles / missing London ORB.** Corpus candles keep
  `orb_high/low` after the NY window (the engine masks them by session; offline consumers
  must not read the field), and the 2024-07..2026-06 corpora have no `london_orb_*`.
  **Handling:** re-derive both ORBs from OHLCV.
- **C3 — Three supply/demand "near/fresh" semantics.** `LC_ZONE` (0.5 × MTR approach,
  real `tests`/`broken`), `PINE_SD` in `wall_context` (`fresh` always `True`), observer
  `near_*` (20% of zone height). **Handling:** §4 names; only `LC_ZONE` admitted.
- **C4 — Overnight range.** Observer construct dead (payload has no overnight fields);
  `location_context` is the only source; ONH/ONL ≡ HOD/LOD before 09:30 ET.
  **Handling:** RTH-only evaluation, tautology removal in E8.
- **C5 — HOD/LOD are trading-day extremes, not RTH extremes** (Pine `time("D")` reset at
  18:00 ET; corpus identical). `confluence_scorer` treats "near HOD/LOD" with a fixed
  8-tick band. **Handling:** exploratory only; scale-free bands.
- **C6 — Impulse-phase heuristics disagree 12%** (known). Not used.
- **C7 — `pdh_reclaim`/`pdl_reclaim` are not reclaim events.** They fire on
  `price_vs_pdh == "above"` (a state), not a cross. **Handling:** the family is named
  `pdh_pdl`; E3 events on PDH/PDL are excluded for it (own-level rule).
- **C8 — MES top-level `market_condition` is `None` since 2026-07-28** (closed-study
  defect 1). **Handling:** regime cuts read `context.market_condition`.
- **C9 — Contract roll.** Live rolled 2026-09-14T22:00Z (U6→Z6); corpus rolls raw 8 days
  pre-expiry; no roll awareness in code. **Handling:** §9.5 windows; the real fix is the
  post-09-30 blocker already ruled.
- **C10 — Four different proximity thresholds in code** (0.5 × MTR; 0.3%/0.5%/0.2%;
  8 / 20 ticks; 20% of zone height). **Handling:** one MTR-normalised set (§5); others
  exploratory.
- **C11 — Deployed Pine `i_bos_swing` unknown** (default 7). Blocks `PINE_SD` until P4.
- **C12 — Ranked-mode selection uses the unvalidated confluence score**, shaping the
  *selected* setup population. **Handling:** the population is the full ungated candidate
  set (`shadow_candidates` / `candidate_audit`), not the selected setup.
- **C13 — `range_signal` candidates have no per-candidate `location` block and their
  bracket derives from `wall_context` (C1).** **Handling:** `LIVE_ONLY` family; features
  are computed offline from bars for them like any other row.
- **C14 — Pine VWAP holiday anchor (found by P3, 2026-09-16).** After the 2026-09-07 CME
  holiday session TradingView's `ta.vwap` did not reset at the Mon 18:00 ET reopen (09-08,
  both instruments, zero bar gap, first-bar difference −3.96 MNQ / −5.61 MES). The repo's
  "one reset per CME trading day at 18:00 ET" convention (`csv_to_replay.vwap_day_range`,
  `polygon_to_replay`, §3.7) reproduces Pine on regular days only. **Handling:** VWAP
  `NOT_ADMITTED` tranche 1; **queued post-2026-09-30 as a live/replay parity BLOCKER for
  `vwap_*` lanes** — no promotion-quality VWAP-family conclusion on a holiday week until
  the exact holiday/session anchor is established and live and replay share one formula.
- **C15 — two "previous close" constructs (P3).** Pine `previous_day.close` (daily
  `close[1]`, the 17:00 ET print) agrees with the collector's last-15m-close `prev_close` on
  1.3% of rows; Pine daily H/L differ from the bar-derived PDH/PDL on 9.4% / 6.3% (outage and
  holiday days). **Handling:** the study's level is `PDC_BAR`; Pine previous-day fields are a
  separate, non-admitted construct; consumers of `context.previous_day.close` (confluence
  "target near PDC", key-level observer) are using the other one.
- **C16 — VWAP gap sensitivity (P3).** One missing 15m bar (below the §9.4 45-minute flag)
  moves the cumulative VWAP by more than one tick; extremes (PDH/ONH/PWH) are robust to it.
  **Handling:** part of the VWAP `NOT_ADMITTED` ruling; any v2 readmission needs an
  "any missing bar in the current trading day" contamination rule.
- **C17 — Polygon rolling ~2-year retention (v1.4).** The provider serves no bars older than
  ≈ today − 2 years; P-REPLAY's v1.0 start was not re-fetchable. **Handling:** Ruling 1, window
  2024-10-01 →; manifests + preserved files are the reproduction anchor.
- **C18 — provider revisions between fetches (v1.4).** 8 revised + 138 backfilled bars between the
  June-2026 and September-2026 fetches of the same contracts. **Handling:** manifests pin the fetch.
- **C19 — replay research-deque reset at UTC day-file boundaries (v1.4).** `replay_engine.run`
  cleared the 8-bar recent window per day file; live's BarHistory does not. **Handling:** fixed
  offline by the operator in PR #621 (`4f07ea0`: prior 8-bar history kept across day files under
  the same 3-day lookback, regression test added; nothing deployed). R4 rerun on the fix:
  `impulse_first_pullback_observed` 0.937 → 0.972, `trend_consolidation_break_observed`
  0.938 → 0.974; every admitted/testable family numerically identical; `ema_pullback_trend`
  still `BRACKET_CONFLICT`; `transition_failed_breakdown_reclaim` changed numerically (Jaccard
  0.101 → 0.102, bracket 1.000 → 0.900, replay-only 77 → 86) and remains `NOT_TESTABLE`
  (`docs/structural-level-v15-m2k-2026-09-17.md` §5).
- **C20 — two resolver implementations (v1.5).** `strategy.shadow_setups.resolve_shadow_candidate`
  (runner/replay) and `execution.cross_instrument_observation._resolve_one` (observation lane,
  M2K's prospective outcomes) implement the same rules separately. **Handling:** P-R proves
  equivalence on synthetic bars (30,000 cases, 0 disagreements); both stay in place.
- **C21 — lane-only canonical families (v1.5).** The observation lane also writes `strat_212` /
  `strat_122` from `advance_strat_212_122`, which replay never emits. **Handling:** `LANE_ONLY`,
  reported in M2K prospective strata only, never pooled.
- **C22 — no journaled level copy for collection-only roots (v1.5).** The observation lane does
  not journal `location_context`, so the P3 gate cannot be run against a live copy for M2K.
  **Handling:** P3-M2K compares the two bar sources instead (levels from live bars vs Polygon
  bars); the level *definitions* are unchanged and already proven on MNQ/MES.
- **C23 — scheduler seams are not feed provenance (v1.5 X0; #625).** `roll_days=N` places a
  seam at UTC midnight of a calendar-derived date; the live continuous feed switches when it
  switches (MNQ/MES: observed 2026-09-14T22:00Z, four trading days after the `roll_days=8`
  convention and two hours before the `roll_days=3` one). For M2K the live switch was never
  observed (no bars before 2026-09-16T12:15Z). **Handling:** every corpus gets an X0 report;
  a corpus whose in-window seam is `NOT_OBSERVABLE` / `FEED_CONTRADICTED` against the live feed
  it is compared with is `ROLL_PROVENANCE_UNKNOWN` and not admitted for parity; single-contract
  windows (`--contract`) are used for live comparison where the feed's contract is proven; for
  P-REPLAY (no feed) the seam rule is the frozen population definition, recorded as
  `SCHEDULER_CONVENTION_ONLY` with the provider volume-crossover offset per seam (informational).
  Detector windows spanning a seam are roll-contaminated per §9.5 item 5 regardless.

---

**Verdict (v1.3): NEEDS DEFINITION FIXES → resolved to the P3 result.** The ten wording
gaps from the two operator reviews are closed (v1.1/v1.2), and v1.3 records what the P3
parity run proved: every admitted level reproduces its journaled copy at ≥ 98% within one
tick on eligible rows, with VWAP withdrawn from tranche 1 and Pine previous-day fields
demoted to a separate construct. The data to answer the question exists (two years of
15m Polygon bars for both instruments, a 53-day live journal with bar history, an MES
holdout, and a prospective Z6 window), and every admitted level is reconstructable from
OHLCV. What does not yet exist is the single offline definition layer that makes the
journaled copies and the corpus agree: ORB and London ORB must be re-derived rather than
read (C1/C2), the two supply/demand constructs must stay separate (C3), and level/zone
parity against the live journal (P3) has to be proven before any candidate row is scored.
None of those fixes touches runtime; all of them are research-layer.

**Safe Next Step:** build P1 + P3 only — the pure offline feature builder
(`research/structural_level_features.py`, no runtime imports) with its synthetic-bar unit
tests, and run the **levels-only** parity check against the 2026-07-16..09-14 journal
snapshot (no outcomes read, no candidates scored). If parity ≥ 98%, the preregistration
becomes executable at P2; if not, the disagreement is reported as a definition conflict
and nothing else proceeds.
