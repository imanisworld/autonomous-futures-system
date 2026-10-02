# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Checkpoint base:** repository `main` `14abe2a23fe55d46ef6e0cefee513a47be5e93a4` (PR #1104, 2026-10-02); shadow daily P&L report on the box = #1102 since 2026-10-02 14:53Z; futures box on `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` since 2026-10-02 03:43Z. Always fetch current `main`; this stored SHA is a comparison base, not a perpetual current-state claim.
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

## Current checkpoint — 2026-10-02 15:17Z (observer ON; canonical epoch HOLD pending #1103 deploy)

Runtime observations below are from Grok's read-only `afs-ro` pass reported by the operator after the 15:00Z checkpoint. Repository facts were independently reconciled against GitHub.

### DONE / DO NOT REDO

- **Read-only server tool updated.** `afs-ro` now exposes the needed settings/pre-live surfaces. Backup `afs-ro.bak-20261002` is the older Sep 28 tool; the active `afs-ro` is the new tool used for the checks below.
- **Observer/runtime posture at 15:17:19Z:** release `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`; generic 1-minute + 4HR observer pins ON and matching their `EXPECTED_PROOF_` pins; pre-live had no mismatched/unpinned setting; live trading OFF; Tradovate DEMO flat; restart created no orders; journal advanced at 15:15:12Z; `strat_4hr_retrigger` remained outside the executable strategy list.
- **`LOG_DIR` proven:** `/root/afs-shared/logs`. This clears #1095's runtime-path prerequisite only; it does not authorize deployment.
- **13:49Z 422:** one TradingView request failed format validation before processing. Payload body was not retained, so the exact bad field remains UNKNOWN. It was the only rejection in the inspected 24-hour window. All six instruments retained their 13:48 one-minute bar, so no 1-minute data gap resulted.
- **Contract hint only partially answered:** 15-minute alerts reported `MNQZ2026` / `MESZ2026` matching routed contracts. Stored 1-minute bar rows do not retain `contract_hint`; the first #1103-compliant touch must resolve MATCH/MISMATCH/UNKNOWN.
- **Watcher P&L display bug fixed in source:** #1107 merged as `a2dbac1`; watcher/push-relay now use `today_pnl_dollars` for day P&L. The standalone server copies are still unchanged until a separate operator-approved install.
- **Watcher BLOCKED is separate from trade safety:** same-release restart baseline state plus the known options-scanner memory/swap condition. Do not reset watcher state or change scanner memory limits without an operator GO.

### CRITICAL RECONCILIATION — 15:17Z is not the canonical #1103 epoch start

Grok called 15:17:19Z the official 4HR epoch start. Repository proof contradicts that as a canonical #1103-compliant evidence boundary:

- deployed release `489b55b` predates #1103;
- #1103 (`43ead15`) adds `contract_check`, mismatch blocking, and `needs_manual_review` to `context/one_min_trigger.py`;
- the current epoch doc says every post-touch record carries `contract_check`, which `489b55b` cannot emit.

Therefore 15:17:19Z is an **observer-on timestamp only**. Any natural-1m 4HR touch before a release carrying #1103 is provisional/non-counting for the canonical epoch and must not be backfilled into it.

The canonical epoch begins only after an exact reviewed release carrying #1103 is deployed and a read-only verification confirms the required pins, observer-only posture, and no broker-order side effect.

### NEXT — in order

1. Do not redo Grok's completed runtime questions.
2. #1107 is merged source-only. Any watcher/push-relay server reinstall is a separate operator GO.
3. Next futures release: #1095 + #1103 only after exact-SHA review and `/futures-deployment-safety-audit`; build/verify/promote requires separate operator authorization.
4. After that release, repeat the read-only pin/posture/no-order verification. That timestamp becomes the canonical 4HR natural-1m epoch start.
5. After the 2026-10-03 12:00Z cron jobs, confirm no release-tree `__pycache__` and confirm the next drift-gate result is OK.
6. The first #1103-compliant touch answers whether the 1-minute alert proves the contract: MATCH/MISMATCH/UNKNOWN.

