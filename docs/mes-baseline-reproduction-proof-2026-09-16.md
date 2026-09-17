# PR #598 offline reproduction — 2026-09-16

Verdict: **REPRODUCED / RESEARCH ONLY**. This verifies the closed PR’s audited producer; it does not reopen the research or authorize a forward cohort. No runtime, strategy, risk, service, deployment, or activation change.

## Provenance and scope

Producer: `af85df786b41451e09a0d4c473bf838b4a772e42` (the complete archived Git tree was verified before execution). The 75-file primary MES 5-minute corpus and 178-file preserved 15-minute bar/journal snapshot passed exact manifest, file-inventory, SHA-256, and row-count checks. All 3,944 selected journal contexts passed required-field checks before cohort selection; missing structural provenance fails closed.

The producer consumes the preserved original 15-minute bars and decision journals, not regenerated shadow candidates. The existing replay engine does not supply the required structural fields. The 5-minute corpus supplies provenance for downstream precursor data; it is not substituted for original journal observations. The archived snapshot documents feed comparisons, four single-print differences, and the September 11 Polygon hole. These limitations remain; no missing observations were synthesized.

Windows use the producer’s inclusive **journal-file dates**, not an independently changed signal-date predicate: 2026-07-13 through 2026-08-31, and 2026-09-01 through **2026-09-15**. September 16 is partial and excluded. “Complete date” denotes the last completed date, not a claim of gap-free coverage.

## Full population before quarantine

| Window | Session | Candidates | WIN | LOSS | NO_FILL | EXPIRED |
|---|---|---:|---:|---:|---:|---:|
| P1 | All | 1542 | 354 | 939 | 113 | 136 |
| P1 | asian | 759 | 198 | 522 | 39 | 0 |
| P1 | london | 331 | 97 | 211 | 22 | 1 |
| P1 | new_york | 452 | 59 | 206 | 52 | 135 |
| P2 | All | 503 | 103 | 329 | 30 | 41 |
| P2 | asian | 262 | 62 | 190 | 10 | 0 |
| P2 | london | 116 | 33 | 73 | 9 | 1 |
| P2 | new_york | 125 | 8 | 66 | 11 | 40 |

## Roll seam and precursor artifacts

Quarantine covers the 2026-09-14 22:00–23:55Z five-minute bars, represented as `[22:00Z, 2026-09-15 00:00Z)`. Any candidate lifetime from signal through exit intersecting that interval is excluded from clean outputs. Raw outputs are preserved unchanged. NO_FILL is evaluated at its signal; an unresolved filled row without an outcome horizon fails closed.

P1 excludes zero rows. P2 excludes four Asian LOSS rows: clean full population = **499** (103 WIN, 325 LOSS, 30 NO_FILL, 41 EXPIRED); clean Asian population = **258** (62 WIN, 186 LOSS, 10 NO_FILL). London and New York counts are unchanged.

Asian terminal artifacts contain **720 rows** for P1 (198 WIN / 522 LOSS) and **248 rows** for P2 (62 WIN / 186 LOSS). They preserve the #596 contract, original candidate identities, geometry and labels. NO_FILL and EXPIRED remain in the full populations and never become terminal losses.

**Feature-window caveat:** four retained P2 candidates have pre-signal lookbacks that overlap the seam (two 60-minute windows and four 120-minute windows). A separate candidate/window exclusion ledger flags these. “Clean terminal” means the outcome lifetime is seam-free, not that every precursor feature window is usable. Consumers must quarantine the flagged windows; this task does not rerun or reinterpret #596 feature analysis.

## Repeatability and validation

Each window was executed twice into identical canonical output paths, with both byte snapshots retained. All seven JSON/JSONL artifacts per window matched byte-for-byte, including producer manifests, raw and clean populations, terminal cohorts, and exclusion ledgers. Raw baseline and terminal bytes also match the previously archived proof. Clean cohort records match the archived clean cohorts; serialization preserves the current producer’s original lines, so old pretty-spaced clean-file hashes differ.

Validation: **23 existing producer tests passed; 16 private reproduction checks passed**. Checks pin the real counts and exact repeated artifacts, seam boundaries/crossing trades, missing outcome horizons, missing context, missing/unproven manifests, and deleted/tampered/extra source files. Parse skips and missing matching decision bars = zero in both windows.

## SHA-256 pins

| Artifact | SHA-256 |
|---|---|
| Primary corpus manifest | `9492b72fb87e1a9ba453cbcaf80983a032c2172892e4afb1c319176dd02cd98d` |
| 15-minute snapshot manifest | `1590948c4b181387926bc40eac5f632c53ac46662e229627b33c11f86de049d3` |
| p1/asian_terminal.jsonl | `fe4a4055424160ce597fdabf195b294687e8a42be80afa2b5b2c4f79c6843877` |
| p1/asian_terminal_clean.jsonl | `fe4a4055424160ce597fdabf195b294687e8a42be80afa2b5b2c4f79c6843877` |
| p1/baseline.jsonl | `e373064dc296aabcddcf42cb60a9683866937739a15b51c76c34a08d37de8db2` |
| p1/baseline_clean.jsonl | `e373064dc296aabcddcf42cb60a9683866937739a15b51c76c34a08d37de8db2` |
| p1/manifest.json | `b73add811b1456500223fe8dadd4ef8b1089e3807d70a5940f2621f44a6d38c6` |
| p1/precursor_seam_lookback_flags.json | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| p1/seam_exclusions.json | `37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570` |
| p2/asian_terminal.jsonl | `187bd76fc04d06f040138cb5f4e5793b364536fe3519547d11053152d6c36995` |
| p2/asian_terminal_clean.jsonl | `1aa0cac42ed6dadb473a5ed2d4f65bd345bbc0ddfc52be580b4a2877e31bbe68` |
| p2/baseline.jsonl | `cabaa54c4e92504a00c919c7189b2fbb62e0cd21faba63281839c8f57fcda389` |
| p2/baseline_clean.jsonl | `a4442f8b6bf41008a309b23f17ccf2c1c59e346ed543009492a4e16e0bd35cc9` |
| p2/manifest.json | `a075635d04c723feaf5bcc6224eee6d4e49b3ad88185a6f1d9143178a69e61cf` |
| p2/precursor_seam_lookback_flags.json | `fe14b667fce57bf81db19ef8d8102bf10d7b04bf01bece72c1902f869149c076` |
| p2/seam_exclusions.json | `5823510048dccff523240f2ea3ceef9d1b8a48cee3d136d2216558fbdca6ec86` |

## Private reproduction bundle

Candidate-level evidence, strategy internals, source snapshots and tests remain gitignored under `private/mes598-proof/`. The bundle contains `reproduce.py`, `test_reproduction.py`, `proof.json`, and `p1/` and `p2/` with both repeated runs. It is local evidence, not a public CI fixture; CI cannot independently reconstruct private inputs.

From the repository root:

```sh
python3 private/mes598-proof/reproduce.py
python3 -m pytest -q private/mes598-proof/test_reproduction.py
```

The reproduction verifies pinned manifests and the audited source tree before running. `proof.json` records exact commands, all hashes, full and per-session counts before and after quarantine, and excluded counts. The two consumer inputs are `p1/asian_terminal_clean.jsonl` and `p2/asian_terminal_clean.jsonl`; accompany them with each `precursor_seam_lookback_flags.json` ledger.
