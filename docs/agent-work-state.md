# Agent Work State

## Current coordination checkpoint — 2026-10-08

- **Repository:** GitHub source baseline at this handoff: `a74c6f8f93e744e93627c09be06a77b0e7e974f7` (#1202), including source-only #1198 adapter plus previously merged watcher/history guards. **Source SHA is not VPS release identity.**
- **Futures:** **Operator-supplied Claude box audit (not independently repeated here):** B1–B10 passed, deployed release unchanged at `c44d32bc`, demo account flat, no deploy lock, rollback pointer `489b55b`. No further general VPS box checks queued. Outstanding *decisions*, not claimed runtime failures: package downgrades in current lock, memory pressure (options-scanner reportedly 457 MB swapped), allow the existing `daily_22_5k` paper LONG to resolve, nominate exact candidate and obtain independent source/trading-path review. Build/promote authorization is separate; candidate remains **UNSET**.
- **Futures wrapper:** Prior **operator-reported, independently unverified** `afs-deploy` 51/51 fake-box tests and real `verify-live` PASS are historical claims tied to the reported version. Do not call them verified proof or transfer them to changes under #1189/#1194; exact-head testing and reviewer confirmation remain required.
- **Options:** #1186 producer stamp (part 1) and **#1198 read-only E1 adapter (part 2)** both source-MERGED; do not reopen the adapter implementation. #1184 issue **CLOSED**; offline-only #1199 **DRAFT / UNMERGED**, further authority-storage work deferred pending approved use and trusted humans. **Operator-supplied** box audit classified the Webull sandbox-paper adapter as execution-capable **but inactive**, with no running caller and both allow flags default off; shared `.env` lacks five named settings, service-specific env remains unverified. Existing options collector reported **PINNED** to `db9bc7e2`, but new producer/journal v0.2 audit/rollback is not complete. #1154 parked, #1167 audit-only. Options authority: `docs/options-current-state-handoff.md`.
- **Evidence/collector:** Companion-daily job last seen Sep 23; independent report suggests it was retired, not failed evidence collection. Root read-only confirmation still missing. Do not treat stale health census as a proven E1 or futures gap.
- **Next — OPTIONS:** Prioritize current-epoch paper P&L / actual-vs-structural outcomes using existing read-only data and diagnostics; preserve cohorts and do not claim edge. No new general VPS audit or broker path test needed for this accounting step. #1199 authority-security review is deferred unless deliberately resumed.

- **Next — FUTURES:** Decide package downgrades, memory pressure, and paper LONG disposition, then candidate nomination/review **separately**. Historical box proof is operator-supplied and must be freshness-checked at any later release GO, not endlessly rerun during source work. No runtime mutation authorized.

## Prepared, not installed — Cursor Cloud maintenance (2026-10-08 / re-verified 2026-10-09)

- **Branch:** `ops/cursor-cloud-vps-maintenance`, draft PR #1206 at `32799045`. Follow-up to `69c435e` for AFS-0194 NB3: recovery records the base-unit sha256 and refuses, before consuming the approval or reloading, when that hash changes or when DropInPaths changes by anything other than our own memory drop-in. Not installed. `main` stays `bd5ea24`.
- **Tests (re-verified this session):** `.venv/bin/python -m pytest tests/test_afs_maintenance.py -q` — 80 passed. Exact-head GitHub CI on `32799045`: handoff-fields / tests / Analyze (python) / Analyze (actions) / CodeQL = SUCCESS. No VPS write. NB3a and NB3b cover a base ExecStart edit and a new runtime `Environment=` drop-in after the pre-reload crash. Approval lifetime and approval-directory ancestor ownership were not changed. These tests do not prove power-loss durability on the VPS.
- **Runtime:** No VPS read or write in this session. Trading-data reconciliation remains blocked on VPS journals/DB/marks.
- **DONE / DO NOT REDO:** AFS-0191 NB1/NB2 and AFS-0194 NB3 source fixes. Do not re-implement #1206. Do not install from an agent session.
- **#1207 (fixture-only review, 2026-10-09):** draft PR `claude/capped-session-reporting` at `f7722697` (AFS-0193 R1 keep recorded P&L when exit time unknown). Focused report tests: 68 passed. Fixture accounting review: SOURCE OK FOR FIXTURES — sessions/caps/costs/OPEN settle/R1/exact-17:00/options ties/fail-soft pinned. Published what-if totals and live options SQLite parity remain UNVERIFIED without journals/bars. No further speculative reporting fix cycle.
- **#1071:** still OPEN/UNMERGED at `0dca986e`, ~286 commits behind `main`. Auditor already accepts `--db` / `--epoch` for a read-only SQLite path. Do not rebase or invent a second P&L system until an authorized ledger snapshot is available.
- **NEXT:** Grok reviews only the #1206 changed code (not a full audit redo) and the #1207 R1 delta. Stop source churn until VPS journals/DB/option marks are accessible. No installation, daemon-reload, MemoryMax change, SSH change, merge, deploy, or capital increase.

> **Purpose:** compact resume checkpoint for agents. This file is coordination state only; it is **not** strategy-status, experiment, deployment, or runtime authority.
>
> **Authoritative records:** follow the source-of-truth table in `AGENTS.md`.
>
> **Historical checkpoints:** `docs/agent-work-state-archive-through-2026-10-07.md`.
>
> Core rule: **checkpoint first; diff first; do not redo proven work; no proof, no run.**

## Resume path

1. Read `AGENTS.md`, this file, and the authoritative record for the requested lane.
2. Fetch current `main` and the exact active PR/branch for that lane.
3. Inspect only the changed scope first. Expand only when evidence requires it.
4. Runtime/deployment claims require fresh runtime evidence; repository state alone is never proof of what is deployed.
5. Update this compact checkpoint after a meaningful work unit. Put dated historical detail in a lane-specific evidence file or archive, not here.

## Current repository checkpoint — 2026-10-07

- **Source-safety baseline:** `1ec48a0ed76aea23c82882ff8c8ae258fda20c95` — U11 / PR #1166. Fetch current `main` before acting; later docs/guidance-only merges may be ahead of this baseline.
- **U3→U11 safety campaign: source-complete / merged.**
  - U3 #1158 → `ebef089fa379fe555136a4f8f492c201139d3a51`
  - U4 #1159 → `36b59aa3ef626c5200d4c070f0a91bcd41a22b9e`
  - U5 #1160 → `3a1b0ab87a443944aa81ef405c749eaa4d1fd4d4`
  - U6 #1161 → `67b7cbf4dc825f2554f3d75cb16ac218c4e28e11`
  - U7 #1162 → `f35b976efe523dbbc2090809ce72e91f0e9d3e95`
  - U8 #1163 → `b2fe86ec136e0a2b36fed4e3fa53fa2d659e0b86`
  - U9 #1164 → `6ae8f58293c3ce0b1025729ae40ef7969dc40621`
  - U10 #1165 → `2254ec9d390d6d495f7bd05d241f876c52905fb7`
  - U11 #1166 → `1ec48a0ed76aea23c82882ff8c8ae258fda20c95`
- Exact-head breaker QA and required CI were green before each U8→U11 merge.
- These are **source facts only**. They do not prove that any of U3→U11 is deployed.

## What the completed campaign now proves in source

- Replay adapters/evidence identity/promotion gates are fail-closed against the audited false-PASS paths.
- Broker contract metadata and contract identity routing fail closed; U8 enforcement remains a separate runtime decision.
- Replay↔paper↔demo reconciliation rejects unverified, malformed, incomplete, or contradictory evidence.
- Promotion requires exact-SHA fault-injection proof and mechanically re-runs the committed FI suite.
- Release build/promotion require an exact merged SHA plus current GitHub CI proof.
- The main release venv is version-locked for Python 3.13 and verifies installed freeze equality.
- None of the above creates deployment authority or enables live trading.

## Runtime boundary

- Last preserved verified futures runtime checkpoint remains release `c44d32bc4961e56fae5c5f88a976eb6783341638` from 2026-10-04.
- **No fresh VPS/runtime verification was performed by the U3→U11 source campaign or this documentation cleanup.**
- Do not infer that current `main` is deployed.
- No deploy, restart, env/config mutation, broker mutation, order action, strategy change, risk change, or execution enablement is authorized by this checkpoint.

## Current open work — keep lanes separate

### Futures runtime / deployment
- **DO NOT DEPLOY yet.** First run a fresh deployment-safety/runtime reconciliation from an authorized read-only source.
- Re-prove exact deployed SHA, rollback target, service/integrity health, demo/live posture, broker positions/orders, journal writes, deploy lock, and U11 host prerequisites.
- Keep `CONTRACT_IDENTITY_GUARD_ENFORCED` off until the Pine contract-hint / roll-seam proof is explicitly complete.
- U11 still has known runtime unknowns: actual release-host GitHub API reachability and VPS Python minor. Both fail closed.

### Cleanup / modernization
- Keep cleanup bounded and classification-first: ACTIVE_RUNTIME / ACTIVE_RESEARCH / REPLAY_REQUIRED / HISTORICAL_EVIDENCE / COMPATIBILITY_ONLY / DEAD-UNREFERENCED.
- Separate-service dependency reproducibility for `ops/push_relay` and the persistent watcher remains unresolved; U11's main release lock does not cover them.
- Release/archive branch deletion still requires current supersession/recoverability proof; do not mass-delete release refs.

### Options / research
- Follow `docs/options-current-state-handoff.md` and the research ledger, not this file.
- Active options stack #1152→#1154 and other research PRs remain separate from futures runtime authorization.

## Lane authorities

- Futures strategy verdict/posture: `docs/strategy-rules/Strategy_Inventory.md`
- Options current state: `docs/options-current-state-handoff.md`
- Experiment/trial history: `docs/research-trial-ledger.jsonl`
- Operator runtime/safety actions: `docs/futures-operator-todo.md`
- Historical long checkpoints: the archive named above plus dated evidence documents

## NEXT

1. Finish this bounded repo cleanup without changing execution behavior.
2. Keep deployment separate; any build/verify/promote attempt requires fresh runtime proof plus explicit operator GO.
3. For options/research work, follow their lane authorities and preserve prospective evidence rules.
4. Do not create another general status/checkpoint document. Update this compact file or the lane authority instead.
