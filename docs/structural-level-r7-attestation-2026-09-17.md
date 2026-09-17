# R7 — Independent Outcome-Blind Attestation of the Frozen MNQ/MES Candidate Brackets and P1 Features (2026-09-17)

**Verdict: `R7 PASS — INDEPENDENT ATTESTATION`.** Three seeded candidate rows (2 MNQ, 1 MES) were
re-derived end-to-end from the source bars by an attester that did not author R6, and every
checked value — candidate identity and B0 alignment, bracket arithmetic, every P1 level (value,
status, zone bounds, tests/broken, formation time, gap minutes and gap flag), the availability /
not-in-window / gap-contaminated lists, the H1–H6 labels with anchors and details — equals the
frozen R6 `features.jsonl` row. **`outcomes.sealed.jsonl` was not opened, read, parsed, joined or
summarised; no outcome, R, P&L, hit-rate, MAE/MFE or headline statistic was computed; nothing was
ranked; no threshold or definition was touched; no runtime / VPS / deployment / Pine / config /
risk / strategy / broker change.** Per §7 of the boundary doc this authorises nothing except
returning for operator review; opening the sealed outcomes still needs a separate explicit go.

Governing documents: `docs/r7-outcome-seal-boundary-2026-09-18.md` (binding scope and seed rule),
`docs/prereg-dynamic-structural-level-attribution-2026-09-16.md` v1.5 (§3–§6, §9.4),
`docs/structural-level-r5-run-2026-09-17.md`, `docs/structural-level-r6-run-2026-09-17.md`.

## 1. Independence (§5)

