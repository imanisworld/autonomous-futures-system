# P8 — Post-R7 Outcome Opening and Preregistered Outcome Analysis of the Frozen MNQ/MES Structural-Level Study (2026-09-17)

**Verdict: `P8 PASS — OUTCOMES VERIFIED`.** Authorised by the operator on 2026-09-17 ("GO — POST-R7
OUTCOME OPENING / P8 VERIFICATION"). The sealed R5 outcome files were opened for the first time in
this session, joined on `candidate_key` only to the frozen R5 candidates and R6 features, and the
preregistered H1–H6 trading-filter (TF) analysis of prereg v1.5 §6–§10 was run. Nothing was
regenerated, no definition / threshold / label / bracket / gap rule was changed, the Good-Friday
465-minute gap flags stand as frozen, and the frozen artifacts on disk are byte-identical before and
after. **No runtime, VPS, deployment, `.env`, broker, Pine, strategy or config change; nothing is
promoted; no forward campaign was started.** RESEARCH / EVIDENCE PHASE ONLY.

**Headline (one sentence):** the frozen wick-reject event on the nearest supportive structural level
(H1 ≈ H3; the two T-populations overlap 95–97 %) is *supported in-sample* on both instruments under
every preregistered §10 criterion (+0.12…+0.21 R, Holm p ≤ 0.003, 3/3 folds, all three sessions,
9/12 families), but the conditioned candidates are **still net-negative** (−0.12 R MNQ / −0.23 R MES
versus −0.19 / −0.29 unconditioned): the event removes worse trades, it does not create a positive
expectancy, and it has no out-of-sample confirmation yet. H2, H5 and H6 do not hold as frozen (H6
reverses sign on both instruments); H4 test-count shows a consistent "fresher is better" trend.

Governing documents: `docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` (v1.5),
`docs/structural-level-p2-candidate-regeneration-spec-2026-09-17.md` (frozen fill/path rules),
`docs/structural-level-r5-run-2026-09-17.md`, `docs/structural-level-r6-run-2026-09-17.md`,
`docs/structural-level-r7-attestation-2026-09-17.md`, `docs/r7-outcome-seal-boundary-2026-09-18.md`.
Evidence files: `docs/structural-level-p8-artifacts/` (results JSON, seeded re-derivation, awk
headline path, auxiliary checks, run log, the two standalone check scripts); analysis tool
`scripts/structural_level_p8_outcome_analysis.py` (`slp8-outcome-analysis-v1`, standalone: stdlib +
numpy only, no repo imports, refuses to run on any hash other than the frozen ones).

---

## 1. Frozen Artifact Check — PASS

Recomputed 2026-09-17 ~15:45Z from the checkout's gitignored `logs/` copies (never modified; also
identical in the `_from_b` copies). `origin/main` at start was `10c3d09` (#641 / #642 merged after
the `b3a639fe` the authorisation expected — docs/ops commits, not study artifacts; nothing under
`research/`, `strategy/`, `replay/`, `scripts/structural_level_*` changed: pinned module hashes
below still equal the R5/R6/R7 records).

| Artifact | Expected | Recomputed | Match |
|---|---|---|---|
| MNQ `candidates.jsonl` | `148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a` | same, 40,460 rows | ✔ |
| MES `candidates.jsonl` | `e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe` | same, 39,987 rows | ✔ |
| MNQ R6 `features.jsonl` | `1bfd7edd3e14ae8c8ea09497b678e69b8f753bbf55ca04fcbd8c9f76c44c0503` | same, 40,460 rows | ✔ |
| MES R6 `features.jsonl` | `eda7cfaf1dc24801b6b94a8e999625a87faefaa9b1c4d577cfcf257d7a84e6b1` | same, 39,987 rows | ✔ |
| MNQ `outcomes.sealed.jsonl` | `b8e86c64cffb12eeb812864b02b1e3f32fbc729f46602efab46e1f9228413c60` | same, 40,460 rows | ✔ |
| MES `outcomes.sealed.jsonl` | `326ae59f9ff13590716d27eda0c25031615864d348ffbbd1871b95f80250bc08` | same, 39,987 rows | ✔ |
| R5 manifests (local vs committed `docs/structural-level-r5-manifests/`) | — | equal except the scrubbed path fields | ✔ |
| Corpus `MANIFEST.json` MNQ / MES | `1f16b81b232f0275…` / `ca4481502b4f9be3…` | same | ✔ |
| `strategy/shadow_setups.py` / `shadow_resolver.py` / `replay/replay_engine.py` | `4e535d70` / `d200ae0d` / `9ab1f72b` | same | ✔ |
| `research/structural_level_features.py` (P1) / `scripts/structural_level_r6_features.py` | `14043ae4` / `b222a9ce` | same | ✔ |