## Account-admission overlay — 2026-10-02, rebased onto `d04cd9a`

Does not change the 4HR epoch above. #1108 stays the runtime record.

- Sibling only: `research/mnq_account_admission_overlay.py`. Pinned #915/#929 files were not edited. Prereg #929 was not run. No historical stream was scored.
- Daily loss uses the UTC calendar-date stand-in for live `date.today()`, and books a resolved fill to the entry's journal day (`open_position_date`). It does not use the CME 18:00 ET observation day. The box timezone is still unconfirmed.
- Drawdown is `DRAWDOWN_GATE_NOT_EVALUATED` unless a provenance-backed starting balance and starting peak are supplied. Those then move only with accepted, resolved fills. After the frozen capacity checks, daily loss runs before that floor.
- Draft, not approved: `docs/prereg-mnq-account-admission-overlay-DRAFT-2026-10-02.md`, registered as PLANNED trial `T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01`. No historical overlay scoring has been run. The same-timestamp census is still blank, so the overlay is not exact live parity. Options candidate admission stays insufficient evidence and was not implemented.
- **2026-10-02 prereg completion attempt: `POPULATION_BLOCKED`.** The #929 forward window was not opened. The #915 historical stream has no committed fillable-event artifact, and a local rebuild did not reproduce its frozen control gate, so no artifact was kept. Futures-bot `date.today()` was read only at `2026-10-02T16:38:01Z`: process `TZ` unset, host zone `Etc/UTC`. No deploy, restart, or scored run.
- **2026-10-02 #915 reproduction:** archived `5a9f14b` reproduces the family controls on the local corpora. The earlier failure was current `main` code in `resolve_bracket` and `PaperBroker`, not a different population. Artifact `research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl` sha256 `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942`, 2257 rows. Same-timestamp census 280, so the account draft is `SAME_TIMESTAMP_ORDER_BLOCKED`. The overlay was not run. #929 was not run.

## Current checkpoint — 2026-10-02 ~15:00Z (#1102 installed, post-deploy audit)

Runtime facts below come from read-only `afs-ro` reads at 14:40–15:00Z, plus the output of the one box change listed.

### DONE / DO NOT REDO

- **#1102 shadow report installed on the box, 2026-10-02 14:53Z** (operator GO, via the operator's new `afs-deploy shadow-report install 14abe2a…`):
  - **The swap:** `/root/afs-shared/shadow_daily_pnl_report.py` went from the #1085 file (`ad0cbdb4…`) to #1102's (`971b251b…`). The deploy lock was taken and released.
  - **Backup:** `shadow_daily_pnl_report.py.bak-20261002T145306Z`. The action is logged in `/root/afs-shared/deploy_actions.log`.
  - **Smoke test (read-only rebuild of Oct 1, nothing written or posted):** 192 rows, **0 count mismatches**. MNQ 48 kept + 31 left out = 79; MES 43 + 26 = 69; total 91 + 57 = 148. The overlaps still open were MNQ 3 and MES 4, which confirms the cause of the old final-pass mismatch.
  - **No change to cron or the bot:** the three cron lines are unchanged and futures-bot was not restarted. The first live card with the new layout is the 2026-10-02 22:10Z first pass.
  - **Rollback:** `afs-deploy shadow-report rollback 20261002T145306Z`.
