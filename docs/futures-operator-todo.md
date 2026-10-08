# Futures Operator TODO

> **Purpose:** compact list of operator-owned futures infrastructure/safety actions. Historical snapshots and completed detail are in `docs/futures-operator-todo-archive-through-2026-10-07.md`.
>
> This file is **not** strategy-status authority. Futures strategy truth is `docs/strategy-rules/Strategy_Inventory.md`; experiment history is `docs/research-trial-ledger.jsonl`; runtime truth requires fresh box evidence.
>
> Core rule: **No proof, no run.**

## Deployment-planning update — 2026-10-08

- **Candidate:** `7c930274179f7c76749f75b35adb14fbb9255e54`, held fixed despite newer `main` at `58606b43ac8f391f4b3ed861fbd6c3fdf807e303`.
- **Audited running release:** `c44d32bc4961e56fae5c5f88a976eb6783341638` (historical snapshot; verify again before execution).
- **CI-proof wrapper:** reported installed; **51/51** isolated fake-box checks passed, including fail-closed wrong-SHA/fetch failures; the real Python U11 proof fetch and live verification passed from the Mac outside the sandbox.
- **Previous immediate blocker is reported resolved.** Do not redo its testing unless code or evidence changes.
- **Next actual work is one plan/review**, already assigned to Futures: classify unreviewed trading-path changes between exact release SHAs; preregister acceptance/abort criteria; define staged rollback and watcher re-arm; define post-release read-only checks; obtain independent approval.
- **Runtime safeguards remain requirements, not standing proof:** live disabled, demo, cap 1, flat account, integrity, deploy lock, risk pins, recovery path. Recheck immediately before a separately authorized release.
- **HOLD — no build, promote, restart, merge, or deploy permission from this doc update.**

## Latest handoff — 2026-10-07 (#1180)

- Verified GitHub `main` is `7c930274179f7c76749f75b35adb14fbb9255e54` (squash-merged #1180); handoff reports reviewed tree matches merged tree.
- #1180 reviewer fixes complete; exact-head CI **8,532 passed / 8 skipped**. **No build, promote, restart, or deployment.**
- Box reportedly has separate Python **3.13.16**, with system Python **3.14.4** unchanged and trading services untouched. Runtime release readiness is not thereby established.
- **Sole currently reported blocker:** `afs-deploy` CI-proof handling. Fix only the wrapper; require **two fake-box regression tests**, independent review, then a **read-only deployment-readiness audit**.
- **Decision: HOLD. No deployment, live trading, broker submissions, risk/strategy/config changes.** Do not redo closed #1180 reviewer work.

## Previous source/runtime boundary — 2026-10-07

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

## IMMEDIATE NEXT — docs/source only

1. Once the development Mac/authorized environment is reachable, inspect exact `afs-deploy` CI-proof failure and patch the wrapper only.
2. Run both fake-box regression tests, covering accepted exact-head CI proof and missing/stale/mismatched CI proof **fail-closed** behavior. Record commands and outputs; do not assume tests pass.
3. Obtain independent scoped review. Perform a final **read-only** readiness audit of wrapper proof plus host/runtime gates below.
4. Keep **NO DEPLOY** until the audit passes and explicit operator GO is separately issued.

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