The analysis tool re-hashes all six files at start and exits `P8 BLOCKED: FROZEN ARTIFACT DRIFT` on any
mismatch (`p8_run.log` line 1: "all 6 hashes match").

## 2. Seeded Outcome Re-derivation — PASS (3 / 3 rows agree on every field)

Independent resolver: `docs/structural-level-p8-artifacts/p8_rederive_outcomes.py` — imports only
`json`/`sys`, does **not** import `strategy.shadow_setups`, `replay/` or any repo module; the rules
were transcribed from the frozen spec and re-implemented: forward window = the remaining candles in the
same replay **day file** after B0 (`candles[idx+1:]`, as `replay_engine.py:549` does); resting entry
fills on the first forward bar with `low ≤ entry ≤ high`; from the fill bar on, LONG `target_hit =
high ≥ target`, `stop_hit = low ≤ stop` (SHORT mirrored); **both hit on one bar → STOP** (pessimistic,
intrabar path unknowable); **target-only touch on the fill bar → ignored** (flagged), resolution
continues; nothing by end of file → `OPEN`; no fill → `NO_FILL`; `pnl_ticks` = signed
`(exit − entry) / 0.25`; net R = `(pnl_ticks·tv − 2·tv − $1.48) / (stop_ticks·tv)` (prereg §8.1,
`resolved_economics`, MNQ tv $0.50 / MES $1.25 — asserted against `config.futures_contracts`).

| # | Row (R7 seed) | Bracket | Path (forward bars) | Independent | Sealed | Agree |
|---|---|---|---|---|---|---|
| 1 | MNQ `strat_22_reversal_observed` SHORT B0 2026-02-04T14:30Z | E 25333.25 / S 25389.50 / T 25220.75 (225 ticks) | fwd 1 (14:45Z) H 25371.75 L 25186.00 → fills; low ≤ target but stop not hit → **fill-bar target-only ignored**; fwd 2 (15:00Z) H 25260.75 L 25107.75 → target | WIN, TARGET_HIT, exit 25220.75, +450.0 ticks, fill 1, exit 2, `fill_bar_target_ambiguous_ignored = true`; gross R +2.000, **net R +1.978** | identical (8/8 fields) | ✔ |
| 2 | MNQ `impulse_first_pullback_observed` LONG B0 2025-06-16T00:15Z | E 21919.75 / S 21881.50 / T 21996.25 (153 ticks) | fwd 1–7 never trade 21919.75 (highs ≤ 21917.75); fwd 8 (02:15Z) H 21938.0 L 21914.75 → fills; fwd 27 (07:00Z) H 21997.0 → target; stop never hit (min low 21876.75 on fwd 1, pre-fill) | WIN, TARGET_HIT, exit 21996.25, +306.0 ticks, fill 8, exit 27, flag false; gross +2.000, **net +1.968** | identical | ✔ |
| 3 | MES `ema_pullback_trend` LONG B0 2026-04-10T00:45Z | E 6864.0 / S 6855.5868 / T 6882.509 (33.65 ticks) | fwd 1 (01:00Z) H 6864.5 L 6860.75 → fills; highs never reach 6882.509 (max 6869.0); fwd 23 (06:30Z) L 6854.25 ≤ stop | LOSS, STOP_HIT, exit 6855.5868, −33.65 ticks, fill 1, exit 23, flag false; gross −0.9999, **net −1.095** | identical | ✔ |

Negative controls on the same code (`p8_rederive_outcomes.py` module, run interactively): moving row 1's
stop to 25370 (fill bar then straddles both) → `LOSS / STOP_HIT / bar 1` (pessimistic rule fires);
unreachable target → `OPEN / EOD_OPEN`; entry never traded → `NO_FILL`. The comparator is therefore not
vacuous. Row 1 is a live exercise of the frozen fill-bar rule (a same-bar fill-and-target touch was
not credited); the pessimistic stop-before-target rule remains in force on every row of the analysis
(the resolver output is what was sealed; this re-derivation confirms it on the seeded rows). Full
record: `p8_seeded_rederivation.json` (`all_rows_agree: true`).

## 3. Independent Headline Check — PASS (two calculation paths agree exactly)

Path A (`p8_headline_awk.sh`): `sed` + `awk` join of the raw JSONL files, no Python, no numpy.
Path B (`scripts/structural_level_p8_outcome_analysis.py`): Python/numpy join. Computed before B was run.

