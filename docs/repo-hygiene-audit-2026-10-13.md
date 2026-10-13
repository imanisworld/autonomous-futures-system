# Repo hygiene audit — 2026-10-13 (source-only)

**Scope:** one controlled pass over Polygon dependency, documentation consolidation, branch/worktree classification, and #1184 security tracking. **No** runtime mutation, deploy, merge, branch delete, worktree remove, stash drop, or strategy/risk change.

**Verified source baseline:** GitHub `origin/main` = `c215060999fc57489b11de8a8e2e2651bd2f79e1` (merge of #1223 after #1224/#1222). Exact-head CI on that SHA: `tests` SUCCESS, CodeQL Analyze (python/actions) SUCCESS, Push-on-main SUCCESS (GitHub Actions run list, this session).

**This cloud checkout:** branch starts from that SHA; working tree clean; one worktree (`/workspace` → `main` then this docs branch); **no** local stashes. Mac local worktrees / stashes / untracked debris = **UNVERIFIED** (Mac unavailable).

---

## 1. Polygon dependency (deadline 2026-10-18)

### Active surfaces (code + config)

| Surface | Role | Classification |
|---|---|---|
| `research/six_micro_daily_tsmom_collect.py` + `ops/systemd/six-micro-daily-tsmom.{timer,service}` + `/root/afs-shared/six-micro-polygon.env` | Only **scheduled runtime** Polygon consumer documented on box (checkpoint 2026-10-11) | ACTIVE_RESEARCH |
| `sources/polygon_client.py` | Shared read-only futures bar client | ACTIVE_RESEARCH / REPLAY_REQUIRED |
| `research/six_micro_daily_tsmom_paper.py` | Paper ledger for six-micro trial | ACTIVE_RESEARCH |
| Historical builders (`scripts/polygon_to_replay.py`, structural-level / prereg929 / options quote probe, etc.) | Offline corpus / study tools | REPLAY_REQUIRED / HISTORICAL_EVIDENCE |
| Local `data/replay_polygon*` / `data/replay_corpus_*` | Operator **RETAIN** 2026-10-11 (~1.7 GB) for fill-sensitive reruns | REPLAY_REQUIRED |
| Options Massive/Polygon historical entitlement | Prior evidence path; not a live order feed | HISTORICAL_EVIDENCE / COMPATIBILITY_ONLY |

### Six-micro collector — unique evidence?

- **Prereg / ledger:** `T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01` is **RUNNING**; first eligible session **2026-10-12**; gate **40** combined round-turns; paper-only; Polygon-dated fronts for MNQ/MES/M2K; listed-front for MGC/MCL/MBT.
- **Unique by design:** six-root daily TSMOM paper journal under `logs/six-micro-daily-tsmom/` — not the MNQ wide-stop Track B path.
- **Useful / proven this session:** **UNVERIFIED.** This environment has no authorized VPS journal read. Checkpoint claimed timer enabled and smoke warmup before 2026-10-12; scorable value after first sessions is not proven here.
- **Recommendation:** Treat six-micro as **retirement candidate if Oct 18 box journals show no path to unique scorable round-turns** (empty/warmup-only, persistent `FRONT_MISMATCH` / missing bars without recoverable fills, or operator waives the trial). Do **not** replace Polygon with another paid feed. Preserve journals + prereg/ledger as HISTORICAL_EVIDENCE. Stopping the timer/env requires **explicit operator GO** (not done by this audit).

### Main MNQ forward trial vs Polygon

- Track B / `E-2026-10-09-4hr-mnq-400-forward-01` uses the **wide-stop paper/demo** path (Tradovate demo + completed-5m IOC collector). Grep of `context/wide_stop*.py` / `webhook/runner.py` found **no** Polygon client usage.
- **Verdict:** the main MNQ 400-tick forward trial is **independent of the Polygon subscription**. Retiring six-micro / Polygon does not by itself stop Track B.

### Retirement plan (approval-gated; do not execute yet)

1. **Read-only box proof** of `six-micro-daily-tsmom.timer` state, pin SHA, and journal files under `/root/afs-shared/logs/six-micro-daily-tsmom/` (counts, first/last session, any completed round-turns).
2. If evidence is unproven / not worth the paid key: operator GO to `systemctl disable --now six-micro-daily-tsmom.timer` only; leave release tree and journals intact; append ledger disposition when scoring is formally ABORTED/NOT_RUN or closed.
3. Keep `POLYGON_API_KEY` only if offline replay rebuilds still need live fetches; otherwise leave env file in place unused until a later delete GO.
4. **Do not** buy a substitute data vendor for this trial. **Do not** global-promote six-micro SHA onto the futures-bot symlink.

---

## 2. Documentation consolidation

### Current open PR census (this session)

- **22 open PRs** (19 draft, 3 ready: #1154, #1140, #1037).
- Exact-head CI on `main` `c215060` green (see above).
- Branch protection API details: **403** to this integration — protection existence **UNVERIFIED** beyond observed required CI behavior on pushes/PRs.

### Reconcile #1214 and #1225 (reuse, do not duplicate)

| PR | State | Problem | Disposition recommendation |
|---|---|---|---|
| **#1214** `docs/futures-handoff-cleanup-20261010` | draft, **dirty** vs `main` | START HERE still claims Oct 10 `572cd13` / open #1215; would **regress** current checkpoint if merged as-is. Useful unique content: five net-edge gates into `futures-operator-todo.md`; safer deferred-removal classification (remove unsafe bulk-delete script). | **CLOSE unmerged after** those useful pieces are copied forward (this audit). Do not merge the stale START HERE. |
| **#1225** `docs/options-research-closure-20261011` | draft, **dirty** | Overlaps #1224 (already merged) on options handoff/next-actions/`agent-work-state`. Unique durable artifact: `docs/research-notes/options-edge-discovery-close-2026-10-11.md` (Polygon 5m closeout; **absent on `main`**). Rewriting `#1222` Alpaca table into Polygon framing would conflict with merged #1224. | **CLOSE unmerged after** closeout note + short authority pointers land (this audit). Prefer pointers over replacing #1224 text. |
| **#1201** | draft docs-only | Five gates already reused into operator TODO via #1214 content | **CANDIDATE CLOSE** after reviewer confirms supersession |
| **#1203 / #1204** | draft docs | Stale deployment-status snapshots vs later checkpoints | **KEEP/REVIEW** — do not mass-close without owner check |
| **#1220** | draft, conflict | Inventory/research evidence handoff | Separate research lane — not this cleanup |

**This docs branch** is the controlled place for mechanically proven stale SHA/PR/task corrections that #1214/#1225 no longer cover. **Do not merge without operator GO.**

### Authority map (unchanged)

- Repo contract: `AGENTS.md`
- Futures strategy status: `docs/strategy-rules/Strategy_Inventory.md`
- Options current state: `docs/options-current-state-handoff.md`
- Trial history: `docs/research-trial-ledger.jsonl`
- Agent coordination only: `docs/agent-work-state.md`

---

## 3. Branch / repository cleanup (classify only)

### `ops/post-cap-shadow-report-20261005`

| Fact | Evidence |
|---|---|
| Tip | `b862c7946421cd4e21e96015490af42bb7d82aff` |
| Unique vs `main` | **3 commits**, +859 lines: `scripts/post_cap_shadow_report.py`, `tests/test_post_cap_shadow_report.py`, `docs/post-cap-shadow-observation-2026-10-05.md` |
| Ancestry | **Not** ancestor of `main` |
| PR | #1135 **CLOSED** unmerged (“SUPERSEDED”) |
| Superseder | #1137 **MERGED** landed `post_cap_eligibility_*` (different module names); **no** `post_cap_shadow_*` blobs on `main` |
| Worktree | None in this cloud checkout |

**Classification:** **ARCHIVE** (unique scripts never landed; superseded conceptually by #1137). Before any remote delete: push annotated `archive/ops-post-cap-shadow-report-20261005-<date>` tag peels to `b862c794…`, then operator GO. Until tagged+deleted, do **not** edit `docs/BRANCH_ARCHIVE_INDEX.md`.

### Other meaningful candidates (remote; no deletes)

| Candidate | Classification | Notes |
|---|---|---|
| Open PR heads (#994–#1225 set) | PRESERVE / ACTIVE WIP | Delete remote only after PR merged/closed + auto-delete or archive tag |
| `release/*` | PRESERVE | VPS/rollback provenance; operator ruling required |
| `research/cpi-shock-fade-shadow-20260924` | PRESERVE | PARKED AFS-0032 |
| `audit/options-daily-cap-shadow-20261005` (#1140 open) | PRESERVE | Docs audit still open |
| Closed/superseded research remotes (many) | DELETE CANDIDATE after Phase 2 tag workflow | Follow `BRANCH_ARCHIVE_INDEX.md`; no ad-hoc mass delete |
| Mac worktrees listed in deferred tracker | NEEDS OPERATOR DECISION / Mac | UNVERIFIED this session |
| SSH-material stash (Mac) | PRESERVE | Explicitly protected; never drop without operator GO |
| This cloud stash list | empty | N/A |

---

## 4. Security / verification

### Issue #1184

- GitHub issue state: **CLOSED** (`completed`) 2026-10-08 after operator **DEFER** of additional infrastructure.
- Unresolved requirements remain tracked:
  - Draft **#1199** offline fail-closed authority-history verifier (UNMERGED; no durable store / no human allowlist).
  - `docs/options-current-state-handoff.md` and `docs/options-next-actions.md` still mark #1184 as mandatory before persistence/runtime use of authority.
- **Do not** treat issue closure as “security done.” Gate is **PARKED until real authority use is proposed**.

### What this audit did **not** do

- No deploy, restart, timer change, env edit, merge, branch delete, worktree remove, stash drop, strategy/detector/execution/broker/risk change.

---

## 5. Smallest next operator action

**Approve or reject six-micro / Polygon retirement after one authorized read-only journal census** (or explicitly waive the trial), then close stale docs PRs #1214/#1225 once this correction PR’s content is accepted — still without merging unless separately authorized.
