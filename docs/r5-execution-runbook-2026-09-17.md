# R5 Regeneration-and-Seal Runbook — 2026-09-17

**Status:** AUDIT / OFFLINE ONLY. This document prepares the exact R5 procedure for the already-admitted MNQ/MES P-REPLAY population. It does not run R5, open sealed outcomes, compute P&L, score hypotheses, change runtime/config/risk/strategy, deploy, restart, or authorize live execution.

**Prerequisite ruling:** `docs/r5-readiness-audit-2026-09-17.md` = `READY_FOR_OPERATOR_GO` for MNQ/MES only.

## Why this runbook does not use `scripts/run_replay_batch.py`

`run_replay_batch.py` is a general-purpose replay command. After replay it automatically reads replay journals and prints a strategy edge/P&L breakdown. That reporting is unnecessary for R5 and weakens the preregistered boundary that R5 is regeneration + extraction + sealing only, with no outcome analysis.

For R5 use `ReplayEngine` directly. The replay journals will still contain the inline shadow-candidate resolution required by P2-X, but the R5 procedure itself will not produce or inspect a performance report.

## Frozen population

- Instruments: `MNQ`, `MES` only.
- Corpus root: `data/replay_polygon_v2`.
- Instrument inputs: `data/replay_polygon_v2/MNQ` and `data/replay_polygon_v2/MES`.
- Confirmatory window: 2024-10-01 through 2026-06-26, with the preregistered warm-up already present in the admitted corpus.
- Timeframe: 15m.
- M2K excluded from historical R5 (`ROLL_PROVENANCE_UNKNOWN`).
- MGC/MCL/MBT excluded (separate tranche-2).
- VWAP observers remain OFF in P-REPLAY as preregistered.
- `ema_pullback_trend` remains split by provenance; never pool its bracket-conflicted live/replay strata.
- `transition_failed_breakdown_reclaim` remains `NOT_TESTABLE`.

## 1. Pin and preflight

Run from a clean repo-local environment that actually contains the admitted gitignored corpora.

```bash
set -euo pipefail

git status --short
git rev-parse HEAD
sha256sum \
  strategy/shadow_setups.py \
  strategy/shadow_resolver.py \
  replay/replay_engine.py \
  scripts/polygon_to_replay.py \
  research/structural_level_features.py \
  risk_rules.yaml

for inst in MNQ MES; do
  test -f "data/replay_polygon_v2/$inst/MANIFEST.json"
  find "data/replay_polygon_v2/$inst" -maxdepth 1 -name '*.jsonl' -print -quit | grep -q .
done
```

**HOLD** if the working tree is dirty for unexplained reasons, either admitted corpus is absent, or the manifest/hash pins cannot be recorded.

Create fresh output roots. Never overwrite the R1 corpus or any prior replay evidence.

```bash
rm -rf logs/replay_p2_r5_a logs/replay_p2_r5_b logs/structural_level_r5
mkdir -p logs/replay_p2_r5_a logs/replay_p2_r5_b logs/structural_level_r5
```

## 2. Regenerate twice for determinism

Use one `ReplayEngine` instance per instrument/run and process each instrument's day files sequentially. This preserves #621 cross-day recent-bar continuity.

### Run A

```bash
python3 - <<'PY'
from pathlib import Path
from config.settings import load_config
from replay.replay_engine import ReplayEngine

cfg = load_config()
for inst in ("MNQ", "MES"):
    corpus = Path("data/replay_polygon_v2") / inst
    paths = sorted(corpus.glob("*.jsonl"))
    if not paths:
        raise SystemExit(f"no corpus files for {inst}")
    out = Path("logs/replay_p2_r5_a") / inst
    out.mkdir(parents=True, exist_ok=True)
    engine = ReplayEngine(config=cfg, log_dir=str(out))
    engine.run_many(paths)
    print(f"{inst}: {len(paths)} day files regenerated to {out}")
PY
```

### Run B

```bash
python3 - <<'PY'
from pathlib import Path
from config.settings import load_config
from replay.replay_engine import ReplayEngine

cfg = load_config()
for inst in ("MNQ", "MES"):
    corpus = Path("data/replay_polygon_v2") / inst
    paths = sorted(corpus.glob("*.jsonl"))
    if not paths:
        raise SystemExit(f"no corpus files for {inst}")
    out = Path("logs/replay_p2_r5_b") / inst
    out.mkdir(parents=True, exist_ok=True)
    engine = ReplayEngine(config=cfg, log_dir=str(out))
    engine.run_many(paths)
    print(f"{inst}: {len(paths)} day files regenerated to {out}")
PY
```

