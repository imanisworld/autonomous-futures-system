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

## Current repository checkpoint — 2026-10-08

- **GitHub `main` tip (verified this session):** `a602501cb0e665952da2101e9c4f73a3e68aea90` — includes merged #1186, #1190, #1194, #1196.
- **Deployed futures release (allowlist, 2026-10-08T12:33:42Z):** still `c44d32bc4961e56fae5c5f88a976eb6783341638` — **not** equal to `main`.
- **VPS readiness audit:** `docs/cursor-vps-readiness-audit-2026-10-08.md` — READ-ONLY allowlist+public evidence; FUTURES HOLD / OPTIONS HOLD; pending Grok challenge. Root B10/watcher/history/journal-ARMED/Python/freeze/collector-pin remain UNVERIFIED under allowlist.
- **Source-safety baseline (historical):** `1ec48a0ed76aea23c82882ff8c8ae258fda20c95` — U11 / PR #1166. Later merges are ahead; do not treat baseline as tip.
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

- Last preserved verified futures runtime checkpoint remains release `c44d32bc4961e56fae5c5f88a976eb6783341638` from 2026-10-04 (still live on box as of 2026-10-08T12:33:42Z allowlist read).
- Fresh Cursor allowlist/public VPS audit completed 2026-10-08 — see `docs/cursor-vps-readiness-audit-2026-10-08.md`. It does **not** authorize deploy.
- Do not infer that current `main` is deployed.
- No deploy, restart, env/config mutation, broker mutation, order action, strategy change, risk change, or execution enablement is authorized by this checkpoint.

## Current open work — keep lanes separate

### Futures runtime / deployment
- **DO NOT DEPLOY yet.** Candidate remains **UNSET**. Cursor VPS audit 2026-10-08: HOLD; handoff for Grok on #1190.
- Allowlist-proven: deployed `c44d32b`, demo/live-off/flat/disarmed, Path-1 posture in process env, deploy lock absent, futures journal writing.
- Still UNVERIFIED without root: watcher unit/path, `release_history.txt`, durable fingerprint pin, Python 3.13, live freeze, options collector pin class, options ARMED journal scan, disk capacity, rollback readiness.
- Keep `CONTRACT_IDENTITY_GUARD_ENFORCED` off until the Pine contract-hint / roll-seam proof is explicitly complete.
- U11 host unknowns remain: release-host GitHub API reachability and VPS Python minor (fail closed).

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

1. Grok: challenge Cursor VPS audit (`docs/cursor-vps-readiness-audit-2026-10-08.md` / #1190 handoff); return independent HOLD/NOT READY/READY FOR OPERATOR REVIEW.
2. Operator: authorize root read-only proofs listed in that audit (B2/B3/B4/B5/B8/B10/journal ARMED/disk).
3. Keep deployment separate; any build/verify/promote requires nominated SHA + fresh root proof + explicit operator GO.
4. For options/research work, follow `docs/options-current-state-handoff.md`; do not silently ride collector on a futures promote.
