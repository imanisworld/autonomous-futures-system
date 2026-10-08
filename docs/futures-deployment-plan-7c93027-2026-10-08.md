# Futures deployment plan — refreshed 2026-10-08; candidate NOT YET SELECTED

**PLAN ONLY / HOLD.** Docs-only. No build, verify, promote, restart, env edit, unit install, journal reset, broker action, or evidence-window transition is authorized by this document.

This revision responds to **Grok exact-head review AFS-0165** (PR comment [6058712793](https://github.com/imanisworld/autonomous-futures-system/pull/1190#issuecomment-6058712793)) on head `c370ef5ee63e274f4d57b8896b453eca990d0614`. Historic material below remains as **provenance only**, not as a live deployment plan.

## Active decision record (governs all older sections)

| Item | Verified or required state |
|---|---|
| Previously deployed futures release | `c44d32bc4961e56fae5c5f88a976eb6783341638` — **operator-reported** and **restricted-account-observed**; reconfirm with authorized **root** read-only before any GO |
| Pre-first-promote rollback destination (historical) | `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` — must be proven via fresh `current.previous` + release-dir integrity, not inferred from directory listings |
| Post-#1189 `main` | `064ee674b788c144fc8d3ca65082ed060927e2f1` (#1189 merged; Grok PASS applies only to that reviewed head `f60ec6f`, not transferred) |
| Historical candidate `7c93027` | `7c930274179f7c76749f75b35adb14fbb9255e54` — **HISTORICAL ONLY**. Do not build, verify, or promote it under this plan |
| **Next candidate** | **UNSET** until #1194 is merged with exact-head CI + **independent Grok PASS**, then a **new** exact `main` SHA is nominated and independently Grok-reviewed |
| Plan status | **CHANGES REQUIRED / HOLD** pending AFS-0165 gates, root read-only proofs, and separate operator GOs |
| Evidence access note | Restricted audit-allowlist observations are **not** independent Grok verification. Builder must not reuse that credential for new workflows. Root/`FUTURES_LANE.md` §5/§3 reads remain required where stated |

### Reviewer attribution (corrected)

| Comment / finding | Actual poster | Not |
|---|---|---|
| #1190 comments `6051298646` and `6051370612` | ChatGPT/Codex account (operator-relayed) | **Not Grok** |
| #1194 watcher WorkingDirectory / pre-mutation findings relayed via those comments | Same — **not** an independent Grok review of #1194 | Do not infer Grok PASS on #1194 |
| Grok AFS-0165 on this plan (`c370ef5`) | Grok (operator-provided review + ledger) | Requires this docs revision + new exact-head CI before Grok re-review |

### Evidence classification legend

Use these labels consistently. Source analysis and restricted-account reads **cannot** substitute for required root proofs.

| Label | Meaning |
|---|---|
| **BOX (root)** | Authorized root / `FUTURES_LANE.md` §5/§3 read-only command output from this session |
| **BOX (restricted)** | Restricted audit allowlist or similar least-privilege surface; useful but not independent Grok QA |
| **PUBLIC STATUS** | Unauthenticated public status endpoints (e.g. `/status/live-preflight`) — process-env view, not `.env` file proof |
| **OPERATOR REPORT** | Operator-supplied handoff; not independently reproduced here |
| **SOURCE** | Repository / git analysis only |
| **UNKNOWN** | Not proven |

### Governing gates (active — not appendix-only)

These items are first-class blockers for any future nomination/build/promote. Distinctions below matter.

#### Host / release tooling

| Gate | Status | Classification | Required proof |
|---|---|---|---|
| Release-host **Python 3.13** | UNVERIFIED | UNKNOWN until BOX (root) | `python3.13` version on the release host; build refuses otherwise |
| **PyYAML** (and other lock material) on host/venv | UNVERIFIED | UNKNOWN until BOX (root) | Deployed venv freeze vs **deployed** lock; candidate lock reviewed separately after nomination |
| GitHub CI-proof fetch | UNVERIFIED | Prior **OPERATOR REPORT** noted a **403 rate-limit** failure mode | Prove current release-host GitHub check-runs API access without assuming success; fail closed on 403 |
| Installed `afs-drift-gate.sh` identity | UNVERIFIED | UNKNOWN until BOX (root) | Hash/identity of `/root/bin/afs-drift-gate.sh` (or configured path) vs reviewed source; do not assume #1129 install state |
| Wrapper tests “51/51” | UNVERIFIED for independence | **OPERATOR REPORT** only | Not independently reproduced in this plan; re-prove modified release code on the **nominated** exact SHA |

#### Rollback / history

| Gate | Status | Classification | Required proof |
|---|---|---|---|
| `current.previous` | UNVERIFIED | UNKNOWN until BOX (root) | Exact file contents |
| Append-only release history | UNVERIFIED | UNKNOWN until BOX (root) | `release_history.txt` (or equivalent) readable and consistent |
| Pre-first-promote rollback destination `489b55b…` | UNVERIFIED | Directory **name** seen via restricted `deploy-state` ≠ validated target | Prior release dir exists; manifest parses; integrity OK with **that** release’s fingerprint injected; venv usable; watcher files present |
| Rollback under U11 tooling | UNVERIFIED | SOURCE mechanics only | No live rollback drill authorized here |

#### B1–B10 (runtime)

| # | Gate | Status | Notes |
|---|---|---|---|
| B1 | Six posture pins + promote Path-1/Path-2 policy | **UNVERIFIED** (`.env` file) | PUBLIC STATUS / restricted reads may show process-env Path-1 values; promote gate greps `$SHARED/.env`. No automatic reset. Optional root `grep -qx` of the six lines |
| B2 | Deployed identity + `EXPECTED_RELEASE_FINGERPRINT` | **UNVERIFIED** | Restricted `release` “UNPINNED” is **not** a proven pin FAIL (`ops.release_integrity` reads process env only; same class as drift-gate without env injection). Tree match ≠ pin proof. **No `.env` write / pin “repair”** until bare-vs-injected root proof |
| B3 | Rollback target validity | **UNVERIFIED** | See rollback table above |
| B4 | Box Python 3.13 | **UNVERIFIED** | See host table |
| B5 | Live venv vs lock drift | **UNVERIFIED** | Git lock expansion `c44d32b→main` is **SOURCE informational**, not live drift. Compare deployed freeze to **deployed** lock; after nomination, repeat vs **candidate** lock |
| B6 | `CONTRACT_IDENTITY_GUARD_ENFORCED` + proof pin | **UNVERIFIED** on `.env` | Deployed `c44d32b` SOURCE lacks U8 enforcement path. Keep OFF on any post-U8 candidate. No enablement |
| B7 | DEMO posture / flat / orders | Partially observed; **fresh query required at GO** | DEMO / live false / cap 1 observed via restricted + PUBLIC STATUS. “No working orders” must be **re-queried at GO** — stale preflight (hours old) is insufficient |
| B8 | Options collector isolation | **UNVERIFIED** effective unit; see B8 section | Prefer preserve-if-pinned; no automatic repin |
| B9 | Lock / timing / in-flight | **UNVERIFIED** at GO | Fresh flatness, no `deploy.lock`, after-close window |
| B10 | Watcher WorkingDirectory safety (#1194) | **UNVERIFIED** / source in progress | Exact-head CI + **independent Grok** review required; no preapproval |

#### MES/MNQ refusal observability

U7/U8 add new refusal paths. After any future promote of a candidate that includes them: watch for `CONTRACT_METADATA_UNSUPPORTED`, `CONTRACT_IDENTITY_UNRESOLVED`, and related ERROR lines. **Absence of signals is allowed** (may be no eligible MES/MNQ orders); do not treat silence as proof of correctness without broker-reachable context.

### B10 — Watcher WorkingDirectory and chmod safety

**Defect class (SOURCE):** In promote/rollback watcher re-arm, unsafe WorkingDirectory handling can expand mutations to filesystem root and/or run after partial activation (`.env` edit, symlink swap, `futures-bot` restart). ChatGPT/Codex comments on #1190/#1194 described this; **Grok has not independently PASSed #1194**.

**Source fix lane:** [draft PR #1194](https://github.com/imanisworld/autonomous-futures-system/pull/1194) — separate from this docs plan. Not merged; not approved by this document.

**Required before any build/promote:**

1. #1194 (or successor) merged with green **exact-head** CI and **independent Grok PASS** on that SHA (do not transfer #1189 PASS).
2. Rendered remote Bash for **promote and rollback** proves pre-mutation validation; quoted paths; **named** `.sh` files (no glob); rejection leaves **zero** `.env`/symlink/copy/chmod/restart changes.
3. Fake-box coverage includes blank, `/`, relative, missing, symlink-escape, and unapproved paths; also when the configured **shared root itself resolves through a symlink**.
4. Fresh BOX (root) `systemctl show afs-watcher.service -p WorkingDirectory` + realpath under trusted shared root.
5. Watcher rollback restores from pinned previous release; rehearsal evidence is a separate operator decision.

**Fail B10 = HOLD.** Do not bypass with manual `chmod` or by deploying any historical SHA.

### B8 — Options collector (corrected)

**Base-unit assumption corrected using repo historical handoff (not today’s proof):**

`docs/options-current-state-handoff.md` records that on **2026-09-22** the installed drop-in `options-122-prospective.service.d/10-release.conf` pinned release `db9bc7e2c00559fc969af7be0e2cb12b00a1454c`, with temporary `zz-rollback-protect-20260922.conf` present, and that **2026-09-23** natural collection was **operator-reported** successful. Those are **historical observations**, not proof of today’s effective unit.

**SOURCE template / base unit (may differ from installed drop-ins):**

- Unit file WorkingDirectory `/root/autonomous-futures-system` + that tree’s `.venv` if **unpinned**.
- Pin template: `ops/systemd/options-122-prospective.service.d/10-release.conf.template` → `/root/afs-releases/@RELEASE_SHA@`.

**Required BOX (root) classification before futures promote:**

| Class | Meaning | Futures promote implication |
|---|---|---|
| **PINNED** | Effective ExecStart/WorkingDirectory/PYTHONPATH/venv point at an immutable release (e.g. historically `db9bc7e2…`) | **Verify and preserve** the existing pin. Do **not** automatically repin |
| **LIVE-TREE** | Effective paths follow the futures live symlink | **HOLD** for separate reviewed migration/GO |
| **MIXED** | Drop-ins/unit disagree or partially override | **HOLD** |

Collect: `systemctl cat` / effective ExecStart, WorkingDirectory, PYTHONPATH, venv, timer, journal path & collector version; prove release/venv `db9bc7e2…` (or current pin) survives pruning if still referenced. Scan journal for duplicate `ARMED` before any v0.2 work. **#1186** v0.1→v0.2 remains a separate migration GO with segregated journal rules. **No collector changes authorized here.**

### UNVERIFIED pending authorized root read-only evidence

Until Ops runs authorized **root** read-only commands (`FUTURES_LANE.md` §5/§3 where that private runbook applies; otherwise equivalent root reads):

- B1 six `.env` pins (if process-env evidence is insufficient for promote-gate confidence);
- B2 fingerprint pin presence + bare vs injected integrity;
- B3 `current.previous` + prior release integrity/venv/watcher;
- B4/B5 Python 3.13 + deployed freeze vs deployed lock;
- B6 env + proof pin (especially for post-U8 candidates);
- B8 PINNED/LIVE-TREE/MIXED classification;
- drift-gate script hash;
- GitHub CI-proof API reachability from the release host.

Builder SOURCE / restricted / PUBLIC STATUS analysis does **not** close these.

### Operator checklist (each step its own GO; none preapproved)

| Step | Action | Gate |
|---|---|---|
| 1 | Merge this plan PR (#1190, docs only) | Exact-head CI + handoff PASS + **Grok re-review on the new docs SHA** + operator merge decision |
| 2 | Merge B10 fix (#1194 or successor) | Exact-head CI + **independent Grok PASS** + operator merge |
| 3 | Nominate candidate: exact `main` SHA after step 2 | Operator in writing; **independent Grok exact-SHA review of that candidate**; delta review from deployed → candidate; exclude unrelated unreviewed changes |
| 4 | Root read-only box evidence (B1–B10, B8 class, epochs, rollback, drift-gate hash, CI-proof access) | Ops read-only; any gap = HOLD |
| 5 | Policy decisions: B1 posture, B5 deps, B6 pin, epoch treatment | Operator GO each |
| 6 | Collector: **preserve pin** if PINNED; else separate migration GO | Reviewed procedure + operator GO; no silent ride-along |
| 7 | `build` then `verify` of the **nominated** SHA only | Operator GO; no service change |
| 8 | Record preregistration boundary and pre-promote readings | Before step 9 |
| 9 | `promote` | Operator GO naming the SHA |
| 10 | Post-promote acceptance; abort → `rollback` | Rollback only as abort response inside approved step 9 |
| 11 | Record outcome with BOX evidence in handoff/work-state | — |

### Required next action (docs lane)

1. Land this AFS-0165 revision; obtain exact-head CI + handoff PASS.
2. **Grok re-review on the new docs SHA** (this PR only).
3. Keep candidate **UNSET**. Do not merge #1194 from this plan.
4. No deploy, restart, journal reset, broker action, pin write, or evidence-window transition.

### Do not bundle (active)

Leave out of any future futures release under this plan:

- enabling `CONTRACT_IDENTITY_GUARD_ENFORCED`;
- installing `options-setup-capture` or any timer/unit;
- systemd unit edits (#1101) as ride-alongs;
- `push_relay` redeploy;
- building/promoting historical `7c93027` or any other non-nominated SHA;
- unrelated, unreviewed commits outside the nominated exact SHA;
- strategy, risk, instrument or contract-size changes;
- TradingView alert changes;
- epoch resets;
- cleanup;
- automatic collector repin.

---

## Historical plan: `c44d32b → 7c93027` (superseded; provenance only)

> **PROVENANCE ONLY.** Everything in this historical section describes a **withdrawn** candidate. Commands naming `7c93027` are **not** live instructions. Do not build, verify, or promote `7c93027` under the active decision record above.

## Status (historical)

**PLAN ONLY / HOLD (historical).** This section does not authorize execution.

| Item | Value |
|---|---|
| Deployed (per 2026-10-08 VPS audit, **OPERATOR REPORT**; reconfirm on box) | `c44d32bc4961e56fae5c5f88a976eb6783341638` |
| Historical candidate (withdrawn) | `7c930274179f7c76749f75b35adb14fbb9255e54` (#1180) |
| Delta | 64 first-parent merges, 227 files, +60,933 / −500 |
| `main` at that writing | `58606b4` (#1152) — historical context only |
| Rollback target at that writing | `c44d32b` (would become `current.previous` at promote) |

## Reused, not redone (historical notes — classify carefully)

These were **accepted as already claimed** in earlier handoffs. This plan does not treat them as fresh BOX (root) proof.

- **U3→U11 source campaign** (#1158–#1166, plus #1174/#1175/#1180): exact-head QA and green required CI before each merge (`docs/agent-work-state.md`) — **SOURCE / merge record**.
- **`c44d32b` deploy proof** (2026-10-04, `docs/futures-current-state-handoff.md`): integrity, posture, 4HR natural-1m epoch start `2026-10-04T21:30:05Z` — **OPERATOR REPORT / dated handoff**.
- **#1129 drift-gate fix**: claimed installed at `/root/bin/afs-drift-gate.sh` on 2026-10-04 — **OPERATOR REPORT**; active plan requires fresh hash/identity check.
- **2026-10-08 VPS audit (operator handoff; not in repo)** — **OPERATOR REPORT**:
  - #1180 merged with CI green;
  - `afs-deploy` wrapper installed, **51/51 tests (operator-reported, not independently reproduced here)**;
  - real GitHub CI-proof fetch and live verification claimed (also note prior **403 rate-limit** failure mode — do not assume access);
  - Tradovate demo, live disabled, one-contract cap;
  - zero broker positions and zero working orders **at that time**;
  - no deploy or restart in that audit.
- **Release mechanics**: `scripts/atomic_release.sh` build → verify → promote → rollback — **SOURCE**.
- **Box checks**: `/futures-deployment-safety-audit` remains the checklist language for future steps 2/6; do not invent a parallel procedure.

## 1. Trading-path changes in the historical delta (review + preregistration)

Unchanged in `c44d32b..7c93027` (verified by `git diff --stat` at the time):

- `strategy/`, `risk/`, `risk_rules.yaml` — so `risk_rules_sha256` was unchanged;
- `journal/`, `replay/`, `config/futures_contracts.py`;
- every `context/` module except `wide_stop_demo_runtime_core.py`.

### Changes that reach the futures-bot process or its order path (historical table)

| PR | Files | Runtime effect |
|---|---|---|
| U7 #1162 | `execution/tradovate_broker.py`, `execution/no_fill_taxonomy.py`, `context/wide_stop_demo_runtime_core.py` | Tick metadata from `config/futures_contracts.py`; unknown root refused before auth; unresolvable position contract → unconfirmed snapshot |
| U8 #1163 | `execution/tradovate_broker.py`, `execution/contract_identity.py`, `ops/live_box_guard.py` | Routed symbol must equal dated front month else `CONTRACT_IDENTITY_UNRESOLVED`; alert-vs-routed observe-only unless `CONTRACT_IDENTITY_GUARD_ENFORCED` |
| #1137 | `webhook/runner.py`, `adaptive/post_cap_eligibility.py` | Observation-only post-cap eligibility re-check |
| #1143 | `webhook/app.py`, `webhook/runner.py`, `notifications/futures_advisory.py` | Advisory cards; fail-soft |
| U11 #1166/#1175/#1180 | `scripts/atomic_release.sh`, `requirements.lock`, … | Python 3.13 venv, `--no-deps` lock install, freeze equality |
| #1107 | `ops/afs_watcher/watcher.py` | Watcher label-only change on promote copy |

### Changes that ride along to other services (historical)

| Item | Effect |
|---|---|
| Options code (#1145–#1151, #1153) | If collector is **LIVE-TREE**, promote changes its code. Active B8 requires PINNED/LIVE-TREE/MIXED classification first. |
| Not applied by promote | `deploy/systemd/*`, `options-setup-capture.*`, `ops/push_relay/app.py` |

### Preregistration (historical proposal — still needs operator decision for any future SHA)

- Code boundary at promote for active futures evidence lanes.
- Proposed treatment was continue epochs, no reset — **operator decision**, not approved by this docs revision.
- Logging: new U7/U8 refusals are execution refusals, not strategy outcomes.

## 2. Blocking items (historical table; statuses superseded by active record)

The following table is **provenance**. Live statuses are in the active decision record (B2 = **UNVERIFIED**, not integrity-pin FAIL; B7 orders require fresh GO-time query; B8 uses pin-classification).

| # | Item | Why it blocked historically | Proof required |
|---|---|---|---|
| B1 | Promotion posture gate | Path-1 baseline or Path-2 + behavior-neutral | Six pins |
| B2 | Deployed identity | Stale audit risk | Symlink, cwd, commit pin, fingerprint handling, `current.previous` |
| B3 | Rollback target | Need prior release usable | Prior release integrity/venv |
| B4 | Box Python 3.13 | U11 build gate | Host python 3.13 |
| B5 | Dependency drift | Lock vs live venv | Freeze vs lock |
| B6 | Contract-identity env | Enforcement risk | Env + proof pin OFF |
| B7 | Demo order lane | Lane posture | Wide-stop pins; **fresh** flat/orders at GO |
| B8 | Options ride-along | Collector coupling | PINNED preserve vs migration GO |
| B9 | Flat broker, no lock, timing | Standard | Fresh readings |

## 3–5. Historical acceptance / rollback / execution order

> **Not live.** Replace every historical `7c93027` command with the **nominated** exact SHA only after active steps 1–3. Example historical forms (do not run):
>
> - `ops.release_ci_proof verify-live` for `<NOMINATED_SHA>`
> - `atomic_release.sh verify <NOMINATED_SHA>`
> - post-promote identity = `<NOMINATED_SHA>`
>
> Rollback command shape remains `scripts/atomic_release.sh rollback` (no ref), restoring whatever `current.previous` records at that time — prove inputs first (active B3).

Historical post-promote thresholds, abort lists, and execution order paragraphs that named `7c93027` are withdrawn as live procedure. Use the **Operator checklist** in the active decision record instead.