| Item | Value |
|---|---|
| R6 author (disqualified) | session `58260d62-b3f4-46f4-ba7d-51afd26cc4ad` (memory + #637 record) |
| R7 attester | session `a3488d14-4fb2-4151-8b4d-6a2347a8c5ad` (the #595 Asia-cohort session; operator go 2026-09-17 ~12:20Z "begin the r7") — a different session from the R6 author |
| What the attester used | frozen prereg text, source bars, the three frozen candidate rows, the P1 level/event *definitions* |
| What the attester did **not** use as a calculation source | `scripts/structural_level_r6_features.py` (never imported or run); `research/structural_level_features.py` (read for the output schema and to resolve definition-level ambiguities of the prereg text, **not imported**); `context/location_context.py` (read for the §4.1 zone definition it pins, **not imported**) |
| Re-derivation code | `docs/structural-level-r7-attestation-2026-09-17/rederive.py` sha256 `15339a786cc9c21bcfb036c2daded3f199da35d3e2b43cede94bf8931fde9d4a` (published copy; the scratch original `4a43ddc0…` differs only in locating its inputs beside itself and writing a `.rerun.json`; rerun from the repo root reproduced `rederivation_report.json` byte-for-byte) — imports only `json, glob, statistics, datetime, zoneinfo`; contains its own ISO-week / CME-day / session map, MTR15, gap counter, CME-open-hours age, 60/240-minute aggregation, impulse-base zone scan, nearest-zone pick, level construction, E1–E9 events, §5.1 anchors, H1–H6 labelling and the three bracket formulas |

The attester acknowledges having read the R5/R6 memory lines and records during the night (as
part of an unrelated lane); none of that carries outcome information, and the boundary doc's
independence requirement is about the R6 *author*, which this session is not.

## 2. Frozen inputs verified before anything else (bytes / hashes only)

| Artifact | Expected (R5 #635 / R6 #637 records) | Recomputed 2026-09-17 12:2xZ | Match |
|---|---|---|---|
| `main` at start | — | `eec07dc` (= `origin/main`); worktree clean; branch `claude/structural-level-r7-attestation` cut from it | — |
| MNQ `candidates.jsonl` | `148768cd9a7b9b6f…` 40,460 rows | `148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a`, 40,460 | ✔ |
| MES `candidates.jsonl` | `e50fa5525422690c…` 39,987 rows | `e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe`, 39,987 | ✔ |
| MNQ `outcomes.sealed.jsonl` | `b8e86c64…` 12,700,716 bytes | `b8e86c64cffb12eeb812864b02b1e3f32fbc729f46602efab46e1f9228413c60`, 12,700,716 bytes — **hashed as bytes, never opened** | ✔ |
| MES `outcomes.sealed.jsonl` | `326ae59f…` 12,450,322 bytes | `326ae59f9ff13590716d27eda0c25031615864d348ffbbd1871b95f80250bc08`, 12,450,322 bytes — **hashed as bytes, never opened** | ✔ |
| MNQ `features.jsonl` (R6) | `1bfd7edd3e14ae8c…` 40,460 rows | `1bfd7edd3e14ae8c8ea09497b678e69b8f753bbf55ca04fcbd8c9f76c44c0503`, 40,460 | ✔ |
| MES `features.jsonl` (R6) | `eda7cfaf1dc24801…` 39,987 rows | `eda7cfaf1dc24801b6b94a8e999625a87faefaa9b1c4d577cfcf257d7a84e6b1`, 39,987 | ✔ |
| Corpus `MANIFEST.json` | MNQ `1f16b81b232f0275…` / MES `ca4481502b4f9be3…` | `1f16b81b232f0275753c295bbca4686dcc675eec6f0b488cff3099693103d8ac` / `ca4481502b4f9be33de6bdb611c02ac462c36eab1b759a0e62dab45c10594de3` | ✔ |
| P1 module | `14043ae4a783a51d…` (v1.5) | `14043ae4a783a51d601d138e49597c56a68af74242a4f00a84932b57752e0ad9`; identical on `HEAD` and `origin/main` | ✔ |
| Tick size | MNQ 0.25 / MES 0.25 | `config.futures_contracts.contract_economics` → (0.25, 0.5) / (0.25, 1.25) | ✔ |

Paths (checkout-local, gitignored): `logs/structural_level_r5_2026_09_17/structural_level_r5/{MNQ,MES}/`,
`logs/structural_level_r6_2026_09_17/structural_level_r6/{MNQ,MES}/`, `data/replay_polygon_v2/{MNQ,MES}/`.
Each selected candidate key occurs exactly once in its `features.jsonl` (no duplicate / conflicting key).

## 3. Seed selection (§4) — recorded before re-derivation

Seed material: `R7|1bfd7edd3e14ae8c8ea09497b678e69b8f753bbf55ca04fcbd8c9f76c44c0503|eda7cfaf1dc24801b6b94a8e999625a87faefaa9b1c4d577cfcf257d7a84e6b1`

`sha256(seed_material + "|" + candidate_key)` over all 80,447 frozen candidate keys (both instruments;
0 duplicate keys), sorted ascending. The first three digests already satisfy both-instrument
coverage, so exactly those three are used (no substitution; `selection.json` sha256
`78491d219021245863ee7e9918e1143759ef2a88f942f636d7f450b2b65b42dc`):

| # | Digest | Instrument | Candidate key |
|---|---|---|---|
| 1 | `00002c632dd56bdb34e8e3eb9a62cdab4c07f2b491dea450b01d2c6280e28a0c` | MNQ | `shadow_setups\|MNQ\|2026-02-04T14:30:00+00:00\|strat_22_reversal_observed\|SHORT\|25333.25` |
| 2 | `0000b18f6ce00cb125340251d270357cec17a29e5b84c3536c40d3f99f2a1398` | MNQ | `shadow_setups\|MNQ\|2025-06-16T00:15:00+00:00\|impulse_first_pullback_observed\|LONG\|21919.75` |
| 3 | `0000b381cfd6398926d9b099e4e243557edc6079f74b5703db090225c0ca9f6d` | MES | `shadow_setups\|MES\|2026-04-10T00:45:00+00:00\|ema_pullback_trend\|LONG\|6864.0` |

No outcome information entered the selection (only feature-file hashes and candidate keys).

## 4. Re-derivation — what was checked, per row

For each row, from the corpus bars with `ts ≤ B0` (last 960, matching the frozen `LIVE_BAR_WINDOW`),
the attester's code computed and compared **every** field below against the frozen candidate row
and the frozen feature row (`rederivation_report.json` sha256
`c3b5125db489db3b7cc4b20d690c8eaf046df477f0bc516ca04034b6c5e494af`; console log `run.log`):

- identity: key segments ↔ row fields; `bar_ts == b0_ts`; B0 present in the corpus; `corpus_day_position`
  and `journal_day_file` recounted from the corpus; `instrument, strategy, family, direction, entry,
  stop, target, contract, contract_roll_utc_date, is_roll_utc_day, recent_bars_warmup,
  candle_present_in_corpus, session_candidate` candidate ↔ feature;
- bracket: entry / stop / target / rr from the family's frozen formula on the source bars (below), plus
  the firing condition where it is bar-derivable;
- P1: `mtr15` (median TR of the last 64 bars), `close_b0`, `session_p1`, `n_bars_in_window`,
  `level_warnings`, `p1_family`;
- all 21 levels (PWH/PWL, PDH/PDL/PDC_BAR, ONH/ONL, PMH/PML, HOD/LOD, NY_ORB_H/L, LDN_ORB_H/L, VWAP
  diagnostic, four `LC_ZONE`s): `value, status, top, bottom, tests, broken, exploratory, reason,
  gap_minutes, gap_contaminated, formed_ts` — 11 fields × 21 levels;
- the `admitted_levels_not_available / _not_in_window / _gap_contaminated` lists;
- H1–H6: label, anchor and every detail field (`break_age`, `test_count`, `age_hours`, `cluster`,
  `dist_mtr`, `opposing`, `room_points`, `room_R`, `target_rel`, reason strings), with the field sets
  compared as well as the values.

### Row 1 — MNQ `strat_22_reversal_observed` SHORT, B0 = 2026-02-04T14:30Z (09:30 ET, new_york)

Source bars: B−2 14:00Z O 25357.75 H 25360.75 L 25325.00 C 25348.75 · B−1 14:15Z O 25349.00 **H 25389.25 L 25333.50** C 25385.50 · B0 14:30Z O 25385.00 H 25386.50 L 25230.00 C 25333.25.
Frozen formula (`_missing_strat_family`, SHORT): `entry = prev_low − tick`, `stop = prev_high + tick`, `target = entry − 2·risk`.
Re-derived: entry 25333.50 − 0.25 = **25333.25** ✔ · stop 25389.25 + 0.25 = **25389.50** ✔ · risk 56.25 · target 25333.25 − 112.50 = **25220.75** ✔ · rr **2.0** ✔. Firing: B−1 is `2U` vs B−2, B0 is `2D` vs B−1 (2-2 reversal) ✔; the corpus `previous_bar_high/low` fields equal the B−1 bar ✔.
P1: MTR15 **32.0** ✔, close **25333.25** ✔, 960 bars ✔. Levels all ✔ (PWH 26349.0 / PWL 25399.5, PDH 26027.75 / PDL 25218.0 / PDC_BAR 25421.5, ONH 25514.5 / ONL 25325.0 AVAILABLE (RTH), NY_ORB 25386.5/25230.0 and LDN_ORB 25509.75/25458.25 NOT_IN_WINDOW, LC_ZONE 1H demand 25218.0–25359.25 tests 3 / 1H supply 25465.75–25620.0 tests 8 / 4H supply 25470.25–26003.5 tests 5 / 4H demand NOT_AVAILABLE; 0 gap minutes everywhere).
H1 N/A (anchor PWL, neither swept nor touched) ✔ · H2 N/A `retested_earlier` on PWL, break_age 6 ✔ · H3 N/A (PWL beyond proximity) ✔ · H4 N/A ✔ · H5 N/A (PWL 2.0703 MTR beyond band) ✔ · **H6 F** (opposing ONL, room 8.25 pts = 0.1467 R, target beyond) ✔.

### Row 2 — MNQ `impulse_first_pullback_observed` LONG, B0 = 2025-06-16T00:15Z (20:15 ET Sun, asian)

Source bars: B−3 23:30Z C 21900.75 · B−2 23:45Z C 21903.75 · B−1 00:00Z H 21921.5 **L 21899.25** C 21917.25 · B0 00:15Z O 21917.0 **H 21919.5 L 21881.75** C 21895.25.
Frozen formula (`_impulse_first_pullback`, UP): `entry = B0.high + tick`, `stop = min(low of B−1, B0) − tick`, `target = entry + 2·risk`.
Re-derived: entry **21919.75** ✔ · stop 21881.75 − 0.25 = **21881.50** ✔ · risk 38.25 · target **21996.25** ✔ · rr **2.0** ✔. Firing: closes 21900.75 < 21903.75 < 21917.25 (three-close impulse) and 21895.25 < 21917.25 (pullback) ✔; corpus `trend_direction` = UP (the trend input is a corpus/state field, not re-derived — noted).
P1: MTR15 **40.5** ✔, close **21895.25** ✔. Levels all ✔ (PWH 22202.0 / PWL 21688.25 from the Jun-9–13 week; PDH 22155.0 / PDL 21688.25 / PDC_BAR 21865.75 from Friday 06-13; ONH/ONL NOT_IN_WINDOW (still forming); both ORBs NOT_AVAILABLE (no 09:30/03:00 bar yet on the new trading day); LC_ZONE 1H demand 21863.5–21921.5 tests 7 / 1H supply 21968.0–22055.0 tests 0 / 4H demand 21783.0–21921.0 tests 4 / 4H supply 21836.0–22064.25 tests 3; 0 gap minutes).
H1 N/A (PWL) ✔ · H2 N/A (no eligible broken MAJOR) ✔ · H3 N/A ✔ · H4 N/A ✔ · H5 N/A (anchor PDC_BAR at 1.3333 MTR) ✔ · **H6 F** (opposing LC_ZONE_1H_SUPPLY, room 48.25 pts = 1.2614 R, target beyond) ✔.

### Row 3 — MES `ema_pullback_trend` LONG, B0 = 2026-04-10T00:45Z (20:45 ET, asian)

Source bar B0: O 6859.25 H 6865.5 **L 6858.25 C 6864.0**, volume 4380.
EMA inputs: the attester recomputed EMA-9/21/55 from every corpus close up to B0 (standard EMA, SMA seed at the first in-window bar): 6858.4015 / **6856.5868** / 6844.2136 — max |Δ| vs the corpus `ema_*` fields **5.5 × 10⁻¹²** (the builder seeds from the 2024-09-17 warm-up; the difference has fully decayed).
Frozen formula (`_ema_pullback_trend`, bull stack): `entry = close`, `stop = min(low − 2·tick, ema21 − 4·tick)`, `target = entry + 2.2·risk`.
Re-derived: entry **6864.0** ✔ · stop min(6857.75, 6855.5868) = **6855.5868** ✔ · risk 8.4132 · target **6882.509** ✔ · rr **2.2** ✔. Firing: 6858.40 > 6856.59 > 6844.21 ✔, low 6858.25 ≤ ema9 and ≥ ema21 − 1.0 ✔, close > ema9 ✔.
P1: MTR15 **7.25** ✔, close **6864.0** ✔. Levels all ✔ (PWH 6654.75 / PWL 6352.0 from the Mar-30–Apr-3 week **gap_minutes 465, gap_contaminated True**; PDH 6876.0 / PDL 6792.5 / PDC_BAR 6860.5; ONH/ONL NOT_IN_WINDOW; ORBs NOT_AVAILABLE; LC_ZONE 1H demand 6793.0–6831.5 tests 19 **gap 465** / 4H demand 6792.5–6807.75 tests 0 **gap 465** / both supply zones NOT_AVAILABLE).
H1 N/A (anchor PDL) ✔ · H2 N/A ✔ · H3 N/A (LC_ZONE_1H_DEMAND beyond proximity) ✔ · H4 N/A ✔ · **H5 F** (anchor PDC_BAR within band, cluster 1) ✔ · **H6 F** (opposing PDH, room 12.0 pts = 1.4263 R, target beyond) ✔.

**Result: 0 differences on 3 / 3 rows** (`ALL ROWS AGREE: True`).

## 5. Comparator negative control

To show the agreement is not vacuous, a copy of the frozen rows was mutated (MES row: H5 label F→T,
PDH +1 tick, candidate stop −1 tick) and the same code re-run: it reported exactly those three
differences plus the consequential `H6.room_R` change (1.4263 → 1.3852), and `ALL ROWS AGREE: False`.
The mutation was discarded and the unmodified run repeated: 3 / 3 AGREE (`run.log`). Frozen
artifacts on disk were never modified (hashes in §2 are from before and the files were only read).

## 6. Observations (descriptive; no action, no change)

- The 465-minute gap flagged on the MES row's PWH/PWL and 1H/4H demand zones is **2026-04-03 (Good
  Friday)**: 31 CME-open 15m slots with no bar. The frozen §9.4 rule counts missing slots in CME
  open hours without a holiday calendar, so the level is flagged `gap_contaminated` (the R6 record's
  "flags carried per row, nothing excluded or repaired"). The attester's independent implementation
  of the same rule produces the same 465 — agreement, not a finding; whether holiday closures should
  count as gaps is a definition question for the operator, outside R7.
- Two inputs are corpus/state fields rather than bar-derivable at R7: `trend_direction` (impulse
  family firing gate) and the EMA seeds (reproduced to 10⁻¹¹). Neither affects the bracket check,
  which was done from OHLC.
- All three seeded rows are `NOT_APPLICABLE` on H1–H4 and label only H5/H6; that is what the seed
  produced (the R6 census shows H1–H4 `NOT_APPLICABLE` is the majority state) and no substitution
  was made.

## 7. Safety confirmation / stop

- Sealed outcome files: hashed as bytes for identity with #635 only; never opened, parsed or joined.
- No outcome, R, P&L, hit-rate, expectancy, MAE/MFE, ranking, or feature-conditioned result computed.
- No threshold, constant, level or event definition changed; the P1 module, corpus and frozen
  artifacts are byte-identical to the R5/R6 records.
- No runtime / VPS / deployment / Pine / config / risk / strategy / broker change. Docs-only PR;
  the files under `docs/structural-level-r7-attestation-2026-09-17/` are evidence (a standalone
  script with no repo imports, its inputs and its outputs), not code paths.
- **STOP.** Opening `outcomes.sealed.jsonl` and the deferred P8 outcome/statistic checks require a
  new explicit operator go (boundary doc §3, §7).
