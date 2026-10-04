# Futures Operator TODO

> **Purpose:** durable operator action tracker for futures infrastructure/safety work. This file is **not** strategy-status authority and must not override `docs/strategy-rules/Strategy_Inventory.md`, the research trial ledger, or verified VPS/runtime evidence.
>
> **Repo reconciliation base:** `main` `d970b40` (2026-10-04, after PR #1131). The MNQ account-admission study is CLOSED / `INSUFFICIENT_EVIDENCE` / DO NOT REDO. Futures box release since the last verified runtime checkpoint is `c44d32bc4961e56fae5c5f88a976eb6783341638` (promoted 2026-10-04 21:29:09Z; rollback target `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`). Fetch current `main` rather than treating this stored SHA as perpetual current state. The freshest runtime facts are checkpointed in `docs/agent-work-state.md`; older reconciliation notes remain dated provenance and require fresh read-only proof before any runtime/broker/access mutation.
>
> Core rule: **No proof, no run.**


## CURRENT — 4HR natural-1m canonical epoch running; collect only

**2026-10-04 21:30:05Z: canonical epoch started on exact release `c44d32b`.** Details and the full proof table are in `docs/agent-work-state.md` (2026-10-04 ~21:30Z checkpoint) and `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`.

- Deployed via `scripts/atomic_release.sh` build → verify → promote (operator GO), tooling at `main` `94136a5` (unchanged since the `489b55b` deploy). Box posture was the reset baseline (`always_on_shadow` / `HTF off` / `static`), so the promote gate took its sanctioned Path 1.
- Post-deploy: symlink + cwd `c44d32b`; integrity OK 1633 files; 0 release-tree `__pycache__`; `LIVE_TRADING_ENABLED=false`; `TRADOVATE_ENV=demo`; `MAX_CONTRACTS_HARD_CAP=1`; both observer pins `true` and matching `EXPECTED_PROOF_`; `strat_4hr_retrigger` not in `enabled_concepts`; broker position null; in-process preflight 0 positions / 0 working orders; futures-bot PID `1851835`, `NRestarts=0`; deploy lock released; live disarmed (`preflight_passed_not_armed`).
- Heartbeat: the `heartbeat_fresh` failure seen 2026-10-02 20:55Z → 2026-10-04 cleared on the promote restart (fresh at 21:29:03Z). If it goes stale again without a restart, that is a new observation, not the known one.
- Drift gate: #1129 fix installed to `/root/bin/afs-drift-gate.sh` at 21:10:57Z (sha256 `3cf086a3…c86f9a`; backup `/root/bin/afs-drift-gate.sh.bak-20261004T211057Z`); one manual run on `489b55b` exited 0 with `OK release-integrity`. It has not yet run from cron against `c44d32b`.
- Access note: there is **no `afs-ro` user or tool on the box** (only `grok-audit` with a sudo-restricted `afs-grok-audit`, and `claude-audit`). The 2026-10-04 read-only preflights were root read-only over `ssh hetzner`. Earlier "Cursor's `afs-ro` lane" wording in checkpoints is inaccurate.

### NEXT — read-only, in order

1. **2026-10-05 after 11:05Z:** confirm `/root/afs-drift-gate.log` shows `OK release-integrity: c44d32bc4961 …` from the cron run and the live tree still has 0 `__pycache__`. If it alarms, read the reason before touching anything.
2. **After the first full session on `c44d32b`:** run `/futures-deployment-safety-audit`; confirm journals advance, `LOG_DIR` observation status lands in `/root/afs-shared/logs/` (not inside the release tree, #1095), RSS plateau, no new errors.
3. **On the first 4HR natural-1m touch:** record `contract_check` MATCH / MISMATCH / UNKNOWN and whether `needs_manual_review` fired. This is the open `contract_hint` question; do not alter TradingView alerts to force an answer.
4. Keep collecting. No parameter tuning, strategy change, Polygon work, or 3-2-2 work while the sample runs. A natural-1m 4HR loss is evidence, not a trigger.

**HOLD on everything else: no deploy, restart, env mutation, broker mutation, strategy enablement, or research rerun without a new operator GO.**

## Last verified runtime checkpoint — no automatic action

**2026-10-02 15:17Z: observer enabled on release `489b55b`; canonical #1103 epoch still NOT started.**

- Grok's read-only pass reported the generic 1-minute and 4HR observer pins ON and matching expected-proof pins; live trading OFF; Tradovate DEMO flat; no restart-created orders; journal advancing after restart.
- `LOG_DIR=/root/afs-shared/logs` is now proven, clearing #1095's path prerequisite.
- #1103 is merged on `main` but absent from deployed `489b55b`. Because #1103 adds the contract-month `contract_check`/mismatch-blocking behavior, any pre-#1103 4HR natural-1m touches are provisional/non-counting.
- The canonical epoch begins only after a release carrying #1103 is deployed and the read-only pin/posture/no-order check is repeated.
- #1107 is merged source-only; standalone watcher/push-relay server copies still require a separate operator-approved reinstall.

**2026-10-02 14:45Z: release-drift remediation verified; live release unchanged at `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`.**

- Repo fix #1101 plus operator box drop-ins/cron setting stopped the known bytecode writers; ten release-tree `__pycache__` directories were removed.
- Integrity with the shared environment loaded returned **OK, 1632 files** and remained clean after the 14:34Z feed-watchdog run.
- futures-bot PID `1327346` remained stable; RSS about 324 MiB at 14:31Z matched the expected plateau.

The next persistence confirmation remains read-only: after the 2026-10-03 12:00Z cron jobs, verify no release-tree `__pycache__` and confirm the next drift-gate result is OK.

**2026-10-02 03:43Z: release `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` promoted** (operator GO; `scripts/atomic_release.sh` build → verify → promote). Read-only proof 03:43–03:46Z:

- futures-bot PID `1327346`, cwd `/root/afs-releases/489b55b…`, `NRestarts=0`.
- Release integrity OK, 1632 files; no `__pycache__` in the live release.
- Tradovate DEMO `HEALTHY`, live trading disabled, flat; journal writing again from 03:45:15Z.
- Rollback target `75f10e4`.

This is a deploy-time snapshot. Run `/futures-deployment-safety-audit` after a full session before treating it as steady state. Details are in `docs/agent-work-state.md`.

Previous: latest verified read-only health pass from 2026-09-30 ET reported **PASS — KEEP COLLECTING / NO CHANGES**. Treat it as a dated checkpoint, not proof of current runtime state:

- futures-bot remains on pinned release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, DEMO, live trading disabled;
- release integrity passed; service remained one PID with zero restarts;
- Tradovate demo account routing remained pinned and healthy;
- journal and broker state agreed flat with zero execution attempts/fills for the current journal day;
- forward evidence is still writing for `vwap_hold`; `orb_reclaim` is stale and `vwap_rejection` is quiet. Those stale/quiet arms are evidence-quality facts, not a reason to tune or restart the current collector;
- GitHub `main` being ahead of the pinned release is reportable source/runtime drift, **not** an instruction to deploy.

Do not deploy, restart, retune, harden SSH, or reopen research merely because paper results are losing. The next work remains operator-window work below.

## Completed — do not reopen by default

- [x] Promotion hard-blocker / CLI exit semantics remain fail-closed (#893).
- [x] Promotion quantity proof requires per-entry-attempt observed quantities and exact count/claim reconciliation (#1070).
- [x] Promotion cap parser rejects fractional/invalid quantities instead of truncating them (#1072).
- [x] Six-market daily evidence reporting is on `main` for MNQ, MES, M2K, MBT, MCL, and MGC (#1068).
- [x] Shadow daily P&L runtime copy now uses the exact #1068 file. The 2026-09-29 scratch comparison kept the same totals and added the four zero-evidence markets. One Discord smoke showed all six markets. Futures-bot was not restarted.
- [x] Paper-collection runtime uses derived pin `b60931a6a9f8-reporter-1068-sixmarket-backport`. It shows MNQ, MES, M2K, MBT, MCL, and MGC, including zeros, on the #899 reporter. The full #1068 reporter file was not installed. Frozen-input data matched, one Discord smoke showed the six markets, and futures-bot was not restarted.
- [x] Reconciled repo-side futures defect list has no remaining open defect from the audited set.
- [x] No live-trading authority was created by these repo changes.

## Next operator phase — VPS / access / runtime truth

These are ordered. Do not skip ahead.

- [x] **Read-only runtime identity, 2026-09-30.** Futures-bot PID `791194`, `NRestarts=0`, release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, DEMO, live trading disabled. Reporter pins and the access map are in `docs/futures-runtime-reconciliation-2026-09-30.md`. A later read-only health pass re-ran release integrity successfully and reconciled journal/broker state flat.
- [x] **Access map checkpoint, read only.** Re-read 2026-09-30: public SSH port 22, `PasswordAuthentication yes`, `PermitRootLogin prohibit-password`, Tailscale absent, non-root login accounts password-locked, Grok audit keys use `command=` and `restrict`, `claude-audit` has an interactive shell and no sudoers entry. MacBook root-key login stays the proven current path from the access audit. Current access stays in place.
- [ ] **Phone SSH login.** Requires the operator. Not proven.
- [ ] **Hetzner/provider console recovery.** Requires the operator. Not proven.
- [x] **Candidate build/verify:** exercised for real on 2026-10-02 as part of the `489b55b` deploy. It first failed on #1056's `__pycache__`/UNPINNED rules; fixed by #1096 (`6ad0bac`).
- [ ] **Rollback drill:** not done. The rollback target is now `489b55b` (`/root/afs-releases/489b55b91b6303c195c8e84bfcbf05ef32d1ab04`, recorded in `current.previous` by the 2026-10-04 promote); `75f10e4` is still on the box as the prior one. Do not drill without an operator window, and not during the 4HR epoch unless the deployed release itself is the problem.
- [ ] **`afs-deploy.sh` (outside the repo):** not checked for the two #1056 problems that #1096 fixed in `atomic_release.sh`. Use `atomic_release.sh` until it is checked.
- [ ] **Not authorized yet:** `PasswordAuthentication no`, root-key cleanup, converting `claude-audit` to a forced command, public SSH restriction, or old-access removal. Do not harden SSH until phone login and provider-console recovery are proven.
- [x] **Gate-condition #1068 runtime:** shipped in release `489b55b` (2026-10-02). It is not yet observed running from the new release; check at the next audit.
- [x] **Reconcile source vs runtime (2026-10-02).** Live `75f10e4` → `489b55b` was reviewed. The release review plus `/futures-diff-review` of #1053/#1054 both concluded APPROVE PAPER-DEPLOY. Deployed under a separate operator GO.
  - [x] Box env proof (operator, boolean-only, values never printed):
    - `MAX_CONTRACTS_HARD_CAP` = `1`, with `EXPECTED_PROOF_MAX_CONTRACTS_HARD_CAP` matching.
    - `TRADOVATE_ENV` exactly `demo`.
    - `/root/afs-shared/.env` unchanged since the 2026-09-30 start.
  - [x] Integrity enforcement: promote's unit drop-in sets `RELEASE_INTEGRITY_ENFORCED=true` and `PYTHONDONTWRITEBYTECODE=1`. The live release had no `__pycache__` after startup.
  - [x] `LOG_DIR=/root/afs-shared/logs`: proven from the running futures-bot on 2026-10-02. #1095 remains undeployed and still needs exact-SHA review + separate deploy GO.
- [x] **Minimal futures release `c44d32bc4961e56fae5c5f88a976eb6783341638` deployed 2026-10-04 21:29:09Z** (operator GO; `scripts/atomic_release.sh` build → verify → promote; exact `489b55b` + #1095 + #1103, 8 files; candidate audit 7359 passed / 8 skipped / 2 governance checks deselected). Post-deploy proof in the CURRENT block above. Rollback target `489b55b`.
- [x] **4HR natural-1m canonical observation epoch started 2026-10-04T21:30:05Z** on `c44d32b` after the read-only pin/posture/no-order verification. Pre-#1103 touches (2026-10-02 15:17Z → 2026-10-04 21:30:05Z on `489b55b`) stay provisional/non-counting. Forward collection only from here.
- [x] **Drift-gate #1129 fix installed on the box 2026-10-04 21:10:57Z** (backup `afs-drift-gate.sh.bak-20261004T211057Z`; manual run exit 0, no restart). First cron run against `c44d32b` is 2026-10-05 11:05Z — see NEXT.
- [x] **Confirm the futures-bot memory plateau after the `489b55b` restart.** Read-only follow-up at 14:31Z showed PID `1327346` unchanged and RSS about 324 MiB, consistent with the expected post-startup plateau. The earlier `memory_critical`/`memory_warning` events were startup projections, not evidence of continuing unbounded growth.
- [ ] **Options-scanner memory cap (needs an operator decision; not urgent).** The watcher tick at 2026-10-02 03:46Z showed `options-scanner` (pid `3901845`, release `47ae01ac`) with RSS 10.8 MiB, swap 470.5 MiB, and cgroup `memory.events` `max=1301`, `sock_throttled=8170`, `oom_kill=0`. Its unit has `MemoryMax=350M`. The working set is now about 480 MiB, so the kernel keeps it in swap instead of OOM-killing it, as it did before swap existed. The `swap_pressure_warning` events on 2026-10-01 ran 14:16–20:51Z, about RTH. That matches the timing but is not proof. Daily scan counts were steady from 2026-09-24 to 10-01 (3,087–3,369), so no skipped cycles are visible. Per-scan latency was not measured, because `options_scanner.sqlite` stores only a row timestamp and the read-only wrapper cannot query it. Options, each a separate operator GO: raise `MemoryMax` (about 600M) with a scanner-only restart, or reduce the scanner's footprint.
- [ ] **Lock down old access only after replacement proof.** Then remove or restrict obsolete broad access paths, keeping documented break-glass recovery.
- [ ] **Optional cleanup — unused duplicate release `2752fe2e04bedf3e8ae9d6ffea8c594eb333b925`.** Read-only check 2026-10-01: same parent (`41ae188`) and identical git tree as the live `75f10e4`. Its `execution/tradovate_broker.py` on the box hashes identically. It was built 13 minutes after `75f10e4` went live and never promoted (not in the release history). Never promote it: switching would only restart the bot onto the same code. Since 2026-10-02 the rollback target is `75f10e4`. Removing the directory and the `candidate/tradovate-auth-only-41ae188` branch is an operator cleanup action for a release-maintenance window; it is not urgent.

## Separate / not blocking the VPS phase

- [ ] **PR #1037 public-repo/governance audit:** keep separate from futures execution safety. Secret-discovery was clean; remaining questions concern public-history operational exposure/governance.
- [ ] **PR #994 MNQ ORB Stage A research:** WAIT / research only. Stage B was not earned. Do not resume without an explicit research decision.
- [ ] Continue normal paper/shadow/guarded-DEMO evidence collection under existing frozen contracts. Do not interpret more data as automatic promotion.
- [x] **PR #1085 shadow daily P&L report fixes** (reporting only: `--final` late-row pass, one-at-a-time view, always-shown open/never-filled counts). Merged 2026-10-01 as `01b9654` by operator decision; all checks green on head `ded1f6a`; no independent agent review was posted.
- [x] **Install the #1085 report on the box.** The operator installed it on 2026-10-01 at 02:45Z: the standalone `/root/afs-shared` copy was updated, and the 12:00Z `--final` cron was added; no bot restart. Both runs are confirmed by file timestamps in `/root/afs-shared/logs` (read-only, 2026-10-02). `shadow_daily_pnl_2026-09-30_final.json` was written 2026-10-01 12:00Z, and `shadow_daily_pnl_2026-10-01.json` was written 22:10Z.
- [ ] **Compare the first `--final` run by hand** (`shadow_daily_pnl_2026-09-30_final.json`) against that day's first-pass JSON. Not done yet.

## Account admission — research only, not a runtime change

Closed on preserved research head `990b11135a1c49bc972981ac302930cd4f7565a3`. This section does not revise the 4HR epoch or the runtime checkpoint above.

- [x] **MNQ overlay, CLOSED / DO NOT REDO.** `research/mnq_account_admission_overlay.py` keeps the frozen capacity rules and applies the $150 journal-day loss rule before the drawdown floor. Drawdown is `DRAWDOWN_GATE_NOT_EVALUATED`. Trial `T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01` is closed as `INSUFFICIENT_EVIDENCE`; ledger disposition is `RESEARCH_ONLY`. Preserved result: `docs/research-evidence/T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01/result.json`; preserved look SHA-256 `0c6c084c830fa227951f2e8e4f47a85a2f22a72898014f243eced8ed8cc6f7e5`. Neither pass is the historical account result. Do not rerun, select, average, retune, run prereg #929, or change strategy status. Exact account-order evidence must come from forward collection with request order recorded.
- [x] **M2K / MGC / MCL / MBT.** Keep collecting. Do not expand the six-market path. MES waits.

## Definition of done for this phase

This phase is complete only when:

- current VPS identity and runtime posture are independently verified;
- replacement human access works from MacBook and phone;
- agent access is proven constrained;
- reporter pin/state is reconciled with #1068 where authorized;
- rollback and break-glass recovery are proven;
- obsolete access is locked down **after** the replacement works;
- docs/handoff are updated with exact verified runtime facts;
- no accidental live execution path or unreviewed broker/risk change was introduced.

Until then: the access phase is not complete. Do not treat a mapped current path as permission to remove it.
