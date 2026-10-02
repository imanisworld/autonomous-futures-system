# Futures Operator TODO

> **Purpose:** durable operator action tracker for futures infrastructure/safety work. This file is **not** strategy-status authority and must not override `docs/strategy-rules/Strategy_Inventory.md`, the research trial ledger, or verified VPS/runtime evidence.
>
> **Repo reconciliation base:** `main` `b74bacf88a5daa605e1d39ea0c21d0584eb8832f` (PR #1101, 2026-10-02). Futures box release since 2026-10-02 03:43Z: `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`. Fetch current `main` rather than treating this stored SHA as current. The 2026-09-30 reconciliation is history. Current resume facts are in `docs/agent-work-state.md`.
>
> Core rule: **No proof, no run.**


## Last verified runtime checkpoint — no automatic action

**2026-10-02 03:43Z: release `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` promoted** (operator GO; `scripts/atomic_release.sh` build → verify → promote). Read-only proof 03:43–03:46Z:

- futures-bot PID `1327346`, cwd `/root/afs-releases/489b55b…`, `NRestarts=0`. Still that process at 15:03Z.
- Release integrity OK, 1632 files, at startup. Auxiliary jobs later wrote `__pycache__`. Those files were removed. At 15:03Z the release-tree bytecode count was 0, and the post-cleanup integrity check the same hour was OK.
- Tradovate DEMO, live trading disabled. Auth was `HEALTHY` and the account was flat on the earlier post-deploy read. Working orders were not re-read after the 03:43Z restart.
- Rollback target `75f10e4`.

#1101 is on `main` and is not the live tree. The installed feed-watchdog and the other live-release jobs already have no-bytecode protection. Do not deploy a release just to pick up #1101. Details are in `docs/agent-work-state.md`.

Previous: latest verified read-only health pass from 2026-09-30 ET reported **PASS — KEEP COLLECTING / NO CHANGES**. Treat it as a dated checkpoint, not proof of current runtime state:

- as of that 2026-09-30 pass, futures-bot was on `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, DEMO, live trading disabled. That commit is now the rollback target;
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
- [ ] **Rollback drill:** not done. The rollback target is now `75f10e4` (`/root/afs-releases/75f10e4540aa1f25b51b77c1ec2a2da40381a188`). Do not drill without an operator window.
- [ ] **`afs-deploy.sh` (outside the repo):** not checked for the two #1056 problems that #1096 fixed in `atomic_release.sh`. Use `atomic_release.sh` until it is checked.
- [ ] **Not authorized yet:** `PasswordAuthentication no`, root-key cleanup, converting `claude-audit` to a forced command, public SSH restriction, or old-access removal. Do not harden SSH until phone login and provider-console recovery are proven.
- [x] **Gate-condition #1068 runtime:** shipped in release `489b55b` (2026-10-02). It is not yet observed running from the new release; check at the next audit.
- [x] **Reconcile source vs runtime (2026-10-02).** Live `75f10e4` → `489b55b` was reviewed. The release review plus `/futures-diff-review` of #1053/#1054 both concluded APPROVE PAPER-DEPLOY. Deployed under a separate operator GO.
  - [x] Box env proof (operator, boolean-only, values never printed):
    - `MAX_CONTRACTS_HARD_CAP` = `1`, with `EXPECTED_PROOF_MAX_CONTRACTS_HARD_CAP` matching.
    - `TRADOVATE_ENV` exactly `demo`.
    - `/root/afs-shared/.env` unchanged since the 2026-09-30 start.
  - [x] Integrity enforcement: promote's unit drop-in sets `RELEASE_INTEGRITY_ENFORCED=true` and `PYTHONDONTWRITEBYTECODE=1` for futures-bot. Startup was clean. Later auxiliary jobs wrote bytecode; the installed jobs are now protected and the release-tree count was 0 at 15:03Z.
  - [x] `LOG_DIR=/root/afs-shared/logs` on PID `1327346`. #1095 is still **not** in `489b55b`. This proof does not authorize that deploy.
- [ ] **4HR natural-1m observation epoch.** Not started. On PID `1327346` at 15:06Z the 1-minute trigger and its proof pin are already on. `ONE_MIN_4HR_OBSERVER_ENABLED` and its proof pin are absent. `state_2026-10-02.json` under `tf1m/4hr_observation` is an invalidated wide-stop publish, not evidence and not the epoch. Follow `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. Enabling the observer is an operator GO and a bot restart.
- [ ] **Confirm the futures-bot memory plateau after the `489b55b` restart.** The watcher fired `memory_critical` at 03:43:45Z and cleared it at 03:46:20Z. Read-only startup readings: RSS 147 MiB (03:43:45Z), 244 MiB (03:46Z), 252 MiB (03:49:29Z). The warning line is about 1,029 MiB and the critical line about 1,221 MiB; MemAvailable was 1,252 MiB. Release `5115b78` showed the same startup alarm and then levelled off near 360 MiB. The full-session plateau was not re-read. Expect about 350–400 MiB; treat steady growth past about 500 MiB as a possible leak.
- [ ] **Options-scanner memory cap (needs an operator decision; not urgent).** The watcher tick at 2026-10-02 03:46Z showed `options-scanner` (pid `3901845`, release `47ae01ac`) with RSS 10.8 MiB, swap 470.5 MiB, and cgroup `memory.events` `max=1301`, `sock_throttled=8170`, `oom_kill=0`. Its unit has `MemoryMax=350M`. The working set is now about 480 MiB, so the kernel keeps it in swap instead of OOM-killing it, as it did before swap existed. The `swap_pressure_warning` events on 2026-10-01 ran 14:16–20:51Z, about RTH. That matches the timing but is not proof. Daily scan counts were steady from 2026-09-24 to 10-01 (3,087–3,369), so no skipped cycles are visible. Per-scan latency was not measured, because `options_scanner.sqlite` stores only a row timestamp and the read-only wrapper cannot query it. Options, each a separate operator GO: raise `MemoryMax` (about 600M) with a scanner-only restart, or reduce the scanner's footprint.
- [ ] **Lock down old access only after replacement proof.** Then remove or restrict obsolete broad access paths, keeping documented break-glass recovery.
- [ ] **Optional cleanup — unused duplicate release `2752fe2e04bedf3e8ae9d6ffea8c594eb333b925`.** Read-only check 2026-10-01: same parent (`41ae188`) and identical git tree as the live `75f10e4`. Its `execution/tradovate_broker.py` on the box hashes identically. It was built 13 minutes after `75f10e4` went live and never promoted (not in the release history). Never promote it: switching would only restart the bot onto the same code. Since 2026-10-02 the rollback target is `75f10e4`. Removing the directory and the `candidate/tradovate-auth-only-41ae188` branch is an operator cleanup action for a release-maintenance window; it is not urgent.

## Account replay — Cursor only, do not rebuild

Audited 2026-10-02 against `risk_rules.yaml` and `risk/risk_engine.py`. Details are in `docs/agent-work-state.md`. Grok does not get a follow-up task.

- [x] **MNQ shared-account audit.** `replay_portfolio` already does chronological order, 1 contract, 1 open position, and 3 fills/day. Do not build another simulator.
- [x] **Missing current-account gates, identified only.** Daily-loss lockout (`max_daily_loss: 150` per contract; at one contract, stop new entries once realized daily P&L is at or below `-$150`) and the 30% survival floor, which needs a real balance and peak. News blackout, session caps, session cutoffs, the consecutive-loss lock, the circuit breaker, the early-session loss floor, and profit-protect are off. Do not add them. The replay’s $5,000 figure and the $1,500 ladder seed are not live equity.
- [x] **Do not run prereg #929 as this proof.** The prereg freezes those mechanics, and the P&L look is not open.
- [ ] **Smallest extension, not started.** A sibling filter on the existing fill stream for the daily-loss skip. The drawdown skip waits for a real balance and peak series. Do not edit the pinned #915/#929 files. Do not score it without a new prereg.
- [ ] **MES waits** until that MNQ account-gate question is actually answered.
- [x] **M2K / MGC / MCL / MBT.** Keep collecting. Do not expand the six-market path.

## Separate / not blocking the VPS phase

- [ ] **PR #1037 public-repo/governance audit:** keep separate from futures execution safety. Secret-discovery was clean; remaining questions concern public-history operational exposure/governance.
- [ ] **PR #994 MNQ ORB Stage A research:** WAIT / research only. Stage B was not earned. Do not resume without an explicit research decision.
- [ ] Continue normal paper/shadow/guarded-DEMO evidence collection under existing frozen contracts. Do not interpret more data as automatic promotion.
- [x] **PR #1085 shadow daily P&L report fixes** (reporting only: `--final` late-row pass, one-at-a-time view, always-shown open/never-filled counts). Merged 2026-10-01 as `01b9654` by operator decision; all checks green on head `ded1f6a`; no independent agent review was posted.
- [x] **Install the #1085 report on the box.** The operator installed it on 2026-10-01 at 02:45Z: the standalone `/root/afs-shared` copy was updated, and the 12:00Z `--final` cron was added; no bot restart. Both runs are confirmed by file timestamps in `/root/afs-shared/logs` (read-only, 2026-10-02). `shadow_daily_pnl_2026-09-30_final.json` was written 2026-10-01 12:00Z, and `shadow_daily_pnl_2026-10-01.json` was written 22:10Z.
- [ ] **Compare the first `--final` run by hand** (`shadow_daily_pnl_2026-09-30_final.json`) against that day's first-pass JSON. Not done yet.

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
