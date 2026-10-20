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

- **Prereg / ledger:** `T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01` is recorded **RUNNING**; first eligible session **2026-10-12**; gate **40** combined round-turns; paper-only. Actual session collection and completed round-turn counts are **UNVERIFIED**. Polygon-dated fronts apply to MNQ/MES/M2K; listed-front to MGC/MCL/MBT.
- **Unique by design:** six-root daily TSMOM paper journal under `logs/six-micro-daily-tsmom/` — not the MNQ wide-stop Track B path.
- **Useful / proven this session (2026-10-13):** **UNVERIFIED** at the time. *Superseded by the 2026-10-19 census below.* This environment has no authorized VPS journal read. Checkpoint claimed timer enabled and smoke warmup before 2026-10-12; scorable value after first sessions is not proven here.
- **Recommendation:** Treat six-micro as **retirement candidate if Oct 18 box journals show no path to unique scorable round-turns** (empty/warmup-only, persistent `FRONT_MISMATCH` / missing bars without recoverable fills, or operator waives the trial). Do **not** replace Polygon with another paid feed. Preserve journals + prereg/ledger as HISTORICAL_EVIDENCE. Stopping the timer/env requires **explicit operator GO** (not done by this audit).

### Verified census — 2026-10-19 00:06Z (Sun 2026-10-18 20:06 ET)

Claude, through the fixed read-only `afs-ro` wrapper. No writes, restarts, timer or env changes. The service journal (`journalctl -u six-micro-daily-tsmom`) and the `10-release.conf` drop-in were **not** readable through the wrapper.

**Six-micro collector**

