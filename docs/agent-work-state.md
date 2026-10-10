# Agent Work State

## SAVED — what worked (2026-10-10)

- **Authoritative saved copy:** `docs/research-evidence/T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01/historical-benchmark-saved-2026-10-10.md` (+ `.json`).
- **Cell:** MNQ `strat_4hr_retrigger`, stop ≤ 400 ticks, R:R ≥ 1.0, one contract, `wide_stop_4k` @ $4,000 — **36 resolved, +$3,076.72, PF 3.125**, both halves positive (artifact `scripts/edge_decomposition_audit_results_candidates.jsonl.gz`, dates 2024-07-09..2026-06-03 consumed).
- **Code that wires it:** branch `research/4hr-mnq-400-forward-20261009` @ **`0c5db3d`** (400-tick ledger, approved forward spec, 3-2-2 fill disabled). **Not pushed. Not deployed.**
- **DO NOT REDO:** gzip reproduction or this save unless the artifact hash or branch head for this lane changes.

## Trading-evidence reconciliation — 2026-10-09 (read-only)

- Retrieved, not installed. Read-only root copy of the production options DB and the small ledgers named below. No service, env, broker, order, or deploy action.
- Options DB: `/root/afs-shared/logs/options_scanner.sqlite`, 886,259,712 bytes, SHA-256 `edca35da4ee6b111b57e0f26f5a1a72a001f51b6bbf5ca4e79b9f7fefe0ff4a8`, `quick_check` ok. Local copy `private/trading-evidence-2026-10-09/options_scanner.sqlite` matches that hash. Journal 10,111 rows through `2026-10-07T19:46:45Z`. Marks 13,670 through `2026-10-08T18:21:45Z`.
- Scanner runtime at retrieval: release `47ae01ac`, PID `1695873`, `NRestarts=0`, `MemoryMax` 350MiB. Futures runtime: release symlink `c44d32bc`, PID `1851835`, `NRestarts=0`.
- Options V1-EPOCH-3 ACTIVE closes: 16 priced, 4 wins / 12 losses, recorded ask-to-bid **-$1,254**. Midpoint change **-$912.50**; entry+exit half-spreads **$341.50**. Fee/slippage overlays are assumptions. Zero OPEN. Oct 8–9 EOD rollups show scans and zero new shadow-journal rows.
- Futures Oct 4–9 production journals: 920 `NO_TRADE`, zero approved `TRADE` rows. Shadow outcomes in those files are hypothetical and net about **-$2,496** before commission. `forward_ab_2026_08_v1` control remains negative (**-$386.10** net, 6/53). `daily_22_5k` is still an open hypothetical LONG; ledger balance $3,613.54 from $5,000.
- **#1206 NB3b:** source fix is on draft head `1acd3009df27006ca3e1a2edc0463f945303f27a`. On-disk runtime bytes are compared to the intent before approval consumption. Not re-tested and not installed this session. DO NOT REDO the implementation unless a new review finds a hole. No install.
- DO NOT REDO: the September 29 snapshot pass, or this exact DB hash's ACTIVE accounting.
- NEXT: operator decision on whether any lane is worth a pre-registered prospective test. No strategy-status edit, no deploy, no memory install from this evidence.
- 2026-10-09 profitability read (no new query): do not retune current rules. Broken close-confirmed lanes stay off. 4HR MNQ and 3-2-2 stay parked under Option B+ until a pre-registered reopen gate is met. Options Epoch-3 stays advisory; the -$1,254 ask-to-bid loss is mostly midpoint (-$912.50), with $341.50 of half-spreads. No status edit.
- 2026-10-09 data census (local copy + corpora only; do not redo unless the DB hash or corpus dirs change): repo 2026-05-23→2026-10-09, 1,304 commits on `9a754ed`. Options DB 77,908 scans / 10,111 journal / 13,670 marks / 14,108 diagnostics. Futures local replay: MNQ+MES 621 five-minute sessions each (2024-07-29→2026-07-23) plus the Sep 23 cross-market store. Ledger: 12 trials since 2026-09-23. Pre-ledger attempt counts remain UNKNOWN.
- 2026-10-09 operator direction: the $1,500 account is not the constraint they want kept. Capital will be sized to the strategy. Evidence cell to fund, if they confirm allocation: 4HR MNQ, 400-tick / $200 cap, R:R ≥ 1.0, isolated ledger ≥ $4,000, 1 contract. That cell is historical and still PROMISING BUT UNPROVEN. It is not the active 300-tick / $150 paper lane. No status edit, no gate change, no enablement. 3-2-2 and Miyagi stay unfunded on evidence. NEXT: operator confirms the $4,000 isolated allocation, then one pre-registered forward spec for that 400-tick cell.
- 2026-10-09 rerun on the committed candidate artifact only (`scripts/edge_decomposition_audit_results_candidates.jsonl.gz`): 4HR MNQ stop≤400 and R:R≥1.0 reproduces 36 resolved, 20/16, +$3,076.72, PF 3.125, H1 +$1,433.86 / H2 +$1,642.86, 2024-07-09→2026-06-03, top-3 months 66.8%. The wired 300-tick cell is 32, +$1,901.14, PF 2.313, and its top-3 months exceed 100% of net. Other lanes in that artifact stay negative or are the known 3-2-2 one-loss bracket. No new strategy, no bar reload, no enablement.
- 2026-10-09 operator OK: draft written, not approved, not committed. Spec `E-2026-10-09-4hr-mnq-400-forward-01` status DRAFT. Trial `T-2026-10-09-prereg-4hr-mnq-400-forward-2026-10-09-01` ledger event PLANNED. Prereg `docs/prereg-4hr-mnq-400-forward-2026-10-09.md`. Schema linkage tests pass. Two ledger git-history tests fail until the prereg and ledger line are committed. No enablement, no risk edit, no deploy. NEXT: operator says commit, then a separate approval before any collection.
- 2026-10-10 verification of the two source fixes, current heads only. **#1208** head `58e6169c0b939f9fb2c6aabad95ee758fcabbde2`: tests run `38022504797` SUCCESS (3m51s), handoff and CodeQL SUCCESS. An earlier head `fdd45fd` failed `test_resolver_uses_genuine_paper_broker` because it edited hash-pinned `execution/paper_broker.py` (blob `c03052bc`, 35,771 bytes). The current head leaves that file at blob `80af10cc` and puts the strict fill in `ResearchReferencePaperBroker`. Default `entry_fill_model` stays `market`. Live `webhook/runner.py` still constructs `PaperBroker`, which rejects `market_at_reference`. **#1209** head `47d226221274b8f3c1734e1e7889bee9164c1048`: tests run `38022182595` SUCCESS (3m21s), handoff and CodeQL SUCCESS. `_check_rr_ratio` now requires both the claimed ratio and the ratio from entry/stop/target to meet `min_rr_ratio`. The 2.0 floor and the runner exemption are unchanged. Neither PR is merged or deployed. DO NOT REDO this diff read or these two CI runs unless the head SHA changes.
- 2026-10-10 local pytest, do not redo unless the heads move. #1208 `58e6169`: 8660 passed, 8 skipped, 1 failed (`test_watcher_preflight_blocks_release_mutation_for_untrusted_paths`, macOS `realpath` has no `-e`; Ubuntu CI on this SHA passed). #1209 `47d2262`: 8655 passed, 8 skipped, same single `realpath` failure. Local branch `research/4hr-mnq-400-forward-20261009`: the approved-spec allowlist now names the frozen 212c experiment and `E-2026-10-09-4hr-mnq-400-forward-01.json`, and execution of the 4HR spec stays blocked because it has no adapter. Targeted pytest: 179 passed, 1 skipped. `private/wt-capped` still fails `test_runtime_code_does_not_import_the_order_layer` on this checkout only. Monday demo candidate is this branch: MNQ 4HR only, one contract, 400-tick/$200 stop, R:R at least 1.0, isolated $4,000 ledger, daily-loss floor $400, New York session, IOC 8 ticks. 3-2-2 and Miyagi are shadow and cannot fill. The $1,500 book stays at 120 ticks and R:R 2.0, and its enabled set does not include 4HR. Not pushed, not deployed.
- 2026-10-10 ChatGPT QA branch, reviewed here, not merged. **#1210** head `47b330120d84bf10740a20f217fa9d02c52ae5bb` stacks #1208 and #1209. CI run `38023981540` SUCCESS: 8,688 passed, 8 skipped. New code beyond those two PRs: strict research fills force stop-first when a bar hits both stop and target; risk rejects a stop on the wrong side of entry even in runner mode. Canonical `paper_broker.py` hash stays `80af10cc`. This does not change the $1,500 stop cap, the 2.0 floor, or the scored profitability file. DO NOT REDO this read unless the head SHA changes. No merge, no deploy.
- Profitability rules already measured on the candidate gzip (do not rerun unless the artifact hash changes): the dollar losses are the written stop and target, not a session filter. Inverse ORB MNQ 50-tick stop and VWAP Hold ~30-tick stop are hit on the entry bar. ORB breakout and ORB reclaim MNQ book wins below the written target and losses past the written stop. Failed-breakdown median R:R is 0.62. The global MNQ 120-tick cap and minimum R:R 2.0 exclude the 4HR MNQ 400-tick / R:R≥1 cell. ORB reclaim MES is wrong-way at 30/60/120/EOD. NEXT: leave #1208 and #1209 unmerged. Do not retune the scored file. Forward collection for the local 400-tick spec is not authorized to start before 2026-10-12 and is not deployed.

