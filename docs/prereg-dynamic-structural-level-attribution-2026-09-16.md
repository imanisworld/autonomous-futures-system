# Pre-Registration — Dynamic Structural-Level Attribution Study

**Version:** 1.0 (2026-09-16), frozen at the commit that introduces this file.
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
| **P-REPLAY** (confirmatory in-sample) | `data/replay_polygon/{MNQ,MES}` (Polygon 15m, raw front-month, `scripts/polygon_to_replay.py`) | 2024-07-01 → 2026-06-26 (622 files each) | MNQ, MES | 15m | `replay/replay_engine.py` shadow-candidate path at a pinned SHA, ungated (`shadow_candidates`), families per §2.3 | S1-R: replay shadow resolver (`strategy/shadow_setups.resolve_shadow_candidate`, pessimistic both-hit, resting-entry fill) | primary confirmatory sample; 3 chronological folds |
| **P-LIVE** (calibration + provenance stratum) | VPS `logs/` read-only snapshot 2026-09-16 22:38Z (journal `journal_*.jsonl`, `bars_{MNQ,MES}_*.jsonl` 15m from 2026-06-05, `strategy_context_observations.jsonl`, PaperBroker evidence files) | 2026-07-16 → **2026-09-14T22:00Z** (contract-roll cut, §9) | MNQ, MES | 15m | live runner `shadow_candidates` + `range_signal` (the closed study's population, same `candidate_key` join) | S1: live `SHADOW_OUTCOME`; S2: PaperBroker (`mnq_strat_22_reversal`, `mes_trend_consolidation_break`); S3: Tradovate demo (n=2, below floor) | feature-definition calibration; live-vs-offline parity; S1/S2 fill-model check; **confirmatory only for constructs not viewed in the closed study** (§11) |
| **P-OOS-MES** (pre-registered holdout) | `data/replay_polygon/MES_oos_2026-07-24_2026-09-08` (40 files) | 2026-07-24 → 2026-09-08 | MES | 15m | replay, same SHA | S1-R | untouched by any structural feature; used once, after IS results are frozen |
| **P-OOS-PROSPECTIVE** | VPS journal + bar history collected **after 2026-09-17 00:00Z** (Z6 contracts) | ≥ 6 weeks, calendar rule in §11 | MNQ, MES | 15m | live runner | S1, S2 | prospective OOS; the only route from "supported" to "v2 shadow-observation spec" |

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
| PDH / PDL / prior close (PDC) | yes (CME trading day, §3.1) | `context.previous_day.*` 100%; `location_context.levels.pdh/pdl/prev_close` 07-16+ | yes | yes | **yes** |
| PWH / PWL | yes (CME trading week, §3.2) | `wall_context` PWH/PWL 75.8% of rows 06-29+ (Pine weekly) | yes (needs ≥1 full prior week of warm-up) | yes | **yes** |
| ONH / ONL | yes (§3.3) | `location_context.levels.onh/onl` (44% of rows — only rows after the 18:00 ET reopen have one) | yes | yes | **yes, RTH-only evaluation** |
| PMH / PML | yes (§3.4) | `location_context.levels.pmh/pml` 33.6% | yes | yes | no — exploratory (subset of the overnight range; RTH-only; small) |
| Running HOD / LOD | yes (§3.5) | `wall_context` HOD/LOD 75.8% (Pine, trading-day reset) | yes | yes | no — exploratory (≡ ONH/ONL before 09:30 ET; touching the running extreme is tautological) |
| NY ORB high/low | yes (§3.6) | `context.orb` when `session == new_york` (31.7% of rows, 06-03+) | re-derived (candle `orb_*` field is stale outside NY — C2) | yes | **yes** |
| London ORB high/low | yes (§3.6) | `context.orb` when `session == london` (28.3%) | re-derived (older corpora lack `london_orb_*` — C2) | yes | **yes** |
| VWAP (CME-day anchored) | yes (§3.7) | `context.vwap.value` 100% | candle `vwap` (same convention) | yes | **yes, price-response only + own-level exclusion (§6)** |
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
| 3.1 | **PDH / PDL / PDC** | high / low / last close of all bars in the previous CME trading day | valid for the entire current trading day 18:00 → 17:00 ET; age = time since 17:00 ET prior day | Pine `request.security("D", high[1]/low[1]/close[1])` on a CME symbol = the 18:00-anchored daily bar; `csv_to_replay.get_prev_day_stats`; `location_context._day_ranges` — all agree |
| 3.2 | **PWH / PWL** | high / low of all bars whose trading day falls in the previous Monday–Friday trading-week (Sunday 18:00 ET reopen belongs to Monday) | valid for the whole current trading week | Pine weekly `high[1]/low[1]`; **no replay copy exists** — parity proven against `wall_context` PWH/PWL on P-LIVE (P3) |
| 3.3 | **ONH / ONL** | high / low of current-trading-day bars with open in [18:00 ET, 09:30 ET) | forming during Asian/London (running); **frozen at 09:30 ET**; events evaluated only for `B0` in new_york | `location_context._day_ranges` overnight (identical); observer copy dead (C4) |
| 3.4 | PMH / PML | as 3.3 restricted to [04:00, 09:30) ET | RTH-only | `location_context` premarket; exploratory |
| 3.5 | HOD / LOD | running max/min of the current trading day up to `B0` | continuous; exploratory | Pine `ta.change(time("D"))` reset = 18:00 ET (Globex-inclusive, **not** RTH HOD/LOD); `polygon_to_replay` CME-day extremes — agree (C5) |
| 3.6 | **NY ORB** | high / low of bars with open in [09:30, 09:45) ET = the single 09:30 15m bar; if that bar is absent (feed gap / holiday), the first observed bar with open in [09:30, 12:00) ET and the range is flagged `orb_start_shifted` | valid from the 09:45 bar to the first bar with open ≥ 17:00 ET; **no ORB exists outside 09:30–17:00** | Pine `i_orb_min=15`, `i_ny_session="0930-1200"`, expiry when leaving `"0930-1700"`; `state_builder` routes NY ORB only when `session == new_york`; `polygon_to_replay` first-bar-in-session reset — agree. Candle `orb_*` fields outside NY are stale and must not be read (C2) |
| 3.6 | **London ORB** | high / low of bars with open in [03:00, 03:15) ET (shifted-start rule as above within [03:00, 08:30)) | valid from 03:15 to the first bar with open ≥ 09:30 ET | Pine `i_london_session="0300-0830"`, expiry when leaving `"0300-0930"`; `state_builder` routes it only for `session == london` — agree; older corpora need re-derivation (C2) |
| 3.6 | Asian/Globex opening range | **NOT DEFINED AS A CANONICAL ORB.** If ever studied it is the research construct `GLOBEX_OR_15 := high/low of the 18:00 ET bar`, evaluated 18:15–02:59 ET only, and never called "ORB" | — | new construct; exploratory only; the canonical NY/London definitions are not altered |
| 3.7 | **VWAP** | cumulative Σ(hlc3·volume)/Σvolume over bars of the current CME trading day, reset at the first bar with open ≥ 18:00 ET | continuous | Pine `ta.vwap(hlc3)` (session-anchored on the CME symbol = 18:00 ET); `csv_to_replay.vwap_day_range` / `compute_vwap` (the proven convention; sub-session resets were a replay bug, fixed, never to be resurrected); `polygon_to_replay` — agree |

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

| Code | Event | Mechanical definition at `B0` | Bars allowed |
|---|---|---|---|
| E1 `TOUCH` | touch | `B0.low ≤ ℓ ≤ B0.high` | B0 |
| E2 `TEST_COUNT`, `FIRST_TOUCH`, `AGE` | prior tests / age | `TEST_COUNT` = number of bars from level-validity start to `B−1` whose range overlaps ℓ (for zones: overlaps the zone; the forming bar(s) of the level are excluded); `FIRST_TOUCH` = `TOUCH ∧ TEST_COUNT == 0`; `AGE` = CME-open hours between level formation (§3) and `B0.close` | ≤ B0 |
| E3a `WICK_REJECT` (same-bar sweep) | wick through, close back | supportive side, LONG: `B0.low ≤ ℓ − tick ∧ B0.close > ℓ ∧ (ℓ − B0.low) ≤ D_max`; SHORT mirrored. Equivalent to the 07-13 study's `failed_breakdown`/`failed_reclaim` (definition inherited, verdict on *trading it* not) | B0 |
| E3b `SWEEP_RECLAIM` (multi-bar) | sweep + close back through | supportive side, LONG: ∃ k ∈ [1, K] with `B−k.close < ℓ` (a close on the far side), `min(low over B−k..B0) ≥ ℓ − D_max`, every close `B−k..B−1 ≤ ℓ`, and `B0.close > ℓ` (first close back). Equivalent to the 07-13 `reclaim`. SHORT mirrored | B−K..B0 |
| E4 `BREAK` / `ACCEPT` | close-through / acceptance | `BREAK` at bar `Bj`: `Bj.close` beyond ℓ by ≥ 1 tick and `Bj−1.close` not beyond. `ACCEPT` = `N` consecutive closes beyond ℓ ending at B0 | ≤ B0 |
| E5a `BREAK_RETEST_HOLD` | break → retest → hold (role reversal) | a `BREAK` of ℓ in the trade direction at `B−j`, 2 ≤ j ≤ R; every close in `B−j+1..B−1` stays beyond ℓ; `B0` range comes within τ of ℓ from the far side (`B0.low ≤ ℓ + τ` for LONG) and `B0.close` stays beyond ℓ | B−R..B0 |
| E5b `BREAK_RETEST_REJECT` / `FAILED_BREAKOUT` | break → retest → reject | as E5a but `B0.close` back through ℓ (the retest fails). For the hypothesis it is the mirror-side supportive event: a failed breakout *above* an opposing level is `FAILED_BREAKOUT` for a SHORT | B−R..B0 |
| E6 `PROXIMITY_ONLY` | approach without touch | `0 < signed distance from B0.close to ℓ ≤ 0.5 × MTR15` and not `TOUCH` | B0 |
| E7 `DIST` | distance at signal | `(B0.close − ℓ) / MTR15`, signed so that positive = level on the supportive side | B0 |
| E8 `CLUSTER` | clustering / confluence | number of distinct admitted levels within ± 0.5 × MTR15 of the supportive-side reference level, after tautology removal: HOD≡ONH / LOD≡ONL before 09:30 ET count once; PDC and VWAP count; a `LC_ZONE` counts if the band intersects the zone; own-level exclusion (§6) applies | B0 |
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
| **H1** | Sweep → reclaim of a major level in the trade direction improves the candidate | T: `E3a ∨ E3b` on a supportive-side level within K bars; F: `TOUCH` of a supportive-side level at B0 without E3a/E3b (plain touch or `BREAK` through it) | `MAJOR` = {PDH, PDL, PWH, PWL, ONH, ONL (RTH only), NY ORB H/L, London ORB H/L} | T better |
| **H2** | Break → retest → hold (role reversal) beats chasing the break | T: `E5a` on a level in the trade direction; F: `BREAK` of the same level set at `B0` or `B−1` with no retest (`ACCEPT` may be true) | `MAJOR` | T better |
| **H3** | Actual rejection beats mere proximity | T: `E3a` (wick-reject) on a supportive-side level; F: `E6 PROXIMITY_ONLY` to a supportive-side level | `MAJOR ∪ LC_ZONE` (zone edge) | T better |
| **H4** | Test count and age matter *separately* | among candidates with `TOUCH` or `PROXIMITY_ONLY` on a supportive-side level: ordered `TEST_COUNT` bins {0, 1–2, ≥3} and, separately, `AGE` tertiles fixed from P-REPLAY fold 1 before any outcome is read; test = permutation Spearman trend of net R across bins (one test each; H4 counts as one Holm entry using the smaller of the two p-values, Bonferroni-2 inside) | `MAJOR ∪ LC_ZONE` | two-sided (the closed study's F4 flipped sign) |
| **H5** | Structural clustering improves the candidate | T: `CLUSTER ≥ 2`; F: `CLUSTER == 1` (exactly one supportive level in band); rows with `CLUSTER == 0` are excluded from this contrast and counted | `MAJOR ∪ LC_ZONE ∪ {PDC, VWAP}` (VWAP allowed here as a cluster member, not as an event) | T better |
| **H6** | Room to the next opposing structure relative to the planned target | T: `TARGET_REL == before` (target reached before the nearest opposing level); F: `TARGET_REL == beyond` | opposing side of `MAJOR ∪ LC_ZONE` | T better |

H4 and H6 overlap constructs already viewed on P-LIVE (F4 fresh-vs-tested; F11
target-blocked). §11 restricts them to exploratory status on P-LIVE; they are confirmatory
on P-REPLAY and P-OOS only. H1, H2, H3, H5 use event constructs never computed before.

No interaction is confirmatory in tranche 1. Session, family, instrument, direction and
provenance are **controls and mandatory strata** (§7), not features.

## 7. Stratification plan (binding; strata first, pooled last)

Every TF and PR estimate is computed and printed per stratum **before** any pooled number:

1. instrument: MNQ | MES (never a pooled MNQ+MES statistic without both strata beside it);
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
   duplicate `candidate_key` = 0 after documented dedupe (first row wins, count kept);
   observer/journal duplicate keys counted.
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
9. **ORB shifted start** (`orb_start_shifted`) rows are reported separately; if > 5% of
   ORB rows in a stratum, that stratum's ORB events are `NOT_TESTABLE`.

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
  → P-OOS-PROSPECTIVE after ≥ 6 weeks of Z6 data that contains ≥ 1 FOMC or monthly OPEX
  week and whose daily realised-volatility median differs by ≥ 25% from P-REPLAY fold 3's;
  no substitute window.
- **Prior-viewing rule for P-LIVE:** H1, H2, H3, H5 constructs were never computed on the
  07-16..09-14 sample and may be reported there as a provenance stratum (S1/S2). H4 and
  H6 overlap F4/F11, which were viewed; on P-LIVE they are **EXPLORATORY** regardless of
  result.
- OOS pass: sign agreement with in-sample and ≥ 50% of the in-sample effect; OOS sign
  reversal = K5.

## 12. Kill criteria (evaluated at every review, pass or fail)

- **K1 — nothing discriminates:** no hypothesis is "supported" in P-REPLAY, or none shows a
  consistent-sign ≥ 0.10R separation in ≥ 2 provenance strata → close the dynamic-level
  direction; no v1.1 on the same constructs without new mechanistic evidence.
- **K2 — feature parity fails:** P3/P4 parity < 98% within one tick on any admitted level,
  or replay/live bracket formulas differ for a family that carries > 25% of rows →
  analysis BLOCKED for that level/family until the definition is reconciled (not in this
  task).
- **K3 — it's all session/family:** every surviving effect is absorbed by the session ×
  family adjustment → session-policy finding, not a level finding.
- **K4 — it's the fill model:** a headline TF effect reverses sign between S1-R/S1 and S2
  → simulation artefact; nothing proceeds to prospective collection on that hypothesis.
- **K5 — OOS contradiction:** P-OOS-MES or P-OOS-PROSPECTIVE sign-reverses a supported
  hypothesis → abandon it; no re-run against a friendlier window.
- **K6 — price-response-only:** PR supported but TF not, across all six → record "levels
  predict price, not these trades" and **close**; do not redesign brackets to capture it
  (that is the rejected 07-13 design).
- **K7 — population integrity:** join < 90% or roll-ledger extraction fails to reproduce
  the known 2026-06-11 gap → BLOCKED.

## 13. Exploratory-only variables (not admitted to the confirmatory family)

Reported, if at all, under an `EXPLORATORY` fence; never Holm-corrected into the family;
never gate evidence: PMH/PML events; running HOD/LOD events; VWAP events as a feature for
non-VWAP families (PR only, and as a cluster member in H5); `PINE_SD` (until P4);
BOS/MSS reconstructions; any FVG definition; `GLOBEX_OR_15`; `wall_context.wall_alignment`
tags and their 0.2/0.3/0.5% thresholds; `confluence_scorer` composite score and its point
weights (unvalidated — no outcome validation exists in the repo; the score is also a
ranking/gate input, so the *selected* setup population is shaped by it, which is why the
study uses the full ungated candidate set); observer `supply_demand_confluence` /
`key_level_confluence` (different "near" definitions); impulse phase (two disagreeing
heuristics); structural regime (46% INSUFFICIENT_DATA); sweep depth, wick ratio, retest
count, acceptance length (`N`), event recency within `K` — any threshold variation of
§5; interactions among H1–H6; any level-identity subgroup ("only PDL works").

## 14. Implementation / data prerequisites (none executed in this task)

| # | Prerequisite | Output | Touches runtime? |
|---|---|---|---|
| P1 | Pure offline feature builder `research/structural_level_features.py`: levels §3, `LC_ZONE` §4.1 via the pure `location_context` functions, events §5, from a list of 15m bars ending at `B0`; no imports from webhook/strategy/execution/journal | module + unit tests on synthetic bars (one test per event) | no |
| P2 | Candidate regeneration spec for P-REPLAY: pinned SHA, `enabled_concepts` list, shadow-candidate output with brackets and resolver outcomes, per family; family compatibility matrix (P5) filled from the run manifest | run manifest + candidate JSONL (not run here) | no (replay is offline) |
| P3 | Parity proof on P-LIVE 07-16..09-14: offline PDH/PDL/PDC/ONH/ONL/PMH/PML vs `location_context.levels`; NY/London ORB vs `context.orb` (NY/London rows only); VWAP vs `context.vwap.value` (± 0.5 pt); PWH/PWL/HOD/LOD vs `wall_context`; 1H/4H zones vs `location_context.zones` (edges within 1 tick, same `tests`/`broken`) — thresholds ≥ 98% | parity report | no |
| P4 | `PINE_SD` reconstruction parity (tranche-2 admission test) incl. determining the deployed `i_bos_swing` | parity report | no |
| P5 | Live vs replay bracket-formula parity per family at the pinned SHA (entry/stop/target arithmetic, tick constants) | compatibility matrix (§2.3) | no |
| P6 | Roll ledger for P-REPLAY (dates, gap sizes) and gap ledger on the 15m grid; P-LIVE ledger reused | ledgers | no |
| P7 | MNQ OOS corpus 2026-07-24 → 2026-09-14 via `polygon_to_replay` (data fetch only) so P-OOS is not MES-only; and the P-OOS-PROSPECTIVE collection is just the existing journal (no collector change) | corpus + manifest | no |
| P8 | Independent spot-check protocol: ≥ 3 seeded rows re-derived end-to-end (levels, events, outcome, R) by a party other than the analysis author; ≥ 1 headline statistic recomputed from raw files | attestation | no |

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

---

**Verdict: NEEDS DEFINITION FIXES.** The data to answer the question exists (two years of
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
