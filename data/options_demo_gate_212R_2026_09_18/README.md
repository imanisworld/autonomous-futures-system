# 212R (2-1-2 REVERSAL, 30m) — options Backtest->DEMO gate, first run

Run: 2026-09-18 ~02:20Z · code_sha 5633a59 (main = #651 + #652 merged; first run was at fd073fb, regenerated after #652 landed mid-session) · verdict **BLOCKED** (60 blockers) · exit 2

Attestation rule used: a flag is `true` ONLY when a repo file at HEAD or a hashed artifact in
`underlying_manifest.json` establishes it. Every section carries a `_basis` note naming the source.
Nothing was set `true` to make the gate pass.

Mechanical checks that PASSED: code_sha == HEAD · underlying manifest sha256 == bytes (11 files, 37 MB) ·
options policy (config.py) sha256 == bytes · strategy id match.

Files: evidence.json (input) · report.txt / report.json (gate output) · underlying_manifest.json ·
underlying/ (frozen 09-16 family-validation + prospective artifacts + classifier hashes) · build_manifest.py.

## Tracked copy (this directory)

Committed subset: evidence.json (repo-relative manifest path), report.txt/.json (re-run from this
location, identical 60-blocker set), underlying_manifest.json, build_manifest.py, and the small
`underlying/` artifacts. Two large raw files are pinned by SHA-256 in the manifest but NOT tracked
(public repo, size): `outcomes_2026-09-09_2026-09-15.json` (34 MB) and `FAMILY_POPULATION_MASTER.csv`
(3.5 MB). Full package preserved locally at `logs/options_demo_gate_212R_2026_09_18/` (gitignored).

Re-run: `python3 scripts/options_demo_qualification_gate.py --strategy strat_212_reversal_30m_options --evidence-file data/options_demo_gate_212R_2026_09_18/evidence.json`

Purpose: negative proof + baseline blocker set to diff against after the shared option-side
infrastructure (mechanical selector, timestamped quote retention, risk-policy cleanup) lands.
