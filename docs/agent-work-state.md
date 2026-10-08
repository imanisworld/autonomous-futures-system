# Agent Work State

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

## Checkpoint — 2026-10-08 (Cursor AFS-0165 docs sync)

- **Lane:** futures deployment plan docs (#1190).
- **DONE / DO NOT REDO:** Cursor B1–B8 RO matrices posted on #1190 (comments 6051491065, 6051610034). Claude AFS-0165/AFS-0166 docs body through `59b4e263`. Cursor parallel full rewrite abandoned as duplicate.
- **Landed:** #1190 head `15ba3675af196a30c2f190fbc4fd5c0fa1c65c87` — audit-allowlist wording redaction + historic B3 align to `489b55b`. Tracking mirror branch/PR: `cursor/futures-deploy-plan-grok-afs0165-dd85` / #1195 (prefer merge via #1190).
- **Grok re-review requested:** comment 6058847973 on #1190. No Grok PASS claimed.
- **NOT DONE:** deploy/promote/restart/env/collector/broker. Candidate remains UNSET. Root box proofs still UNVERIFIED.
- **Branches:** `docs/futures-deploy-plan-7c93027-20261008` (#1190), `cursor/futures-deploy-plan-grok-afs0165-dd85` (#1195 mirror).

## NEXT

1. Finish this bounded repo cleanup without changing execution behavior.
2. Keep deployment separate; any build/verify/promote attempt requires fresh runtime proof plus explicit operator GO.
3. For options/research work, follow their lane authorities and preserve prospective evidence rules.
4. Do not create another general status/checkpoint document. Update this compact file or the lane authority instead.
