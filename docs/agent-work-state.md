# Agent Work State

> **Purpose:** durable resume/checkpoint ledger for Grok and other agents so work survives quota exhaustion, outages, context loss, and multi-day gaps without being recreated.
>
> This file is **not** strategy-status authority, deployment authority, or experiment authority. Authoritative records named in `AGENTS.md` always win.
>
> **Checkpoint base:** repository `main` `d970b40` (PR #1131 merged, 2026-10-04); MNQ account-admission study is CLOSED / `INSUFFICIENT_EVIDENCE` / DO NOT REDO; shadow daily P&L report on the box = #1102 since 2026-10-02 14:53Z; futures box is on release `c44d32bc4961e56fae5c5f88a976eb6783341638` since 2026-10-04 21:29:09Z (rollback `489b55b`); **canonical 4HR natural-1m epoch start = 2026-10-04T21:30:05Z**. Always fetch current `main`; this stored SHA is a comparison base, not proof of current runtime state.
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

## Current checkpoint — 2026-10-06 (U2: chronological partitions + once-only OOS)

Task: U2 only from clean main after U1 merge (`de4486d`). Mechanically enforce chronological experiment partitions development → validation → untouched_oos with once-only OOS consumption per exact experiment/trial. Research/evidence plumbing only. No U3. No deploy. No merge from this session.

### VERIFIED

- Base `origin/main`: `de4486d6cef0c8116edce70d5f164b958de90a2e` (U1 merged).
- Branch `cursor/u2-chronological-partitions-f2da`.
- Single Experiment Runner retained; no second runner.

### CHANGED

- New `ops/experiment_partitions.py` (partition validation + durable OOS receipts).
- Runner/schema/docs/CLI wired for optional `chronological_partitions` / `evaluation_partition`.
- Legacy specs without partitions remain compatible; U1 trade evidence path unchanged when partitions omitted.

### TESTS

- Recorded in PR body after focused + full suite.

### RUNTIME MUTATIONS

None. No deploy, no VPS, no broker, no strategy change.

### DONE / DO NOT REDO for this lane

- Do not start U3 in this PR.
- Do not invent a family-wide OOS lock.

### NEXT

Independent Claude breaker QA of the U2 PR. Do not merge or deploy.

## Superseded / provenance — 2026-10-06 (U1: r_multiple / P&L direction consistency; merged via #1155)

Task: Continue PR #1155 only. Prior three Claude HOLD blockers closed at `5325eb1`. Fix the narrow new U1 blocker: FILLED rows must not report contradictory `r_multiple` vs `net_pnl`, or favorable gross P&L on adverse price move. Research/evidence plumbing only. No U2/U3. No merge/deploy. **Superseded** after `#1155` squash-merged to `main` as `de4486d`.

### VERIFIED

- Reviewed head before this patch: `5325eb126a5476fe9b0a885621f32a6297cf2a68`.
- Claude APPROVED U1 logic at `dc9963548502415c12fb8356fb83348b106e6a87`; this branch is a rebase-only onto current `main`.
- Branch remains `cursor/u1-trade-evidence-contract-f2da`.
- U1 rebase base includes `#1145` (`ae8c897`) and `#1146` (`445393f`) on `origin/main`.
- Prior three blockers remain closed; only Cases F/H consistency added.
- Existing runner + options coverage adapter remain the single runner path; no second runner invented.

### CHANGED

- `validate_trade_execution_row`: enforce `r_multiple` sign vs `net_pnl` (eps=`PNL_TOLERANCE`); enforce directional `gross_pnl` vs signed fill→exit move (no dollar recomputation).
- Cases F/H regression tests added.

### TESTS

- Focused + full suite + exact-head CI/handoff recorded in PR body after the new SHA.

### RUNTIME MUTATIONS

None. No deploy, no VPS, no broker, no strategy change.

### DONE / DO NOT REDO for this lane

- Do not start U2/U3 in this PR.
- Do not address Claude minor findings unless required by this blocker.

### NEXT

Independent Claude final re-review of the new exact head. Do not merge or deploy.

## Previous lane checkpoint — 2026-10-06 (#1145 merged; #1146 on main as `445393f`; no timer install)

`#1145` squash-merge: `ae8c8970966e4ed09d6679fe8d873f4b7de27cd9`. `#1146` security gate landed on `main` as `445393fe0b65c74be47a86e9fa14295a7af0c69a`. Options current-state authority remains `docs/options-current-state-handoff.md`. Futures runtime checkpoint (c44d32b) below is unchanged. **No observer timer install.** Kept as provenance while U1 is the active current checkpoint above.

### DONE / DO NOT REDO

- Oct. 5 SPY shadow 9925/9933 audit: late `H1_222_CONTINUATION` AHEAD after trigger 770.0768; not a prospective catch. Do not retune Strat from them.
- Observer scaffold + B1–B13 red-team fixes (see prior checkpoints; fix SHAs `d29b2cb` / `8a5eeed` / `00dbd59` / journal-path redaction on tip).
- `/health` setup_capture telemetry omits absolute journal filesystem path; `/setup-capture` retains path for operators.
- **#1145 MERGED** into `main` as `ae8c897` (2026-10-06). Observation-only setup-capture collector landed. Timer units remain uninstalled.
- **#1146 MERGED** into `main` as `445393f` (2026-10-06). Options scanner in-app access gate landed.

### OPEN / UNVERIFIED

- Live A13 sessions and Public INDEX real-time entitlement remain UNVERIFIED.
- CodeQL medium alerts on `/health`/`/setup-capture` may need operator dismissal (do not claim CodeQL green).

### DO NOT REDO

- Do not invent a scoring producer (#1089 stays telemetry).
- Do not enable SPXW, expand production watchlist, or promote 1H to ACTIVE.
- Do not deploy without explicit operator GO.
- Do not expose `/root/afs-shared/...` capture-journal paths on `/health`.
- Do not install the observer timer without a separate install GO.
- Do not re-merge or re-land #1145/#1146.

### NEXT (options observer lane; not U1)

Separate operator GO later for observer timer install + live prove. U1 rebase/delta review is the active NEXT above.

## Governance PR provenance — 2026-10-06 (docs-only; does not override runtime NEXT)

Task: remove overlapping agent responsibilities and make the research pipeline explicit without changing trading behavior.

- Base `main`: `31c9281f41e61791dc050d74f33acf02f87085e4`.
- Branch: `docs/clarify-agent-roles-20261006`.
- Grok role proposed: research / edge discovery / read-only futures+options contract discovery and comparison, including operator-authorized Robinhood/Webull account/market context when available.
- Cursor role proposed: implement approved changes, fix proven defects, and run registered and approved trials mechanically.
- Claude/Codex role remains independent breaker / QA.
- ChatGPT + operator own experiment approval/registration, reconciliation, status changes, and progression decisions.
- Forward paper/demo/observer lanes remain evidence collectors, not research agents.
- Read-only broker/account linkage is explicitly data context only; it grants no order/cancel/replace/exercise/close authority.
- Research flow: Grok proposes → ChatGPT/operator approve/register → Cursor runs/builds → Claude/Codex attacks → ChatGPT/operator decide.
- Files changed on this branch: `AGENTS.md`, `GROK.md`, `docs/options-current-state-handoff.md`, this checkpoint.
- Runtime mutations: none.
- Strategy/risk/broker/execution changes: none.
- PR workflow note: independent review/merge state is transient GitHub metadata, not the durable runtime NEXT. After this PR is resolved, follow the current runtime checkpoint below.
- DO NOT REDO: do not recreate a second role matrix, experiment selector, or parallel research queue elsewhere.

## Superseded / provenance — 2026-10-06 (PR #1143: PAPER posture + advisory Discord off alert lock)

Task: close the two remaining independent-QA blockers on existing branch `cursor/futures-advisory-visibility-f2da`, plus local presentation corrections. No scope expansion. **Superseded** after `#1143` merged to `main` as `55b9d4d`; kept as provenance.

### VERIFIED

- Independent breaker QA at exact prior head `13ae326c466e7ef19703efc65f6f35d04e721b6f`: prior three HOLD findings remain fixed; two new blockers were real (PAPER on unselected TRADE candidates; synchronous `notify_futures_advisory()` inside `_handle_alert_blocking` / `_alert_lock`).
- Code fix commit: `02eb57577ead63a3a42a5532093042ed863552af`.
- Canonical identity remains `strategy.shadow_resolver._candidate_key(...)`. This change does not edit `_candidate_key()` or resolver outcome math.

### CHANGED

- `_posture()` returns `PAPER` on a TRADE bar only when `selected is True`; unselected candidates stay `SHADOW / ADVISORY ONLY`.
- Advisory Discord uses the observation-style daemon queue: `_handle_alert_blocking` copies `dict(result)` and enqueues; HTTP/retries/429 sleeps run off `_alert_lock`.
- Discord fan-out capped at `MAX_ADVISORY_CARDS_PER_ALERT = 3` (delivery only).
- Evidence labels match the leading inventory verdict; same-key different stop/target does not attach; orphan SHADOW_OUTCOME cards are presentation-deduped; resolver identity reconstruction prefers `ts` before `timestamp`.

### TESTS

- `python3 -m pytest -q tests/test_futures_advisory.py` → **27 passed**.
- `python3 -m pytest -q tests/test_futures_advisory.py tests/test_discord_notifier.py tests/test_candidate_snapshot.py tests/test_why_no_trade_report.py tests/test_shadow_resolver.py tests/test_shadow_setups.py tests/test_discord_card.py tests/test_discord_router.py tests/test_observation_discord_route.py tests/test_viewer.py tests/test_webhook.py tests/test_e2e_scenarios.py tests/test_notification_market_gate.py tests/test_observation_discord_delivery.py tests/test_one_min_response_audit.py` → **346 passed, 1 Starlette TestClient deprecation warning**.

### RUNTIME MUTATIONS

None. No deploy, no VPS, no env, no broker.

### DONE / DO NOT REDO for this lane

- Do not recreate candidate detection, shadow resolution, or a new ranker.
- Do not retune confluence weights or change inverse/evidence epochs.
- Do not change Discord router retry policy globally.

### NEXT

Independent Claude/Codex breaker-QA re-review of PR #1143. Do not merge or deploy.

## Superseded / provenance — 2026-10-06 (PR #1143 HOLD fixes: OPEN, candidate_key join, panel name)

Task: three presentation/evidence correctness fixes on existing branch `cursor/futures-advisory-visibility-f2da`. No scope expansion. **Superseded** by the PAPER-posture / alert-lock checkpoint above; kept as provenance of the prior HOLD round.

### VERIFIED

- Independent review HOLD on PR #1143: fabricated OPEN; loose outcome join; "Qualified setups" label.
- Canonical identity remains `strategy.shadow_resolver._candidate_key(lane, instrument, bar_ts, strategy, direction, entry[, epoch, variant])`.

### CHANGED

- Omit Later outcome unless a recorded canonical SHADOW_OUTCOME exists.
- Join outcomes only on unique reconstructed/recorded `candidate_key`.
- Dashboard panel renamed to Observed setup candidates (advisory only). Card line "Why setup qualified" → "Setup notes".

### NEXT

Re-review PR #1143. Do not merge or deploy.

## Superseded / provenance — 2026-10-06 (futures advisory visibility, source-only)

Task: surface already-recorded futures shadow/candidate setups to the operator in real time. Presentation only. No deploy, merge, VPS, strategy, risk, or broker changes. **Superseded** as an active current checkpoint; kept as provenance of the original advisory-visibility implementation.

### VERIFIED

- `origin/main` at start of this work: `31c9281`.
- Existing machinery: `strategy/shadow_setups.py` (geometry + `resolve_shadow_candidate`), `strategy/signal_engine.py` (`candidate_audit` / rank metadata), `webhook/runner.py` (`_record_candidate_audit`, journal `shadow_candidates`, `resolve_pending_shadow_outcomes`), `strategy/shadow_resolver.py` (canonical `SHADOW_OUTCOME`), `scripts/why_no_trade_report.py`, Discord decision cards, dashboard inventory/log.
- Gap was presentation: candidate_audit was not copied onto `process_alert` result; Discord default notify list misses `SHADOW_NO_ORDER`; dashboard `_public_entry` stripped candidate geometry.

### CHANGED

- Branch `cursor/futures-advisory-visibility-f2da`, draft PR #1143.
- New `notifications/futures_advisory.py` + `tests/test_futures_advisory.py`.
- `webhook/runner.py` copies recorded candidate_audit / shadow outcomes onto the alert result.
- `webhook/app.py` sends advisory cards on the existing Discord signal route and lists them on Home/Futures dashboard tabs.

### TESTS

- `python3 -m pytest -q tests/test_futures_advisory.py tests/test_discord_notifier.py tests/test_candidate_snapshot.py tests/test_why_no_trade_report.py` → **40 passed**.
- `python3 -m pytest -q tests/test_shadow_resolver.py tests/test_shadow_setups.py tests/test_candidate_snapshot.py tests/test_discord_card.py tests/test_discord_router.py tests/test_observation_discord_route.py tests/test_viewer.py tests/test_webhook.py tests/test_e2e_scenarios.py` → **257 passed, 1 warning**.

### RUNTIME MUTATIONS

None. No deploy, no VPS, no env, no broker.

### DONE / DO NOT REDO for this lane

- Do not recreate candidate detection, shadow resolution, or a new ranker.
- Do not retune confluence weights or change inverse/evidence epochs.

### NEXT

Independent review of PR #1143. Operator may deploy an exact reviewed SHA later. Do not merge or deploy from this session.

## Previous checkpoint — 2026-10-04 ~21:30Z (c44d32b deployed; canonical 4HR epoch started)

Exact release `c44d32bc4961e56fae5c5f88a976eb6783341638` was built, verified, and promoted successfully. Rollback remains the prior `489b55b` release.

### DONE / DO NOT REDO

- 3-2-2 remains WAIT / PARKED. Do not rerun or tune.
- #1129 drift-gate fix is merged and installed; manual gate verification passed and no service restart was required.
- Exact `c44d32b` deployment completed successfully: build PASS, verify PASS, promote PASS.
- Post-deploy verification passed: deployed SHA/cwd match `c44d32b`; release integrity PASS; no release-tree bytecode drift; required paper/demo and one-contract pins match; observer flags are on.
- 4HR observer remains non-executable and outside the executable strategy list.
- Fresh post-deploy broker proof: 0 open positions and 0 working orders.
- futures-bot is active with no restart loop.
- Heartbeat refreshed after restart and now passes.
- Live remains disarmed; no arming action was taken.

Exact identifiers (Cursor, 2026-10-04 21:08–21:30Z; runtime path = root read-only over `ssh hetzner`, one authorized install, the sanctioned release script):

- **Drift gate installed:** `/root/bin/afs-drift-gate.sh` = `scripts/afs-server-drift-gate.sh` at `94136a5` (blob `1347906…`, sha256 `3cf086a3…c86f9a`, mode 0755) at 21:10:57Z; backup `/root/bin/afs-drift-gate.sh.bak-20261004T211057Z` (sha256 `49b41262…e25bdc` = #680 version + the two hand-added `-B` flags; nothing box-local lost). Manual run 21:11:10Z on `489b55b`: exit 0, `OK release-integrity: 489b55b91b63`, 0 `__pycache__`. Cron line unchanged (`5 11 * * *`). Not yet observed from cron against `c44d32b`.
- **Deploy:** `scripts/atomic_release.sh` from checkout `94136a5` (release tooling unchanged since `6ad0bac`), `AFS_BOX=hetzner`. build 21:27:12–21:27:59Z (integrity OK 1633 files, local + box); verify 21:28:27–21:28:36Z (candidate unit on `127.0.0.1:57963`, `BROKER=paper`, live false); promote 21:28:57–21:29:13Z via Path 1 (box on reset-baseline posture `always_on_shadow`/`off`/`static`; behavior-neutral gate not required and would have refused on 4 context/notification files — not bypassed, simply not applicable). Manifest fingerprint `a2860378…2e4f1` pinned in `.env`; `release_history.txt` 101 entries; `current.previous` = `489b55b`. futures-bot PID `1851835`, `NRestarts=0`, ActiveEnter 21:29:02Z; afs-watcher resynced and active 21:29:09Z.
- **Broker proofs** were the bot's own in-process preflight (`POST /admin/live-preflight/run`, secret taken from the process env into a header, never printed): 21:20:50Z on `489b55b` (only `heartbeat_fresh` failed) and 21:30:05Z on `c44d32b` (all 8 checks PASS, `armed: False`). No second Tradovate session was opened (two-session limit; see `execution/tradovate_broker.py`).
- **Access correction:** no `afs-ro` user or binary exists on the box; `afs-ro@` rejects the workstation key; `claude-audit` has no key here. Only `grok-audit` (sudo-restricted `/usr/local/sbin/afs-grok-audit`) and `claude-audit` accounts exist. Prior "Cursor's `afs-ro` lane" wording is inaccurate.
- **Repo hygiene (same day, after the deploy):** 14 merged local branches and 4 clean merged worktrees removed; 5 CLOSED-unmerged superseded remote branches tagged `archive/*-2026-10-04` and deleted with SHA lease (recorded in `docs/BRANCH_ARCHIVE_INDEX.md`, PR #1131). 54 remote branches remain; `release/*`, `hold/*`, `archive/*`, KEEP refs, open PRs, `../afs-options-1111` (dirty), and `stash@{0}` untouched. Further remote cleanup is a separate audited pass.

### CANONICAL 4HR NATURAL-1M EPOCH

**START = 2026-10-04T21:30:05Z UTC.**

This is the timestamp of the last required post-deploy proof on exact release `c44d32b`. It precedes the Sunday CME reopen.

From this timestamp forward: prospective evidence only; no backfill; no parameter tuning; no strategy changes; no Polygon work; no 3-2-2 work.

### OPEN QUESTIONS / NEXT

1. Collect forward 4HR natural-1m evidence only.
2. On the first #1103-compliant touch, record `contract_hint` as MATCH / MISMATCH / UNKNOWN and whether manual review is required.
3. Preserve observer-only posture and live-disarmed state.
4. Keep historical evidence separate from this forward epoch.
5. Verify the next drift-gate cron result read-only when convenient (2026-10-05 after 11:05Z, expect `OK release-integrity: c44d32bc4961`); it is not a prerequisite to collecting the epoch.
6. After the first full session on `c44d32b`: `/futures-deployment-safety-audit`; confirm #1095 writes observation status under `/root/afs-shared/logs/`, not inside the release tree.

## Earlier checkpoint — 2026-10-04 ~20:00Z (3-2-2 corrected rerun DONE; system HOLD unchanged)

Repo `main` `f38acde` (#1124, #1126). Box release still `489b55b`; candidate `c44d32b` NOT built/deployed; rollback `75f10e4` present. futures-bot PID 1457117 since 2026-10-02 15:10:47Z, NRestarts=0. These runtime facts are from Cursor's read-only 2026-10-04 ~19:50Z preflight relayed by the operator; nothing was mutated. Branch for this work: `research/322-corrected-rerun-20261004` (from `f38acde`).

### DONE / DO NOT REDO

- **3-2-2 #1057-corrected rerun — DONE 2026-10-04 (Claude), trial `T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01`.** Operator lifted only the research-rerun HOLD. Frozen harness `scripts/322_trigger_timing_ab_2026_09_18.py` unedited; same 34 candidates; corpora byte-identical to the 09-18 run; resolver at #1057. Exactly one row changed: 2025-01-20 SHORT (MLK early close) `TARGET_HIT` at 19:50 ET → `EOD_BAR_MISSING`. Pre-armed 3-tick: **+$2,471.64**, 32 resolved / 32-0, H1 +$1,128.32 / H2 +$1,343.32, PF ∞, max DD $0 (archived +$2,709.66 / 33-0; delta −$238.02, all in H1). Classification **PROMISING BUT UNPROVEN**. Artifact `docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json`; narrative `docs/322-corrected-eod-rerun-erratum-2026-10-04.md`. Ledger PLANNED + COMPLETED lines appended. Disclosure: scoring ran before the PLANNED commit (zero free parameters); reconciliation may downgrade. Do not rerun. Inventory wording update is **proposed in the erratum, not applied**.
- **Env note for any future offline PaperBroker research:** `MAX_CONTRACTS_HARD_CAP=1` must be in the process env since #1053 or every bracket is `NO_FILL`. Harness `check_repro` pins are now known-stale (pre-#1057); left unedited deliberately.
- Cursor 2026-10-04 preflight (relayed): delta-only MNQ/MES evidence audit done; pins verified by name; 4HR natural-1m canonical epoch NOT started; 3-2-2 1m observer 0 arms / 0 touches (10 rows 09-21→10-02); `wide_stop_4k` filled_count=0; drift-gate ALARM 10-02/03/04 is a tooling false alarm (`/root/bin/afs-drift-gate.sh:81` omits `EXPECTED_RELEASE_FINGERPRINT`); release tree intact.

### UNKNOWN (carried)

- MNQ 1m alert `contract_hint`; #929 forward fill count (Mac-only Polygon fetch, last 0 as of 09-23); broker flat / no unexpected orders not re-proven since 2026-10-02 15:17Z.

### NEXT — in order

1. Reconciliation role (ChatGPT + operator): accept/reject the proposed Strategy Inventory wording in `docs/322-corrected-eod-rerun-erratum-2026-10-04.md`; decide whether the ledger line stays `COMPLETED` or is downgraded for execution-order.
2. Operator: acknowledge the drift-gate false alarm; queue the gate pin fix as a separate GO.
3. Only then: decide whether `c44d32b` deploy + canonical 4HR 1m epoch deserves attention.
No new strategy work. No broad research cycle. No second 3-2-2 rerun.

## Earlier checkpoint — 2026-10-02 15:17Z (observer ON; canonical epoch HOLD pending #1103 deploy)

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

## Next futures release candidate — PREPARED / HOLD FOR RUNTIME GATE

- **Candidate:** `c44d32bc4961e56fae5c5f88a976eb6783341638`, based on deployed `489b55b91b6303c195c8e84bfcbf05ef32d1ab04`.
- **Scope:** exactly #1095 + #1103 only: 8 changed files total. No unrelated current-`main` changes ride along.
- **Lineage:** PR #1123 merged history-only with zero file changes, so the candidate is reachable from `main`.
- **QA:** seven source/test blobs are byte-identical to the reviewed #1095/#1103 commits; candidate audit rerun passed **7359 passed, 8 skipped, 2 deselected**. The two deselected tests are the current-`origin/main` research-spec/ledger governance comparisons, which cannot pass on an intentionally old minimal release tree without importing unrelated current research files.
- **Safety review:** the 1-minute 4HR observer returns before DecisionEngine/RiskEngine/broker paths, with `fill=None` and `execution_reachable=false`. #1095 changes only observer-status state location and prefers proven `LOG_DIR`.
- **NOT DEPLOYED:** no build, promote, restart, broker, env, or runtime mutation occurred.
- **BLOCKER / NEXT:** after 2026-10-03 12:00Z, read-only verify release-tree `__pycache__` remains absent and drift-gate is OK; then perform fresh deployment-safety/runtime verification before any build/verify/promote. Candidate promotion remains HOLD until those gates pass.

## Account-admission overlay — CLOSED / INSUFFICIENT EVIDENCE

Does not change the 4HR epoch above. #1108 stays the runtime record.

- **Current authority / DO NOT REDO:** trial `T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01` is closed as `INSUFFICIENT_EVIDENCE` on preserved research head `990b11135a1c49bc972981ac302930cd4f7565a3`. Result: `docs/research-evidence/T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01/result.json`; preserved look SHA-256 `0c6c084c830fa227951f2e8e4f47a85a2f22a72898014f243eced8ed8cc6f7e5`. Ledger is `COMPLETED` / `RESEARCH_ONLY` because the enum has no `INSUFFICIENT_EVIDENCE` token.
- Neither pass is the historical account result. Do not select the sensitivity, average the passes, rerun, retune, or change strategy status. Further evidence for exact account ordering must be forward evidence with request order recorded.
- `research/mnq_account_admission_overlay.py` is research-only. Pinned #915/#929 files were not edited; prereg #929 was not run.
- Daily loss uses the UTC calendar-date stand-in for live `date.today()`, and books a resolved fill to the entry's journal day (`open_position_date`). It does not use the CME 18:00 ET observation day.
- Drawdown stayed `DRAWDOWN_GATE_NOT_EVALUATED`; no provenance-backed starting balance/peak was supplied.
- **Historical progression below is provenance only. Do not resume from these intermediate blockers.**
- **2026-10-02 prereg completion attempt: `POPULATION_BLOCKED`.** The #929 forward window was not opened. The #915 historical stream had no committed fillable-event artifact, and a local rebuild did not reproduce its frozen control gate, so no artifact was kept. Futures-bot `date.today()` was read only at `2026-10-02T16:38:01Z`: process `TZ` unset, host zone `Etc/UTC`. No deploy, restart, or scored run.
- **2026-10-02 #915 reproduction:** archived `5a9f14b` reproduces the family controls on the local corpora. The earlier failure was current `main` code in `resolve_bracket` and `PaperBroker`, not a different population. Artifact `research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl` sha256 `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942`, 2257 rows. Same-timestamp census 280, so the account draft is `SAME_TIMESTAMP_ORDER_BLOCKED`. The overlay was not run. #929 was not run.
- **2026-10-02 reachable-collision census:** frozen capacity arbitration accepts 488 fills. 47 of the 280 raw equal-time pairs are reachable. The account draft stays `SAME_TIMESTAMP_ORDER_BLOCKED` for those 47. No overlay scoring. #929 was not run.
- **2026-10-02 ordering audit:** 45 reachable rows are `EXIT_BEFORE_CANDIDATE_PROVEN` on the same 15m Asia bar. 2 Daily/Asia rows are `DISTINCT_REQUEST_ORDER_UNKNOWN`. The account draft stays blocked. No overlay scoring. #929 was not run.
- **2026-10-02 one scored look: DONE / DO NOT REDO.** Head `1550e5219d410ff73600c6a1105bc277ee4c52d6`. Two passes only, no seed. Input 2257, artifact SHA unchanged, frozen capacity 488. Primary: 484 accepted, 102 `SKIPPED_DAILY_LOSS`, realized P&L `+$7,776.31`, max consecutive losses 14, max dollar drawdown `$3,544.51`, 39 proven overrides exercised, both unresolved candidates busy. Sensitivity: 409 accepted, 57 `SKIPPED_DAILY_LOSS`, realized P&L `+$13,292.39`, max consecutive losses 9, max dollar drawdown `$1,767.86`, 31 proven overrides exercised, both unresolved candidates filled. Sign stays positive. 141 dispositions differ beyond the two candidates. Classification `INSUFFICIENT_EVIDENCE`. `strategy_status_change_authorized = false`. #929 was not run. No deploy.
- **2026-10-02 operator close: DONE / DO NOT REDO.** Historical account-admission study closed as `INSUFFICIENT_EVIDENCE` and preserved at `docs/research-evidence/T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01/result.json`. Ledger disposition `RESEARCH_ONLY` because that enum has no `INSUFFICIENT_EVIDENCE` token. Neither pass is the historical account result. Do not select the sensitivity, average the passes, rerun, retune, or change strategy status. **NEXT:** stop. Further evidence has to be forward, where request order is recorded.

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

- **Current-state authority:** `docs/options-current-state-handoff.md`. Checklist: `docs/options-next-actions.md`. Do not create a competing options status file.
- **Verified main 2026-10-02 at machinery branch base:** `5d19257e3a690851c0aa580135eba66536b09ec4` (#1118). #1115 merged as `65847295521be1ff0d6b9ef89c8fb8699aff7735`. Registration-time main remains `d304c22eac0b95a93377230f2a238b97fb6d9a57` (#1114). The closed options one-look remains #1113 `3f5e3f928d88fc3a8e43f11c159dd3dc3066f703`.
- **DONE / DO NOT REDO:** #1067 capacity PASS on `fd280906` (not deployed; watchlist stayed 20). #1069 SPXW 0DTE PASS on `a265fe68` (lane stayed OFF). #1111 approval and the frozen 59-episode one-look. Result: SUPPORTED BY THIS EXPERIMENT / coverage only / not edge. `floor_ge1r` 2 vs `nearest_v1` 0. Report SHA-256 `0d47bf46fd62748e9e6b67a248d2ef6ef76aad072e6ad1d2fd43192f7f3343e8`. Do not rerun, rescore, or extend it.
- **2026-10-04 (Claude) — DONE / DO NOT REDO:** read-only inventory of the closed 59-episode population (pinned dataset `1963db73…` present locally; manifest `2ee0db91…` reproduced 59/59; both rescued activations `UNRESOLVED_AT_CLOSE`, so zero completed actionable W/L). Verdict `NOT READY` for a retrospective "winning conditions" analysis. Do not mine the 59 or the 40 floor-valid episodes for winner factors.
- **SUPERSEDED / NOT_RUN:** `T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01` (`E-2026-10-02-options-212c-floor-outcome-01`) was never approved, collected, or scored. 2026-10-05 is ineligible and is not backfilled. `#1133` companion `T-2026-10-04-prereg-options-212c-preentry-factors-2026-10-04-01` is also SUPERSEDED / NOT_RUN (activation-only tables; gate factors constant).
- **DRAFT / PLANNED / NOT RUN:** parent successor `T-2026-10-04-prereg-options-212c-floor-outcome-2026-10-04-01` (`E-2026-10-04-options-212c-floor-outcome-02`) plus floor-eligible companion `T-2026-10-04-prereg-options-212c-floor-factor-2026-10-04-01` (`E-2026-10-04-options-212c-floor-factor-01`). Eligible start remains **UNSET**. 2026-10-05 is permanently ineligible and is not backfilled. Path record `options_212c_floor_outcome_path-v0.2`. Companion is descriptive/hypothesis-generating over `MARKET_ALIGNMENT_REJECTED` + `WOULD_OTHERWISE_QUALIFY`. Companion denominators: population = floor-eligible scored rows; `activation_count` = `WOULD_OTHERWISE_QUALIFY` only; `MARKET_ALIGNMENT_REJECTED` describes W/L/timeout/R and contributes 0 activations. Stage B NOT EVALUATED. No trial data collected.
- **MERGED / SOURCE PREPARATION ONLY (2026-10-05 13:52:23Z):** #1134 merged to `main` as `7873f37ed89d32cb67960e673fbfb3e320c1613a` (parents `378d702` + reviewed head `d79a1a415e90e899193b2e6a28029c36081366f6`). Delta re-review of `b7830ca` → `d79a1a4` was APPROVE. CI on that exact head was green: handoff-fields, CodeQL, Analyze Python, Analyze Actions, and full `tests` (run `37319461943`). The DRAFT registration, path-v0.2 primitive, and collector quarantine are merged. **Coverage-release deploy verdict: NO-GO for starting the trial.** On current `main`, `build_session_artifact()` remains a pure in-memory primitive; repo search shows it is defined in `ops/options_212c_floor_outcome_study.py` and called only from `tests/test_options_212c_floor_outcome_study.py`. Nothing in the collector or service calls it to write `path-v0.2` session seals or append the manifest. Deploying the current coverage release would deploy the quarantine and would not produce the trial evidence required for `-02`. No deploy, approval, real seal, collection, provider I/O, or scoring occurred. Quarantined outcome files, including the full held files, remain blind/do-not-open until the legitimate one-look. Current coverage-box release pin, timer, and data state are unverified: the connected Mac/remote path is offline.
- **NEXT:** capture integration preparation + independent review, not deployment. The integration must consume already-collected causal `cov-v0.1` / `ep-v0.1` data, use the actual same-session 5-minute bars, call the reviewed `build_session_artifact()`, write exactly one immutable session seal plus one manifest entry, fail closed on any mismatch, add no scoring or readout beyond the allowed blind monitor, and change no broker or execution path. After that integration is implemented, independently reviewed, and merged, a fresh box/runtime deploy-readiness check is required before any deploy decision. Trial start is set only after the real path-v0.2 capture integration is implemented, independently reviewed, merged, and successfully deployed. No trial approval, no eligible start, no 2026-10-05, no backfill. Do not deploy #1067/#1069/#1077, expand the watchlist, or enable SPXW.

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