Do not open replay journal `shadow_candidates[].outcome` by hand.

## 3. P2-X extract and seal — instrument-separated

P2-X is the only component allowed to split the inline replay result into candidate rows and the sealed outcome file. Run separately by instrument so populations remain auditable.

```bash
python3 scripts/structural_level_p2_extract.py \
  --log-dir logs/replay_p2_r5_a/MNQ \
  --corpus-root data/replay_polygon_v2 \
  --out-dir logs/structural_level_r5/MNQ \
  --instruments MNQ \
  --determinism-log-dir logs/replay_p2_r5_b/MNQ

python3 scripts/structural_level_p2_extract.py \
  --log-dir logs/replay_p2_r5_a/MES \
  --corpus-root data/replay_polygon_v2 \
  --out-dir logs/structural_level_r5/MES \
  --instruments MES \
  --determinism-log-dir logs/replay_p2_r5_b/MES
```

Each invocation must exit 0 and print `status=PASS`.

Expected R5 artifacts per instrument:

- `candidates.jsonl` — contains no outcome field;
- `outcomes.sealed.jsonl` — never opened during R5/R6/R7;
- `manifest.json` — contains sealed-outcome SHA256 and integrity report.

## 4. Mechanical seal/integrity verification only

These checks inspect filenames, manifest metadata and candidate schema only. They do not open the sealed outcome file.

```bash
python3 - <<'PY'
import json
from pathlib import Path

for inst in ("MNQ", "MES"):
    root = Path("logs/structural_level_r5") / inst
    manifest = json.loads((root / "manifest.json").read_text())
    integ = manifest["integrity"]
    assert integ["status"] == "PASS", (inst, integ.get("problems"))
    assert manifest.get("outcomes_sealed_file", {}).get("sha256"), inst
    assert (root / "outcomes.sealed.jsonl").exists(), inst

    with (root / "candidates.jsonl").open() as fh:
        for lineno, line in enumerate(fh, 1):
            row = json.loads(line)
            if "outcome" in row:
                raise SystemExit(f"{inst} candidates line {lineno} leaked outcome")

    det = integ.get("determinism")
    assert det and det.get("identical") is True, (inst, det)
    print(inst, "PASS", "sealed_sha256=", manifest["outcomes_sealed_file"]["sha256"])
PY
```

Record, but do not interpret, the manifest population census, duplicate counts, roll/gap ledger status, deterministic-byte comparison and the two sealed SHA256 values.

## 5. R5 stop conditions

R5 is `BLOCKED` immediately if any of these occur:

- either corpus/manifest is missing or differs from the admitted R1 population without a preregistered explanation;
- regeneration crashes or day-file processing is incomplete;
- determinism comparison is non-identical;
- P2-X integrity status is not `PASS`;
- conflicting duplicate candidate keys appear;
- roll/gap ledger integrity fails;
- replay produces prohibited/forbidden population rows under the frozen family matrix;
- Asian `orb_false_break_fade` appears where the prereg says it must be zero;
- candidate rows contain an `outcome` field;
- M2K/MGC/MCL/MBT rows appear in this R5;
- anyone opens/analyses `outcomes.sealed.jsonl` before R6 + R7 and a later explicit go.

If blocked, preserve all generated evidence and stop. Do not tune, patch thresholds, rerun with friendlier filters, or substitute a different population.

## 6. Required R5 return packet

Return only:

1. exact code SHA + pinned file hashes;
2. corpus manifest identifiers/hashes for MNQ and MES;
3. day-file counts processed in run A and run B;
4. P2-X integrity verdict per instrument;
5. candidate counts by family/instrument from the manifest;
6. duplicate/conflict counts;
7. roll/gap-ledger verdicts;
8. determinism result;
9. sealed outcome SHA256 for MNQ and MES;
10. final verdict: `R5 PASS — SEALED`, `R5 BLOCKED`, or `R5 FAILED`.

Do **not** return wins, losses, P&L, expectancy, feature-conditioned results, hypothesis scores, or any information read from the sealed outcome contents.

## 7. After a clean R5

Stop. Do not open outcomes.

Next gates remain:

- R6: build/freeze/hash `features.jsonl` over `candidates.jsonl` only;
- R7: independent seeded spot-check of at least three candidate rows;
- separate explicit authorization after R7: only then may sealed outcomes be opened and joined by frozen candidate key for the preregistered analysis.