| Statistic | MNQ (A) | MNQ (B) | MES (A) | MES (B) |
|---|---|---|---|---|
| candidates = outcome rows, unjoined | 40,460 / 0 | 40,460 / 0 | 39,987 / 0 | 39,987 / 0 |
| WIN / LOSS / NO_FILL / OPEN | 9,299 / 22,820 / 4,330 / 4,011 | same | 9,126 / 22,719 / 4,116 / 4,026 | same |
| terminal rows (WIN+LOSS) | 32,119 | 32,119 | 31,845 | 31,845 |
| win rate (terminal) | 0.2895 | 0.2895 | 0.2866 | 0.2866 |
| mean gross R | −0.1308 | −0.1308 | −0.1363 | −0.1363 |
| mean net R | **−0.1868** | **−0.1868** | **−0.2862** | **−0.2862** |
| sum net R | −6,000.74 | −6,000.74 | −9,113.33 | −9,113.33 |

Per-session and per-family means in path A's output (`p8_headline_awk.out`) equal path B's
`by_session` / `by_family` tables to 4 dp. Join = 100 % (prereg K7 gate ≥ 90 %); the R5 roll ledger
reproduces the known 2026-06-11 MNQ gap (+277.5) → K7 PASS.

## 4. Outcome Analysis — population (P-REPLAY, 2024-10-01 → 2026-06-26, S1-R provenance)

Every number below is per instrument; nothing is pooled without both strata beside it. Net R is
after the pinned cost model. `breakeven` = 0 by construction (the resolver has no BE exit).

| | MNQ | MES |
|---|---|---|
| candidates | 40,460 | 39,987 |
| terminal (WIN+LOSS) / NO_FILL / OPEN | 32,119 / 4,330 (10.7 %) / 4,011 (9.9 %) | 31,845 / 4,116 (10.3 %) / 4,026 (10.1 %) |
| win / loss | 9,299 / 22,820 (WR 28.95 %) | 9,126 / 22,719 (WR 28.66 %) |
| `fill_bar_target_ambiguous_ignored` rows | 1,389 | 1,029 |
| mean gross R / mean net R / median net R | −0.131 / **−0.187** / −1.03 | −0.136 / **−0.286** / −1.09 |
| net R quantiles 5/25/50/75/95 % | −1.12 / −1.06 / −1.03 / +1.91 / +2.12 | −1.31 / −1.17 / −1.09 / +1.76 / +2.01 |
| net R min / max | −1.97 / +5.31 | −2.06 / +2.82 |
| median stop (ticks) | 109 | 24.5 |
| cost drag (gross − net, R) | 0.056 | 0.150 |
| sum net R (1 lot each, all rows) | −6,001 | −9,113 |
| worst-1 % / worst-5 % of losses as share of total $ loss | 5.2 % / 17.5 % | 5.0 % / 16.2 % |
| by session (mean net R, n terminal) | asian −0.149 (12,491) · london −0.198 (11,179) · new_york −0.229 (8,449) | asian −0.272 (12,072) · london −0.305 (11,164) · new_york −0.282 (8,609) |
| by direction | LONG −0.184 (17,692) · SHORT −0.190 (14,427) | LONG −0.287 (17,285) · SHORT −0.285 (14,560) |
| by fold (1/2/3) | −0.166 / −0.202 / −0.193 | −0.254 / −0.303 / −0.302 |
| by regime (`reconstructed_market_condition`) | CHOPPY −0.157 · TRENDING −0.158 · RANGE_BOUND −0.194 · DEAD −0.303 | TRENDING −0.239 · CHOPPY −0.266 · RANGE_BOUND −0.309 · DEAD −0.365 |

Descriptive reading: the ungated shadow-candidate population is uniformly net-negative on both
instruments in every session, direction, fold and regime; the loss distribution is not concentrated
(worst 1 % of losers carry 5 % of the loss dollars — losses are the −1 R stop, not outliers).
MES's worse net figure is cost drag on a 24-tick median stop, not a worse gross. Family table:
`p8_results.json → by_family` (all 12 families net-negative on MNQ; MES `strat_122_pullback` +0.02 R
on n = 98 terminal, the only positive cell, below the ≥ 30-per-contrast-level floor for any
inference; `transition_failed_breakdown_reclaim` has WR 54–56 % with negative mean R because its
frozen bracket is < 1 : 1, and is `NOT_TESTABLE` per the P5 matrix). A "sequential 1-lot drawdown"
is not meaningful here (rows overlap; it equals the running sum) and is recorded only as such.

Not available from the frozen outcome data: MAE/MFE (neither convention — the sealed row has no
excursion fields), so the §8.1 "both MAE conventions" line is `NOT_AVAILABLE` for the whole study.