| Fact | Evidence |
|---|---|
| Timer | `six-micro-daily-tsmom.timer` fired every weekday at 21:10 UTC. Last run Fri 2026-10-16 21:10:08Z. Next run Mon 2026-10-19 21:10Z. |
| Service | `Result=success`, exit `0` on 10-12, 10-13, 10-14, 10-15 and 10-16 (`forward_lanes_daily_proof.jsonl`). |
| Code on the box | The journal has `FRONT_BAR_MISSING` rows. That kind exists only from `4f10d7d` (#1223) on, and not in `f8d4845`. So the timer runs `4f10d7d` or later. This is inferred from the journal, not from the drop-in. |
| Journal files | `round_turns.jsonl` 422 lines, sha256 `5b5eaa46…a80528`; `state.json` sha256 `5e7c8478…ab0de`. |
| Forward rows (from 2026-10-12) | 5 `OPEN` on 2026-10-12: MNQZ6, MESZ6, MCLX6 and MBTV6 long; M2KZ6 short. MGC: `FRONT_BAR_MISSING` 10-12..10-15. |
| Sessions stored | Closes through **2026-10-15**; `sessions_after=3` for every open position. The run on 10-16 appended the 10-15 row. So each 17:10 ET run records the previous session, one session behind (inferred). |
| Completed round-turns | **0 of 40.** `state.round_turns=0`. No P&L exists yet. |
| MGC | `FRONT_BAR_MISSING` on all **13** sessions from 2026-09-29 through 10-15. The frozen `listed_front` rule picks MGCV6, and Polygon has no RTH bar for it. In practice MGC is not part of the trial. |
| Roll / mismatch | No `FRONT_MISMATCH` or `ROLL_PRICE_MISSING` rows yet. The MBTV6 roll is still ahead. |
| Proof tool | `afs-forward-lanes-daily-proof` marks six-micro `passed:false` every day, and its unit is now `failed`. **This is a tool defect, not a collector failure.** `systemctl show -p Result,ExecMainStatus,… --value` prints values in systemd's own order, so `ops/forward_lanes_daily_proof.py::_oneshot_result` puts each value under the wrong label (`result="inactive"`, `state_change="success"`). |

**Track B (`E-2026-10-09-4hr-mnq-400-forward-01`)**

| Fact | Evidence |
|---|---|
| Collector runs | `tf1m/4hr_observation/state_2026-10-12..16.json` was written every session by `source: wide_stop_forward_v1` with `status: INVALIDATED` at 11:00 ET. That write happens inside `_process_five_min_bar_locked`, after the lane-active check (`context/wide_stop_forward_collector.py` @ `94a71bd`). |
| Feed | `tf5m/bars_MNQ_2026-10-12..16.jsonl` hold full sessions (~36–40 KB each). Bars are still arriving 2026-10-19 00:05Z. |
| Why there are 0 fills | The canonical 4HR setup was invalidated every session, so no candidate was produced. `wide_stop_4k` writes journal and state only on a candidate, so they stay at 2026-09-30 (the last candidate, rejected `ENTRY_DETACHED_FROM_PRICE`). |
| Pine vs backend | `futures-bot` logged `Pine bracket ignored: strategy mismatch (Pine='strat_4hr_retrigger' backend='vwap_hold')` on 10-12, 10-14, 10-15 and 10-16 (×3). This is recorded as a fact only. It was not diagnosed here, and it is not a Track B collection gap. |
| Polygon | The Track B path at `94a71bd` has no Polygon import. Cancelling Polygon does not affect Track B. |

**Other Polygon consumers:** six-micro is the only scheduled runtime consumer. `options_manager/adapters/polygon_historical.py` is manual-only and runs on no schedule. `execution/cross_instrument_evidence_quality.py` imports only the `front_contract` calendar helper, with no key and no fetch. Research pulls run from the Mac, and the 66-symbol Polygon 5m dataset is already stored locally.

### Decision — 2026-10-18: **CANCEL Polygon / RETIRE six-micro**

- **Useful evidence produced so far:** the collector works end to end (warm-up, signals, 5 paper entries at the frozen 09:30 opens). There is **no scorable outcome**: 0 round-turns and no P&L.
- **Still unknown:** whether daily 60/20 TSMOM on these micros is net positive. Nothing in the data so far points either way. The trial has not failed. It is incomplete.
- **Path to the gate:** at most one round-turn per root every ~21 sessions when the 20-session time exit binds. Signal flips make cycles shorter. With 5 working roots, 40 round-turns most likely arrive around **March–June 2027**, which is roughly **$1,000–1,600** more at $199/month. This is an estimate, not a measurement. The first exits cannot come before about 2026-11-09 unless a signal flips first.
- **Value of continuing:** low. It is a single-look paper-only test with no demo path. It is adjacent to the cross-market momentum ideas that failed in the 414-test Strategy Lab campaign, and one of its six roots structurally never fills. More Polygon data would only finish an unfinished experiment. It does not answer an open question that Track B or the inventory depends on.
- **Offline work:** none is needed to decide this. The `tf5m` feed keeps recording all six roots at no Polygon cost. Any later daily-momentum question would need a **new** preregistration on that data. That would be a different data source, so it cannot be treated as a continuation of this trial.
- **Why this is not a HOLD:** no single missing read-only fact would change the answer. Even a perfectly healthy collector leaves the gate months away at continuing cost.

**Approval-gated steps (none executed):** (1) operator GO: `systemctl disable --now six-micro-daily-tsmom.timer`. Leave journals, `six-micro-polygon.env` and the release tree untouched. (2) Operator cancels the Polygon subscription, after step 1 so the collector does not run against a dead key. (3) Operator GO to append the ledger disposition `ABORTED` / `NOT_RUN` (operator-withdrawn, 0 round-turns, not a FAIL). Keep the box journals as HISTORICAL_EVIDENCE, with the sha256 values above.

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

- **23 open PRs** (20 draft, 3 ready: #1154, #1140, #1037), including this hygiene PR #1226.
- Exact-head CI on `main` `c215060` green (see above).
- Branch protection API details: **403** to this integration — protection existence **UNVERIFIED** beyond observed required CI behavior on pushes/PRs.

### Reconcile #1214 and #1225 (reuse, do not duplicate)

| PR | State | Problem | Disposition recommendation |
|---|---|---|---|
| **#1214** `docs/futures-handoff-cleanup-20261010` | draft, **dirty** vs `main` | START HERE still claims Oct 10 `572cd13` / open #1215; would **regress** current checkpoint if merged as-is. Useful unique content: five net-edge gates into `futures-operator-todo.md`; safer deferred-removal classification (remove unsafe bulk-delete script). | **CLOSE unmerged after** those useful pieces are copied forward (this audit). Do not merge the stale START HERE. |
| **#1225** `docs/options-research-closure-20261011` | draft, **dirty** | Overlaps #1224 (already merged) on options handoff/next-actions/`agent-work-state`. Unique durable artifact: `docs/research-notes/options-edge-discovery-close-2026-10-11.md` (Polygon 5m closeout; **absent on `main`**). Rewriting `#1222` Alpaca table into Polygon framing would conflict with merged #1224. | **CLOSE unmerged after** closeout note + short authority pointers land (this audit). Prefer pointers over replacing #1224 text. |
| **#1201** | draft docs-only | Five gates already reused into operator TODO via #1214 content | **CANDIDATE CLOSE** after reviewer confirms supersession |
| **#1203 / #1204** | draft docs | Stale deployment-status snapshots vs later checkpoints | **KEEP/REVIEW** — do not mass-close without owner check |
| **#1220** | draft, **clean/mergeable** at `e0b525f6`; tests+handoff SUCCESS | Inventory/research evidence handoff | Separate research lane — independent review before any merge GO; no longer a conflict blocker |

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

## 5. Runtime-safety reconciliation after the source-only hygiene pass

A later same-day Cursor/Cloud Agent restricted read-only box check superseded the earlier "runtime unavailable" assumption for the futures lane; this docs review did not independently replay the VPS commands:

- Active futures release: `94a71bdbb975b53f21a81b7084482ea3c5270ceb`; service active; no restart drift observed.
- Live execution off; Tradovate DEMO; hard cap 1; account pin match; broker flat; no working orders; deploy lock absent.
- **Release integrity FAIL:** extra `scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc` exists inside the immutable release.
- Safety verdict therefore becomes **BLOCKED — SAFETY DRIFT**, not merely UNVERIFIED.
- Do **not** delete the bytecode file in place. `AGENTS.md` forbids editing `/root/afs-releases` directly. Any recovery must use the sanctioned immutable-release/deploy-lock path under separate operator authorization, then re-prove release integrity and runtime/broker/journal state.
- *(2026-10-19 update)* The six-micro and Track B read-only censuses are **done**: see §1 "Verified census". Track B is collecting, six-micro has 0/40 round-turns, and the recommendation is CANCEL / RETIRE. The integrity FAIL still blocks only mutations.

This safety finding does not change the branch/worktree/PR cleanup classifications above; it only changes operator ordering. Runtime integrity recovery comes before any deploy/restart or Polygon/timer mutation; authorized **read-only** Track B and six-micro collection checks may proceed in parallel.

---

## 6. Smallest next operator action

**Next, in parallel where authorized:** (a) confirm the reported integrity failure and choose a sanctioned immutable-release recovery with separate operator GO; never edit the release tree in place, and no deploy/restart until integrity PASS. (b) Obtain **read-only** six-micro timer/journal and Track B October 12 onward collection censuses. Decide Polygon GO/NO-GO by 2026-10-18 without treating inaccessible evidence as poor performance. No timer/env mutation without explicit GO.
