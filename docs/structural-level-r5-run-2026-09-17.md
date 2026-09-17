# R5 — MNQ/MES Candidate Regeneration and Outcome Seal (2026-09-17)

**Verdict: `R5 PASS — SEALED`.** Authorised by the operator on 2026-09-17 for MNQ/MES only, run
exactly per `docs/r5-execution-runbook-2026-09-17.md` (direct `ReplayEngine`, one instance per
instrument/run, sequential day files; not `run_replay_batch.py`). **No outcome was opened, read,
summarised, aggregated, ranked or analysed. R6 and R7 were not started. No runtime, VPS,
deployment, config, risk, strategy, broker or Pine change.** The replay engine's own
`daily_review.md` / `replay_report.md` / `multi_day_replay_report.md` side files (DecisionEngine
performance summaries) were written by the engine and were not opened either.

## 1. Exact provenance

| Item | Value |
|---|---|
| Starting `main` SHA | `7ac5d53eb6d3dc833f5a4d17638b79879b24dc01` (origin/main, working tree clean; run in a fresh worktree on branch `claude/structural-level-r5-mnq-mes`, no `.env` present, `FORWARD_EVIDENCE_CAMPAIGN` unset, no other env) |
| Pinned files (sha256 prefix) | `strategy/shadow_setups.py` `4e535d70` · `strategy/shadow_resolver.py` `d200ae0d` · `replay/replay_engine.py` `9ab1f72b` (#621 included) · `scripts/polygon_to_replay.py` `a42e1cae` · `scripts/csv_to_replay.py` `332e48a9` · `risk_rules.yaml` `6c7e3f13` — all equal to the regeneration-spec §3.1 pins. `research/structural_level_features.py` `14043ae4` ≠ spec pin `4686ddf9`: the only diff since the pin is the two `PREREG_VERSION`/`PREREG_SHA` label constants (v1.3 → v1.5, #620/#623); level/event definitions unchanged; the module is not imported by the regeneration or extraction path (it is R6's input). Recorded, not a stop condition. |
| Corpus builder pin | manifest `builder.file_sha256` of `scripts/structural_level_corpus_build.py` = `da62561f` (at build); the file is now `8e53355c` because #623 added the `--contract` option (default scheduler path untouched). Corpus bytes are unchanged (below). |
| Corpus (MNQ) | `data/replay_polygon_v2/MNQ`, `MANIFEST.json` sha256 `1f16b81b232f0275…` = committed `docs/structural-level-corpus-manifests/replay_polygon_v2_MNQ_MANIFEST.json`; **543/543 day files sha256 = manifest**; 40,907 rows; 2024-10-01T00:00Z → 2026-06-26T20:45Z (warm-up fetched 2024-09-17); segments Z4→H5→M5→U5→Z5→H6→M6→U6 (`roll_days=8`); 7 rolls; 35 gap runs |
| Corpus (MES) | `data/replay_polygon_v2/MES`, manifest `ca4481502b4f9be3…` = committed copy; 543/543 files match; 40,907 rows; same window/segments (MES tickers); 7 rolls; 35 gap runs |
| Instruments | MNQ, MES only. M2K (`ROLL_PROVENANCE_UNKNOWN`) and MGC/MCL/MBT (tranche 2) excluded — the `--instruments` filter was set per run and every candidate row's `instrument` field was checked |
| Admitted window | 2024-10-01 → 2026-06-26 (prereg v1.4 Ruling 1), 15m |
| Replay run dirs | run A `logs/replay_p2_r5_a/{MNQ,MES}`, run B `logs/replay_p2_r5_b/{MNQ,MES}` — 543 day files each, 543 journals each, both runs exit 0 (A 10:33:11 → 10:33:27Z, B 10:33:27 → 10:33:43Z) |
| P2-X output (run A, canonical) | `logs/structural_level_r5/{MNQ,MES}/` — `candidates.jsonl`, `outcomes.sealed.jsonl`, `manifest.json` (tool `slp2-v1.5`, mode `full (outcomes sealed)`) |
| Candidates MNQ | `candidates.jsonl` sha256 `148768cd9a7b9b6f48d003d91cdf73b757b3018dca1343dc9a56b7e4f91c1a7a`, 40,460 rows, **no `outcome` field on any row** |
| Candidates MES | sha256 `e50fa5525422690ceff69a4a115c78517eb9cdec51f8dee5ee004733045a7ebe`, 39,987 rows, no `outcome` field |
| **Sealed outcomes MNQ** | `outcomes.sealed.jsonl` sha256 **`b8e86c64cffb12eeb812864b02b1e3f32fbc729f46602efab46e1f9228413c60`**, 40,460 rows (manifest) — unopened |
| **Sealed outcomes MES** | sha256 **`326ae59f9ff13590716d27eda0c25031615864d348ffbbd1871b95f80250bc08`**, 39,987 rows — unopened |
| Manifests (committed copies) | `docs/structural-level-r5-manifests/r5_2026-09-17_{MNQ,MES}_manifest.json` (sha256 of the originals `fd170dba…` / `e8ac537b…`; the only edit in the copies is the worktree path scrubbed to `<r5-worktree>`) |
| Evidence preserved | full `logs/structural_level_r5/` + `logs/structural_level_r5_from_b/` + run logs copied to the checkout's gitignored `logs/structural_level_r5_2026_09_17/` |

## 2. Integrity (from the P2-X manifests; recorded, not interpreted)

| Family | MNQ | MES |
|---|---|---|
| `strat_22_continuation_observed` | 13,819 | 13,563 |
| `strat_22_reversal_observed` | 6,644 | 6,377 |
| `ema_pullback_trend` (REPLAY_ONLY stratum; never pooled with live) | 6,252 | 6,811 |
| `impulse_first_pullback_observed` | 6,075 | 5,571 |
| `trend_consolidation_break_observed` | 2,336 | 2,358 |
| `orb_false_break_fade` | 1,491 | 1,550 |
| `strat_322_reversal_observed` | 894 | 763 |
| `strat_122_observed` | 763 | 1,402 |
| `strat_312_observed` | 719 | 663 |
| `transition_failed_breakdown_reclaim` (NOT_TESTABLE) | 612 | 501 |
| `strat_122_pullback` | 584 | 155 |
| `strat_4hr_retrigger_observed` | 271 | 273 |
| **Total** | **40,460** | **39,987** |

- Decision rows evaluated 40,907 = corpus bars 40,907 per instrument; bars in corpus not evaluated: 0.
- **Duplicate keys:** unique keys = candidate rows (40,460 / 39,987); exact duplicates collapsed: none; conflicting duplicates: none.
- **Determinism:** spec §3.3 per-bar canonical `shadow_candidates` digest A vs B — 40,907/40,907 identical for both instruments (`rows_differ 0`; the outcome block is hashed as bytes, never read). Whole-journal files differ only in the wall-clock `ts` field (the only differing key on all 40,907 rows per instrument; values not read). P2-X run on the run-B journals (`logs/structural_level_r5_from_b/`) produced **byte-identical `candidates.jsonl` and `outcomes.sealed.jsonl`** (same sha256 as above) for both instruments.
- **Forbidden / non-admitted families:** `forbidden_families_present` = {} ; `vwap_*_observed`, `ovn_*`, `gap_fill`, `range_break_close` absent; lane-only `strat_212`/`strat_122` absent; `families_blocked` = []. Census = exactly the 12 replay families of the frozen matrix (spec §2.3).
- **Asian `orb_false_break_fade`:** 0 rows (both).
- **Population separation:** every MNQ candidate row has `instrument == MNQ`, every MES row `== MES`; no other instrument in any journal or candidate file.
- **Manifest / roll / gap:** `corpus_manifest_sha256` in each P2-X manifest = admitted manifest; roll ledger in the extract = corpus roll ledger (7 seams, same dates, `is_roll_utc_day` / `contract_roll_utc_date` carried on every candidate row); gap ledger 35 runs = corpus; ORB availability: NY ORB available on all 447 NY-session days, London ORB unavailable on 1 of 449 (2025-11-28, Thanksgiving Friday gap).
- **Integrity status:** `PASS` (MNQ), `PASS` (MES); `problems` = [] for both.

## 3. Safety confirmation

- `outcomes.sealed.jsonl` (both) remained sealed: created by P2-X, hashed, never opened, printed, joined, summarised or filtered.
- No win rate, expectancy, P&L, hit rate, MAE/MFE, ranking, "best family" or any feature-conditioned number was computed or read.
- R6 (feature table) and R7 (spot-check) were not started.
- No prereg definition, tolerance, threshold, strategy parameter, runtime, risk, broker, config, Pine or VPS change; nothing deployed or restarted; the box was not touched.

**Next gates (each needs its own explicit go):** R6 over `candidates.jsonl` only; R7 seeded spot-check; only after both, a separate authorisation to open the sealed outcomes. STOP.
