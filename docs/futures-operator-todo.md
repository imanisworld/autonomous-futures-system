# Futures Operator TODO

## Tomorrow's To-Do — Thursday, 2026-10-08 (0/15 complete; pending verification)

**Priority:** Resolve Grok's actual blockers first. Do not deploy merely to finish this checklist.

### 1. Overnight audit
- [ ] Review Grok's consolidated PASS / FAIL / BLOCKED findings.
- [ ] Identify genuine blockers and assign fixes without duplicating other agents' work.
- [ ] Confirm current `main` SHA, CI, and exact deployment candidate.

### 2. Options system
- [ ] Continue #1177 dedicated 1-2-2 collector integration (coordinate with existing owner).
- [ ] Advance #1184 authority-history security; no authority persistence/runtime use before verification.
- [ ] Keep #1154 parked until #1177 prerequisites pass.

### 3. Futures deployment
- [ ] Review #1189 and #1190 status and exact-head reviews/CI.
- [ ] Resolve B1, B5, B6, B8 and evidence-window decisions using fresh, authorized VPS proof; never assume missing evidence.
- [ ] Confirm options collector isolation and rollback readiness.
- [ ] Decide whether the exact release is eligible for *separate* operator deployment approval. No automatic merge/build/promote/restart.

### 4. Documentation and cleanup
- [ ] Review #1191 CI and independent Grok verdict.
- [ ] Reconcile current handoffs with actual `main`; account for overlapping docs PRs #1187/#1192/#1193.
- [ ] Keep #1167 cleanup audit-only; no code or evidence deletion.

### 5. Trading evidence
- [ ] Continue **existing** daily read-only collection if its collector and evidence integrity are verified; do not change strategies or restart collectors automatically.
- [ ] Defer combined trade-performance review until the weekend. Preserve existing reports without promotion claims.

**End-of-day goal:** Verified readiness, current documentation, and a clear deployment decision — not necessarily deployment itself.

## Latest operator queue — 2026-10-08

- GitHub `main`: `f8e4257e2b9481f4b670b24e7602634e9cf47649` at this check. Latest audited deployed futures release remains `c44d32bc4961e56fae5c5f88a976eb6783341638` until a fresh box check.
- **#1189 (OPEN)** — scoped Bash remote build quoting fix at `f60ec6ff3268e1b193ff8e21aefa3cd5d8420319`. Reported CI green; second Grok review is sought, not yet independently evidenced here. Merge requires explicit operator decision; no automated merge.
- **#1190 (OPEN)** — deployment **plan only**, head `c90af59ec5edcafad4d8ebf9b6b30a4c7789baeb`. Review its preregistration, trading-path delta, acceptance/abort conditions, post-deploy read-only checks, rollback and watcher re-arm; do not duplicate the plan.
- Prior `afs-deploy` CI-proof fix has operator-reported 51/51 fake-box passes and real U11 fetch/`verify-live` pass. Reuse verified proof, do not re-run unless inputs/code change.
- If #1189 merges, **do not reuse** proof or deployment-plan candidate SHA `7c930274179f7c76749f75b35adb14fbb9255e54` as if it covered the new merged tree. Pin the new exact release SHA, reconcile scope and pass required release/CI gates.
- Just before any separately authorized release action re-prove deploy lock, current source/box identity, integrity, demo + live-off + cap-one pins, broker flatness/orders, recovery/rollback and watcher state.
- **HOLD**: no build, promote, restart, deployment or live execution authorized by completed source tests or any docs PR.

> **Purpose:** compact list of operator-owned futures infrastructure/safety actions. Historical snapshots and completed detail are in `docs/futures-operator-todo-archive-through-2026-10-07.md`.
>
> This file is **not** strategy-status authority. Futures strategy truth is `docs/strategy-rules/Strategy_Inventory.md`; experiment history is `docs/research-trial-ledger.jsonl`; runtime truth requires fresh box evidence.
>
> Core rule: **No proof, no run.**

## Current source/runtime boundary — 2026-10-07

- Source-safety baseline: `1ec48a0ed76aea23c82882ff8c8ae258fda20c95` after U11 / #1166. Fetch current `main` before acting; later docs/guidance-only merges may be ahead of this baseline.
- U3→U11 are merged **source changes only**. Do not treat them as deployed.
- Last preserved verified futures runtime checkpoint: `c44d32bc4961e56fae5c5f88a976eb6783341638` from 2026-10-04.
- No fresh runtime verification was performed during this source/docs cleanup.

## COMPLETE — source safety campaign

U3→U11 is complete on `main`. Do not reopen or reimplement those units merely because runtime is behind source.

- evidence/replay/promotion identity gates
- rejected-candidate follow-through
- broker metadata and contract identity routing
- replay/paper/demo reconciliation
- exact-SHA fault-injection promotion proof
- reproducible main-release version lock + build/promotion live-CI proof

A merge is not deployment authority.

## OPERATOR ACTION — before any future deployment/runtime mutation

Run a fresh deployment-safety/runtime reconciliation first. At minimum re-prove:

- exact deployed SHA and rollback target;
- release integrity, service health, and restart state;
- `LIVE_TRADING_ENABLED`, `TRADOVATE_ENV`, hard-cap and expected-proof posture;
- broker account identity, open positions, and working orders;
- journal/evidence writing and current observation state;
- deploy lock state and candidate-release prerequisites;
- release host can reach GitHub check-runs API required by U11;
- VPS/release Python minor satisfies U11's Python 3.13 lock.

If any item cannot be proven, stop.

## OPERATOR ACTION — U8 enforcement remains off

Do **not** enable `CONTRACT_IDENTITY_GUARD_ENFORCED` merely because U8 merged.

Before enforcement, prove the TradingView/Pine contract hint and rollover-seam behavior under the exact production path. Unknown/mismatch must remain fail-closed.

## OPERATOR ACTION — access / recovery prerequisites

Re-verify before changing access:

- phone SSH login;
- provider-console / break-glass recovery;
- rollback drill.

Do not remove or tighten existing access until replacement and recovery paths are proven.

## CLEANUP / MODERNIZATION — source only

- Classify before deletion: ACTIVE_RUNTIME / ACTIVE_RESEARCH / REPLAY_REQUIRED / HISTORICAL_EVIDENCE / COMPATIBILITY_ONLY / DEAD-UNREFERENCED.
- Do not mass-delete `release/*`, archive refs, or old candidate refs without recoverability + supersession proof.
- `ops/push_relay` and the persistent watcher remain separate dependency environments; U11 does **not** make those reproducible.
- Old duplicate release/candidate cleanup remains an operator maintenance-window action only after fresh box/reference proof.
- Keep agent/current-state docs consolidated; do not create competing status files.

## SEPARATE — options / research

- Active options stack #1152→#1154 remains outside this futures deployment lane.
- Other open options/research PRs require their own evidence/review decisions; do not merge them as “cleanup.”
- PR #1037 public-repo/governance work remains separate from futures execution safety.
- MNQ ORB Stage A #994 remains WAIT / research-only unless its authority explicitly reopens it.

## Definition of done before any deploy

A future deployment is eligible for operator consideration only when:

- current runtime identity/posture is freshly verified;
- exact candidate SHA is reviewed and merged;
- U11 release proof can run from the operator/release host;
- broker state is flat or otherwise explicitly reconciled;
- rollback and recovery paths are known;
- no unverified execution/risk/config change is riding along.

Until then: **DO NOT DEPLOY.**
