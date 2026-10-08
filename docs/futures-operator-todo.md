# Futures Operator TODO

### When home — cloud-to-VPS read-only audit access

- [ ] Reuse the existing **working Cursor forced-command SSH audit route**; confirm current allowed verbs and exactly which B1–B10, collector-pin, watcher/history, rollback and journal reads remain blocked. Do not rebuild access or give the agents a general root shell.
- [ ] From the existing authorized administrator path, separately review/approve the **smallest read-only** method for missing protected evidence; verify account/command permissions and full UTC outputs. Do not install a new wrapper, edit sudoers/keys/SSH, or broaden permissions before explicit approval and independent review.
- [ ] Verify the route works **from the cloud without the Mac**; have Grok check captured evidence and preserve the current VPS/collector state. Keep FUTURES/OPTIONS HOLD until actual gate proof exists.
## Active handoff — 2026-10-08 (source-only; no release authorization)

- **GitHub main:** `307fe56771031b44eeb8d0235224cf010616ce4a` (#1198 source-merged). **Last reported deployed futures:** `c44d32bc4961e56fae5c5f88a976eb6783341638`, not freshly verified. **Candidate UNSET; FUTURES HOLD / OPTIONS HOLD.**
- **Reviewed source baseline:** Grok AFS-0181 PASS on exact `393ad72bc1caaa736e1958beb35124908a2054a2` against last reported deployed `c44d32b`. It is not a release GO and does not automatically approve the later main merge. #1198 received independent AFS-0183 PASS on `25dec22371fa686acb5a794c901cf2fc7c028aed` and source-merged as `307fe567` (read-only adapter/tests). Before release candidacy, compare any nominated exact SHA to AFS-0181 and verify changed paths separately.
- **Documentation work:** #1191 and #1192 are already merged; do not redo. Draft #1197 preserves historical Cursor restricted-access VPS evidence and is pending its own review. Other options PRs remain separate; issue #1184 is closed/deferred with draft #1199 unmerged.
- **Current operator action:** collect authorized read-only, timestamped B1–B10/collector/journal/disk proof, with **exact command, complete safely redacted output, exit status, UTC timestamp, host/path identity**; summaries alone do not substantiate a PASS. For **B7 + B9**, capture positions, working orders, demo/live posture and deploy lock **in one read-only session with a target span of 120 seconds**, and repeat before any GO. Restricted Cloud Agent credentials are **not** root authorization.
- **Decisions remain separate:** access method; candidate nomination; approved evidence windows; build/verify; demo promote. Never change keys, permissions, env, collector pins, broker positions, release symlinks, services or journals as part of read-only evidence collection.
- **Audit-command blockers (do not execute #1197 block unchanged):** Claude's read-only source cross-check found #1197 B3 incorrectly points at `/root/afs-releases/current(.previous)` rather than live symlink `/root/autonomous-futures-system` and text pointer `/root/afs-shared/current.previous`; additionally B5's `check-freeze --freeze -` cannot read pip's stdin because the checker expects an actual path. The options ARMED scan also silently skips invalid JSON and can miss setup-ID lifecycle anomalies. Correct/independently review the existing #1197 commands first (see PR #1197 review comment `6061369464`); any missing/invalid evidence is HOLD. Claude's `docs/claude-vps-readiness-audit-2026-10-08.md` is source-only supporting evidence, not box verification. GitHub's full ancestry comparison from reported deployed `c44d32b` to main `307fe567` is 204 commits ahead/0 behind, 237 changed files; AFS-0181 `393ad72b` to main is the single source-only #1198 merge.

## Operator checklist — 2026-10-08 (completion requires operator verification)

**Priority:** Resolve Grok's actual blockers first. Do not deploy merely to finish this checklist.

### 1. Overnight audit
- [ ] Read Grok AFS-0159–0163 consolidated findings; distinguish completed review from remaining operator decisions.
- [ ] Identify genuine blockers and assign fixes without duplicating other agents' work.
- [ ] Confirm current `main` SHA, CI, and exact deployment candidate.

### 2. Options system
- [x] #1186/#1177 part 1 source-merged as `0d02a8bd`; #1198/#1177 part 2 read-only adapter source-merged as `307fe567` after reported Grok AFS-0183 PASS on `25dec223` and green CI. [ ] Effective options collector pin, raw journal/ARMED integrity, authoritative quote-age config and approved rollback rehearsal remain **UNVERIFIED**.
- [x] #1184 issue closed/deferred; draft #1199 offline authority-history validator remains unmerged. [ ] No authenticated human approvers or independent durable approval-store proof; no authority persistence/runtime use, and do not restart this work absent explicit approval.
- [ ] Keep #1154 parked until #1177 prerequisites pass.

### 3. Futures deployment
- [x] #1190 plan merged as `e3c84a78` (Grok AFS-0174 PASS); #1194 watcher guard merged as `168e7420` (Grok AFS-0168 PASS); #1196 release-history preflight source-merged as `a602501c` after Grok PASS AFS-0178 at `a3535c7`.
- [ ] Authorized Ops/Cursor/Grok restricted read-only box proof of the effective watcher and `release_history.txt` paths, B1/B5/B6 pins, B8 collector isolation, journal integrity and rollback remains outstanding. Keep their audit access available; use separate, least-privilege evidence access without exposing secrets or enabling deployments. Next nominated release candidate SHA remains UNSET.
- [ ] Resolve B1, B5, B6, B8 and B10 (watcher preflight) plus evidence-window decisions using fresh authorized VPS proof and reviewed source/tests; never assume missing evidence.
- [ ] Confirm options collector isolation, fresh-journal rollback boundaries, and futures watcher rollback can fail safely **before** any release mutation.
- [ ] Decide whether the exact release is eligible for *separate* operator deployment approval. No automatic merge/build/promote/restart.

### 4. Documentation and cleanup
- [x] #1191 options handoff already merged; do not reopen its reviewed document work. Later #1198 source merge belongs in the current options authority record (avoid concurrent edits with active draft #1200).
- [x] #1192 coordination/futures documentation already merged. #1187 and #1193 CLOSED without merge. [ ] #1197 dated VPS audit PR remains draft/pending independent review; keep the single agent checkpoint and do not treat historical snapshots as root box proof.
- [ ] Keep #1167 cleanup audit-only; no code or evidence deletion.

### 5. Trading evidence
- [ ] Keep existing read-only collection unchanged if evidence integrity is verified; check whether 'dead companion daily' was a retired Discord job using root read-only census, not assume loss of trading evidence.
- [ ] Defer combined trade-performance review until the weekend. Preserve existing reports without promotion claims.

**End-of-day goal:** Verified readiness, current documentation, and a clear deployment decision — not necessarily deployment itself.

## Latest operator queue — 2026-10-08

- GitHub `main`: `307fe56771031b44eeb8d0235224cf010616ce4a` after source-only #1198 merge. Grok AFS-0181 covers source main only through exact `393ad72bc1caaa736e1958beb35124908a2054a2`; new source changes have their own review boundaries. Last reported VPS futures release `c44d32bc4961e56fae5c5f88a976eb6783341638` still requires authorized readback.
- **#1189 (MERGED)** — reviewed remote-build quote repair at `f60ec6ff3268e1b193ff8e21aefa3cd5d8420319`, merged as `064ee674b788c144fc8d3ca65082ed060927e2f1`; GitHub exact-head CI and post-merge checks reported green. Merging a fix does not authorize build/verify or promote.
- **#1190 (MERGED / PLAN ONLY):** AFS-0174 Grok PASS on `aa96563e`; merged as `e3c84a78`. Candidate remains UNSET, root-runtime and rollback gates remain HOLD. #1194 watcher source fix separately Grok-PASS-reported at `1b7b996` and source-merged as `168e7420`. Neither source merge authorizes a release.
- Prior `afs-deploy` CI-proof wrapper **51/51 tests and real U11 fetch/`verify-live` PASS are operator-reported, not independently verified here**. They apply only to the reported code and evidence; do not transfer them to changed #1189/#1194 paths or call them verified proof. New exact-head regression checks and independent review are required for relevant changes.
- **Remaining futures gate:** #1196 release-history preflight is source-MERGED as `a602501c`; #1194 watcher pre-mutation fix is source-MERGED as `168e7420`. Candidate remains UNSET. Authorized B1–B10 read-only verification, B8 options pin/ARMED journal integrity, B7/B9 coherent flat/lock snapshot, rollback and Python 3.13/dependency state remain UNVERIFIED on box. Reconcile net source since AFS-0181 for an exact future nominee, then seek separate GOs. No auto journal reset or collector repin.
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
