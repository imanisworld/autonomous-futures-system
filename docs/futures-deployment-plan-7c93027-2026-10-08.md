# Futures deployment plan — refreshed 2026-10-08; candidate NOT YET SELECTED

**PLAN ONLY / HOLD.** This revision supersedes the former `c44d32b → 7c93027` release proposal below. The previous version remains as a historical review baseline, **not as a live deployment plan**.

## Active decision record (governs all older sections below)

| Item | Verified or required state |
|---|---|
| Previously deployed release | `c44d32bc4961e56fae5c5f88a976eb6783341638`, reported in the read-only VPS audit; reconfirm on box before GO |
| Post-#1189 main | `064ee674b788c144fc8d3ca65082ed060927e2f1` (PR #1189 merged, Grok PASS on original head `f60ec6f`) |
| Previous candidate | `7c930274179f7c76749f75b35adb14fbb9255e54` is HISTORICAL ONLY; do not build/promote it under this plan |
| **Next candidate** | **UNSET** until #1194 watcher safety is independently reviewed, green on the exact head and merged. Select exact merged SHA and rerun source/trading-path delta and CI against that SHA |
| Plan status | **CHANGES REQUIRED / HOLD** pending B1/B5/B6/B8/B9/B10, validated rollback, and an exact-SHA operator GO |
| Cross-service boundary | Futures promotion must not silently change `options-122-prospective` collector code, Python, venv, journal schema or evidence cohort; independently pin or explicitly approve a proven migration under separate GO |
| Previous 51/51 release-wrapper tests | Reuse only for unchanged code paths. Post-#1189 and #1194 quote/working-directory changes require their own fresh exact-head tests and reviewed render evidence |

### B10 — Watcher WorkingDirectory and chmod safety (new mandatory gate)

Grok identified a dangerous remote-shell quote expansion in **both** promote and rollback watcher re-arm. On an empty systemd WorkingDirectory, the effective unquoted `chmod 700 $watcher_dest/*.sh` could target `/*.sh`. Merely testing that a Bash source file parses is not enough.