### 4.1 Outcome-blind pre-commitments (fixed before the outcome file was read; recorded in `precommit`)

| | MNQ | MES |
|---|---|---|
| fold boundaries (1/3, 2/3 row quantiles of B0) | 2025-05-02T07:15Z / 2025-11-24T16:15Z | 2025-05-02T20:00Z / 2025-11-27T02:00Z |
| H4 age tertiles, fold-1 APPLICABLE rows (h) | 3.00 / 10.50 (n 4,215) | 2.75 / 10.00 (n 4,486) |
| roll seams (CME trading day containing the UTC-midnight seam) | 8 (2024-10-01 warm-up seam + 7 in-window) | same |

Per-row eligibility for a hypothesis (all counted, §9): label ∈ {T, F} (H4: APPLICABLE); the anchor
level (H6: the opposing level) not `gap_contaminated` in the frozen feature row (§9.4, the
Good-Friday flags included, untouched); B0 not inside the §9.5 roll window for that level type
(PDH/PDL/PDC/ONH/ONL/ORB: seam day + 1 trading day; PWH/PWL: seam week + next; `LC_ZONE`: 10 trading
days — derived here from `contract_roll_utc_date`, R6 did not pre-flag it); outcome terminal.
Permutation strata = session × strategy id (finer than the prereg's `family_of` grouping, i.e. more
conservative; 10,000 shuffles, seed 20260917); day-block bootstrap 2,000 resamples of CME trading days.

## 5. H1–H6 Results

Effect = mean net R (T) − mean net R (F); frozen direction "T better" (H4 two-sided). Holm over the
six TF tests per instrument. `wf` = walk-forward (sign agreement ≥ 2/3 folds and no fold < −0.05 R
against the frozen direction). Cells = session strata with n ≥ 30. All exclusion counts are in
`p8_results.json → hypotheses.<H>.exclusions` and `label_census`.

| H | Instr. | label census T / F / N-A | eligible terminal (T / F) | mean R T / F | WR T / F | effect R | perm p | Holm p | 95 % day-block CI | ex-top-1 / ex-top-5 | sess×fam-adjusted | folds 1/2/3 | wf | session cells (asian / london / NY) | families same sign (n≥30) | §10 "supported" (in-sample) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **H1** sweep→reclaim vs touch | MNQ | 4,800 / 3,996 / 31,663 | 6,102 (3,580 / 2,522) | −0.132 / −0.288 | 30.5 / 25.0 % | **+0.156** | 0.0007 | 0.0028 | [+0.084, +0.227] | +0.157 / +0.160 | +0.100 | +0.179 / +0.179 / +0.111 | ✔ | +0.204 / +0.242 / +0.046 | 9 / 12 | **all 7 criteria met** |
| | MES | 4,785 / 4,872 / 30,328 | 6,936 (3,631 / 3,305) | −0.226 / −0.348 | 29.7 / 25.4 % | **+0.122** | 0.0004 | 0.0016 | [+0.054, +0.195] | +0.123 / +0.123 | +0.104 | +0.084 / +0.124 / +0.155 | ✔ | +0.225 / +0.147 / +0.094 | 9 / 12 | **all 7 criteria met** |
| **H2** retest-hold vs age-matched accept | MNQ | 191 / 3,426 / 36,842 | 1,841 used (127 / 1,714); bin 6–8 dropped (n_T 6 < 12), 521 rows | −0.329 / −0.272 | 22.8 / 25.4 % | −0.057 (equal-weight bins **−0.215**) | 0.680 | 1.0 | [−0.316, +0.217] | −0.056 / −0.051 | −0.065 | +0.016 / −0.030 / −0.157 | ✗ | −0.788 (n 203) / +0.066 / +0.009 | 1 / 3 | not supported |
| | MES | 210 / 3,237 / 36,538 | 1,749 (145 / 1,604); bin 6–8 dropped, 514 rows | −0.460 / −0.276 | 21.4 / 27.6 % | −0.184 (bins **−0.285**) | 0.915 | 1.0 | [−0.411, +0.042] | −0.183 / −0.177 | −0.144 | −0.164 / −0.319 / −0.025 | ✗ | −0.447 / −0.195 / −0.129 | 0 / 3 | not supported |
| **H3** wick-reject vs proximity | MNQ | 6,042 / 1,326 / 33,091 | 4,904 (4,029 / 875) | −0.129 / −0.337 | 30.6 / 24.7 % | **+0.207** | 0.0001 | 0.0006 | [+0.100, +0.315] | +0.208 / +0.209 | +0.190 | +0.300 / +0.139 / +0.193 | ✔ | +0.280 / +0.230 / +0.263 | 8 / 11 | **all 7 criteria met** |
| | MES | 5,988 / 1,282 / 32,715 | 4,898 (4,088 / 810) | −0.232 / −0.429 | 29.6 / 24.8 % | **+0.196** | 0.0001 | 0.0006 | [+0.073, +0.322] | +0.196 / +0.194 | +0.175 | +0.188 / +0.051 / +0.370 | ✔ | +0.165 / +0.240 / +0.279 | 7 / 9 | **all 7 criteria met** |
| **H5** cluster ≥ 2 vs == 1 | MNQ | 7,422 / 2,531 / 30,506 | 6,514 (4,782 / 1,732) | −0.189 / −0.203 | 29.0 / 28.6 % | +0.014 | 0.417 | 1.0 | [−0.073, +0.104] | +0.014 / +0.015 | +0.007 | +0.018 / −0.002 / +0.025 | ✔ | +0.130 / −0.126 / +0.100 | 5 / 12 | not supported (null) |
| | MES | 7,884 / 2,762 / 29,339 | 7,079 (5,220 / 1,859) | −0.286 / −0.301 | 28.4 / 28.5 % | +0.015 | 0.384 | 1.0 | [−0.083, +0.111] | +0.015 / +0.014 | +0.011 | +0.009 / −0.076 / +0.110 | ✗ | +0.037 / +0.017 / −0.032 | 6 / 11 | not supported (null) |
| **H6** target before vs beyond opposing level | MNQ | 13,753 / 19,884 / 6,822 | 20,569 (8,454 / 12,115) | −0.232 / −0.157 | 28.3 / 29.2 % | **−0.075** | 1.0 | 1.0 | [−0.136, −0.013] | −0.075 / −0.074 | −0.078 | −0.145 / −0.022 / −0.069 | ✗ | −0.132 / −0.062 / −0.125 | 2 / 12 | not supported — **sign reversed** |
| | MES | 12,262 / 21,379 / 6,344 | 20,663 (7,500 / 13,163) | −0.345 / −0.245 | 28.0 / 29.0 % | **−0.100** | 1.0 | 1.0 | [−0.163, −0.039] | −0.101 / −0.100 | −0.079 | −0.109 / −0.064 / −0.136 | ✗ | −0.161 / −0.033 / −0.151 | 2 / 12 | not supported — **sign reversed** |

