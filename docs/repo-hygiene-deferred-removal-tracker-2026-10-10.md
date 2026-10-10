# Deferred removal tracker (2026-10-10)

**Purpose:** Classify what is **not needed for the current futures lane** so it can be removed **later** in one controlled pass. **Nothing in this file authorizes deletion now.**

**Policy:** Follow `docs/BRANCH_ARCHIVE_INDEX.md` and `AGENTS.md` — classify first; tag/archive remote branches before delete; never delete VPS-referenced `release/*` without operator ruling.

**Remove only after ALL gates (edit when done):**

- [x] Grok sign-off on #1208 / #1209 (merged on `main` @ `a675a8d`)
- [x] **#1211** merged on `main` @ `71e5f45`
- [x] **#1210** @ `7d766b2` → **`465abc2`**; **#1212** @ `9df1a09` → **`05363a4`**; **#1213** @ `730ef53` → **`6d3078f`**
- [ ] Track B decision: research SHA deployed **or** forward prereg explicitly paused with SHA frozen in checkpoint
- [ ] Claude options handoff merged/archived on GitHub (`claude/options-time-exit-research-20261010` @ `a30f0a6` or successor)
- [ ] Any open PR you still care about is **merged, closed, or copied** to `main`/checkpoint docs

**Snapshot:** Mac checkout @ `8c57d84` (#1211); ~105 remote branch refs; ~**1.3 GB** `private/`; ~**2.3 GB** ignored `data/replay*`.

---

## 1. Local disk — `private/` (gitignored)

| Path | ~Size | Role | Deferred action | Block until |
|---|---:|---|---|---|
| `private/trading-evidence-2026-10-09/` | 849 MB | Options scanner sqlite snapshot (Oct 9) | **DELETE** local copy | Claude handoff + operator confirm no pending scanner re-sim needs raw DB |
| `private/cross_market_grid_2026_09_23/` | 271 MB | Closed Grok grid work | **DELETE** | Checkpoint marks grid DO NOT REDO |
| `private/mes598-proof/` | 104 MB | Old MES proof bundle | **DELETE** | Confirm not cited in open trial/prereg |
| `private/profitability-review-2026-10-08/` | 3.1 MB | Session notes | **DELETE** or move to ChatGPT Library | Public checkpoint + Library handoff saved |
| `private/research-notes-2026-10-10/` | 1.1 MB | Session scratch | **DELETE** after | Content absorbed into `docs/futures-*-2026-10-10.md` on GitHub |
| `private/qqq-4hr-options-2026-10-10/` | 48 KB | Claude QQQ scratch | **DELETE** after | Claude branch handoff complete (§6 pending runs done or waived) |
| `private/paper-card-899/` | 5.1 MB | Historical paper card | **REVIEW** | Options lane operator says unneeded |
| `private/grid_2026_09_21/` | 116 KB | Old grid | **DELETE** | Same as cross_market |
| `private/prune-review/` | 272 KB | Hygiene notes | **DELETE** | Superseded by this tracker |
| `private/webull-proof/` | 24 KB | Proof snippet | **KEEP** until options authority doc says otherwise | — |
| `private/research/` | small | Misc | **REVIEW** listing before delete | — |
| `private/honest-baseline-*.md`, `plain-cancelled-audit-*.md`, etc. | KB | July 2026 notes | **KEEP** or archive to Library | Operator preference |

**Estimated recoverable from table (conservative):** ~**1.2 GB** if top rows deleted.

---

## 2. Local disk — ignored `data/` (replay corpora)

Not in git; tests may expect paths under `data/replay*`, `data/htf/`, etc. (see `.gitignore`).

| Path | ~Size | Deferred action | Block until |
|---|---:|---|---|
| `data/cross_market_2026_09_23/` | 573 MB | **DELETE** | No active study using this corpus (closed grid) |
| `data/replay_corpus_v1_5m/` | 455 MB | **DELETE** if unused | No local replay scheduled |
| `data/replay_corpus_v1_5m_4hr_audit/` | 454 MB | **KEEP until** | #1208 merge + any 4HR replay parity re-run finished |
| `data/replay_polygon_5m/` | 375 MB | **DELETE** if unused | Same |
| `data/replay_polygon_v2/` | 201 MB | **DELETE** if unused | Same |
| `data/replay_polygon/` | 113 MB | **DELETE** if unused | Same |
| Other `data/replay_corpus_v1*` | ~145 MB | **REVIEW** | — |

**Estimated recoverable:** up to ~**2 GB** if all replay dirs dropped (re-download/regenerate if a future trial needs them).

---

## 3. Git worktrees (extra checkouts)

| Path | Branch / HEAD | ~Size | Deferred action | Block until |
|---|---|---:|---|---|
| `private/wt-options-exits` | `claude/options-time-exit-research-20261010` | 30 MB | **`git worktree remove`** | Handoff on **origin**; local worktree optional |
| `private/wt-capped` | `claude/capped-session-reporting` | 36 MB | **`worktree remove`** | #1207 merged/closed/abandoned |
| `../afs-wt-1067` | `cursor/options-scanner-universe-expand-5735` | 34 MB | **`worktree remove`** | #1067 closed or explicitly parked forever |
| `../afs-wt-1069` | `cursor/spx-spxw-paper-lane-5735` | 33 MB | **`worktree remove`** | #1069 closed or parked |
| `../afs-cursor-cloud-maintenance` | `ops/cursor-cloud-vps-maintenance` | 37 MB | **`worktree remove`** | #1206 closed |
| `../afs-options-1111` | detached `d238ea4` | 33 MB | **`worktree remove`** | Identify purpose; likely stale |

**Estimated recoverable:** ~**200 MB** (mostly duplicate `.git` object access — main repo keeps objects).

---

## 4. Local git branches

| Branch | PR / state (2026-10-10) | Deferred action | Block until |
|---|---|---|---|
| `fix/timeframe-causal-buckets-research-20261010` | #1211 OPEN | **KEEP** → delete local after merge to `main` | PR merged |
| `research/4hr-mnq-400-forward-20261009` | no PR; on origin | **KEEP** → delete after deploy or abandon recorded | Track B GO + deploy or explicit cancel |
| `fix/strict-paper-reference-fills-20261009` | #1208 OPEN | remote only after merge | #1208 merged |
| `fix/rr-verify-actual-bracket-20261009` | #1209 OPEN | remote only after merge | #1209 merged |
| `test/combined-execution-risk-20261010` | #1210 OPEN | remote only after merge | #1210 merged |
| `claude/options-time-exit-research-20261010` | pushed; no PR | **KEEP** until options lane archived | Handoff merged or waived |
| `claude/capped-session-reporting` | #1207 OPEN | delete after close | #1207 |
| `ops/cursor-cloud-vps-maintenance` | #1206 OPEN | delete after close | #1206 |
| `cursor/options-scanner-universe-expand-5735` | #1067 OPEN (parked) | delete local + worktree when PR closed | Operator |
| `cursor/spx-spxw-paper-lane-5735` | #1069 OPEN (parked) | same | Operator |
| `docs/212c-1134-prep-checkpoint` | #1138 **MERGED** | **`git branch -d`** | Verify merge on `main` |
| `docs/post-c44d32b-epoch-docs-20261004` | #1132 **MERGED** | **`git branch -d`** | Verify merge on `main` |
| `research/options-212c-forward-prep-20261004` | #1134 **MERGED** | **`git branch -d`** | Verify merge on `main` |
| `research/options-212c-merged-checkpoint-20261002` | #1119 **CLOSED** | **DELETE local** after confirm superseded | Options handoff authority |
| `audit/public-readiness-20260925` | #1037 OPEN | keep until close | — |
| `overlay-pre-rebase-2859edd` | none | **DELETE local** if SHA reachable from `main`/tag | Quick `git merge-base --is-ancestor` check |
| `release/futures-cddef4a-curated-20260924` | local release ref | **KEEP** unless operator confirms VPS no longer references | VPS audit |
| `release/options-5b7be0f7-989` | local release ref | **KEEP** | VPS audit |
| `candidate/tradovate-auth-only-41ae188` | none | **REVIEW** | — |
| `main` | — | **KEEP** | — |

---

## 5. Remote branches (GitHub) — defer bulk cleanup

**Do not batch-delete** without `archive/*` tags per `BRANCH_ARCHIVE_INDEX.md`.

| Category | Count (approx) | Deferred policy |
|---|---:|---|
| `release/*` | 33+ | **KEEP** until operator + VPS SHA map says otherwise; tag then delete |
| `archive/*` on remote | few | **KEEP** (already archived) |
| Open PR heads (#994–#1211, etc.) | ~21 open | Delete remote branch **only when PR merged/closed** (GitHub often auto-deletes) |
| Old closed research (`research/mnq-orb-*`, etc.) | many | **Phase 2 cleanup:** list vs merged/superseded; tag then delete |
| `research/4hr-mnq-400-forward-20261009` | 1 | **KEEP** through Track B deploy decision |

**Phase 2 task (end of campaign):** run `/repo-hygiene-check` skill or `docs/BRANCH_ARCHIVE_INDEX.md` workflow on remote list; no ad-hoc mass `git push origin --delete`.

---

## 6. Committed repo artifacts — do **not** remove

These look like “old research” but are **authoritative or DO NOT REDO**:

| Artifact | Location |
|---|---|
| 4HR benchmark + forward prereg | `research/4hr-mnq-400-forward-20261009` branch |
| ORB/VWAP sweep results | `docs/research-evidence/orb-vwap-tf-window-sweep-2026-10-10/` (on research branch) |
| Futures checkpoints (2026-10-10) | #1211 branch docs |
| Claude options handoff | `claude/options-time-exit-research-20261010` |
| Strategy inventory / trial ledger | `docs/strategy-rules/Strategy_Inventory.md`, `docs/research-trial-ledger.jsonl` |

---

## 7. End-of-campaign removal script (draft — run manually)

**Only after Section gates are checked.**

```bash
# Example — adjust paths; review each line before running.

# Worktrees
git worktree remove private/wt-options-exits --force
git worktree remove private/wt-capped --force
git worktree remove ../afs-wt-1067 --force
git worktree remove ../afs-wt-1069 --force
git worktree remove ../afs-cursor-cloud-maintenance --force
git worktree remove ../afs-options-1111 --force

# Merged local branches
git branch -d docs/212c-1134-prep-checkpoint docs/post-c44d32b-epoch-docs-20261004 research/options-212c-forward-prep-20261004

# Private disk (irreversible)
rm -rf private/trading-evidence-2026-10-09 private/cross_market_grid_2026_09_23 private/mes598-proof

# Optional replay corpora
# rm -rf data/cross_market_2026_09_23 data/replay_polygon_5m ...
```

---

## 8. Maintenance

| When | Action |
|---|---|
| After each merge/deploy milestone | Tick gates at top; mark rows **DONE** or strike through |
| New scratch/worktree | Add a row here before creating |
| Before any delete | Confirm SHA on `origin` or an `archive/*` tag |

**Checkpoint pointer:** add one line to `docs/agent-work-state.md` START HERE when this file changes materially.
