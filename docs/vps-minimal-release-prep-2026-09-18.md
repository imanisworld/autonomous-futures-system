# VPS Minimal Release Prep — 2026-09-18

**Status: PREP / AUDIT ONLY. NO DEPLOYMENT OR RESTART AUTHORIZED.**

This record updates the post-freeze release preparation after C8 and R6 merged. It does not touch the VPS, `.env`, broker state, campaigns, runtime process, Pine, risk rules, strategy parameters, or sealed research outcomes.

## Verdict

**HOLD FOR SANCTIONED RELEASE WINDOW / EXPLICIT WAIVER.**

Do **not** deploy current `main` wholesale. Current repository `main` is `9c5164c3ec87f13ad2d4a6a7b1f4fdeb99773a3e`; the last verified deployed VPS release remains `8fd8b215063c83428fa15028ae76f0e7f6d25a8e`. The repository has accumulated research, collector, reporter, replay, documentation, and evidence tooling since that VPS release. The next runtime release must remain a minimal, pinned package.

## Required runtime delta

Only these proven runtime repairs are in scope:

### #612 — execution-budget observation repair

Runtime files:

- `webhook/runner.py`
- `strategy/signal_engine.py`

Purpose: after the shared daily trade-count / consecutive-loss execution limit is reached, later bars must still be evaluated and journaled for observation while execution remains blocked before risk/broker/order paths. Production limits are unchanged.

Current-main Git blob pins:

- `webhook/runner.py` = `0ad19162519a3d09a717f8ee63ded44324a28140`
- `strategy/signal_engine.py` = `39b5049232ed8f05ec452fadc3d8832098be47e8`

These blob IDs are byte-identical to the files at #612 merge commit `4ebb29abf086dbd0ed127f98e7818a9640c512c7`; there has been **no runtime drift in those two files since #612**.

Focused proof file on current main:

- `tests/test_runner_budget_observation.py` = `6ba6a3e2448c87e7144f5a5879d5ce1283b84bba`

### C8 / #638 — journal market-condition normalization

Runtime file:

- `journal/journal_logger.py`

Purpose: preserve a non-null top-level `market_condition`; otherwise copy a non-null `context.market_condition` to top level before append; if both are null/absent, leave the row unchanged.

Current-main Git blob pin:

- `journal/journal_logger.py` = `72b81b7a60b21001f6743e6a2163347486237a3b`

This blob is byte-identical to C8 merge commit `d65c1189e9b9fac6f577ac6313ba567335c48948`; there has been **no runtime drift in this file since C8**.

Focused proof file on current main:

- `tests/test_c8_market_condition_normalization.py` = `ace1af28c03d5b863f90060c68de7e2fb6d86028`

## Explicit exclusions

The minimal VPS release must not acquire unrelated merged work merely because it exists on `main`.

Exclude unless separately authorized and proven:

- R5 / R6 / R7 research artifacts or drivers;
- `replay/replay_engine.py` #621 (offline-only repair);
- M2K/MGC/MCL/MBT research/probe tooling;
- Discord feature PR #563;
- futures Signa feature PR #574;
- gap-aware wide-stop feature PR #568;
- collector/reporting/evidence-registry changes;
- config or `.env` changes;
- risk-rule or strategy-parameter changes;
- Pine changes;
- any live broker route or live-trading enablement.

## Candidate construction rule

Do not use "pull latest main and restart" as the release procedure.

Before the sanctioned release, construct or identify an immutable **exact-SHA candidate** whose reviewed runtime effect is limited to the #612 and C8 repairs above. Then prove the candidate contains the exact pinned runtime blobs and no unrelated runtime/config/risk/strategy/broker delta relative to the intended deployed baseline.

Do not create an unsafe fallback by weakening the behavior-neutral gate. #612 is behavior-changing and must use the full baseline promotion path defined in `docs/post-freeze-futures-runtime-release-plan-2026-09-17.md`.

## VPS preflight — required from the actual box

Immediately before any candidate verify/restart/promotion, record and compare:

