# Futures deployment plan — refreshed 2026-10-08; candidate NOT YET SELECTED

**PLAN ONLY / HOLD.** This revision supersedes the former `c44d32b → 7c93027` release proposal below. The previous version remains as a historical review baseline, **not as a live deployment plan**.

## Active decision record (governs all older sections below)

| Item | Verified or required state |
|---|---|
| Previously deployed release | `c44d32bc4961e56fae5c5f88a976eb6783341638`, reported in the read-only VPS audit; reconfirm on box before GO |
| Post-#1189 main | `064ee674b788c144fc8d3ca65082ed060927e2f1` (PR #1189 merged; Grok PASS on original head `f60ec6f` is operator-reported) |
| Previous candidate | `7c930274179f7c76749f75b35adb14fbb9255e54` is HISTORICAL ONLY; do not build/promote it under this plan |
| **Next candidate** | **UNSET** until #1194 watcher safety is independently reviewed, green on the exact head and merged. Select exact merged SHA and rerun source/trading-path delta and CI against that SHA |
| Plan status | **CHANGES REQUIRED / HOLD** pending B1/B2/B5/B6/B7/B8/B9/B10, the AFS-0165 gates below, validated rollback, an independent Grok review of the nominated SHA, and an exact-SHA operator GO |
| Cross-service boundary | Futures promotion must not silently change `options-122-prospective` collector code, Python, venv, journal schema or evidence cohort; independently pin or explicitly approve a proven migration under separate GO |
| Previous 51/51 release-wrapper tests | **Operator-reported, not independently reproduced.** Reuse only for unchanged code paths. Post-#1189 and #1194 quote/working-directory changes require their own fresh exact-head tests and reviewed render evidence |

### B10 — Watcher WorkingDirectory and chmod safety (new mandatory gate)

Review comments on #1190/#1194 (posted via the ChatGPT/Codex account, not Grok) identified a dangerous remote-shell quote expansion in **both** promote and rollback watcher re-arm. Grok has **not** independently reviewed #1194; no reviewer PASS is inferred. On an empty systemd WorkingDirectory, the effective unquoted `chmod 700 $watcher_dest/*.sh` could target `/*.sh`. Merely testing that a Bash source file parses is not enough.