## Local analysis checkpoint — 2026-10-08 (Codex)

- Task: reuse existing futures/options tools to assess profitability and loss attribution. Historical options analysis DONE; current-data refresh BLOCKED on authorized read-only inputs/access.
- Verified source main `bd5ea2436ce9b15c821ee557041deea2763ad11c`; checkout `docs/212c-1134-prep-checkpoint` at `9a754ed9292333215d1490c9383f2d860d44133a`. Prior local checkpoint was stale; this file now carries fetched coordination state plus this local entry. Prior bytes preserved privately.
- Reused #1071 auditor at `0dca986edbb975e75c7c086c8b53f3361fdc4c4f` and existing V1 diagnostics on the saved September 29 Epoch-3 snapshot; retained both time/ID boundaries. Report and exact input hashes: `private/profitability-review-2026-10-08/report.md` and companion JSON/driver. No new analytics system or experiment.
- Checks: DB quick_check PASS; saved cohort accounting exactly reproduced; five priced closes independently reconcile to executable quote arithmetic; source SHA-256 unchanged; 11 existing diagnostic/baseline tests PASS. Findings and reporting limitations are in the private report, not strategy-status updates.
- Futures: existing inventory unchanged versus refreshed main; reused closed historical findings. Current honest runtime baseline INCONCLUSIVE: no representative current production journals available. No closed trial rerun or blind/quarantine outcome read.
- Changes: private analysis artifacts and this checkpoint only. No commit/push, code-policy change, status change, runtime action, order or deployment.
- DO NOT REDO: historical diagnostic/accounting pass over this exact input hash. October 7 figures remain operator-supplied until reconciled.
- NEXT: user was asked for the authorized read-only snapshot or Codex-specific access path. Obtain current consistent options DB/marks/diagnostics plus Oct. 7 EOD artifact and source release/epoch provenance; obtain current futures production journals/epoch provenance. Refresh only eligible cohorts through existing tools. Root-only SSH aliases are not a Codex-specific read-only access path; no other agent identity used.

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