**H4** (APPLICABLE rows; test-count bins {0, 1–2, ≥ 3}; age tertiles fixed from fold 1; permutation
Spearman, two-sided, bins shuffled within session × family; Bonferroni-2 inside, one Holm entry):

| Instr. | eligible (excl. NO_FILL / OPEN / gap / roll) | test-count bins mean net R (n) | ρ, p | folds ρ | age bins mean net R (n) | ρ, p | folds ρ | Bonf-2 / Holm |
|---|---|---|---|---|---|---|---|---|
| MNQ | 7,971 (1,248 / 1,263 / 1,561 / 839) | 0: −0.116 (855) · 1–2: −0.194 (1,984) · ≥3: −0.225 (5,132) | **−0.099**, 0.0001 | −0.098 / −0.107 / −0.097 | ≤3.0 h: −0.162 (3,198) · ≤10.5 h: −0.265 (2,652) · >10.5 h: −0.197 (2,121) | −0.032, 0.0011 | −0.023 / −0.035 / −0.027 | 0.0002 / 0.001 |
| MES | 8,885 (1,292 / 1,300 / 1,548 / 970) | 0: −0.198 (1,032) · 1–2: −0.315 (2,162) · ≥3: −0.314 (5,691) | **−0.133**, 0.0001 | −0.140 / −0.167 / −0.088 | ≤2.75 h: −0.266 (3,373) · ≤10.0 h: −0.301 (2,942) · >10.0 h: −0.346 (2,570) | −0.063, 0.0014 | −0.063 / −0.117 / **+0.004** | 0.0002 / 0.001 |

Test-count: fewer prior tests → better R on both instruments, all six folds same sign; the
untested-vs-≥3 gap is 0.109 R (MNQ) / 0.117 R (MES). Age: the ordering is not monotone (the middle
tertile is worst on MNQ) and MES fold 3 flips sign — not established. H4 overlaps the closed study's
F4 (fresh-vs-tested, sign-flipped there), which is why it is two-sided and why its only clean
confirmation is P-OOS-PROSPECTIVE (§11).

Holm-adjusted family per instrument: MNQ H1 0.0028 · H3 0.0006 · H4 0.001 · H2 / H5 / H6 1.0;
MES H1 0.0016 · H3 0.0006 · H4 0.001 · H2 / H5 / H6 1.0. Pooled (n-weighted, descriptive only, both
strata above): H1 +0.138, H3 +0.202, H5 +0.015, H2 −0.119, H6 −0.088; sign agreement MNQ/MES on all five.

