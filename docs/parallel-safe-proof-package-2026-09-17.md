# Parallel-Safe Proof Package — 2026-09-17

**Status:** AUDIT / PAPER ONLY. No deployment, restart, outcome read, R5 step, strategy change, risk change, broker change, `.env` change, or campaign mutation is authorized by this document.

**Pinned repo state reviewed:** `1dd5be745cd60238836ea7bfd700b95f77d3c87a` (`main` immediately after #623).

This package freezes three tasks that can advance independently of the active MGC/MCL/MBT tranche-2 work:

1. the eventual #612 + optional C8 post-freeze runtime release proof;
2. the M2K >=5-session P3/P5 rerun procedure;
3. a current-main execution-safety/readiness audit.

---

## A. Post-freeze #612 + optional C8 release package

This section supplements `docs/post-freeze-futures-runtime-release-plan-2026-09-17.md`; it does not replace its full-baseline promotion requirement.

### A1. Exact release composition

Last verified deployed release: `8fd8b215063c83428fa15028ae76f0e7f6d25a8e`.

Current reviewed `main`: `1dd5be745cd60238836ea7bfd700b95f77d3c87a`, 25 commits ahead of that deployed baseline.

The direct compare contains many research/docs/reporting files. The futures runtime changes relevant to the next defect release remain narrowly bounded:

- `webhook/runner.py` — #612 execution-capacity block while preserving observation;
- `strategy/signal_engine.py` — #612 decision evaluation past spent execution capacity;
- **conditional only if separately merged and proven:** the exact C8 journal normalization file(s).

`replay/replay_engine.py` (#621) is offline replay/parity code and is not a reason to activate a new VPS runtime.

Do **not** bundle held features or behavior changes merely because their branches exist:

- #563 Discord signal card — separate feature release;
- #574 futures Signa shadow — separate feature release;
- #568 gap-aware wide-stop forward resolver — separate evidence-model release with its own fresh epoch decision.

### A2. C8 inclusion contract

C8 is eligible to join the defect release only if a separate PR proves exactly this normalization contract:

- if top-level journal `market_condition` is non-null, preserve it byte-for-byte;
- if top-level `market_condition` is null/absent and `context.market_condition` is non-null, copy the context value to the top level before append;
- if both are null/absent, leave the top level null/absent;
- no DecisionEngine, setup, risk, broker, fill, strategy, campaign or historical-row mutation.

Required C8 regressions:

1. top-level null + context `TRENDING` -> written top-level `TRENDING`;
2. existing top-level value is never overwritten by context;
3. both null/absent remain null/absent.

C8 remains excluded from the target SHA until that PR is independently reviewed and merged.

### A3. Pre-deploy capture checklist

Run from the actual box only after the freeze ends or an explicit waiver is recorded. Any failure is `HOLD`.

Capture without exposing secret values:

- running futures process PID;
- `/proc/<pid>/cwd` and exact deployed release SHA;
- hash of the effective `.env`/environment baseline;
- `LIVE_TRADING_ENABLED` from the running process = `false`;
- `SCHEDULE_MODE=always_on_shadow`;
- `EXIT_MODE=static`;
- effective broker mode plus every additive paper/demo route;
- external broker order census = expected approved state (last verified: zero external orders);
- all open paper/demo positions and ledgers;
- active evidence campaign/epoch values and state files;
- watcher service state and release-history baseline.

Then pin one immutable target SHA. Do not deploy a moving `main` ref.

### A4. Candidate verify gate

Use the sanctioned immutable release verifier on the exact target SHA before promotion. Candidate must be forced to `BROKER=paper` and must prove:

- candidate process starts;
- `/health` passes;
- status/reliability reads work;
- release fingerprint/integrity passes;
- exact-head CI for the same SHA is green;
- focused #612 tests pass;
- focused C8 tests pass if C8 is included.

Candidate verification is necessary, not authorization to promote.

### A5. Post-promotion proof

Before the release can be called complete:

- running `/proc/<pid>/cwd` equals the intended immutable release;
- current symlink and process cwd resolve to the same release SHA;
- `LIVE_TRADING_ENABLED=false` re-proven from the running process;
- `SCHEDULE_MODE=always_on_shadow` and `EXIT_MODE=static` remain unchanged;
- broker/paper/demo posture equals the approved pre-state;
- pre-existing paper positions/campaign state reconcile exactly or are explicitly accounted for;
- ordinary decision journaling still advances;
- top-level execution limits are unchanged;
- #612 blocked-capacity proof shows `BLOCKED_MAX_TRADES` or `BLOCKED_LOSS_LOCKOUT`, preserves `observed_decision`/setup evidence, and reaches no RiskEngine/broker/fill path;
- if C8 included, a paper journal row with nested market condition demonstrates the top-level normalization without altering the nested context;
- M2K/MGC/MCL/MBT remain observation-only and cannot enter DecisionEngine/RiskEngine/PaperBroker/Tradovate;
- watcher is reconciled/re-armed for the sanctioned restart;
- no unexpected Discord/runtime error appears.

### A6. Rollback / stop triggers

Immediate `HOLD` / rollback if:

- target SHA changes after approval;
- live flag or broker posture differs from pre-state;
- a collection-only root reaches the trading pipeline;
- campaign epoch/state changes unexpectedly;
- #612 can reach risk/broker after its execution block;
- C8 overwrites a valid top-level market condition;
- unrelated runtime/config/risk/strategy files appear in the release delta;
- a pre-existing paper position cannot be reconciled.

---

## B. M2K >=5-session P3/P5 rerun — frozen procedure

### B1. Preconditions

Do not run the confirmatory rerun until all are true:

- at least five distinct M2K observation sessions exist after epoch `2026-09-16T12:17:19Z`;
- observation evidence and `bars_M2K_*.jsonl` are copied read-only from the box;
- current compared window remains inside one proven dated contract (`M2KZ6`) with no roll seam;
- the M2K corpus is built with `--contract M2KZ6`, not the rejected stitched scheduler corpus;
- X0 for the compared corpus is `PROVEN`;
- no outcomes are opened/read for this parity run.

If the requested window crosses a real M2K roll before the rerun is performed, stop and establish a new X0-approved seam-free window; do not silently stitch contracts.

### B2. Build/refetch the seam-free corpus

Choose `END_DATE` to include the last complete live M2K session being tested. Use a fresh output directory so the old preliminary corpus remains immutable.

```bash
python3 scripts/structural_level_corpus_build.py \
  --symbol M2K \
  --start 2026-09-01 \
  --end "$END_DATE" \
  --timeframe 15 \
  --warmup-days 14 \
  --contract M2KZ6 \
  --out data/replay_polygon_parity_m2kz6_5session \
  --label m2k_z6_5session_parity \
  --fresh
```

Record manifest hash, builder SHA, provider fetch time, first/last bar, gaps and required-field coverage. No scheduler/roll-days option is permitted for this rerun.

### B3. Generate replay decision rows

Use one `ReplayEngine` instance across all day files so #621 cross-day history continuity remains active. The resulting replay journal directory must be fresh and preserved.

```bash
python3 - <<'PY'
from pathlib import Path
from config.settings import load_config
from replay.replay_engine import ReplayEngine

corpus = Path('data/replay_polygon_parity_m2kz6_5session/M2K')
out = Path('logs/replay_m2k_z6_5session')
paths = sorted(corpus.glob('M2K_*.jsonl'))
assert paths, 'no M2K corpus files'
ReplayEngine(config=load_config(), log_dir=str(out)).run_many(paths)
print(out)
PY
```

Do not run day files through separate `ReplayEngine` instances.

### B4. P5 firing/bracket parity

```bash
python3 scripts/structural_level_p2_parity.py \
  --live-source observation \
  --observation-evidence "$SNAPSHOT/cross_instrument_observation_v1.jsonl" \
  --bars-root "$SNAPSHOT" \
  --replay-log-dir logs/replay_m2k_z6_5session \
  --corpus-root data/replay_polygon_parity_m2kz6_5session \
  --instruments M2K \
  --end-ts "$END_TS_EXCLUSIVE" \
  --out docs/structural-level-m2k-5session-p5.json
```

Frozen gates from the tool/prereg:

- firing Jaccard >= `0.90` per testable family on bars evaluated by both sides;
- entry/stop/target all within one M2K tick (`0.10`) on >= `98%` of co-fired rows;
- `LANE_ONLY` families remain separate and are never forced into replay;
- zero-count families remain `NOT_TESTABLE`, not PASS;
- no cross-instrument pooling.

### B5. P3 bar/level parity

```bash
python3 scripts/structural_level_bar_source_parity.py \
  --instrument M2K \
  --bars-root "$SNAPSHOT" \
  --corpus-dir data/replay_polygon_parity_m2kz6_5session/M2K \
  --end-ts "$END_TS_EXCLUSIVE" \
  --out docs/structural-level-m2k-5session-p3.json
```

Frozen gates:

- OHLC agreement within one tick on >= `98%` of common bars;
- every admitted level with an eligible denominator must agree within one tick on >= `98%` of eligible rows;
- gap-contaminated rows stay excluded exactly as §9.4 specifies;
- one-sided `NOT_AVAILABLE` is counted and reported, not silently dropped and not treated as PASS.

### B6. Required rerun verdict

Return one of:

- `M2K P3/P5 PASS` — only if every testable gate passes and X0 remains PROVEN;
- `M2K P3/P5 FAIL` — any testable firing/bracket/OHLC/level gate fails;
- `M2K P3/P5 WAIT` — five sessions exist but required level denominators are still unavailable/insufficient;
- `M2K P3/P5 BLOCKED` — X0/contract identity/window integrity is not proven.

Even `PASS` does **not** create an M2K historical P-REPLAY population and does not authorize R5, strategy admission or execution.

---

## C. Current-main execution-safety audit

### Verdict

**PAPER ONLY / CURRENT MAIN SAFETY BOUNDARIES INTACT at `1dd5be7`.** This is a repo audit only; it does not prove the VPS currently runs this SHA.

### C1. Deployed-baseline delta

Compare `8fd8b215063c83428fa15028ae76f0e7f6d25a8e` -> `1dd5be745cd60238836ea7bfd700b95f77d3c87a`:

- 25 commits ahead;
- overwhelming majority is docs/research/reporting/collector tooling;
- runtime-relevant #612 files remain `webhook/runner.py` and `strategy/signal_engine.py`;
- `replay/replay_engine.py` is offline replay behavior (#621), not an execution activation;
- no evidence from this compare authorizes broad deployment of `main`.

### C2. Collection-only hard boundary

Current `webhook/app.py` routes `M2K/MGC/MCL/MBT` as observation-only roots. M2K/MBT additionally require the observation campaign switch to be active; MGC/MCL are diverted to observation unconditionally.

Current `webhook/runner.process_alert()` has an independent structural backstop at the beginning of the trading pipeline:

- if the ticker root is collection-only, return decision `OBSERVATION_ONLY`;
- `execution_reachable = false`;
- nothing below the boundary — journal daily state, DecisionEngine, RiskEngine or broker — runs.

`execution/cross_instrument_observation.py` defines:

- observation universe: `MNQ, MES, M2K, MGC, MCL, MBT`;
- collection-only roots: `M2K, MGC, MCL, MBT`;
- campaign defaults disabled unless both campaign id and explicit epoch are present;
- no DecisionEngine/RiskEngine/PaperBroker/Tradovate imports in the observation module.

### C3. #612 safety semantics

The merged #612 design is still the required semantics:

- daily max-trades/loss-lockout is an **execution capacity** gate;
- the setup is still evaluated/observed past capacity;
- the final row records the execution block while preserving observed decision/setup evidence;
- processing stops before RiskEngine/broker/fill.

The numeric execution limits themselves are not changed by #612.

### C4. R5 cannot be inferred from merged research

#623 explicitly leaves:

- R5 `HOLD`;
- M2K historical stitched corpus `NOT_ADMITTED` / `ROLL_PROVENANCE_UNKNOWN`;
- M2K only in prospective observation plus a seam-free Z6 parity window;
- any future P-REPLAY R5 population limited to `{MNQ, MES}` unless a separate M2K historical roll proof is approved.

Merging prereg/parity tooling therefore grants no execution eligibility and no outcome-read authorization.

### C5. Remaining repo-side safety blocker

C8 remains a real evidence/journal-contract defect until its small normalization PR is merged and tested. It does not make collection-only roots executable, but it can blind regime/context consumers by leaving a top-level `market_condition` null when the nested context contains the real value.

### C6. Current-main ruling

- **No accidental execution route discovered in this audit.**
- **No reason to broaden the next defect release beyond #612 + separately-proven C8.**
- **No R5 authorization exists.**
- **No VPS deployment/restart has occurred.**
- Box posture must still be re-proven from `/proc`, environment, broker state and campaign state before any future release.
