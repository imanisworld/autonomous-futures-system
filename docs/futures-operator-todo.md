# Futures Operator TODO

## Operator checklist — 2026-10-08 (completion requires operator verification)

**Priority:** Resolve Grok's actual blockers first. Do not deploy merely to finish this checklist.

### 1. Overnight audit
- [ ] Read Grok AFS-0159–0163 consolidated findings; distinguish completed review from remaining operator decisions.
- [ ] Identify genuine blockers and assign fixes without duplicating other agents' work.
- [ ] Confirm current `main` SHA, CI, and exact deployment candidate.

### 2. Options system
- [ ] Return #1186/#1177 part-1 B1 duplicate ARMED and B2 fresh-journal rollback blockers to its builder, then require exact-head retest and Grok review; keep adapter separate.
- [ ] Advance #1184 authority-history security; no authority persistence/runtime use before verification.
- [ ] Keep #1154 parked until #1177 prerequisites pass.

### 3. Futures deployment
- [ ] Confirm merged #1189 (`064ee674`) and review corrected #1190 exact candidate, root/watcher path tests and exact-head CI.
- [ ] Resolve B1, B5, B6, B8 and evidence-window decisions using fresh, authorized VPS proof; never assume missing evidence.
- [ ] Confirm options collector isolation and rollback readiness.
- [ ] Decide whether the exact release is eligible for *separate* operator deployment approval. No automatic merge/build/promote/restart.

### 4. Documentation and cleanup
- [ ] Revise #1191 options handoff to include #1186 and corrected #1183 merge, then confirm CI and Grok verdict.
- [ ] Reconcile current handoffs with actual `main`; #1191 owns options handoff, #1192 coordination/futures TODO, #1193 shadow reports remain draft; avoid merging obsolete #1187.
- [ ] Keep #1167 cleanup audit-only; no code or evidence deletion.

### 5. Trading evidence
- [ ] Keep existing read-only collection unchanged if evidence integrity is verified; check whether 'dead companion daily' was a retired Discord job using root read-only census, not assume loss of trading evidence.
- [ ] Defer combined trade-performance review until the weekend. Preserve existing reports without promotion claims.

**End-of-day goal:** Verified readiness, current documentation, and a clear deployment decision — not necessarily deployment itself.

## Latest operator queue — 2026-10-08

- GitHub `main`: `064ee674b788c144fc8d3ca65082ed060927e2f1` (#1189 merge) at this check. Latest audited deployed futures release remains `c44d32bc4961e56fae5c5f88a976eb6783341638` until a fresh box check.
- **#1189 (MERGED)** — reviewed remote-build quote repair at `f60ec6ff3268e1b193ff8e21aefa3cd5d8420319`, merged as `064ee674b788c144fc8d3ca65082ed060927e2f1`; GitHub exact-head CI and post-merge checks reported green. Merging a fix does not authorize build/verify or promote.
- **#1190 (OPEN)** — deployment **plan only**, head `c90af59ec5edcafad4d8ebf9b6b30a4c7789baeb`. Review its preregistration, trading-path delta, acceptance/abort conditions, post-deploy read-only checks, rollback and watcher re-arm; do not duplicate the plan.
- Prior `afs-deploy` CI-proof fix has operator-reported 51/51 fake-box passes and real U11 fetch/`verify-live` pass. Reuse verified proof, do not re-run unless inputs/code change.
- **#1190 CHANGES REQUIRED:** retarget from superseded `7c930274179f7c76749f75b35adb14fbb9255e54` to candidate `064ee674b788c144fc8d3ca65082ed060927e2f1`; require safe, nonempty, non-root watcher cwd validation before any privileged re-arm/copy/chmod action. Root read-only evidence for B1/B5/B6, evidence epochs, rollback and B8 isolation is missing. Prefer separate reviewed collector pin/GO to prevent #1186 format from riding a futures release; no journal reset approved.
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