### 5.1 What the H1/H3 effect is — and is not (auxiliary checks, `p8_aux_checks.json`)

- **One event, not two.** 96.9 % (MNQ) / 95.5 % (MES) of terminal H1-T rows are also H3-T (H3-T ⊃
  most of H1-T: 77 % the other way). Both T sets are the E3a wick-reject on the nearest supportive
  level; H1 and H3 differ in their F group and level set. Treat H1 + H3 as **one finding**.
- **The contrast is mostly F being worse, not T being good.** Against the unconditioned base
  (−0.187 / −0.286): H1-T +0.066 / +0.061 above base, H1-F −0.102 / −0.064 below; H3-T +0.051 /
  +0.049 above, H3-F −0.156 / −0.180 below; NOT_APPLICABLE rows sit exactly at base (±0.003).
  A "keep only T" filter therefore moves expectancy from ≈ −0.19 / −0.29 R to ≈ −0.12 / −0.23 R
  and from a 29 % to a 30 % win rate. **No conditioned subgroup is net-positive.**
- **Dependence.** rows-per-bar 1.15–1.25 among eligible rows; one-per-(bar, direction) dedupe leaves
  the effects unchanged (H1 +0.152 / +0.121; H3 +0.228 / +0.208). Under the literal §10 wording
  ("labelled anti-conservative when rows/bars < 1.5") the row-level permutation p carries that label;
  the day-block CIs, which do not depend on it, exclude 0 on both instruments for H1 and H3.
- **Fillability differs by label** (prereg §8.1 requires this to be reported): H1 NO_FILL 8.8 % (T) vs
  16.5 % (F) on MNQ, 8.1 % vs 13.9 % on MES; H3 8.4 % vs 10.3 % / 7.9 % vs 12.5 %; OPEN rates similar.
  Part of what the T label captures is that the candidate's resting entry gets filled at all.
- **Direction and session.** H1: LONG +0.104 / SHORT +0.220 (MNQ), +0.105 / +0.155 (MES);
  H3: LONG +0.077 / SHORT +0.412 (MNQ), +0.231 / +0.129 (MES) — positive in every cell, but the
  MNQ H3 effect is SHORT-heavy and the MNQ H1 new_york cell is only +0.046. T rows are 45–49 %
  london, 37–40 % new_york, 11–18 % asian; by family 34 % `strat_22_continuation_observed`,
  25 % `ema_pullback_trend` (REPLAY_ONLY stratum), 17 % `strat_22_reversal_observed`.
- **Level identity (descriptive, §7):** the T-minus-F gap is positive on every MAJOR level for H1 on
  MNQ except ONH (−0.04) and PWL (−0.04), and on MES except ONH (−0.07) with PWH / PDL / PWL ≈ 0;
  for H3 on all levels except `LC_ZONE_1H_DEMAND` (−0.26), `LC_ZONE_4H_DEMAND` (−0.03) and ONH
  (−0.04) on MNQ, and PDH (−0.06), PWH (−0.37) and `LC_ZONE_4H_SUPPLY` (−0.27) on MES. No single
  level carries the effect; the ORB and PDL/PWL levels contribute most.

## 6. Strategy / Family Findings

- All 12 replay families are net-negative on MNQ; on MES only `strat_122_pullback` (+0.018 R, n 98)
  is above zero and it is below every inference floor. Best large families: `ema_pullback_trend`
  −0.098 / −0.199 (REPLAY_ONLY — never pooled with live, Ruling 2), `strat_22_reversal_observed`
  −0.157 / −0.283, `strat_22_continuation_observed` −0.167 / −0.263. Worst: `impulse_first_pullback_observed`
  −0.290 / −0.421, `trend_consolidation_break_observed` −0.265 / −0.402, `strat_312_observed` −0.334 / −0.340.
- H1/H3 per family (`family_cells`): positive in 9/12 (H1) and 8/11 (H3) families on MNQ, 9/12 and
  7/9 on MES. Negative cells: H1 MNQ `trend_consolidation_break_observed` −0.070 (n 256),
  `strat_122_pullback` −0.083 (n 113), `transition_failed_breakdown_reclaim` −0.193 (n_F 17); H1 MES
  `orb_false_break_fade` −0.063 (n 169), `strat_122_observed` −0.052 (n 290), `strat_122_pullback`
  −0.277 (n_T 13); H3 MNQ `strat_322_reversal_observed` −0.382 (n_F 15), `orb_false_break_fade`
  (n_F 2), `transition…` −0.045; H3 MES `ema_pullback_trend` −0.078 (n_F 145, while MNQ is +0.210)
  and `transition…` −0.018. So the finding is broad but not universal, and the biggest family-level
  numbers (`strat_312` +0.45…+0.50, `strat_322` +0.34…+0.64, `strat_122_observed` +0.26…+0.57) sit
  on 80–180-row cells.