**Source fix:** [draft PR #1194](https://github.com/imanisworld/autonomous-futures-system/pull/1194), separate from this docs-only plan. It adds a fail-closed nonempty, realpath-resolved directory guard, restricts the canonical destination to the configured trusted shared-root subtree and preserves quoting through the nested remote command. #1194 is **not merged or approved** as of this revision.

**Required proof before any build or promote:**
1. Verify exact SHA of #1194 merged to main with green exact-head CI and Grok PASS; do not transfer #1189's PASS.
2. Inspect the *rendered* remote Bash for promote **and** rollback. It must use quoted `"$watcher_dest"` in every path, including `chmod 700 "$watcher_dest"/*.sh`.
3. Run isolated fake-box regressions: empty, root, relative, symlink escaping shared root, and other unapproved directories must fail **before any copy/remove/chmod/restart**.
4. Obtain fresh read-only `systemctl show afs-watcher.service -p WorkingDirectory --value` and resolved path; confirm a nonempty existing directory beneath the configured shared root, or stop for a separate operator-approved safe-path decision.
5. Verify watcher rollback still restores from the pinned previous release; require a non-production rehearsal and reviewer-approved rollback evidence. No actual service operation is authorized here.

**Fail B10 = HOLD** even if every other CI/dependency/posture check passes. Do not use a manual `chmod`, bypass path guards, or deploy old `7c93027` to avoid this gate.

### AFS-0166 — canonical watcher directory and reviewed rollback break-glass

**Source:** PR #1194 (unmerged), current proposed fix in its own source PR. The
reviewer found that the early watcher check resolves the configured
`$SHARED` with `realpath`, while the late check previously compared the
resolved destination against an unnormalized literal. A symlinked shared
directory or trailing slash can therefore produce a *late failure after*
release .env edits, symlink replacement and futures restart.

**Required gate before any release approval:**

- The same pre-mutation canonical watcher destination and canonical shared
  root must be reused by the late re-arm path in **both** promote and rollback;
  do not reread the unit or reinterpret raw `$SHARED` after mutations.
- Render nested SSH Bash and run fake-box canaries for a valid shared-root path,
  a symlinked shared root, a symlinked destination, trailing-slash variants,
  blank/root/relative/unapproved paths, and symlink escape. Invalid preflight
  must prevent **all** release mutations, valid path variants must not spuriously
  abort re-arm. Keep explicitly named chmod targets, not glob expansion.
- Require fresh exact-head CI, independently attributed reviewer PASS, verified
  effective watcher unit/path from root read-only evidence, and a separate
  operator GO. PR comments entered via the ChatGPT Codex app are *not* by
  themselves evidence that Grok performed independent review.

**Rollback break-glass (DOCUMENTED CONTINGENCY, NOT AN AUTOMATIC BYPASS):**

If watcher directory validation fails, the standard rollback must refuse
rather than write outside its proven scope. Do **not** disable the guard,
set an invented directory, recursively chmod root, repoint the collector,
or force an untested script to execute.

1. Declare **ROLLBACK BLOCKED**, stop further release operations and capture
   immutable read-only snapshots: current/previous release symlinks, .env
   proof pins (secret values redacted), systemd effective paths, manifests,
   rollback target integrity, service status, watcher state, journal hashes
   and broker/demo posture.
2. The operator and a separately assigned recovery reviewer must decide on
   an **explicit, narrowly scoped recovery plan** (including recovery of
   futures service and watcher independently), identify exact target release,
   paths, ordered mutations, rollback-of-recovery and expected interruption;
   preserve evidence and options isolation.
3. Review a **non-production rehearsal** of that exact plan, including invalid
   watcher directory and root-file mutation canaries. Verify backups/recovery
   inputs exist before granting separate operator GO for any live repair.
4. Execute only under that distinct GO and supervision, with after-close/flat
   constraints and post-recovery integrity/service/watchdog/evidence checks.
   Do not silently call normal rollback, edit systemd/env, or promote in this
   document's name.

If independent review, system recovery evidence or operator GO is absent,
the verdict remains **HOLD; manual intervention required**. This plan
does not authorize a production break-glass operation.

### B1/B5/B6/B8/B9 — still unresolved

- **B1:** Read actual six posture pins and release-script promote policy. No automatic reset.
- **B5:** Compare deployed effective venv with the exact candidate lock and confirm Python 3.13. List/approve every material delta.
- **B6:** Verify effective `CONTRACT_IDENTITY_GUARD_ENFORCED` and its proof pin. Keep OFF until separately reviewed Pine/rollover proof.
- **B8:** See "B8 classification" below: the collector was historically **pinned** by drop-in to `db9bc7e2` (2026-09-22), so B8 is first a root read-only classification, not a repin. Futures promote changes options collector code, dependencies and journal behavior only if the collector is effectively on the live symlink. Prefer a reviewed independent pin/partition boundary; do not repin automatically. For #1186's v0.2 journal rollback, preserved old rows and separate new partitions require distinct operator-approved procedures and proof of the pinned old collector's actual startup.
- **B9:** Fresh broker flatness, no working orders, no deploy lock, after-close window, and no in-flight lane actions. Restarts can lose webhook intake for at least several seconds; do not assume zero missed signals.

**Evidence windows:** Preserve old journal bytes and report exact code/collector/source boundaries; do not merge cohorts with changed producer schemas or retroactively label non-catches. Whether to continue or reset each active epoch is an explicit operator decision supported by a recorded exact-SHA cutover.


### Active gates added by Grok review AFS-0165 (comment 6058712793)

Evidence class key: **BOX** = box observation with timestamp; **OPR** = operator report; **SRC** = source inference from the repo; **UNK** = unknown. Only BOX evidence from an authorized root read-only run (`FUTURES_LANE.md` §5/§3) closes a runtime gate. Builder source analysis never substitutes for it.

| Gate | Class today | Requirement |
|---|---|---|
| Release-host Python | SRC / UNK | The operator host runs `python3 -m ops.dependency_lock check-python`, `ops.release_manifest` (imports `yaml`) and `ops.release_ci_proof`. Prove **Python 3.13 and PyYAML** on the release host, and `python3.13` on the box (B4), before build. |
| CI-proof fetch | OPR | A prior GitHub **403 rate-limit** failure on the CI-proof fetch was reported. Verify access at build and again at promote; do not assume success. A 403 or any fetch failure = STOP; no bypass, no hand-written proof. |
| Rollback chain | SRC / OPR / UNK | Read `current.previous` and the append-only `release_history.txt`. Repo history (`docs/4hr-natural-1m-observation-epoch-2026-10-01.md`) names **`489b55b`** as `current.previous` while `c44d32b` is live, so **before the first promote the rollback destination is `489b55b`, not `c44d32b`**. After a successful promote it becomes `c44d32b`. Both release dirs, manifests and venvs must be proven. |
| B2 deployed identity / fingerprint pin | **UNVERIFIED** (not FAIL) | The restricted audit account cannot read `.env`, and its integrity check does not load the pin, so its "UNPINNED" is not a failed pin. Root read: pin present and equal to the manifest fingerprint; integrity run with the pin injected. **No `.env` write or pin "repair".** |
| B7 broker / working orders | Stale | The cited preflight (~03:17Z) was ~2h40m old at review. A **fresh** position and working-order query is required at GO. |
| B1 six posture pins, B5 deployed venv freeze, B6 effective guard, collector installed unit | **UNVERIFIED** | Pending root read-only commands in `FUTURES_LANE.md` §5/§3. Public status pages and restricted-account output are supporting evidence only. |
| MES/MNQ refusal observability | SRC | Zero `CONTRACT_*` refusals after promote may simply mean no MES/MNQ signals arrived. Post-promote acceptance must report the signal count beside the refusal count; zero signals = **INCONCLUSIVE**, not PASS. |
| Release-wrapper 51/51 | OPR | Operator-reported on 2026-10-08, not independently reproduced. Covers only unchanged paths; #1189/#1194 paths need their own exact-head tests. |
| Installed drift gate | OPR / UNK | `/root/bin/afs-drift-gate.sh` was replaced by the #1129 fix on 2026-10-04 (OPR). The last recorded hash in the repo (`cc4d9f5a…`, 2026-09-18) predates that fix and is stale. Root read: installed sha256 vs `scripts/afs-server-drift-gate.sh` at the commit it was installed from. |
| B10 watcher (#1194) | SRC; not Grok-reviewed | Prove pre-mutation checks on #1194's exact head for promote **and** rollback; independent Grok review; no preapproval or merge implied. Also prove: the pre-flight and post-activation path checks agree when the shared root itself resolves through a symlink, and a rejection leaves **no partial edit** (`.env`, symlink, drop-in, service state). |

**Evidence independence and access.** Cursor's B1–B8 matrices (comments 6051491065, 6051610034) used the restricted **Grok audit** identity. Those observations are not independent Grok verification; Grok compares them with its own captures. That credential is not to be reused for builder workflows. A separate least-privilege read-only builder identity is its own operator access decision. This Claude session has no VPS access and used none. No credential is posted, injected, rotated or revoked through this PR.

### B8 classification (corrects the base-unit assumption)

**Historical observations (OPR, from `docs/options-current-state-handoff.md`):**

- 2026-09-22: drop-in `options-122-prospective.service.d/10-release.conf` pinned the collector to **`db9bc7e2c00559fc969af7be0e2cb12b00a1454c`**;
- a temporary `zz-rollback-protect-20260922.conf` drop-in was also present;
- 2026-09-23: natural collection on `db9bc7e2` was reported successful.

These do not prove today's effective unit.

**Root read-only classification required:**

- `systemctl cat options-122-prospective.service` and every drop-in;
- effective `ExecStart`, `WorkingDirectory`, `PYTHONPATH`, venv and timer;
- installed journal path and last-row collector version;
- that the `db9bc7e2` release directory and venv still exist and survive release pruning.

| Result | Meaning |
|---|---|
| **PINNED** (all effective paths under `/root/afs-releases/db9bc7e2…`) | B8 = **verify and preserve the existing pin** across the futures promote. **No automatic repin.** |
| **LIVE-TREE** (effective paths follow `/root/autonomous-futures-system`) | **HOLD** for a separate reviewed migration or pin with its own GO. |
| **MIXED** (any path disagreement, or `zz-rollback-protect` changes the effective result) | **HOLD** for a separate reviewed migration or pin with its own GO. |

Before **any** v0.2 repin (#1186), scan the installed journal for duplicate `ARMED` rows per `setup_id`. No change is authorized by this plan.

### Required next action

1. Obtain Grok exact-head review and fresh CI for #1194 and #1186; **do not merge as part of this plan**.
2. Once #1194 is separately merged with operator approval, nominate the **new** exact deployment candidate, compare deployed → candidate end-to-end, and refresh all identities and risk-path checks below. The nominated SHA then needs its own **independent Grok exact-SHA review**. Candidate stays UNSET until then.
3. Obtain read-only root-level box proof for B1/B5/B6/B8/B9/B10 and rollback readiness.
4. Return a **GO FOR OPERATOR DECISION** or **HOLD** gate table with timestamps, artifacts and all decisions. Never build, promote, restart or submit orders from this document.

## Refreshed plan detail (2026-10-08; under the decision record above)

Responds to review comments 6051298646 and 6051370612 on #1190 (posted via the ChatGPT/Codex account — **not Grok**) and to Grok review AFS-0165 (comment 6058712793). Where this section and the decision record differ, the decision record governs. Nothing here is approved for execution.

### Delta `7c93027` → `064ee67` (verified)

Re-reviewed so the trading-path review in the historical plan carries forward. It must be redone for the actual nominated SHA.

- **#1152** `options_evidence/fitness.py` (new) and **#1183** `options_evidence/signal.py`: no module outside `tests/`, `options_evidence/` and `docs/` imports `options_evidence`. No live service runs this code.
- **#1188**: docs only.
- **#1189** `scripts/atomic_release.sh`: escapes inner quotes in the remote **build** block only. This is release tooling.
- `git diff --stat 7c93027 064ee67` lists 12 files, none under `strategy/`, `risk/`, `execution/`, `webhook/`, `context/`, `journal/`, `alert_ranker/` or `ops/afs_watcher/`.

Every commit merged after `064ee67` before nomination needs the same review. Any runtime-path change re-opens the trading-path review.

### B10 detail — defect at `064ee67` (and `7c93027`)

**The defect.** In both `promote` and `rollback`, the watcher block sits inside the outer `remote "…"` double-quoted string, and its inner quotes are unescaped. A local render of that block shows the remote shell receives:

```
test -n $watcher_dest
test -d $watcher_dest
cp -f $watcher_src/$watcher_file $watcher_dest/$watcher_file
chmod 700 $watcher_dest/*.sh
```

With an empty `WorkingDirectory`:

- `test -n` and `test -d` with no operand both succeed;
- the copies land at `/watcher.py`, `/run_ro.sh` and so on;
- `chmod 700 /*.sh` runs at the filesystem root.

A value of `/` passes as well.

**Ordering.** The block runs only **after** the `.env` pin edit, the `current` symlink swap and the `futures-bot` restart. So a refused value still leaves a partially activated release, and the same block in rollback can stop a clean rollback. That is the finding recorded on #1194 head `547738c06781ae0fe74307c8c71ed9b6953d501e` (comment 6051361758, not a Grok review). Grok has not independently reviewed #1194.

**Additional B10 requirements:**

1. **Pre-mutation validation in both promote and rollback**, before the `.env` edit, symlink swap or any restart:
   - `WorkingDirectory` is non-empty, absolute and not `/`;
   - it resolves (`readlink -f`) inside the approved shared tree;
   - it is an existing directory;
   - every watcher source file is present in the release being activated.

   Any failure exits non-zero with **no** file, env, symlink or service change.
2. **Fake-box regression on the rendered remote command** for blank, `/`, relative, missing, symlink-escape and unapproved values. It must prove:
   - no `.env` edit;
   - no symlink change;
   - no `systemctl restart`;
   - no write or chmod outside a scratch root.
3. **No wildcard mutation:** name the `.sh` files explicitly rather than relying on a quoted glob.
4. **Box reads:** before promote, record the `afs-watcher` `WorkingDirectory`, its resolved path, its file hashes, and a listing of `/`. After promote or rollback, confirm there are no new files at `/`.

### B8 detail — collector boundaries (source on `main`; box state UNVERIFIED)

**Base unit in the repo (not the effective box unit).** The historical box state is a drop-in pin; see "B8 classification" in the active section. `ops/systemd/options-122-prospective.service` alone sets:

- `WorkingDirectory=/root/autonomous-futures-system` — the live symlink;
- `EnvironmentFile=/root/afs-shared/.env`;
- `ExecStart=/root/autonomous-futures-system/.venv/bin/python -m scripts.options_122_prospective_collect`, writing to:
  - journal `/root/afs-shared/logs/options_122_prospective.jsonl`;
  - raw trades `/root/afs-shared/logs/options_122_source_trades`.

It is driven by `options-122-prospective.timer`. Code, venv and imports therefore follow the futures symlink. The `afs-paper-collection-{eod,eow}` units also run from the live symlink.

**Pin mechanism.** The #1147 template `ops/systemd/options-122-prospective.service.d/10-release.conf.template` rewrites `WorkingDirectory`, `PYTHONPATH` and `ExecStart` to `/root/afs-releases/@RELEASE_SHA@` and its own `.venv`. Journal and raw paths are unchanged. Pinning is its own reviewed procedure with its own GO.

**Required root reads** (see "B8 classification" in the active section; the `FUTURES_LANE.md` §3 classification; that file is referenced by review but is not in this repository, so its location is unverified):

- the effective release identity the collector process loads;
- the unit and any drop-ins;
- the timer state;
- the journal path, size, sha256 and last-row collector version.

**#1186 boundary.** #1186 (not merged) moves the collector from `122-iex-collector-v0.1` to `v0.2`.

- If it merges before nomination, the collector must already be pinned to its pre-switch release.
- The switch to v0.2 is its own migration GO, using a fresh, segregated journal path with the immutable pre-switch snapshot and hash preserved, per `docs/options-122-canonical-producer-binding-2026-10-07.md`.
- **Rollback across that boundary:** restore the v0.1 pin and the v0.1 journal path. Never point v0.1 at a journal containing v0.2 rows.

**Classification.**

| Condition | Outcome |
|---|---|
| Isolation verified (pinned, effective identity read back, journal untouched) | Futures promote may proceed with respect to B8. |
| Isolation cannot be verified | **HOLD.** |

**Post-promote check.** The collector's code, venv, journal path and journal schema equal the pre-promote reading. Any change is an abort condition.

### UNVERIFIED pending root read-only box evidence

These stay unverified until Ops runs the `FUTURES_LANE.md` §5 commands:

- B1 posture pins;
- B5 exact lock vs live venv freeze, and box `python3.13`;
- B6 effective `CONTRACT_IDENTITY_GUARD_ENFORCED` and its pin;
- epoch boundaries and pins;
- rollback inputs (`current.previous`, the `c44d32b` release, manifest, integrity, watcher files);
- watcher recovery;
- the companion collector classification (also the §3 root check).

Source analysis does not satisfy any of them.

### Operator checklist (each step its own GO; none preapproved)

| Step | Action | Gate |
|---|---|---|
| 1 | Merge this plan PR (#1190, docs only) | Exact-head CI green + Grok PASS + operator merge decision |
| 2 | Merge the B10 fix (#1194 or successor) | Exact-head CI green + Grok PASS + operator merge decision |
| 3 | Nominate the candidate: exact `main` SHA, plus delta review after `064ee67` | Operator, in writing; then **independent Grok exact-SHA review** of that SHA |
| 4 | Root read-only box evidence: B1–B10, B8 reads, epoch readings | Ops, read-only; any gap means HOLD |
| 5 | Policy decisions: B1 posture, B5 dependency changes, B6 pin, epoch treatment | Operator GO each |
| 6 | Collector pin, and separately any v0.2 journal migration | Its own reviewed procedure + operator GO |
| 7 | `build` then `verify` of the nominated SHA | Operator GO; no service change |
| 8 | Record preregistration boundary and pre-promote readings | Before step 9 |
| 9 | `promote` | Operator GO naming the SHA |
| 10 | Post-promote acceptance; on any abort condition, `rollback` | Rollback is authorized only as the abort response inside an approved step 9 |
| 11 | Record the outcome with box evidence in `docs/futures-current-state-handoff.md` and `docs/agent-work-state.md` | — |

---

## Historical plan: `c44d32b → 7c93027` (superseded; provenance only)

## Status

**PLAN ONLY / HOLD.** This document authorizes no build, verify, promote, restart, env edit, unit install, TradingView change or broker action. Execution needs (1) review of this plan, (2) every blocking item below resolved, and (3) explicit operator GO naming the exact SHA.

| Item | Value |
|---|---|
| Deployed (per 2026-10-08 VPS audit, operator-supplied; **reconfirm on the box before any action**) | `c44d32bc4961e56fae5c5f88a976eb6783341638` |
| Candidate at the time (**HISTORICAL — withdrawn; not a live target**) | `7c930274179f7c76749f75b35adb14fbb9255e54` (#1180) |
| Delta | 64 first-parent merges, 227 files, +60,933 / −500 |
| `main` at writing | `58606b4` (#1152). (Historical note; superseded by the active decision record. The live target is the future nominated SHA.) |
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

1. `ops.release_ci_proof verify-live` passes for `<NOMINATED_SHA>` at build time **and again at promote** (historical draft named `7c93027`; withdrawn).
2. Box `python3.13` passes the check.
3. `pip install --no-deps -r requirements.lock`, `pip check` and `check-freeze` all exit 0.
4. Release integrity is OK with the built fingerprint, and the completion marker matches it.
5. `atomic_release.sh verify <NOMINATED_SHA>` passes.

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
| Identity | Symlink, cwd and `EXPECTED_LIVE_COMMIT` = `<NOMINATED_SHA>`. Integrity OK with the pin. Zero `__pycache__` in the release. |
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
2. Fetch the CI proof for `<NOMINATED_SHA>`. Run `build`, then `verify`. No service change.
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
- any change outside the nominated SHA's reviewed delta, and any unrelated or unreviewed change;
- strategy, risk, instrument or contract-size changes;
- TradingView alert changes;
- epoch resets;
- cleanup.