**Source fix:** [draft PR #1194](https://github.com/imanisworld/autonomous-futures-system/pull/1194), separate from this docs-only plan. It adds a fail-closed nonempty, realpath-resolved directory guard, restricts the canonical destination to the configured trusted shared-root subtree and preserves quoting through the nested remote command. #1194 is **not merged or approved** as of this revision.

**Required proof before any build or promote:**
1. Verify exact SHA of #1194 merged to main with green exact-head CI and Grok PASS; do not transfer #1189's PASS.
2. Inspect the *rendered* remote Bash for promote **and** rollback. It must use quoted `"$watcher_dest"` in every path, including `chmod 700 "$watcher_dest"/*.sh`.
3. Run isolated fake-box regressions: empty, root, relative, symlink escaping shared root, and other unapproved directories must fail **before any copy/remove/chmod/restart**.
4. Obtain fresh read-only `systemctl show afs-watcher.service -p WorkingDirectory --value` and resolved path; confirm a nonempty existing directory beneath the configured shared root, or stop for a separate operator-approved safe-path decision.
5. Verify watcher rollback still restores from the pinned previous release; require a non-production rehearsal and reviewer-approved rollback evidence. No actual service operation is authorized here.

**Fail B10 = HOLD** even if every other CI/dependency/posture check passes. Do not use a manual `chmod`, bypass path guards, or deploy old `7c93027` to avoid this gate.

### B1/B5/B6/B8/B9 — still unresolved

- **B1:** Read actual six posture pins and release-script promote policy. No automatic reset.
- **B5:** Compare deployed effective venv with the exact candidate lock and confirm Python 3.13. List/approve every material delta.
- **B6:** Verify effective `CONTRACT_IDENTITY_GUARD_ENFORCED` and its proof pin. Keep OFF until separately reviewed Pine/rollover proof.
- **B8:** Futures promote changes options collector code, dependencies and journal behavior if the collector remains on the live symlink. Prefer a reviewed independent pin/partition boundary; do not repin automatically. For #1186's v0.2 journal rollback, preserved old rows and separate new partitions require distinct operator-approved procedures and proof of the pinned old collector's actual startup.
- **B9:** Fresh broker flatness, no working orders, no deploy lock, after-close window, and no in-flight lane actions. Restarts can lose webhook intake for at least several seconds; do not assume zero missed signals.

**Evidence windows:** Preserve old journal bytes and report exact code/collector/source boundaries; do not merge cohorts with changed producer schemas or retroactively label non-catches. Whether to continue or reset each active epoch is an explicit operator decision supported by a recorded exact-SHA cutover.

### Required next action

1. Obtain Grok exact-head review and fresh CI for #1194 and #1186; **do not merge as part of this plan**.
2. Once #1194 is separately merged with operator approval, nominate the **new** exact deployment candidate, compare deployed → candidate end-to-end, and refresh all identities and risk-path checks below.
3. Obtain read-only root-level box proof for B1/B5/B6/B8/B9/B10 and rollback readiness.
4. Return a **GO FOR OPERATOR DECISION** or **HOLD** gate table with timestamps, artifacts and all decisions. Never build, promote, restart or submit orders from this document.

---

## Historical plan: `c44d32b → 7c93027` (superseded; provenance only)

## Status

**PLAN ONLY / HOLD.** This document authorizes no build, verify, promote, restart, env edit, unit install, TradingView change or broker action. Execution needs (1) review of this plan, (2) every blocking item below resolved, and (3) explicit operator GO naming the exact SHA.

| Item | Value |
|---|---|
| Deployed (per 2026-10-08 VPS audit, operator-supplied; **reconfirm on the box before any action**) | `c44d32bc4961e56fae5c5f88a976eb6783341638` |
| Candidate (exact, reviewed, CI-verified) | `7c930274179f7c76749f75b35adb14fbb9255e54` (#1180) |
| Delta | 64 first-parent merges, 227 files, +60,933 / −500 |
| `main` at writing | `58606b4` (#1152). It is **not** the candidate. Building `7c93027` is allowed because it is merged into `main`; do not substitute `main`. |
| Rollback target | `c44d32b` (becomes `current.previous` at promote) |

## Reused, not redone

These are accepted as already proven. This plan does not repeat them.

- **U3→U11 source campaign** (#1158–#1166, plus #1174/#1175/#1180): exact-head QA and green required CI before each merge (`docs/agent-work-state.md`).
- **`c44d32b` deploy proof** (2026-10-04, `docs/futures-current-state-handoff.md`): integrity, posture, 4HR natural-1m epoch start `2026-10-04T21:30:05Z`.
- **#1129 drift-gate fix**: already installed at `/root/bin/afs-drift-gate.sh` on 2026-10-04.
- **2026-10-08 VPS audit (operator handoff; not in repo):**
  - #1180 merged with CI green;
  - `afs-deploy` wrapper installed, 51/51 tests;
  - real GitHub CI-proof fetch and live verification passed;
  - Tradovate demo, live disabled, one-contract cap;
  - zero broker positions and zero working orders;
  - no deploy or restart.
- **Release mechanics**: `scripts/atomic_release.sh` build → verify → promote → rollback, deploy lock, exact-SHA, merged-to-main, live CI proof, completion marker, and integrity re-check.
- **Box checks**: `/futures-deployment-safety-audit` is the checklist for steps 2 and 6. Do not invent a parallel procedure.

## 1. Trading-path changes in the delta (review + preregistration)

Unchanged in the delta (verified by `git diff --stat c44d32b..7c93027`):

- `strategy/`, `risk/`, `risk_rules.yaml` — so `risk_rules_sha256` is unchanged;
- `journal/`, `replay/`, `config/futures_contracts.py`;
- every `context/` module except `wide_stop_demo_runtime_core.py`.

Strategy and risk decision logic is therefore byte-identical.

### Changes that reach the futures-bot process or its order path

| PR | Files | Runtime effect |
|---|---|---|
| U7 #1162 | `execution/tradovate_broker.py`, `execution/no_fill_taxonomy.py`, `context/wide_stop_demo_runtime_core.py` | Tick metadata comes only from `config/futures_contracts.py`. Values for MES/ES/MNQ/NQ/MGC/MCL are identical to the old in-module tables (checked). An unknown root is refused **before auth** (`CONTRACT_METADATA_UNSUPPORTED`) instead of defaulting to 0.25 / $1.25. No dated-contract policy now means refuse, not "nearest suggestion". An unresolvable position contract id now gives an *unconfirmed* position snapshot (an existing fail-closed state) instead of assuming MES. |
| U8 #1163 | `execution/tradovate_broker.py`, `execution/contract_identity.py`, `ops/live_box_guard.py` | Before any order body is built, the routed symbol must equal the computed dated front month, else `CONTRACT_IDENTITY_UNRESOLVED`. Alert-vs-routed identity is **observe/log only** unless `CONTRACT_IDENTITY_GUARD_ENFORCED` is set. Any value other than unset / `""` / `false` / `0` / `no` **enforces**. That variable is now proof-critical for the live-box guard. |
| #1137 | `webhook/runner.py`, `adaptive/post_cap_eligibility.py` | On `BLOCKED_MAX_TRADES` + `TRADE` only, runs an observation-only eligibility re-check: journal-ledger reads, no broker, no order path. Errors are recorded as `status: ERROR`; the block is unchanged. Adds `post_cap_eligibility` to that journal entry. |
| #1143 | `webhook/app.py`, `webhook/runner.py`, `notifications/futures_advisory.py` | Advisory cards on the existing `signal` Discord route via a new daemon thread with a bounded queue; fail-soft. Dashboard gets a read-only advisory panel. |
| U11 #1166/#1175/#1180 | `scripts/atomic_release.sh`, `requirements.lock`, `ops/dependency_lock.py`, `ops/release_ci_proof.py` | Release venv is now built with box `python3.13`, `pip install --no-deps -r requirements.lock`, `pip check` and a freeze-equality check. The lock now also pins transitive packages that were previously resolver-chosen. |
| #1107 | `ops/afs_watcher/watcher.py` | Promote copies the watcher from the release and restarts `afs-watcher`. The change is a label only: today P&L vs cumulative. |

### Changes that ride along to other services on the live path

| Item | Effect |
|---|---|
| Options code (#1145–#1151, #1153) | `options-122-prospective.service` and `afs-paper-collection-{eod,eow}` run from `/root/autonomous-futures-system`, so promote changes their code too. Collector imports touched: `alert_ranker/config.py` (additive), `market_data.py` (equity quote type unchanged; SPXW refused), `public_chart_bars.py` (additive), and `paper_v1.py` (invalid open risk now refused). Per #1177, the 1-2-2 collector is the intended `122-IEX-E1` producer. |
| Not applied by promote | `deploy/systemd/*` and `scripts/install_timers.sh` (#1101), the new `ops/systemd/options-setup-capture.*` units, and `ops/push_relay/app.py` (separate environment). These stay as they are on the box. **Do not install them as part of this release.** |

### Preregistration (record before promote)

- **Code boundary:** the promote timestamp is a code boundary for every active futures evidence lane:
  - 4HR natural-1m (epoch `2026-10-04T21:30:05Z`);
  - MNQ wide-stop three-lane (`2026-09-09T04:21:04Z`);
  - MES 15m 1-2-2 (`2026-09-09T06:12:55Z`);
  - every other lane pinned in `.env`.
- **Proposed treatment: continue epochs, no reset.**
  - Signal, risk and context decision logic is unchanged, and the only `context/` edit swaps a literal 0.25 for the identical canonical tick.
  - Execution changes add refusal paths only.
- **Logging:** any order refused by a new U7/U8 code is logged as an execution refusal for that lane. It is not a strategy outcome.
- **Exact pre-deploy readings:** record them for every epoch pin; the post-deploy readings must match them.
- **`122-IEX-E1` collector:** treated the same way — a code boundary at promote, no relabelling of earlier rows.
- **Operator decision:** this treatment needs operator confirmation. A reset is the alternative.

## 2. Blocking items before any build

Each item needs fresh read-only evidence from the box. Any item unproven means **STOP**.

| # | Item | Why it blocks | Proof required |
|---|---|---|---|
| B1 | Promotion posture gate | `_promote_gate_check` allows any release only on the reset baseline (`SCHEDULE_MODE=always_on_shadow`, `HTF_DIRECTION_MODE=off`, `EXIT_MODE=static`, each with matching `EXPECTED_PROOF_` pin). On `current` / `runner_shadow` it requires a behavior-neutral diff, and this candidate is **not** behavior-neutral (broker and runner changes). It will be refused by design. | Read the six pins. On baseline, proceed. Otherwise it is an operator policy decision whether to change posture. Do not edit the gate. |
| B2 | Deployed identity | The 2026-10-08 audit is a day old at most and must be reconfirmed. | Live symlink, `futures-bot` cwd, `EXPECTED_LIVE_COMMIT`, integrity with the fingerprint pin. All must equal `c44d32b`. `current.previous` must be readable. |
| B3 | Rollback target present | Rollback reuses the `c44d32b` release directory and venv. | `/root/afs-releases/c44d32b…` exists; its `release_manifest.json` parses; `ops.release_integrity` passes on it with its own fingerprint. |
| B4 | Box Python 3.13 | Build refuses unless `${AFS_BOX_PYTHON:-python3.13}` reports exactly 3.13 (U11 runtime unknown). | `python3.13 -c 'import sys; print(sys.version_info[:2])'` on the box. |
| B5 | Dependency drift | The new venv comes from the lock. The live venv was resolver-built, so library versions can change silently. | `"$CURRENT/.venv/bin/pip" freeze` diffed against `requirements.lock`. Every version change, especially fastapi/starlette/uvicorn/pydantic/httpx/numpy, must be listed and accepted by the operator. |
| B6 | Contract-identity env | Any non-false value enforces blocking. The variable is now proof-critical, so an unpinned, set value is flagged by the live-box guard. | `CONTRACT_IDENTITY_GUARD_ENFORCED` is unset or exactly `false`. If it is present in any form, a pin `EXPECTED_PROOF_CONTRACT_IDENTITY_GUARD_ENFORCED` (or `<unset>`) is an operator env decision before promote. Enforcement stays off (`docs/futures-operator-todo.md`). |
| B7 | Demo order lane posture | U7/U8 change the order path used by `tradovate_demo` (4HR Re-Trigger, 60M 3-2-2). | Read `WIDE_STOP_LEDGER_EXECUTION_ROUTE`, `WIDE_STOP_DEMO_EXECUTION_ENABLED`, `WIDE_STOP_DEMO_SESSIONS` and their `EXPECTED_PROOF_` pins. Record them unchanged. |
| B8 | Options ride-along | Promote changes the code that the `122-IEX-E1` producer runs. | Operator decision: accept as a recorded code boundary, or pin `options-122-prospective` to its current release first with the #1147 template. Pinning is its own reviewed action and is not bundled here. |
| B9 | Flat broker, no lock, timing | Standard. | Zero positions, zero working orders, no open lane position or claim, no `deploy.lock`. The window is after the futures close and outside every `WIDE_STOP_DEMO_SESSIONS` window. The next MES/MNQ quarterly roll is December, so there is no roll-seam overlap. |

## 3. Acceptance thresholds and abort conditions

### Build / verify (no service change)

Pass only if all hold:

1. `ops.release_ci_proof verify-live` passes for `7c93027` at build time **and again at promote**.
2. Box `python3.13` passes the check.
3. `pip install --no-deps -r requirements.lock`, `pip check` and `check-freeze` all exit 0.
4. Release integrity is OK with the built fingerprint, and the completion marker matches it.
5. `atomic_release.sh verify 7c93027` passes.

Any failure is an **abort**: the live service is untouched; release the lock and stop.

### Promote

The tool itself enforces:

- `futures-bot` active within the scripted wait;
- service cwd equals the release;
- `/health` 200;
- integrity OK with the new fingerprint;
- `afs-watcher` active with byte-identical watcher files.

Any failure is an **abort** and triggers the rollback in §4.

### Post-promote acceptance

Run `/futures-deployment-safety-audit`, read-only. **Every** item must pass:

| Check | Threshold |
|---|---|
| Identity | Symlink, cwd and `EXPECTED_LIVE_COMMIT` = `7c93027…`. Integrity OK with the pin. Zero `__pycache__` in the release. |
| Posture | `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `MAX_CONTRACTS_HARD_CAP=1`, live preflight disarmed. B1/B6/B7 values equal their pre-deploy readings. |
| Broker | Position flat, zero working orders, no auth-breaker trip. |
| Stability | `NRestarts` unchanged for 15 minutes after promote. |
| Errors | No new `errors.log` lines beyond the pre-deploy tail. Specifically, zero `CONTRACT_METADATA_UNSUPPORTED`, `CONTRACT_IDENTITY_UNRESOLVED`, `no exact dated-contract policy`, `Tradovate position contract id … has no provable supported root`, and `futures advisory … skipped` at ERROR level. |
| Journal | The first natural bar of the next session is journaled. Lane state and epoch pins equal the pre-deploy readings; no lane reset or reopened. |
| Webhook | A synthetic, non-order intake payload validates and never reaches `execute_bracket`. |
| Watcher / drift | The watcher reports a clean cycle. The next `afs-drift-gate.sh` cron run passes with the new fingerprint. |
| Options ride-along (if B8 accepts) | The next `options-122-prospective` run exits 0 and appends well-formed rows. Integrity holds. |

**Abort, then roll back immediately on any of these:**

- integrity failure or an unhealthy service;
- any live enablement or posture drift;
- an unexpected position or working order;
- auth-breaker trip;
- lane or epoch reset;
- journal write failure;
- a new U7/U8 refusal on MES/MNQ while the broker is reachable;
- the drift gate cannot reconcile;
- any unexplained ERROR.

## 4. Rollback and watcher recovery

- **Command:** `scripts/atomic_release.sh rollback` (no ref). It:
  - takes the deploy lock;
  - re-pins `EXPECTED_RELEASE_FINGERPRINT`, `EXPECTED_LIVE_COMMIT` and `EXPECTED_RISK_RULES_SHA256` from the `c44d32b` manifest;
  - swaps the symlink to `current.previous` and restarts `futures-bot`;
  - checks cwd, `/health` and integrity;
  - restores watcher files from the `c44d32b` release and restarts `afs-watcher`.

  Rollback does **not** require a U11 completion marker, so the pre-U11 `c44d32b` release is accepted (verified in source).
- **Before promote:** back up `/root/afs-shared/.env`. Record `current.previous`, the B2/B6/B7 readings and the epoch pins.
- **After rollback:** prove:
  - identity is `c44d32b` with integrity OK;
  - posture is unchanged;
  - broker is flat with zero orders;
  - journal continuity holds;
  - epoch pins are unchanged;
  - watcher is active with files byte-equal to the `c44d32b` release;
  - the next drift-gate run passes.
- **Watcher-only failure** (bot healthy, watcher not active after promote): roll back the whole release. Do not hand-edit the watcher directory.
- **Drill:** no rollback drill has been run under U11 tooling. Before GO, do a dry read-only walk of the rollback inputs (B2/B3). A live drill is a separate operator decision (`docs/futures-operator-todo.md`, access/recovery).

## 5. Execution order (only after review + GO)

1. Re-run B1–B9. Any change means STOP.
2. Fetch the CI proof for `7c93027`. Run `build`, then `verify`. No service change.
3. Record the preregistration boundary (§1) and the pre-deploy readings.
4. Run `promote`.
5. Run the post-promote acceptance (§3). Any failure means rollback (§4).
6. Record the outcome, with box evidence, in `docs/futures-current-state-handoff.md` and `docs/agent-work-state.md`.

## Do not bundle

Leave out of this release:

- enabling `CONTRACT_IDENTITY_GUARD_ENFORCED`;
- installing `options-setup-capture` or any timer/unit;
- systemd unit edits (#1101);
- `push_relay` redeploy;
- `58606b4` or any later commit;
- strategy, risk, instrument or contract-size changes;
- TradingView alert changes;
- epoch resets;
- cleanup.