- H6 per family: negative in 10/12 families on both instruments (exceptions `transition_failed_breakdown_reclaim`
  +0.13 / +0.16 — NOT_TESTABLE family — and `strat_312` ≈ 0 / `strat_122_observed` +0.09 MES).
- No family, session or regime cell is a candidate for anything on its own: every cell is negative
  before conditioning and the conditioning gain is ≈ 0.05–0.07 R relative to base.

## 7. Problems / Limitations

1. **In-sample only.** P-REPLAY is the confirmatory in-sample population; the H1/H3 "supported" status
   is exactly what §10 defines and nothing more. P-OOS-MES (holdout, H1/H2/H3/H5 only) and
   P-OOS-PROSPECTIVE have not been scored. K5 is open.
2. **PR (price-response) outcomes were not computed.** They need bars after B0 (excursions at 4/8/16
   bars, `REACT_1`) which are not in the sealed outcome file and were not part of this authorisation
   ("join only to the frozen candidates and features"). K6 ("levels predict price, not trades") is
   therefore unassessed. The TF result already says the events do not make the trades net-positive.
3. **K4 (fill model) unassessed.** No S2 PaperBroker stratum exists on P-REPLAY; the sign check across
   S1-R / S1 / S2 needs P-LIVE.
4. **MAE/MFE not available** from the frozen outcome rows (both conventions `NOT_AVAILABLE`).
5. **Roll contamination was derived here, not in R6** (§9.5 windows applied from `contract_roll_utc_date`
   and the CME trading-day calendar of the candidate rows; the 2024-09-17 warm-up seam snaps to
   2024-10-01). Counts per hypothesis are in `exclusions.anchor_roll_contaminated` (e.g. H1 337 / 353,
   H6 2,787 / 2,679). A different but defensible trading-day count would move the eligible n by tens
   of rows, not the effect.
6. **Gap rule as frozen** (§9.4 counts holiday closures such as 2026-04-03 as gaps): H1 lost 345 / 378
   rows, H3 858 / 778, H5 1,023 / 1,006, H6 5,053 / 5,052 to `anchor_gap_contaminated`. Not repaired,
   not reclassified. Whether holidays should count is an operator definition question for a v2.
7. **Permutation strata** are session × strategy id (12), finer than the prereg's `family_of` mapping
   (which collapses the `strat_*` ids); this is the more conservative choice and is recorded.
8. **H2 is under-powered by construction:** T (retest-hold) is 0.5 % of candidates; the 6–8-bar
   age bin has n_T = 6 on both instruments and is dropped as the prereg says; 7 of 12 families have
   zero T rows. The negative point estimate is real on the rows that exist but is not a test of the idea.
9. **H4 age tertiles** were fixed here from fold-1 feature rows (outcome-blind, recorded) because R6
   did not pin them; the test-count bins are the prereg's.
