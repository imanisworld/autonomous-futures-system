# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Checkpoint base:** repository `main` `b74bacf88a5daa605e1d39ea0c21d0584eb8832f` (PR #1101, 2026-10-02); futures box still on `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` since 2026-10-02 03:43Z. Always fetch current `main`; this stored SHA is a comparison base, not a perpetual current-state claim.
>
> Core rule: **checkpoint first; diff first; do not redo proven work.**

## Mandatory startup gate

After any outage, quota reset, context loss, reconnect, or multi-day gap, use the cheapest safe resume path:

1. Read `AGENTS.md` and this file.
2. Fetch only the current `main` SHA.
3. Read the authoritative record for the requested lane.
4. If this checkpoint names an active branch/PR for the task, inspect that exact branch/PR.
5. Compare those identifiers to this checkpoint and classify the result:
   - **UNCHANGED:** resume from **NEXT** immediately. Do not re-audit completed work.
   - **SCOPED DRIFT:** inspect only the changed diff/files/PRs needed to understand that drift.
   - **INSUFFICIENT STATE:** fetch the next-smallest source needed to resolve the unknown.
   - **SAFETY-CRITICAL DRIFT:** verify the exact runtime facts required by the proposed action before mutation.
6. Inspect broader related PRs/branches/issues only if the scoped check shows they are relevant.
7. Perform a full repository/runtime reconciliation only if the checkpoint is missing, contradictory, materially stale, cannot identify the active work, conflicts with an authoritative record, or scoped evidence cannot resolve a safety-critical question.

A returning session is therefore **RESUME FIRST, ESCALATE ONLY AS NEEDED** — not a mandatory full reconciliation.

## Quota / context guard

When the platform exposes quota/usage:

- At roughly **70–75% used** or **25–30% remaining**, start no new workstream.
- Finish only the current atomic step.
- Verify the result.
- Write the checkpoint below.
- Stop before a hard provider limit destroys the handoff.

Any rate-limit, context-limit, or usage warning triggers the same behavior immediately.

When no quota meter is available, checkpoint after every completed atomic task and before every new independent workstream.

## Required checkpoint payload

Every substantial unit of work must leave:

- timestamp / session context;
- task and scope;
- what was verified;
- what changed;
- repository branch / PR / exact SHA;
- tests/checks and exact result;
- runtime mutations, if any (normally none);
- blockers / unknowns;
- **DONE** items;
- **DO NOT REDO** items;
- exact **NEXT** action.

If the agent cannot persist this file, it must return this payload verbatim-ready for the next agent/operator to save.

## Current checkpoint — 2026-10-02 11:06 ET

### DONE / DO NOT REDO

- **Bytecode closeout, verified 2026-10-02 15:03Z.** Do not redeploy `489b55b` and do not reopen #1100.
  - #1101 merged as `b74bacf` (repo-side no-bytecode for feed-watchdog, the wide-stop EOD fallback, day-only-exit, and `scripts/afs-server-drift-gate.sh` `-B`). Not deployed. The live tree is still `489b55b`.
  - #1100 closed unmerged at `2026-10-02T14:35:49Z`. It duplicated #1101. Its only extra repo line was calendar-sync in `scripts/install_timers.sh`. Do not merge it.
  - Installed box protection, not a new release: systemd drop-ins at `2026-10-02T14:29:16Z` set `PYTHONDONTWRITEBYTECODE=1` on feed-watchdog, wide-stop EOD fallback, daily-digest, ibkr-watchdog, and calendar-sync. Root crontab sets the same variable for the live-release cron jobs. `/root/bin/afs-drift-gate.sh` uses Python `-B` on both integrity checks (backup `/root/afs-shared/backups/afs-drift-gate.sh.pre-nobytecode-20261002T1435Z`). `afs-day-only-exit.service` is not installed.
  - `calendar-sync.service` was not started. Its drop-in is in place. Its `ExecStart` still pulls git and restarts futures-bot.
  - Feed-watchdog succeeded at `14:59:11Z` under the drop-in. Release-tree `__pycache__` count was 0 at `15:03Z`. Integrity was `OK`, 1632 files, exit 0, on the post-cleanup check the same hour. Startup at 03:43Z had also been clean; auxiliary jobs wrote `__pycache__` after that, and those files are gone.
  - Same process: futures-bot active, PID `1327346`, `NRestarts=0`, started `03:43:25Z`. `TRADOVATE_ENV=demo`, `LIVE_TRADING_ENABLED=false`, `PYTHONDONTWRITEBYTECODE=1`, `RELEASE_INTEGRITY_ENFORCED=true`. `LOG_DIR` on that process is `/root/afs-shared/logs`.
  - Drift-gate cron has not run since the `-B` edit. Next fire `2026-10-03 11:05Z`. Do not invoke it just to prove the edit.