- **`afs-deploy` exists (Claude's Mac-side deploy tool, `~/.config/afs-deploy/afs-deploy`):**
  - It offers fixed operations only: `shadow-report install|rollback|backups`, and `release build|verify|promote|rollback`, which runs GitHub main's own `atomic_release.sh`.
  - Every SHA must be on GitHub main. It honours the deploy lock and never forces it.
  - A shadow-report install backs up first, runs a smoke test, and restores the old file automatically if the test fails.
  - All 39 fake-box tests passed.
  - Grok does not use it. It matters only because box actions it takes show up in `/root/afs-shared/deploy_actions.log`.
- **`/futures-deployment-safety-audit` on `489b55b` (Claude, ~14:55Z) = APPROVE, DEMO BROKER ONLY:**
  - **Release:** reviewed = deployed = `489b55b`, so there is no ride-along gap. futures-bot PID `1327346`, `NRestarts=0`, the only webhook process.
  - **Broker and trading state:** Tradovate DEMO `HEALTHY`, account session active, no position. Live trading disabled. `paper_mode=false` is acceptable only with demo + live off.
  - **Activity:** feed healthy for MNQ and MES; journal writing (14:45Z); alerts about 40×200 per tick.
  - **Memory:** `memory_guard` `HEALTHY` (RSS 338 MB, 1058 MB available); the 03:43Z `memory_critical` has cleared. The swap-pressure warnings come and go and are the known options-scanner swap issue.
  - **Release integrity:** no `__pycache__` in the release outside `.venv`; only the 14:29Z cleanup and `logs/` (the #1095 bug) changed.
  - **Deploy lock:** free at 14:53Z.
  - **Unverified with read-only tools:** `/status/*` endpoints, `EXIT_MODE`/`SCHEDULE_MODE`, live-preflight armed state, and `errors.log` (none under `/root/afs-shared/logs`).
  - **Two unexplained items, both warnings:**
    1. One TradingView alert was rejected `422 Unprocessable` at 13:49:05Z. Every other alert returned 200. The payload body isn't logged.
    2. The watcher's `today.realized_pnl_dollars = -59.75` with `trade_count 0`, while broker `realized_pnl` is 0.0.
- **Independent reviews of #1102 and #1103 (fresh-context reviewer, 2026-10-02): both APPROVE WITH NITS.** The nits were fixed before merge (`a7aac7f`, `179b676`). Still open, both low:
  - the shadow report crashes if a row has `bars_to_exit < bars_to_fill` (old, not from #1102);
  - #1103 silently drops a later conflicting contract hint for the same arm.

### NEXT — in order (after 15:00Z)

1. **Grok, read-only:** answer the three open runtime questions with exact evidence (file, line, timestamp):
   1. Do the **1-minute** TradingView alerts send `contract_hint`? Check the 1m webhook payloads and Pine alert config. `tf1m/bars_*.jsonl` doesn't store it, so look at raw request logs or the alert template. If they never send it, every #1103 row will be UNKNOWN.
   2. **`LOG_DIR`** of the running futures-bot. Boolean or path only, never other env values. It must equal `/root/afs-shared/logs` before the #1095 release.
   3. **The 13:49:05Z `422`:** which alert, which field failed validation, and whether it repeats.
2. Then the afternoon list below, unchanged: confirmations after 2026-10-03 12:00Z, then the next release (#1095 + #1103) with an operator GO per step, then the 4HR epoch.

## Earlier checkpoint — 2026-10-02 afternoon (after #1103)

All runtime facts below come from read-only `afs-ro` reads at 14:28–14:45Z, except the box changes, which the operator ran.

### DONE / DO NOT REDO

- **Release drift alert and fix (2026-10-02).**
  - **The alert:** at 11:05Z the drift gate reported "manifest integrity FAILED" on `489b55b`.
  - **The cause:** Python jobs that run code from the live release wrote cpython-314 `__pycache__` files into it, in three batches:
    - 03:47Z, most likely `feed-watchdog.timer`;
    - 11:05Z, the drift gate's own `-m ops.release_integrity` run;
    - 12:00Z, a cron job that was not identified.
  - **The risk:** `webhook/app.py` refuses to start on an integrity failure, so any futures-bot restart would have failed.
  - **Repo fix:** #1101 (`b74bacf`). The drift gate runs `ops.release_integrity` with `-B`, and the feed-watchdog, wide-stop EOD fallback and day-only-exit units set `PYTHONDONTWRITEBYTECODE=1`. #1100, a duplicate from another session, was closed.
  - **Box fix (operator, ~14:28Z):**
    1. A `no-bytecode.conf` drop-in on every unit referencing the live path except futures-bot: afs-coverage-collector, afs-wide-stop-demo-eod-fallback, calendar-sync, daily-digest, feed-watchdog, ibkr-watchdog, options-122-prospective, options-scanner, risksentinel. Then `daemon-reload`, with no restarts.
    2. `PYTHONDONTWRITEBYTECODE=1` added at the top of root's crontab (backup `/root/crontab.bak.pre-nobytecode.*`); nothing in `/etc/cron.d` matched.
    3. Ten `__pycache__` directories removed from the release, leaving `.venv` alone.
    4. Integrity with the shared `.env` loaded: **OK, 1632 files**.
  - **Still clean after the 14:34Z feed-watchdog run:** no `__pycache__` in the release.
  - **Restart safety:** futures-bot can restart safely again.
- **Box memory:** the post-deploy `memory_critical` was a startup projection. futures-bot PID `1327346` (unchanged) was at RSS 324 MiB at 14:31Z, a plateau consistent with release `5115b78` (about 360 MiB).
- **Options scanner:** `apt-daily-upgrade` restarted it at about 06:49Z (new PID `1354682`, RSS about 232 MB), along with the watcher and push-relay. futures-bot was **not** restarted.
- **Options-scanner swap:** before that restart the scanner held 470 MiB in swap under `MemoryMax=350M` (cgroup `max` events 1301, `sock_throttled` 8170). Daily scan counts stayed steady from 09-24 to 10-01 (3,087–3,369), so no skipped cycles are visible. It is recorded in `docs/futures-operator-todo.md` (#1099) and needs an operator decision.
- **Merged since #1098:**
  - #1099: TODO items for the memory plateau check, the scanner memory cap, and the #1085 install marked done.
  - #1101: release jobs no longer write bytecode.
  - #1102: shadow-report one-at-a-time labels.
  - #1103 (`43ead15`): 4HR arm contract-month check.
    - Behavior: `contract_check` is MATCH, MISMATCH (recorded as `TRIGGER_BLOCKED` / `CONTRACT_MONTH_MISMATCH`), or UNKNOWN (`needs_manual_review`).
    - Review: `/futures-diff-review` APPROVE PAPER-DEPLOY at head `179b676`. Locally, 95 targeted and 323 wider tests passed, and the new tests fail on the base. CI green.
  - None of these is deployed. #1101 is a repo change, and its units are already covered on the box by the drop-ins above.
- **4HR observation publisher is live:** `logs/tf1m/4hr_observation/state_2026-10-02.json` is being written (14:40Z status `INVALIDATED`). The observer flag is still OFF, so no natural-1m evidence exists and the epoch has not started.
- **#1095 bug seen live:** `logs/discord_observation_status.json` is written inside the release directory (last at 14:30Z). It is not flagged by the integrity check because `logs/` is excluded. #1095 fixes it and is not deployed.

### NEXT — in order (afternoon; still valid, after the 15:00Z list above)

1. **Read-only confirmations:**
   - after the 2026-10-03 12:00Z cron jobs: no `__pycache__` in the release;
   - the next drift-gate run reports OK;
   - futures-bot RSS stays flat.
2. **Next futures release**, with an operator GO for each step:
   1. prove `LOG_DIR=/root/afs-shared/logs` on the box (required for #1095);
   2. `/futures-deployment-safety-audit`;
   3. build, verify and promote an exact reviewed `main` SHA carrying #1095 and #1103;
   4. install #1101's `afs-server-drift-gate.sh` on the box separately, since the box runs its own copy.
3. **4HR natural-1m epoch:** follow `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`, after #1103 is deployed and before the 2026-12-11 roll.
4. **Open question:** do the 1-minute TradingView alerts send `contract_hint`?
   - The Pine script sends it only when `cc_proven`.
   - The box's `tf1m/bars_*.jsonl` rows do not store it.
   - If the 1-minute alerts never send it, every #1103 check will be UNKNOWN and need manual review.
5. **Options-scanner memory cap:** operator decision.

## Earlier checkpoint — 2026-10-02 morning

### DONE / DO NOT REDO

- **DEPLOYED `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`, 2026-10-02 03:43Z (operator GO; operator ran `scripts/atomic_release.sh` build → verify → promote from a checkout at `6ad0bac`).** Read-only `afs-ro` proof at 03:43–03:46Z:
  - **Release and process:** symlink and futures-bot PID `1327346` (cwd `/root/afs-releases/489b55b…`, started 03:43:25Z, `NRestarts=0`).
  - **Integrity:** OK at every step, 1632 files (local build, box build, verify, promote). The live release has no `__pycache__`; promote's unit drop-in sets `PYTHONDONTWRITEBYTECODE=1` and `RELEASE_INTEGRITY_ENFORCED=true`.
  - **Startup checks on real startup:** promote health `broker=tradovate` passed #1054's exact-`TRADOVATE_ENV` check; verify's candidate started under #1053's required hard cap.
  - **Broker and trading state:** Tradovate DEMO `HEALTHY`, account session active, no position, live trading disabled.
  - **Writes and history:** first post-deploy journal write 03:45:15Z. `release_history.txt` appended; promote restarted `afs-watcher` itself.
  - **Watcher warning:** the watcher's first tick flagged `memory_critical`, extrapolated from 2 startup samples (RSS 157 MiB, 1312 MiB available). RSS was 227 MiB and rising normally at 03:46Z. Confirm it cleared at the next read.
  - **Rollback target:** `75f10e4`.
  - **Pre-deploy env proof** (operator, boolean-only read of the running process's environ, 2026-10-02): `TRADOVATE_ENV` exactly `demo`; `MAX_CONTRACTS_HARD_CAP` = `1` with a matching `EXPECTED_PROOF_` pin. `/root/afs-shared/.env` mtime 2026-09-30 00:35:53Z, unchanged since that process started.
- **Release tooling incident, 2026-10-02 (fixed):** the first `atomic_release.sh build 489b55b…` failed locally (`release integrity: FAIL … ops/__pycache__/*.pyc`). #1056 refuses any `__pycache__` and reports `UNPINNED` (exit 1) without `EXPECTED_RELEASE_FINGERPRINT`; the script handled neither. Nothing reached the box; the deploy lock was confirmed free afterwards. Fixed by #1096 (`6ad0bac`), which is what made the deploy above possible. `afs-deploy.sh` (outside the repo) was not reviewed for the same two problems; use `atomic_release.sh` until it is.
- **4HR MNQ forward evidence:** 0 prospective fills since the 2026-09-09 wide-stop epoch. The `wide_stop_4k` lane saw 2 candidates, both blocked before an order (2026-09-15 `REGIME_RESTRICTED`; 2026-09-30 `ENTRY_DETACHED_FROM_PRICE`). The natural-1m 4HR lane never wrote evidence; cause and the new-epoch rules are in `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. Do not re-audit; do not backfill.
- **Merged 2026-10-02:** #1077 Signa v2 (`8c4e2e4`), #1092 4HR natural-1m observation (`8b7d968`), #1094 observation publish fail-safe (`489b55b`), #1095 observation status path follows `LOG_DIR` (`1c43291`), #1096 release-script bytecode/fingerprint fixes (`6ad0bac`), plus docs #1073, #1093 and #1097.
  - **On the futures box (release `489b55b`):** #1092, #1094, #1053, #1054 and gate-condition #1068.
  - **Not on the box:** #1095 (after `489b55b`; not independently reviewed for deploy).
  - **No deploy needed:** #1096 is Mac-side release tooling, effective from an updated checkout.
  - **Separate GO:** #1077 is options-scanner code; that deploy needs its own GO.
- **Reviews done:** `/options-diff-review` of #1077 at `9387964` = APPROVE (260 tests). `/futures-diff-review` of #1092 = APPROVE PAPER-DEPLOY; its should-fix landed as #1094.
- **Release review `75f10e4` → `6ad0bac` done (2026-10-02):** code tightens safety; #1096 is release-script tooling only (bytecode off and fingerprint pin on every integrity check); `strategy/`, `journal/`, and `risk_rules.yaml` unchanged; all 7 live-only commits are patch-present on main (stale-bearer fix and FI-18 account pin intact). The HOLD was cleared by the env proof above and `489b55b` was deployed. `/futures-diff-review` of #1053 + #1054 (Claude, 2026-10-02) = APPROVE PAPER-DEPLOY: 274 targeted + 1249 trading-path tests passed. Re-review only commits after `6ad0bac`.
- **Live release preserved on GitHub:** tag `archive/futures-stale-bearer-curated-75f10e4-2026-09-29` → `75f10e4`.
- **60M 3-2-2 corpus rerun:** operator re-confirmed **HOLD** 2026-10-02. A rerun cannot change the $6k account-size blocker. Revisit near $6k equity, before quoting any 3-2-2 figure, or when forward 3-2-2 trades need a clean historical baseline.

### NEXT — morning list (superseded by the afternoon list above; items 1, 3 and 4 are folded into it, and item 4 landed as #1103)

1. **Post-deploy audit of `489b55b`:** run `/futures-deployment-safety-audit` after a full session on the new release. Confirm the watcher's startup `memory_critical` cleared, journals keep advancing, and there are no new errors.
2. **4HR natural-1m epoch:** start only per `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`. Set `ONE_MIN_TRIGGER_ENABLED` and `ONE_MIN_4HR_OBSERVER_ENABLED` with both `EXPECTED_PROOF_` pins; that is a pinned env change, so it needs an operator GO and a bot restart. Then an operator read-only verification; that time is the epoch start. The flag is OFF today, so no natural-1m 4HR evidence exists yet.
3. **#1095 in a later release:** review it, prove `LOG_DIR=/root/afs-shared/logs` on the box, then build/verify/promote an exact reviewed SHA with a separate GO.
4. **Before the 2026-12-11 roll:** add a contract-month check to the 4HR observation snapshot (see the epoch doc).
5. **#1077 options scanner:** separate deploy GO.

## Previous checkpoint — 2026-09-30 ET

### DONE / DO NOT REDO

- **Paper reporter:** installed and verified; active pin `releases/b60931a6a9f8-reporter-1068-sixmarket-backport`. Do not rebuild merely because Grok returns.
- **Paper reporter smoke:** frozen-input comparison already passed; another Discord smoke is not required just to resume context.
- **Futures runtime tonight:** read-only health result PASS. Keep collecting; no strategy/config/deploy response to losses.
- **Docs closeout:** PR #1080 and agent-resume/governance PR #1081 merged; PR #1083 then refreshed this checkpoint metadata. The stored repo SHA above is the pre-refresh comparison base. Fetch current `main` instead of inferring it from this file.
- **Gate-condition #1068:** decision already made — deferred until the next sanctioned futures release. Do not force it into the immutable live release or rewrite cron solely to pick it up.
- **#994:** WAIT. Stage B was not earned. Do not run the study unless a new explicit research decision changes that.
- **Secret discovery for #1037:** closed. Do not repeat secret scanning merely to reconstruct context.
- **SSH hardening:** not authorized yet. Do not disable password authentication, remove current access, or convert access paths until replacement/recovery proofs exist.
- **Branch cleanup:** no branch was deleted in the closeout because delete proof was absent. Do not mass-delete old release/hold/archive/research branches.

### CURRENT PRESERVE

- **Runtime freshness boundary:** the runtime facts below are the last verified 2026-09-30 ET checkpoint, not perpetual current-state claims. Before any runtime/broker/access mutation, obtain the smallest fresh read-only proof required. Later external monitor/agent messages do not supersede this checkpoint unless independently verified.
- Futures trading service: last verified pinned release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, Tradovate DEMO, live trading disabled.
- At the last verified 2026-09-30 ET checkpoint, paper/shadow evidence collection was continuing under frozen contracts.
- Last verified reporter rollback pin: `releases/b60931a6a9f8-reporter-6512dc3578e9`.
- Preserve `candidate/tradovate-auth-only-41ae188` and `fix/watcher-resolve-only-when-cleared` unless fresh containment/deletion proof is produced.

### NEXT — operator window, in order

1. Prove phone SSH login.
2. Prove Hetzner/provider-console recovery.
3. Run candidate build/verify and a rollback drill in an explicit operator window.
4. Only after 1–3 pass, evaluate SSH hardening and old-access removal.
5. Carry gate-condition #1068 into the next sanctioned futures release.
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