1. running PID, `/proc/<pid>/cwd`, deployed release SHA, current symlink;
2. `LIVE_TRADING_ENABLED=false` from the running environment;
3. `SCHEDULE_MODE` and `EXIT_MODE` and their approved baseline values;
4. effective broker mode and every paper/demo route; zero unexpected external orders;
5. open paper position state and any order IDs;
6. active campaign/epoch state, including observation evidence that must survive restart;
7. `.env` byte/hash baseline without exposing secret values;
8. watcher/monitor state and restart expectations;
9. target candidate SHA and exact changed-file allowlist;
10. candidate exact-head CI and focused #612/C8 tests.

Any mismatch, unclear broker route, unexpected position state, or target-SHA drift = **HOLD**.

## Candidate verification — no promotion yet

The sanctioned immutable-release verifier must run the exact target candidate with forced paper broker posture and prove:

- candidate starts;
- `/health` succeeds;
- release fingerprint/integrity succeeds;
- broker/auth status is readable without enabling live execution;
- #612 focused tests pass;
- C8 focused tests pass;
- no unrelated runtime/config/risk/strategy/broker files entered the candidate.

A successful candidate verify does **not** authorize promotion.

## Post-promotion proof — only after separate deployment authorization

Before declaring the future release complete:

- process cwd and symlink point to the intended immutable release;
- `LIVE_TRADING_ENABLED=false` reverified;
- broker remains paper/demo-only as approved;
- open position and campaign/epoch state preserved/reconciled;
- health/status endpoints respond;
- normal decision journaling works;
- C8 top-level `market_condition` normalization is visible on a naturally eligible new row;
- #612 capacity-block behavior, when naturally encountered, preserves observation but reaches no risk/broker/fill path;
- numeric risk limits remain unchanged;
- M2K/MGC/MCL/MBT remain observation-only;
- watcher/Discord state is reconciled after the sanctioned restart;
- no new runtime errors appear.

## Cleanup note

Three stale C8 remote branches were identified during this prep:

- `chatgpt/fix-c8-journal-market-condition` — old pointer at `4d75226f`, no C8 implementation;
- `chatgpt/fix-c8-journal-market-condition-v2` — merged #638 implementation branch at `73df2ad`;
- `chatgpt/fix-c8-journal-market-condition-v3` — points exactly at current `main` `9c5164c` and carries no unique work.

They are cleanup candidates only. Do not delete any branch unless its unique-work status is rechecked at deletion time. This prep action does not delete remote branches.

## Safe next step

Keep the VPS untouched. When the freeze ends or an explicit waiver is granted, perform the **actual-box preflight first**, then build/verify the exact minimal candidate. Do not promote anything until the candidate and box-state gates pass.

---

## Update 2026-09-17 — candidate built, deployment window approved (PAPER ONLY)

Supersedes the verdict above for the facts below; everything not restated stays in force.

**Deployed base (proven from the box, `/proc/<pid>/cwd`):** `3beffb7b4ebd97a11cc2c23b17407e7212bbd38a` — #612 is **already in this base** (`4ebb29a` is an ancestor; `webhook/runner.py` blob `0ad19162519a…` and `strategy/signal_engine.py` blob `39b5049232ed…` are identical in base, candidate and `main`). The earlier "last verified deployed = `8fd8b21`" statement is stale.

**Candidate (immutable, exact SHA):** `11b3d910822b1dec930c8d4309b20c3aa01b42b1` on branch `release/minimal-3beffb7-c8-641-642` = base + three `cherry-pick -x` commits:

| Item | Source commit on `main` | Runtime file (candidate blob = `main` blob) |
|---|---|---|
| C8 journal `market_condition` normalization | `d65c118` (#638) | `journal/journal_logger.py` = `72b81b7a60b2…` |
| #641 daily safety gate fails closed on unverifiable proof-critical state | `6383ab2` | `ops/project_check/daily.py` = `5d8289d18a0c…` |
| #642 malformed / non-ASCII webhook secret → 401, not 500 | `10c3d09` | `webhook/app.py` = `c2fc1213ef63…` |

Exact diff vs base = those three files + `tests/test_c8_market_condition_normalization.py`, `tests/test_project_check_daily_drift_unverified.py`, `tests/test_webhook_secret_non_ascii.py` (347/−3). No change under `strategy/`, `risk/`, `execution/`, `config/`, `context/`, `adaptive/`, `replay/`, `research/`, `scripts/`, `risk_rules.yaml`. Focused tests 359 pass; full suite 5652 pass / 7 skip. The exclusion list above (incl. #621 replay repair, research tooling, #563/#568/#574) is honoured by construction.

**Why #642 joined the set:** on 2026-09-17 a re-created TradingView M2K 15m alert delivered a secret containing a non-ASCII homoglyph; `hmac.compare_digest(str, str)` raised `TypeError`, every delivery returned HTTP 500 and the bar was dropped after TradingView's retries (M2K 15m gap 13:30→16:00Z, 11 bars; 2026-09-17 is not a clean M2K session). The alert was corrected externally (16:30Z close → 200). #642 turns that input class into a clean 401; it does not make a wrong secret valid.

**Deployment rules (operator-approved):**

- deploy the **SHA**, never the branch name (the release branch is unprotected);
- method = the sanctioned local `afs-deploy.sh --release <full-sha>` (manifest built from a detached worktree at the exact SHA; `EXPECTED_LIVE_COMMIT` / `EXPECTED_RELEASE_FINGERPRINT` are written **from the generated manifest** — never pre-pinned from a predicted value);
- window = after the 21:00Z close (no mid-RTH restart);
- immediately before mutation reconfirm `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `SCHEDULE_MODE=always_on_shadow`, 0 positions, 0 working orders;
- one controlled `.env` edit (backup first) adding the four proof pins the live-box guard reports as unpinned — `EXPECTED_PROOF_WIDE_STOP_LEDGER_MODE`, `EXPECTED_PROOF_WIDE_STOP_LEDGER_EPOCH_START`, `EXPECTED_PROOF_ASIA_D_EMA_PAPER_MODE`, `EXPECTED_PROOF_ASIA_D_EMA_PAPER_EPOCH_START` — with the currently observed values, before the restart so the new process loads them; these are what #641's gate will otherwise block on;
- `systemctl restart afs-watcher` afterwards per runbook; rollback = `--release 3beffb7b4ebd97a11cc2c23b17407e7212bbd38a` + restore the `.env` backup + watcher restart;
- post-deploy proof: cwd/symlink = candidate, release integrity PASS, posture unchanged, 0 positions/orders, #612 markers present, `project_check daily` PASS with the new pins, first naturally eligible C8-normalized row, a controlled non-ASCII-secret POST returns 401, M2K 15m bars continue, campaign/epoch identifiers and journal continuity preserved.

**Pre-declared divergence — not runtime drift.** This release is deliberately *not* `main` head. After promotion the box will legitimately lack the research/replay files merged to `main` since `3beffb7` (`research/structural_level_*.py`, `replay/replay_engine.py` #621, `scripts/structural_level_*`, …). None is imported by the service. That difference is intentional and must not be read as drift.

**Drift-gate finding (checked read-only 2026-09-17, before deployment):** the box-side gate `/root/bin/afs-drift-gate.sh` (cron 11:05Z) clones **public `main` HEAD** and compares it to the live tree; it has no notion of the pinned release (`EXPECTED_LIVE_COMMIT`) or the release manifest. It already alarmed on 2026-09-17T11:05Z with exactly the pre-existing 3-item set (`DIFFER replay/replay_engine.py`, `MISSING research/structural_level_features.py`, `MISSING research/structural_level_p2.py`; `scripts/`, `tests/`, `docs/` are filtered out), because `main` moved after the `3beffb7` deploy. The same 3 items are expected after this release. The repo-side `scripts/afs-drift-gate.sh` does accept `AFS_DRIFT_REF` but watches a different, narrower path set. **Recorded as a monitoring defect for a separate focused fix:** the box gate should compare against the pinned deployed release SHA/manifest (or an approved-release allowlist), so intentional release divergence is distinguishable from unexpected drift. Do **not** reseed or suppress the alarm blindly, and do not treat a daily known-false alarm as acceptable.

**Also backlog, separate from this release:** the AFS watcher marks 15-minute-cadence webhook 500s "RECOVERED" between bar closes (false recovery); the single 2026-09-17T16:25:49Z 422 (body-validation reject during the alert edit; detail not logged).