- **4HR observer is OFF. The epoch has not started.** On PID `1327346` at 15:06Z: `ONE_MIN_TRIGGER_ENABLED=true` with its proof pin, `ONE_MIN_4HR_OBSERVER_ENABLED` absent, and its proof pin absent. `/root/afs-shared/logs/tf1m/4hr_observation/state_2026-10-02.json` is rewritten while the flag is off (`status=INVALIDATED`, `executable=false`, `trade_authorized=false`, `source=wide_stop_forward_v1`; last seen `15:00:03Z`, source timestamp `2026-10-02T11:00:00-04:00`). No `4hr_trigger_evidence` file. Do not count that state file as the natural-1m epoch. Rules: `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`.
- **DEPLOYED `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`, 2026-10-02 03:43Z (operator GO; operator ran `scripts/atomic_release.sh` build → verify → promote from a checkout at `6ad0bac`).** Read-only `afs-ro` proof at 03:43–03:46Z:
  - **Release and process:** symlink and futures-bot PID `1327346` (cwd `/root/afs-releases/489b55b…`, started 03:43:25Z, `NRestarts=0`).
  - **Integrity:** OK at every step, 1632 files (local build, box build, verify, promote). Startup left no `__pycache__`. Promote's unit drop-in sets `PYTHONDONTWRITEBYTECODE=1` and `RELEASE_INTEGRITY_ENFORCED=true`. Later auxiliary jobs wrote bytecode; see the bytecode closeout above. Do not describe the 03:43 tree as still clean without that later pass.
  - **Startup checks on real startup:** promote health `broker=tradovate` passed #1054's exact-`TRADOVATE_ENV` check; verify's candidate started under #1053's required hard cap.
  - **Broker and trading state:** Tradovate DEMO `HEALTHY`, account session active, no position, live trading disabled.
  - **Writes and history:** first post-deploy journal write 03:45:15Z. `release_history.txt` appended; promote restarted `afs-watcher` itself.
  - **Watcher warning:** the watcher's first tick flagged `memory_critical`, extrapolated from 2 startup samples (RSS 157 MiB, 1312 MiB available). It cleared at `03:46:20Z`. The full-session RSS plateau was not re-read.
  - **Rollback target:** `75f10e4`.
  - **Pre-deploy env proof** (operator, boolean-only read of the running process's environ, 2026-10-02): `TRADOVATE_ENV` exactly `demo`; `MAX_CONTRACTS_HARD_CAP` = `1` with a matching `EXPECTED_PROOF_` pin. `/root/afs-shared/.env` mtime 2026-09-30 00:35:53Z, unchanged since that process started.
- **Release tooling incident, 2026-10-02 (fixed):** the first `atomic_release.sh build 489b55b…` failed locally (`release integrity: FAIL … ops/__pycache__/*.pyc`). #1056 refuses any `__pycache__` and reports `UNPINNED` (exit 1) without `EXPECTED_RELEASE_FINGERPRINT`; the script handled neither. Nothing reached the box; the deploy lock was confirmed free afterwards. Fixed by #1096 (`6ad0bac`), which is what made the deploy above possible. `afs-deploy.sh` (outside the repo) was not reviewed for the same two problems; use `atomic_release.sh` until it is.
- **4HR MNQ forward evidence:** 0 prospective fills since the 2026-09-09 wide-stop epoch. The `wide_stop_4k` lane saw 2 candidates, both blocked before an order (2026-09-15 `REGIME_RESTRICTED`; 2026-09-30 `ENTRY_DETACHED_FROM_PRICE`). The natural-1m 4HR lane never wrote evidence; cause and the new-epoch rules are in `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. Do not re-audit; do not backfill.
- **Merged 2026-10-02:** #1077 Signa v2 (`8c4e2e4`), #1092 4HR natural-1m observation (`8b7d968`), #1094 observation publish fail-safe (`489b55b`), #1095 observation status path follows `LOG_DIR` (`1c43291`), #1096 release-script bytecode/fingerprint fixes (`6ad0bac`), #1097–#1099 docs, #1101 timer/drift-gate no-bytecode (`b74bacf`). #1100 closed unmerged.
  - **On the futures box (release `489b55b`):** #1092, #1094, #1053, #1054 and gate-condition #1068. #1101 is not in that tree; the box uses the drop-ins and `/root/bin/afs-drift-gate.sh` edit above.
  - **Not on the box:** #1095 (after `489b55b`; not independently reviewed for deploy). `LOG_DIR=/root/afs-shared/logs` is already set on PID `1327346`. That does not authorize deploying #1095.
  - **No deploy needed:** #1096 is Mac-side release tooling, effective from an updated checkout. #1101 does not need a futures release to keep the already-installed jobs from writing bytecode.
  - **Separate GO:** #1077 is options-scanner code; that deploy needs its own GO.
- **Reviews done:** `/options-diff-review` of #1077 at `9387964` = APPROVE (260 tests). `/futures-diff-review` of #1092 = APPROVE PAPER-DEPLOY; its should-fix landed as #1094.
- **Release review `75f10e4` → `6ad0bac` done (2026-10-02):** code tightens safety; #1096 is release-script tooling only (bytecode off and fingerprint pin on every integrity check); `strategy/`, `journal/`, and `risk_rules.yaml` unchanged; all 7 live-only commits are patch-present on main (stale-bearer fix and FI-18 account pin intact). The HOLD was cleared by the env proof above and `489b55b` was deployed. `/futures-diff-review` of #1053 + #1054 (Claude, 2026-10-02) = APPROVE PAPER-DEPLOY: 274 targeted + 1249 trading-path tests passed. Re-review only commits after `6ad0bac`.
- **Live release preserved on GitHub:** tag `archive/futures-stale-bearer-curated-75f10e4-2026-09-29` → `75f10e4`.
- **60M 3-2-2 corpus rerun:** operator re-confirmed **HOLD** 2026-10-02. A rerun cannot change the $6k account-size blocker. Revisit near $6k equity, before quoting any 3-2-2 figure, or when forward 3-2-2 trades need a clean historical baseline.

### NEXT — in order

1. **Fresh working-order read.** Read-only, in-process. Do not open a second Tradovate login and do not POST `/admin/live-preflight/run`. Position and auth were healthy earlier on this PID; the working-order count was not re-read after the `03:43Z` restart.
2. **Do not start the 4HR epoch.** The trigger flag and its pin are already on. The observer flag and its pin are not. Enabling them is an operator GO plus a bot restart, per `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. The invalidated `state_2026-10-02.json` file is not that epoch.
3. **#1095 in a later release:** review it, then build/verify/promote an exact reviewed SHA with a separate GO. `LOG_DIR` is already `/root/afs-shared/logs` on the running process.
4. **Memory plateau:** `memory_critical` cleared at `03:46:20Z`. Re-read RSS after a full session before calling the startup alarm a leak. Expect about 350–400 MiB; treat steady growth past about 500 MiB as a possible leak.
5. **Before the 2026-12-11 roll:** add a contract-month check to the 4HR observation snapshot (see the epoch doc).
6. **#1077 options scanner:** separate deploy GO.

## Account-replay lane — 2026-10-02 11:46 ET

Cursor lane. Grok does not get another task here. This is not strategy-status authority.

### DONE / DO NOT REDO

- **MNQ shared-account gate audit, repo only, 2026-10-02.** Do not build a second simulator. The existing machinery is `research/mnq_combined_portfolio_audit.py` `replay_portfolio`, fed by the frozen family adapters in `scripts/mnq_combined_portfolio_audit.py`, with the forward contract in `research/prereg929_forward_portfolio.py` and `docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md`. Those #915 blobs stay pinned.
- **Already present, matching current account capacity:** chronological fill order, 1 contract, 1 open MNQ position (`SKIPPED_BUSY_PORTFOLIO`, same rule as `position_rules.max_open_positions: 1`), max 3 fills/day (`SKIPPED_MAX_TRADES_PORTFOLIO`, same number as `daily_limits.max_trades_per_day`). Each family keeps its own stop, target, fill model, slippage, commission, and holding/EOD behavior. The box hard cap is already 1 contract, so the sizing ladder cannot raise size on that process.
- **Missing live gates (these are on in `risk_rules.yaml` / `risk/risk_engine.py`):**
  - Daily-loss lockout. `max_daily_loss: 150`, scaled by contracts in `_check_daily_loss_limit`. At 1 contract that is -$150 realized. The replay never reads running P&L, so a later fill the same day is blocked only by the count of 3.
  - 30% survival floor. `max_drawdown_percent: 0.30` in `_check_max_drawdown`, using live `account_balance` and `account_peak_balance`. The replay’s drawdown figure is a report against a fixed $5,000 start. It does not reject later events, and $5,000 is not the live account. `position_sizing.starting_balance: 1500` is the ladder seed, not live equity either.
- **Not missing.** News blackout, per-session caps, session cutoffs, consecutive-loss lock, circuit breaker, early-session loss floor, and profit-protect are off in the current daily-limits block. Do not add them.
- **Pre-fillable gap.** `replay_portfolio` only sees events a family adapter already called fillable. Candidates that never filled are not account decisions. `SIGNAL_NO_FILL` appears only in the historical script’s descriptive large-move report.
- **Do not run prereg #929 for this question.** Its contract freezes portfolio mechanics (§3, §6, §9.5). A P&L look stays closed until 40 terminal fills and 120 CME days, or the 2027-09-30 deadline. Scoring started 2026-09-23. Operational counts would still be the frozen study, not the current account, and every run must re-fetch Polygon.
- **MES stays out.** Prove the MNQ account gates first. M2K, MGC, MCL, and MBT stay collection-only. Do not expand six-market reporting.

### NEXT

1. **Smallest futures extension, not started and not authorized to score.** A sibling beside `replay_portfolio` that consumes the existing fill stream and adds only `SKIPPED_DAILY_LOSS` when that day’s realized net from already-accepted fills is at or below -$150. Add `SKIPPED_DRAWDOWN_FLOOR` only when a real account balance and peak series is supplied. Do not invent that series from $5,000 or $1,500. Do not edit the pinned #915/#929 files. A scored run needs its own prereg.
2. **Options candidate admission: INSUFFICIENT EVIDENCE (2026-10-02 schema audit).** Do not implement. `account_equity.py` replays recorded ACTIVE trades only. The shadow journal does not store available cash, historical aggregate open risk, open orders, or a quote-complete rejected-candidate stream. COUNTERFACTUAL rows stay non-trades. A scored options run needs both the missing evidence and its own prereg.

## Previous checkpoint — 2026-09-30 ET

### DONE / DO NOT REDO

- **Paper reporter:** installed and verified; active pin `releases/b60931a6a9f8-reporter-1068-sixmarket-backport`. Do not rebuild merely because Grok returns.
- **Paper reporter smoke:** frozen-input comparison already passed; another Discord smoke is not required just to resume context.
- **Futures runtime tonight:** read-only health result PASS. Keep collecting; no strategy/config/deploy response to losses.
- **Docs closeout:** PR #1080 and agent-resume/governance PR #1081 merged; PR #1083 then refreshed this checkpoint metadata. The stored repo SHA above is the pre-refresh comparison base. Fetch current `main` instead of inferring it from this file.
- **Gate-condition #1068:** shipped in release `489b55b`. Do not force another copy into the tree or rewrite cron to pick it up. It had not yet executed from that release at the 2026-10-02 post-deploy read.
- **#994:** WAIT. Stage B was not earned. Do not run the study unless a new explicit research decision changes that.
- **Secret discovery for #1037:** closed. Do not repeat secret scanning merely to reconstruct context.
- **SSH hardening:** not authorized yet. Do not disable password authentication, remove current access, or convert access paths until replacement/recovery proofs exist.
- **Branch cleanup:** no branch was deleted in the closeout because delete proof was absent. Do not mass-delete old release/hold/archive/research branches.

### CURRENT PRESERVE

- **Runtime freshness boundary:** futures runtime truth is the 2026-10-02 11:06 ET checkpoint above (release `489b55b`, PID `1327346`). The 2026-09-30 lines below are history. Before any runtime/broker/access mutation, obtain the smallest fresh read-only proof required.
- Futures trading service on 2026-09-30 was release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`. That commit is now the rollback target, not the running release.
- At the last verified 2026-09-30 ET checkpoint, paper/shadow evidence collection was continuing under frozen contracts.
- Last verified reporter rollback pin: `releases/b60931a6a9f8-reporter-6512dc3578e9`.
- Preserve `candidate/tradovate-auth-only-41ae188` and `fix/watcher-resolve-only-when-cleared` unless fresh containment/deletion proof is produced.

### NEXT — operator window, in order

1. Prove phone SSH login.
2. Prove Hetzner/provider-console recovery.
3. Run candidate build/verify and a rollback drill in an explicit operator window.
4. Only after 1–3 pass, evaluate SSH hardening and old-access removal.
5. Carry gate-condition #1068 into the next sanctioned futures release. **Done in `489b55b`.** Do not repeat this from the 2026-09-30 list.
6. Resolve remaining #1037 owner decisions: main protection/ruleset governance, historical operational exposure, retain-history vs fresh-root decision, public branch/ref cleanup, old VPS-address check, clean export.
7. Continue ordinary paper/shadow/guarded-DEMO collection. Losses alone do not trigger strategy changes.

### OPTIONS CHECKPOINT — resume, do not rebuild

Options resume is governed by the same checkpoint-first / diff-first rule.

- **Current-state authority:** `docs/options-current-state-handoff.md`. Do not create a competing options status file.
- **PR #1073 MERGED** as `3c3dcd5b0567943e5af9d452d3adf7c06d41b7e5` (squash of reviewed head `0232c187e601b87c286af0ad70c5dd47acac3a58`). `docs/options-current-state-handoff.md` is now dated 2026-09-29. **Options task checklist: `docs/options-next-actions.md`.** Do not recreate that checklist elsewhere.
- **PR #1077 MERGED** as `8c4e2e4` (squash of reviewed head `93879644c9b248c8cb2d29b172b086fa9af5c88a`; exact-head CI green; `/options-diff-review` APPROVE). It adds display-only **Observation Rating (A/B/C/N/A)** and **AFS Trade Grade (A/B/C/F/N/A)** surfaces; neither changes scanner score, alert eligibility, setup state, contract selection, risk permission, orders, or execution. **Not deployed** to the options scanner. Do **not** start a second Signa-v2 implementation.
- Options production posture recorded by the merged #1073 remains advisory/read-only with the existing frozen evidence lanes; repository changes are not runtime proof.
- Real-data gates as recorded by the merged #1073 (none is permission to deploy): #1071 Epoch-3 real-data audit COMPLETE (`0dca986`; do not tune from the five-close sample); #1069 SPX→SPXW provider mapping/preflight COMPLETE on `d7a546c`, with an RTH check that SPXW 0DTE appears while the lane stays OFF still pending; #1067 real 66/66 RTH capacity proof inside the five-minute cadence still pending (`a4de6f9`). Verify which gates changed before doing work.

### OPEN / NEEDS DECISION — verify before touching

- #1077 options-scanner deploy: needs a separate operator GO; the scanner box release is unchanged.
- Closed-unmerged agent-safety doc branches associated with PR #1028 and #1036: verify whether their content is superseded before recreating anything.
- Forward evidence: `orb_reclaim` was stale and `vwap_rejection` quiet in the latest read-only check. This does not block current collection, but future summaries must not imply continuous evidence for those arms.

## Resume rule

A returning agent must not ask, “What should I redo?” It must answer:

1. Did the checkpoint identifiers actually change?
2. If yes, what changed in the smallest relevant scope?
3. Which items remain genuinely unresolved?
4. What is the smallest safe next action?
5. What additional information, if any, is specifically required before that action?

If more information is needed, retrieve only that information first. Broaden the search only when the narrower evidence is insufficient.

If nothing changed and no operator-window task is authorized: **do nothing and keep collecting.**
