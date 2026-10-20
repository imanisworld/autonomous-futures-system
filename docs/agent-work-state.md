# Agent Work State

## START HERE — source checkpoint 2026-10-19/20 (GitHub only; runtime HOLD)

**GitHub `main` verified:** `4db26a112ea5c2f045d9853ed6819b22f6b9f1a7`, following approved squash merges #1231, #1232, #1233, #1234 and #1235. This source checkpoint records no VPS read, no installed-release update, no broker action and no global redeploy. The previously reported **unexpected bytecode / release-manifest integrity alarm remains UNRESOLVED until independently verified on the VPS.** No deploy, promote, build, service restart, timer/cron/collector change, or Discord cutover under this checkpoint. The operator's separate explicit GO is required after integrity proof.

| Workstream | Latest source state | Real next gate |
|---|---|---|
| Runtime / release | GitHub source ahead of box; last **reported** futures symlink `94a71bd` from Oct 11, **not re-read** | Read-only manifest/bytecode incident root-cause and independent integrity restoration proof; then separate release GO |
| Notifications | #1231/#1232 source-merged, #1233 handoff docs-merged; no VPS changes proven | Read-only weekly/collector/Discord receipts and destination review; no service/channel cutover |
| Lifetime money | #1234 and #1235 merged, source-only gross reporter; **historical coverage, net fees, account mode and options side unverified** | Read-only complete journal+SQLite/WAL census and price/order/release reconciliation; truthful incomplete labels |
| Ticker label correctness | Draft focused source fix (see GitHub PR #1236); not merged | Exact-head CI, independent review and separate operator merge approval |
| Futures proof | 4HR MNQ promising but unproven; paper 5m IOC vs historical 1m trigger mismatch persists | Existing forward evidence/DEM0 parity and risk proof; respect sealed MNQ data |
| Options proof | V1 purchased-premium model in source, no independently verified profitable option P&L; 66-symbol screening is research-only | Existing ACTIVE cohort ledger, exit marks, direction and costs; do not launch a new trial |
| Deferred PRs | 23 original PRs inventoried; GitHub now shows 25 including new unmerged #1236/#1237 drafts | Per-PR independent review/classification; no auto-close, merge or delete |

**Source-only exit:** PR #1236 is pending independent review; the operator requested a separate GitHub hygiene/coverage/source audit and VPS evidence handoff, without broad rewrites. Maintain `docs/strategy-rules/Strategy_Inventory.md` and `docs/research-trial-ledger.jsonl` as authoritative strategy/trial sources. Do not infer deployed mode or profitable edge from source CI. Compact open-PR and report inventories are in `docs/afs-repo-readonly-closeout-2026-10-19.md` (draft until reviewed).

**Do not follow the dated October 11 NEXT list below.** It is preserved solely as historical box/source provenance.

## Historical checkpoint — 2026-10-11 (archived in place; NOT current instructions)

Read **only** this block + `AGENTS.md` unless drift is detected. Private numeric handoff: ChatGPT Library `/Futures Research/Futures Research Handoff - 2026-10-10.md`.

| Item | Value |
|---|---|
| **GitHub `main` (source, 2026-10-11)** | **`4d563332757a28ccf39928634101bab34044f2cc`** (merged options research-only #1222; NR7/Keltner earlier at `75015af`/`a10668e`). Most recent *reported* `futures-bot` deployed symlink still **`94a71bd`** (#1216); **not re-read for this docs update**. No global promote/restart. |
| **Merged on `main` (Oct 10)** | **#1208** research reference fills · **#1209** actual bracket R:R · **#1210** combined QA · **#1211** timeframe integrity helper · **#1216** 400-tick paper + 300-tick demo cap. Independent breaker sign-off on merged heads is **not re-run here**. |
| **Recorded branch** | `research/4hr-mnq-400-forward-rebase-20261010` @ **`94a71bd`**. Remote branch **deleted** after merge. Local branch remains. Its tree **equals** `58d4cd6`. |
| **Deployed release** | **`94a71bd`** — symlink **`/root/autonomous-futures-system` → `/root/afs-releases/94a71bd…`**; **`EXPECTED_LIVE_COMMIT=94a71bd…`** (**match YES**, re-read 2026-10-11 post-install). **Do not `futures-bot` restart** without drift proof. Side release **`f8d4845…`** built+verified on box for six-micro only (not live symlink). |
| **Box read (2026-10-11)** | **`futures-bot` active** (same PID epoch since 2026-10-10 promote; **`NRestarts=0`**); posture pins present (**`LIVE_TRADING_ENABLED=false`**, demo, cap **1**, wide-stop + 5m + 1m observer — values not re-printed); **no deploy lock**; **:8000** `/health` OK. **Six-micro:** **`afs-paper`**, **`six-micro-polygon.env`**, pin **`f8d4845…`**, **`--verify-sha` PASS**; **`six-micro-daily-tsmom.timer` enabled** — next **Mon 2026-10-12 17:10 America/New_York** (21:10 UTC). Journal **`/root/afs-shared/logs/six-micro-daily-tsmom/`** (install smoke run wrote warmup lines; scorable from **2026-10-12** per prereg). Account flat / Monday journal gates **still open**. |
| **Verdict** | **HOLD.** Live trading off, Tradovate demo, 1 contract, account flat (checkpoint claim), demo stop cap **300**, paper `wide_stop_4k` **400** on **`main`**, rollback pointer `c44d32bc`. Wide-stop demo lane was **already armed** at promote. Operator GO required for any further runtime action. |
| **DO NOT deploy** | `2f4f776`, any second promote of #1216, or redeploy “to pick up” #1208–#1211 (already on `main` at `58d4cd6`). Do not roll back unless separate operator GO. |
| **Track B / E-2026-10-09** | Spec **`docs/research-experiment-specs/E-2026-10-09-4hr-mnq-400-forward-01.json`** is **APPROVED**; first eligible session **2026-10-12**. **`main` matches the 400-tick paper cell** (#1216); box SHA **`94a71bd`**/**`58d4cd6`** if symlink proof holds. See **`docs/futures-track-b-forward-readiness-2026-10-10.md`**. |
| **Daily tsmom / AFS-0256** | **#1218 MERGED** **`f8d4845`**. Operator **2026-10-11:** code matches reviewed **`e510dc4`** (docs-only delta) — **release good**. **TIMER ON — operator GO** (builder): `systemctl enable --now six-micro-daily-tsmom.timer`; weekdays **17:10 America/New_York**; pin **`f8d4845`** in `10-release.conf`; **no `futures-bot` restart**. Prior box read reported timer enabled — **re-verify after GO** (unverified this turn). Trial **`T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01`**: **RUNNING**; scorable from **2026-10-12**; **40** round-turn gate. |
| **Open futures PRs (not HOLD deploy)** | **#1217** (draft) MNQ seal guard · **#1219** (draft) six-micro paper cleanup · **#1214** (draft) handoff docs |
| **Public resume** | `docs/futures-research-resume-checkpoint-2026-10-10.md` (+ Cursor + Claude options appendices) |
| **Options ETF research** | Remote **`claude/options-time-exit-research-20261010`** **deleted 2026-10-11** (operator GO; contained trade files). Not on `main`. |
| **Options edge screen / #1222** | **SOURCE MERGED / QA PASS** at `4d56333` (2026-10-11): 66 names / 110 sessions, pullback + failed-break **KEEP FOR RESEARCH**, compression **REJECT**. No approved options-profitability trial; no deployment. Details: `docs/options-current-state-handoff.md` and `docs/research-notes/options-underlying-edge-2026-10-11/`. 1-2-2 SIP underlying reconciliation done (443 separate rows), **101 entry quotes / no option exits**; prospective exit spec design only. **No further strategy research.** |
| **Saved 4HR benchmark** | `docs/research-notes/4hr-mnq-400-historical-benchmark-2026-10-10/` (on **`main`** via #1216) |
| **Saved ORB/VWAP sweeps** | `docs/research-notes/orb-vwap-tf-window-sweep-2026-10-10/` (on **`main`** via #1216) |
| **Vault NR7 + Keltner exploratory** | **`T-2026-10-11-exploratory-nr7-keltner-capital-unconstrained-01`** — closed **RESEARCH_ONLY** / **FAIL**; artifacts `docs/research-notes/capital-unconstrained-strategy-research-2026-10-11/` + ledger evidence dir |
| **Forward 4HR start** | **2026-10-12** — forward fills **not collected** yet; requires read-only box proof before counting (see **NEXT**) |
| **Step 4 parity doc** | `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` |
| **Track B readiness** | `docs/futures-track-b-forward-readiness-2026-10-10.md` (§2 updated 2026-10-11; §4.3 box gates still apply) |
| **Deferred cleanup list** | `docs/repo-hygiene-deferred-removal-tracker-2026-10-10.md` (classify only; delete at end) |
| **Unreviewed later branch** | **`fix/mnq-seal-research-guard-20261010`** / **#1217** — on origin, **not** in this HOLD |

**DO NOT REDO:** gzip 4HR cell reproduction; ORB/VWAP TF×window sweeps (baseline + trending + orb48); Grok PASS work on merged **#1208**/**#1209**; Sep decompositions listed below; **NR7ID + Keltner KC-long** capital-unconstrained exploratory (`T-2026-10-11-exploratory-nr7-keltner-capital-unconstrained-01`).

**OPTIONS STOP:** #1222 QA + merge complete; do not re-run the 66-name screen or start an options trial. New strategy research is HOLD; Options Paper V1 current-epoch accounting is a separate unresolved workstream. **NEXT (futures only):** (1) **Builder/VPS:** confirm **`six-micro-daily-tsmom.timer`** enabled+active after operator **TIMER ON** GO (**unverified** from Mac this turn). (2) **2026-10-12 ~17:17 ET:** operator Track B check; **17:10 ET** first scheduled six-micro collect. (3) **2026-10-19:** Polygon check (operator). (4) Wide-stop live symlink stays **`94a71bd`** — no global promote to **`f8d4845`**.

Prior chat context for this session is **closed**. Section below is coordination archive unless stale.

## Latest futures research coordination — 2026-10-10 (source-only; no trading authority)

**Operator sequence:** **(2) assess rules → (3) choose lead → (1) finish justified correctness fixes → (4) prepare DEMO → (5) independent forward/DEMO proof only after gates and explicit operator GO.** This is a work order, **not** approval to merge, deploy, activate a broker, reset a paper/observer epoch, inspect blinded P&L, or loosen global risk rules. Do not tell the operator work will run in the background.

**DONE / DO NOT REDO (existing evidence):**
- The Sep 7 decomposed signal-vs-fill-vs-bracket-vs-risk waterfall, its ablations, the Sep 18 causal 4HR pre-armed touch A/B, the Sep 8 wide-stop 300t paper amendment, the Sep 20 4H 2→2 treatment, and the Oct 4 corrected 3-2-2 EOD rerun are **already completed**. Do not re-run these populations or create different default parameters to get greener historical results. See `docs/edge-decomposition-audit-2026-09-07.md`, `docs/4hr-prearmed-touch-ab-2026-09-18.md`, `docs/wide-stop-hypothetical-ledger-lane-amendment-2026-09-08.md`, `docs/4hr-prearmed-continuation-treatment-2026-09-20.md` and the trial ledger.
- The previously broken 4HR natural-1m observer was **repaired** and had a canonical evidence epoch independently documented as started **2026-10-04T21:30:05Z**, release `c44d32b`; do **not** rebuild it or backfill the invalid Sep 18–Oct 4 interval. Current runtime state/event count as of Oct 10 is **UNVERIFIED here**, because the Mac connector was offline. See `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`.

**Step 2 results / Step 3 lead (evidence-only, not a new Inventory verdict):**
- **Primary:** MNQ **4HR Re-Trigger — broad pre-armed trigger**, not the post-hoc 4H-2→2 treatment. The corrected pre-armed historical fills retain a positive net sign across chronological halves and adverse ticks, with a larger sample than 3-2-2. Risk stop cap and 2R global minimum exclude most trades; *this is not a justification to loosen account safety*. On **`main` after #1216**, the isolated `wide_stop_4k` **paper** cell is **400 ticks / 1.0 R:R / 8-tick completed-5m IOC** (demo orders capped at **300**), which is not identical to 1m pre-armed execution. Its valid IOC fill participation is low. Do not pretend this established 1m DEMO edge.
- **Comparator:** MNQ **60M 3-2-2 First Live**; corrected historical sample is smaller (32 terminal, no stop hits) and cannot be called validated; its preregistered forward 1m touch proof is still required.
- **Hold:** MGC wide is a **blinded** distinct prospective trial; Miyagi lacks sample/engine, MES 4HR historically failed, and the already rejected UTC-timeframe momentum/ORB/VWAP variants are **DO NOT REDO**. Do not change the authoritative Strategy Inventory or `research-trial-ledger.jsonl` without an actual registered state/approved decision. Full numeric findings are in the operator's private ChatGPT Library handoff `/Futures Research/Futures Research Handoff - 2026-10-10.md`, not for this public repository.

**Step 1 source fixes — MERGED to `main` 2026-10-10 (DO NOT re-merge):** #1208 strict next-executable-price research fill, #1209 actual-bracket R:R and protective stop direction, #1210 combined QA, #1211 explicit anchor and complete/cross-contract bar validation. Exact-head CI was green on merge heads; **independent breaker PASS on deployed tree is not recorded here.** #1211 remains a **new-study isolated helper**, not wired to the frozen MGC scorer. Method comparison: `docs/futures-research-resume-checkpoint-2026-10-10.md`.

**Step 4 DEMO parity report (2026-10-10):** `docs/futures-demo-step4-1m-vs-5m-ioc-parity-2026-10-10.md` — source-only. **Conclusion:** existing DEMO/paper wide-stop = **5m close IOC8**; primary historical lead = **1m pre-armed touch**; honest DEMO tests Track B unless/until approved wiring for Track C. VPS box check still **open**.

**Step 4 existing DEMO scaffolding / blockers:**
- A separate **guarded Tradovate DEMO** wide-stop route already exists in `context/wide_stop_execution.py` and an isolated paper ledger exists in `context/wide_stop_ledger_paper.py`; do not create a competing execution path. **VERIFIED SOURCE MISMATCH:** `webhook/runner.py` returns `ONE_MIN_CONTEXT` with `execution_reachable=False` before DecisionEngine/RiskEngine/broker for every eligible 1m alert; `context/wide_stop_execution.py::DEMO_ENTRY_EXECUTION_MODE` is `ioc_limit` with `FROZEN_MNQ_IOC_TICKS=8`, used as `BracketOrder.entry_execution_mode_override` by `context/wide_stop_demo_runtime_core.py`. The wide-stop collector is driven from completed 5m bars (`context/wide_stop_forward_collector.py`). Therefore the existing guarded DEMO order route is **NOT the 1m pre-armed trigger path**. Do not claim 1m historical pre-armed profits apply to this DEMO model. Before proposing any route change, obtain separate human approval + independent order-safety review; next task is source-only 1m-vs-5m parity/feasibility report, not order wiring. No new demo order is authorized.
- The existing forward 1m observer prereg `docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md` requires **10 distinct eligible natural trigger arm_keys**, **20 trading days**, and **2 calendar months** separately for 4HR and 3-2-2, zero causal/parity/dedupe/isolation violations. Synthetic tests are NOT eligible. The 4HR verified epoch began Oct 4; the calendar-month gate cannot have elapsed as of Oct 10, no matter how well software tests pass. Even passing this gate establishes mechanism parity only, not profitable strategy/DEMO authority.
- Remaining release readiness requires fresh restricted read-only VPS checks of deployed exact SHA, live-off/demo/one-contract pins, broker orders/positions, journal integrity, deploy lock, rollback and services. **Box not checked this turn.** See `docs/futures-operator-todo.md` and source deployment plan; do not duplicate the deployment audit or skip independent reviewer.

**Agent next tasks without duplication:**
- **Grok (research):** identify *unexamined* causal 4HR failure conditions or truly distinct strategy ideas; do not rerun closed grids or tune exhausted same-fold samples. Provide prereg hypothesis only; no orders or config edits.
- **Cursor (builder/test runner):** inspect the existing 4HR paper-vs-natural-1m DEMO route, then produce the **smallest source-only QA/parity change** that closes a demonstrated defect; respect existing observer/ledger names and frozen epochs. Run focused + exact-head full CI. Do not deploy.
- **Claude/Codex (independent breaker):** post-merge review of **`58d4cd6`** (#1208–#1211 + #1216) and the mechanism/DEMO path; attack stop-first, timestamp alignment, order-route isolation, gap stops, risk arithmetic, intrabar lookahead. Independent PASS/FAIL on **deployed** tree, not a fresh duplicate test suite.
- **ChatGPT/operator (lead):** reconcile verified agent reports by exact SHA, nominate one narrowly defined **approved/preregistered** new analysis only if genuinely unresolved, choose a viable position-risk/margin budget based on documented stops, and withhold DEMO progression until both profitability and mechanism proof are established.

**DONE criterion for this step:** Preserve state, proofs, blockers, and agent owners; don't restart full repo audit. **Current status: NOT DEMO-READY; no net-positive forward proof in this session.**

---

## Current coordination checkpoint — 2026-10-08

- **Repository:** GitHub source baseline at this handoff: `a74c6f8f93e744e93627c09be06a77b0e7e974f7` (#1202), including source-only #1198 adapter plus previously merged watcher/history guards. **Source SHA is not VPS release identity.**
- **Futures:** **Operator-supplied Claude box audit (not independently repeated here):** B1–B10 passed, deployed release unchanged at `c44d32bc`, demo account flat, no deploy lock, rollback pointer `489b55b`. No further general VPS box checks queued. Outstanding *decisions*, not claimed runtime failures: package downgrades in current lock, memory pressure (options-scanner reportedly 457 MB swapped), allow the existing `daily_22_5k` paper LONG to resolve, nominate exact candidate and obtain independent source/trading-path review. Build/promote authorization is separate; candidate remains **UNSET**.
- **Futures wrapper:** Prior **operator-reported, independently unverified** `afs-deploy` 51/51 fake-box tests and real `verify-live` PASS are historical claims tied to the reported version. Do not call them verified proof or transfer them to changes under #1189/#1194; exact-head testing and reviewer confirmation remain required.
- **Options:** #1186 producer stamp (part 1) and **#1198 read-only E1 adapter (part 2)** both source-MERGED; do not reopen the adapter implementation. #1184 issue **CLOSED**; offline-only #1199 **DRAFT / UNMERGED**, further authority-storage work deferred pending approved use and trusted humans. **Operator-supplied** box audit classified the Webull sandbox-paper adapter as execution-capable **but inactive**, with no running caller and both allow flags default off; shared `.env` lacks five named settings, service-specific env remains unverified. Existing options collector reported **PINNED** to `db9bc7e2`, but new producer/journal v0.2 audit/rollback is not complete. #1154 parked, #1167 audit-only. Options authority: `docs/options-current-state-handoff.md`.
- **Evidence/collector:** Companion-daily job last seen Sep 23; independent report suggests it was retired, not failed evidence collection. Root read-only confirmation still missing. Do not treat stale health census as a proven E1 or futures gap.
- **Next — OPTIONS:** Prioritize current-epoch paper P&L / actual-vs-structural outcomes using existing read-only data and diagnostics; preserve cohorts and do not claim edge. No new general VPS audit or broker path test needed for this accounting step. #1199 authority-security review is deferred unless deliberately resumed.

- **Next — FUTURES:** Decide package downgrades, memory pressure, and paper LONG disposition, then candidate nomination/review **separately**. Historical box proof is operator-supplied and must be freshness-checked at any later release GO, not endlessly rerun during source work. No runtime mutation authorized.

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

## NEXT

1. Finish this bounded repo cleanup without changing execution behavior.
2. Keep deployment separate; any build/verify/promote attempt requires fresh runtime proof plus explicit operator GO.
3. For options/research work, follow their lane authorities and preserve prospective evidence rules.
4. Do not create another general status/checkpoint document. Update this compact file or the lane authority instead.
