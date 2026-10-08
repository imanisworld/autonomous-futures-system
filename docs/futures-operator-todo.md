# Futures Operator TODO

## Next operator window — 2026-10-08 (priority order)

1. **Review Grok's unified audit output**, if completed; verify each cited PR/head, CI, reviewer verdict and current `main` against GitHub before accepting PASS. No claim that the audit has completed yet.
2. **Futures release decision:** reconcile #1189 review/CI and #1190 candidate plan; check B1 (fresh box posture), B5 (Python/dependency lock), B6 (guard setting remains off pending contract proof), B8 (options collector isolation), rollback and evidence-window effects. Choose an exact candidate only after the required gates; otherwise HOLD.
3. **Options lanes (separate):** check #1186/#1177 dedicated 1-2-2 producer/adaptor review and CI, #1184 authority-history hard prerequisite before persistence/runtime use, and #1154 parked until prerequisites pass. #1167 cleanup stays audit-only. Avoid overlapping another agent's implementation.
4. **Evidence/monitoring:** review the dead/stale companion collector report (last seen Sep 23 21:15 UTC) with actual collector census/logs if authorized. Oct 5–7 shadow P&L and trending/sideways screenshots are operator-provided observations, not a strategy promotion or deployment requirement; if reconciling them, use dated handoff in draft #1193 and original journal evidence rather than launching a fresh study.
5. **Documentation hygiene:** reconcile overlapping open drafts #1187/#1191/#1192/#1193 without merging conflicting status claims or discarding unique evidence. Make no deletion or broad refactor solely for tidiness.

**Stop condition:** only operator-approved exact-SHA merge/deploy actions after independent review and a fresh runtime safety check; no overnight deployment, automatic merge, broker execution or strategy rule change.

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