10. `origin/main` moved to `10c3d09` (#641, #642) between the authorisation and this run; no study
    input or pinned module changed (§1).
11. The awk headline path shares the *inputs* (same JSONL) and the *formula* (net R) with the primary
    path — it is an independent implementation, not an independent data source.

Kill criteria: K1 not triggered (H1/H3 supported in-sample); K2 closed by P3/R4; K3 not triggered
(session × family adjustment leaves +0.10…+0.19 R); K4 open; K5 open; K6 unassessed (PR not run);
K7 PASS.

## 8. Classification (frozen labels only; nothing is promoted)

| Item | Classification | Basis |
|---|---|---|
| **Wick-reject on the nearest supportive structural level (H1 ≈ H3 as frozen)** | **PROMISING BUT UNPROVEN** | meets every §10 in-sample criterion on both instruments (+0.12…+0.21 R, Holm ≤ 0.003, 3/3 folds, 3/3 sessions, ≥ 7 families, ex-top-5 and adjusted effects intact, dedupe-stable); but in-sample only, one event counted twice, T subgroup still −0.12 / −0.23 R, fillability confound, K4/K5/K6 open |
| H4 test-count trend ("untested level better than ≥ 3 tests") | **PROMISING BUT UNPROVEN** (descriptive-strength) | consistent sign in all 6 folds and both instruments, 0.11–0.12 R between extreme bins; but overlaps prior-viewed F4, confirmatory only prospectively, and the age component does not hold |
| H4 age trend | **WAIT** | ρ −0.03 / −0.06, non-monotone bins, MES fold-3 sign flip |
| H2 break→retest→hold vs accepted break | **BROKEN** (as frozen) | negative on both instruments (equal-weight bins −0.22 / −0.29 R), 0/3 and 1/3 families, T population too small for the 6–8 bin — and the sign is against the hypothesis on every bin that exists |
| H5 structural clustering | **BROKEN** (null) | +0.014 / +0.015 R, CIs span 0, adjusted ≈ 0 |
| H6 room-to-target ("target before opposing level is better") | **BROKEN** (as frozen — **sign reversed**) | −0.075 / −0.100 R with day-block CIs excluding 0, negative in 10/12 families and every session on both instruments. This is the closed study's F11 construct; the reversal is recorded, **not** flipped into a "beyond is better" rule |
| Any family / session / regime as a standalone trade | **BROKEN** | every cell net-negative before and after conditioning |
| Promotion of anything to demo / live | **not applicable — nothing qualifies** | VALIDATED requires OOS, realistic fills, controlled drawdown, live/replay formula identity; none of that exists here, and the conditioned expectancy is negative |

Descriptive finding vs evidence for further paper testing: the H1/H3 wick-reject result is a
**descriptive in-sample finding strong enough to justify the preregistered holdout step**; it is
**not** evidence that any paper trade should be taken on it, because the filtered population still
loses.

## 9. Safe Next Step

**2026-09-18 update:** the preregistered one-shot P-OOS-MES holdout below has now been consumed. Exact record: `docs/structural-level-p8-oos-mes-2026-09-18.md`.

Result: H1 preserved sign and narrowly met its frozen 50%-effect floor (+0.0644 R vs +0.061 R); H3 preserved sign but missed its floor (+0.0618 R vs +0.098 R). Neither sign reversed, so K5 is not triggered, but the single H1≈H3 wick-reject finding is **not confirmed**. Classification remains **PROMISING BUT UNPROVEN**. Do not rerun or tune against P-OOS-MES. The next confirmatory gate is the already-frozen P-OOS-PROSPECTIVE window; no runtime change is required.

Historical next-step text, now completed:

The smallest justified step was the one the prereg's own order of operations named next (§11), and it
was **offline and box-free**:

1. **P-OOS-MES holdout, once, for the single frozen contrast only** (H1 and H3 as frozen — the
   wick-reject event; H2/H5 are also permitted on the holdout by §11 but have nothing to confirm):
   regenerate candidates + seal outcomes on `data/replay_polygon/MES_oos_2026-07-24_2026-09-08`
   with the R5 procedure (own go; the holdout corpus predates `london_orb_*`, so London
   `orb_false_break_fade` firings will be absent — P1 re-derives the ORB levels from OHLCV, so the
   *features* are unaffected; record the asymmetry), build R6 features, run this tool. Pass rule is
   already frozen: sign agreement and ≥ 50 % of the in-sample effect (MES: ≥ +0.061 R for H1,
   ≥ +0.098 R for H3); reversal = K5. Report sample size: the holdout is ~40 trading days, so expect
   roughly 500–700 eligible H1 rows — at the floor, not comfortably above it.
2. **Nothing on the box.** P-OOS-PROSPECTIVE is already collecting by definition (journal rows from
   2026-09-17T00:00Z on Z6; features are computed offline from bars) — no collector, runtime, `.env`,
   or gate change is needed or proposed. Its evaluation waits for the frozen window (≥ 2026-10-31
   and ≥ 50 % of the fold-3 terminal-row count: ≈ 5,380 MNQ / 5,320 MES terminal rows).
3. If a paper-forward *observation* is wanted before then, the only justified form is a **collection-only
   tag** (offline labelling of live shadow candidates with the frozen H1/H3 label, no gate, no order,
   no size) — and that requires its own pre-registration and go per prereg §10 ("gate candidacy is not
   an outcome of this study").

**Not recommended:** any paper campaign that *trades* on the label, threshold changes, a "beyond is
better" H6 rule, level-subset rules ("only PDL works"), or pooling `ema_pullback_trend` with live rows.

## 10. Safety confirmation / stop

- Frozen artifacts: read only; hashes identical before and after; nothing under `logs/` or `data/`
  written. No candidate, feature, label, bracket, gap flag or definition regenerated or edited.
- No runtime / VPS / deployment / restart / `.env` / broker / Pine / strategy / config / gate change;
  no instrument expansion; no unrelated PR touched. Docs + one standalone read-only script only.
- Nothing promoted; no forward campaign started. **STOP — return for operator review.**
