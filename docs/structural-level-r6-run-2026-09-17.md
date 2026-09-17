# R6 — Frozen P1 Structural Feature Table over the Sealed MNQ/MES Candidates (2026-09-17)

**Verdict: `R6 PASS — FEATURES FROZEN`.** Authorised by the operator for R6 only. The frozen P1
implementation (`research/structural_level_features.py`: `build_levels` once per B0,
`label_candidate` once per candidate — unmodified, no constant tuned) was run over the canonical
R5 `candidates.jsonl` of each instrument (#635) and the resulting `features.jsonl` files are frozen
and hashed. **Neither sealed outcome file was opened, read, joined, printed or summarised; R7 was
not started; no performance metric of any kind was computed; no runtime, VPS, deployment, Pine,
config, risk, strategy or broker change.**

## 1. Provenance

| Item | Value |
|---|---|
| Starting `main` SHA | `04c3dcb3f00716054074525926cef83063874e59` (worktree clean apart from the corpus symlink; no `.env`) |
| R5 candidates (input) | MNQ `148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a` (40,460 rows) · MES `e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe` (39,987 rows) — equal to the hashes recorded in #635; the driver refuses any other hash (`--expect-sha256`) |
| R5 record / manifests | on-disk `logs/structural_level_r5/{MNQ,MES}/manifest.json` == committed `docs/structural-level-r5-manifests/r5_2026-09-17_{MNQ,MES}_manifest.json` (path fields aside) |
| Sealed outcomes | `logs/structural_level_r5/{MNQ,MES}/outcomes.sealed.jsonl` present — MNQ `b8e86c64…` 12,700,716 bytes, MES `326ae59f…` 12,450,322 bytes (hashes recomputed as bytes only; contents never read); the R6 driver takes no outcome path and its manifest records `outcome_files_read: []` |
| Corpus | `data/replay_polygon_v2/{MNQ,MES}` — `MANIFEST.json` sha256 MNQ `1f16b81b232f0275…`, MES `ca4481502b4f9be3…` = committed copies (per-file hashes verified 543/543 in R5; the driver refuses another manifest hash); 40,907 bars each, 2024-10-01T00:00Z → 2026-06-26T20:45Z |
| P1 module | `research/structural_level_features.py` sha256 `14043ae4a783a51d601d138e49597c56a68af74242a4f00a84932b57752e0ad9`, `PREREG_VERSION = "1.5"` (level/event definitions unchanged since v1.3); frozen constants recorded in every feature row (`TAU_MTR` 0.25, `PROX_MTR` 0.5, `K_SWEEP_BARS` 4, `R_RETEST_BARS` 8, `N_ACCEPT` 2, `D_MAX_MTR` 1.0, `LIVE_BAR_WINDOW` 960); VWAP diagnostic / NOT_ADMITTED (reported in `levels`, never an anchor) |
| Driver | `scripts/structural_level_r6_features.py` (`slr6-features-v1`, new in this PR; offline, read-only, deterministic; 3 tests in `tests/test_structural_level_r6.py` incl. never-imports-runtime) |
| Population | MNQ + MES only (every row's `instrument` and candidate-key instrument segment checked); no M2K / MGC / MCL / MBT |
| Feature output | `logs/structural_level_r6/MNQ/features.jsonl` sha256 **`1bfd7edd3e14ae8c8ea09497b678e69b8f753bbf55ca04fcbd8c9f76c44c0503`** (40,460 rows, 269,242,942 bytes) · `logs/structural_level_r6/MES/features.jsonl` sha256 **`eda7cfaf1dc24801b6b94a8e999625a87faefaa9b1c4d577cfcf257d7a84e6b1`** (39,987 rows) — manifests committed as `docs/structural-level-r6-manifests/r6_2026-09-17_{MNQ,MES}_manifest.json`; feature files and the independent rerun (`logs/structural_level_r6_b/`) preserved in the checkout's gitignored `logs/structural_level_r6_2026_09_17/` (copies byte-identical) |
| Runs | MNQ A 11:29:17 → 11:37:08Z, MNQ B → 11:44:46Z, MES A → 11:52:24Z, MES B → 11:59:59Z; all exit 0 |

Feature row contents: `candidate_key` (verbatim), `instrument`, `bar_ts` / `b0_ts`, `strategy` / `family` / `p1_family`, `direction` / `entry` / `stop` / `target`, `session_candidate` / `session_p1`, `contract` / `is_roll_utc_day` / `contract_roll_utc_date`, `recent_bars_warmup`, `candle_present_in_corpus`, `corpus_day_position`, `p1` identity block, `n_bars_in_window`, `mtr15`, `close_b0`, `levels` (every P1 level with value / status ∈ {AVAILABLE, NOT_AVAILABLE, NOT_IN_WINDOW} / kind / zone top-bottom / tests / broken / exploratory / reason / gap_minutes / gap_contaminated / formed_ts), `admitted_levels_not_available`, `admitted_levels_not_in_window`, `admitted_levels_gap_contaminated`, `level_warnings`, `hypotheses` H1–H6 (label ∈ {T, F, APPLICABLE (H4), NOT_APPLICABLE, NOT_AVAILABLE} + anchor / reason / P1 detail). Causality: the driver hands P1 only bars with `ts ≤ B0` (last 960); the test proves appending later bars leaves every row unchanged. Missing defining data → `NOT_AVAILABLE`; neither T nor F → `NOT_APPLICABLE`; nothing substituted.

## 2. Integrity

| Check | MNQ | MES |
|---|---|---|
| feature rows vs candidate rows | 40,460 = 40,460 | 39,987 = 39,987 |
| candidate-key set equality (missing / extra) | equal; 0 / 0 (order identical to the candidate file) | equal; 0 / 0 |
| duplicate / conflicting feature keys | 0 | 0 |
| population separation | 40,460 × `MNQ`, no other instrument | 39,987 × `MES` |
| outcome / outcome-derived fields | none (checked against `outcome, result, pnl, pnl_ticks, exit_price, exit_reason, entry_filled, mae, mfe, win, loss`) | none |
| outcome file read | no (`outcome_files_read: []`) | no |
| determinism (independent rerun B) | byte-identical, same sha256 | byte-identical, same sha256 |
| B0 present in corpus | 40,460 / 40,460 (30,464 unique B0) | 39,987 / 39,987 (30,300 unique B0) |
| P1 version / hash, corpus manifest hash recorded | yes (every row + manifest) | yes |
| **integrity status** | **PASS** | **PASS** |

**Census (descriptive only — not edge, not performance; label counts say nothing about outcomes):**

| Hypothesis | MNQ T / F / other | MES T / F / other |
|---|---|---|
| H1 sweep→reclaim vs touch | 4,800 / 3,996 / NOT_APPLICABLE 31,663, NOT_AVAILABLE 1 | 4,785 / 4,872 / 30,328, 2 |
| H2 break→retest→hold vs accept | 191 / 3,426 / 36,842, 1 | 210 / 3,237 / 36,538, 2 |
| H3 wick-reject vs proximity | 6,042 / 1,326 / 33,091, 1 | 5,988 / 1,282 / 32,715, 2 |
| H4 test count / age | APPLICABLE 12,882 / NOT_APPLICABLE 27,577 / NOT_AVAILABLE 1 | 13,995 / 25,990 / 2 |
| H5 cluster | 7,422 / 2,531 / 30,506, 1 | 7,884 / 2,762 / 29,339, 2 |
| H6 room / target relation | 13,753 / 19,884 / 6,822, 1 | 12,262 / 21,379 / 6,344, 2 |

Family census = the R5 census exactly (12 families; MNQ 13,819 / 6,644 / 6,252 / 6,075 / 2,336 / 1,491 / 894 / 763 / 719 / 612 / 584 / 271; MES 13,563 / 6,377 / 6,811 / 5,571 / 2,358 / 1,550 / 763 / 1,402 / 663 / 501 / 155 / 273 — order as in the R5 record).

Availability (rows where at least one admitted level has the status; per-level counts in the manifests): NOT_AVAILABLE — MNQ 34,200 rows / MES 33,897 (dominated by `NY_ORB_*` outside the NY session 26,829 / 26,366, `LDN_ORB_*` 15,041 / 14,644, `LC_ZONE_4H_SUPPLY` 14,782 / 14,677; `PDH/PDL/PDC_BAR` 82 / 89 and `PWH/PWL` 2,879 / 2,879 = the corpus's first day / first week, fail-closed rather than back-filled from the warm-up); NOT_IN_WINDOW — MNQ 40,446 / MES 39,974 (`ONH/ONL` outside the NY session 26,829 / 26,366, `LDN_ORB_*` 14,014 / 14,029, `NY_ORB_*` 261 / 336); gap-contaminated (prereg §9.4/§9.5, a ≥ 45-min CME-open gap inside the formation window) — MNQ 16,180 / MES 16,056 rows (`LC_ZONE_4H_DEMAND` 14,972 / 14,383, `LC_ZONE_1H_DEMAND` 9,573 / 9,613, `LC_ZONE_4H_SUPPLY` 9,236 / 9,183, `PWH/PWL` 8,098 / 8,030, `LC_ZONE_1H_SUPPLY` 7,893 / 7,497, `PDH/PDL/PDC_BAR` 2,013 / 2,002, `ONH/ONL` 298 / 295). These flags are carried per row for the preregistered eligible-denominator rules; nothing was excluded or repaired here.

## 3. Safety confirmation

- Sealed outcomes never opened or read (bytes hashed only to confirm identity with #635).
- R7 not started; no seeded spot-check performed (R7 must be run by a separate party per #636).
- No win rate, P&L, expectancy, MAE/MFE, hit rate, ranking or feature-conditioned result computed; features were never joined to anything.
- No threshold tuned; no prereg definition changed; no runtime / VPS / deployment / Pine / config / risk / strategy / broker change. STOP.
