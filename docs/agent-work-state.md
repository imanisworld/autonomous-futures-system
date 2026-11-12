# Agent Work State

## START HERE — 2026-11-11 slice scoring flip

Evidence only. No slice was scored. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-slice-scoring-flip-ba8e` |
| **Base** | `834e8f52` (`#1278` merged). |
| **Flip** | `SLICE_SCORING_AUTHORIZED` is true. Each scored slice stamps `evaluator_path` and `evaluator_sha256`. Both attempt-01 artifacts produce `combine.attempt-01.json`. |
| **Evaluator file** | Unchanged from `834e8f52`. T14 is an import allowlist. |
| **Prereg lines** | Several wrapper citations in the registered prereg have drifted. The prereg bytes were not edited. |
| **Tests** | `python3 -B -m pytest -q`: 9275 passed, 8 skipped. |
| **NEXT** | Independent review. Do not score a real slice from this checkpoint. |

## OPS CHECKPOINT — 2026-11-11 SQLite backup / storage (read-only VPS; copy verified on encrypted Mac)

**Verdict: BACKUP COPY VERIFIED / VPS SNAPSHOT DELETION HOLD.** This is an operations checkpoint only; it does not change the current 3-2-2 trial, score authorization, trading status, or release-integrity gate. Detailed evidence and recovery prerequisites: [options scanner SQLite backup verification](ops/options-scanner-sqlite-backup-2026-11-11.md).

- **Verified:** VPS `df` showed about **1.55 GiB available** (96% `df` utilization). Four same-disk scanner snapshots total approximately **7.38 GiB**. The 2026-11-11 snapshot (2,069,614,592 bytes) was copied to a FileVault-enabled Mac at `~/AFS-Backups/options_scanner.2026-11-11.sqlite` with owner-only file permissions. Source and destination SHA-256 matched, and Mac read-only SQLite `quick_check(1)` and full `integrity_check` both returned `ok` (9 schema tables). The VPS source remained unchanged.
- **Unverified:** Completed Time Machine backup (only inclusion is verified); Hetzner-managed backups/snapshots; full application restore/recovery; formal snapshot retention approval; off-server copies of older snapshots or raw evidence. The VPS SQLite integrity attempts timed out; the Mac copy passed.
- **HOLD:** Do **not** delete/move/rotate VPS SQLite snapshots, the live database, the 7.31-GiB options-122 source evidence, or referenced releases. Three older scanner snapshots represent about **5.45 GiB potential** recovery, **not authorized reclaim**. No cleanup, service restart, collector change, deployment, or broker action is authorized by this record.
- **NEXT:** Confirm provider-backup coverage, required restore points/retention, and a non-production application-level recovery test; obtain a separate explicit operator decision for any narrow cleanup. No repeat full-server inventory without changed evidence.


## Previous — 2026-11-11 slice-success evaluator

Evidence only. No slice was scored. `SLICE_SCORING_AUTHORIZED` stays false. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-slice-evaluator-ba8e` |
| **Base** | `5cc08f59` (`#1277` merged). |
| **Trial** | `T-2026-11-11-prereg-322-afs0523-slice-success-2026-11-11-01` |
| **Module** | `research/afs0523_322_slice_evaluator.py`. Checks run INSUFFICIENT, FAIL, WAIT_UNRESOLVED, FAIL_DRAWDOWN, WAIT_CI, PASS. Nothing is labeled VALIDATED. |
| **Tests** | T1–T20 and N1. T16 reads the committed reproduction replay only. `python3 -B -m pytest -q`: 9274 passed, 8 skipped. |
| **NEXT** | Independent review. Do not flip `SLICE_SCORING_AUTHORIZED` from this checkpoint. |

## Previous — 2026-11-11 GATE_REVIEW recorded

Evidence only. The reproduction gate was not rerun. Nothing was scored. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-gate-review-record` |
| **Base** | `3cff609d` (`#1276` merged). |
| **Trial** | `T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01` |
| **Event** | `GATE_REVIEW` recorded at `2026-11-11T19:41:15Z` by `gate-review`. Not a GATE attempt. |
| **Artifact** | `gate_artifact_sha256` `41d76ab1ef549316eebe58b1c8f5878184f43a181bb2164802805974f6f54da8` |
| **Review** | `pr_head_sha` `a21ee24e277e53f1d0909ea583fa91b5bc4b4e82`. Imani GO, AFS-0624. PM recorded it on the box. |
| **Line** | sha256 including the trailing newline `d500a8be448e46de9ce97506aa7afe70cf71491552cc209e78a17f90dda04e31`. Main's ledger is a byte prefix. |
| **Tests** | `python3 -B -m pytest -q tests/test_afs0523_322_repro_wrapper.py tests/test_research_trial_ledger.py`: 76 passed. A temporary ledger with no `GATE_REVIEW` line refuses slice admission. |
| **NEXT** | Independent review of the ledger append. Do not score from this checkpoint. |

## Previous — 2026-11-11 strict bracket drop on research candidates, merged at 3cff609d

Evidence only. The reproduction gate was not rerun. No real slice was scored. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-strict-bracket-drop` |
| **Merged** | `#1276` at `3cff609d` |
| **Base** | `e5d3b780` (`#1274` merged). |
| **Scope** | `partition_invalid_gap_opens` drops every research candidate whose entry fails the strict bracket in `strategy/strat_322_first_live.py` lines 193-198. SHORT requires `target < entry_price < stop`. LONG requires `stop < entry_price < target`. `INVALID_ENTRY_BRACKET` is the label at line 200. Gap-open and non-gap-open drops are counted separately on the slice report, the slice artifact, and the SLICE event. `SLICE_SCORING_AUTHORIZED` stays false. |
| **Gate** | Not rerun. The committed replay still compares 306 records with 0 mismatches. Derived candidate count stays 34 (306/9). Recorded invalid-bracket drops stay 0, and the widened check drops none of those 34. Gaps stay 2025-01-09 and 2025-11-28, count 2. |
| **Tests** | `python3 -B -m pytest -q tests/test_afs0523_322_repro_wrapper.py tests/test_322_databento_bar_adapter.py tests/test_research_trial_ledger.py`: 106 passed. |
| **NEXT** | Merged. Do not score a real slice from this checkpoint. |

## Previous — 2026-11-11 holiday closures held out of slice candidates, merged at e5d3b780

Evidence only. The reproduction gate was not rerun. No real slice was scored. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-holiday-closed-unscored` |
| **Merged** | `#1274` at `e5d3b780` |
| **Scope** | On a slice window, only `expected_holiday_closures` rows with `kind` `closed` are unscored. Early-close rows stay on `EARLY_CLOSE_DISPOSITION`. The reproduction window does not add closure dates. `SLICE_SCORING_AUTHORIZED` stays false. |
| **Report** | The slice artifact and the SLICE event carry `holiday_closed_unscored` and `holiday_closed_unscored_count`. |
| **Gate** | Recorded gaps stay 2025-01-09 and 2025-11-28, count 2, 306 comparisons (34 candidates). |
| **Tests** | `python3 -B -m pytest -q tests/test_afs0523_322_repro_wrapper.py tests/test_322_databento_bar_adapter.py tests/test_research_trial_ledger.py`: 101 passed. |
| **NEXT** | Merged. Do not score a real slice from this checkpoint. |

## Previous — 2026-11-11 early-close slice classification, merged at e9e1d041

Evidence only. The reproduction gate was not rerun. No Databento slice was scored. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-early-close-slice-ba8e` |
| **Merged** | `#1272` at `e9e1d041` |
| **Scope** | Every day requires 07:00 ET. A missing 07:00-08:15 bar is `DATA_GAP`. A complete early-close day on a slice window is `EARLY_CLOSE` and `EARLY_CLOSE_DISPOSITION=exclude` holds it out of scoring. Jul 3 and Dec 24 are half-days only when that date is a weekday and not the observed closure. |
| **Gate labels** | Reproduction window still requires bars until 13:00 ET on every early close. Gaps stay 2025-01-09 and 2025-11-28, count 2. Slice halt is 13:15 ET for Thanksgiving Friday, Jul 3, and Dec 24, and 13:00 ET for Monday holidays and Juneteenth. |
| **Gate run** | Not rerun. The recorded PASS bytes are untouched. |
| **Tests** | `python3 -B -m pytest -q tests/test_322_databento_bar_adapter.py tests/test_afs0523_322_repro_wrapper.py`: 82 passed. |
| **NEXT** | Independent review. Do not merge from this checkpoint. Slice scoring is a separate change and stays unauthorized. |

## Previous — 2026-11-11 slice scoring, merged at f612049f

Evidence only. No slice was scored against real Databento data. The reproduction gate was not rerun. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/322-slice-scoring-ba8e` |
| **Merged** | `#1273` at `f612049f` |
| **Scope** | `run_declared_slices` can score one admitted slice. `SLICE_SCORING_AUTHORIZED` is false and is not flipped. Admission checks are unchanged. |
| **Artifact** | Each attempt writes its own slice artifact and replay ledger, never overwritten. Both sha256 values are on the artifact and on a `SLICE` ledger event. The artifact records git HEAD, a clean-tree check, and the wrapper file sha256. A dirty checkout is refused. |
| **Results** | Each slippage and model reports candidates, fills, resolved, unresolved with dates and reasons, and no-fills. P&L, win rate, and drawdown are resolved-only. No pass/fail criterion. |
| **Identity** | The artifact records the manifest sha256, both tree hashes, data_root, and MAX_CONTRACTS_HARD_CAP. A scoring exception still appends a SLICE event with null artifact and ledger hashes. |
| **Tests** | `python3 -B -m pytest -q tests/test_afs0523_322_repro_wrapper.py`: 59 passed. Synthetic fixtures only. |
| **NEXT** | Independent review. Do not set `SLICE_SCORING_AUTHORIZED` true from this checkpoint. Success criteria belong in a separate preregistration. |

## Previous — 2026-11-11 structural reproduction gate PASS record

Evidence only. The gate was not rerun from this checkout. Do not score older slices. Do not merge, deploy, or change execution from this note.

| Item | State |
|---|---|
| **Trial** | `T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01` |
| **Attempt** | First structural attempt. No `.attempt-NN` suffix. PASS at 11:05 ET. `recorded_at` `2026-11-11T16:05:35Z`. 306 of 306 comparisons, 0 mismatches. |
| **Bytes** | `reproduction-gate.json` sha256 `41d76ab1ef549316eebe58b1c8f5878184f43a181bb2164802805974f6f54da8`. `reproduction-replay-ledger.json` sha256 `6c6b0141e4936795db1dd19c87efb3a257684943d0e637d7c7143f52530ae573`. GATE line sha256 including its trailing newline `9e7d29978a0d1a1d006fc4e6dceaa1dc166bd88c395aebe0a3052a8b78f3b1a1`. |
| **Provenance** | `docs/research-evidence/<trial>/run/` holds `gate_run.log` `6e9c9a2a884fb19f1c97c12b80ba7b4169ef45891a042fbb66861a283cc13264`, `run_gate.py` `bbebf832c7fdf08155e1b67f550b2392c8201f00537faf0ff5a3414cca3efc02`, and `sha256sums.txt` `a4cd030543c83b6487fb03d2872eb109646575636fd06bfd40c423c00f449a3d`. |
| **Base** | Source `ac334f5eb73722b3f4f70ab058bd655dff916971`. |
| **NEXT** | Claude reviews this record. Do not merge from this checkpoint. Older slices stay blocked. |

## Previous — 2026-11-10 post-hoc structural trial

| Item | State |
|---|---|
| **Trial** | `T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01` is SUPERSEDED. The new trial is `T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01`. It was written after AFS-0586. `RUN_AUTHORIZED` is true. No scoring run. A run waits for Imani's typed GO after Claude reviews this change. |
| **Fixed** | AFS-0592 on this draft. The wrapper is bound to the structural trial and `RUN_AUTHORIZED` is false, so the superseded trial has no run path. `BAR_EVIDENCE.json` is pinned at `9bbd516d8c7a43b8c42d829723bc6f800b1dabe3f372ed8d20295f839017931d`. Close deltas are recomputed from the two committed closes. Every slice artifact carries `OLDER_SLICE_IOC_NOTE`. The prereg states the Easter fix moves the AFS-0586 data-gap count from 3 to 2 and lists the 2010-2023 Good Friday label shifts. The contract-cap tests use `monkeypatch.setenv`. 2026-03-06 is not refused; AFS-0594 excludes it as `REFERENCE_DEFECT`. |
| **Deferred** | Early-close pre-open 15-minute bars stay `DATA_GAP`. The adapter carries a TODO. Do not label them in this change. |
| **AFS-0594** | 2026-03-06 is excluded as `REFERENCE_DEFECT`, not refused. The 1-minute rows rebuild the Databento 5-minute bar. `MAR06_BAR_EVIDENCE.json` sha256 `408cebf65e72a4bf43685ad80b406b6bc6b18081c2c07658154c84e0a1ef00a7`. `MAR06_BAR_EVIDENCE.md` sha256 `6f970effb72bb573fe5bd5a38d4d45e14ae7e483a64bac204ef0605c7d080c15`. `RUN_AUTHORIZED` stays false. Do not run the gate. No `COMPLETED` line was added for the superseded trial. |
| **Scored bar** | A reference-defect exclusion stands only when the replay `trigger_bar_open/high/low/close` equals the pinned Databento bar. A close-delta day stands only when `trigger_bar_close` equals the pinned Databento close. Otherwise the structural comparison fails. `RUN_AUTHORIZED` stays false. |
| **Replay ledger** | Each gate attempt writes its replay ledger beside the gate artifact. The artifact and the GATE line record that file's path and SHA-256. An attempt that fails before a ledger exists records both as null. |
| **NEXT** | The first attempt is recorded in the 2026-11-11 section above. This block is the pre-run checkpoint. Do not merge from this checkpoint. |

## Previous — 2026-11-10 AFS-0586 reproduction gate rerun FAIL record

| Item | State |
|---|---|
| **Run** | AFS-0586, one authorized rerun at `000f43128764aedfc6a5702d9fb11847f8a58d1c` with `MAX_CONTRACTS_HARD_CAP=1`. Recorded FAIL. Not a GATE_REVIEW. Not retried. |
| **Result** | 306 comparisons, 12 mismatches, all honest-IOC `fill_price` and `net` on 2024-09-06 and 2026-06-11. Artifact sha256 `255f669c785a7396c773f6143ecf8b82afe7292cd012566cda879c880e0d1a0f`. GATE `recorded_at` `2026-11-10T01:29:32Z`. `prior_attempts` lists the AFS-0580 FAIL. |
| **Files** | AFS-0580 stays at `reproduction-gate.json` (`ede35c326057ba4fa39cebfeaad8851d97ad60df9de1c2ddedd9b888dcd1dfeb`). The rerun bytes are `reproduction-gate.attempt-02.json`. The GATE line still names the path the box wrote. It was not edited. |
| **Bars** | AFS-0588 CONFIRMED. Decision-bar close only: 2024-09-06 Polygon 18754.0 vs Databento 18754.75; 2026-06-11 28928.75 vs 28928.5. AFS-0590 CONSISTENT: the five Databento 1-minute bars inside each decision bar aggregate to that Databento 5-minute bar, and the close deltas stay +0.75 and -0.25. `BAR_EVIDENCE.md` sha256 `95f229625c0ec507df6d86fbe92bf1559900a4e92cdee154aab2c887ad974d95`. `BAR_EVIDENCE.json` sha256 `9bbd516d8c7a43b8c42d829723bc6f800b1dabe3f372ed8d20295f839017931d`. |
| **NEXT** | Do not score again from this record. A later change writes each attempt to its own file and preregisters the post-hoc structural trial. |

## Previous — 2026-11-09 AFS-0580 reproduction gate FAIL record

| Item | State |
|---|---|
| **Run** | AFS-0579 authorized one `run_reproduction_gate`. It ran once on the box at `5d97af25`. AFS-0580 is the FAIL. Not retried. |
| **Cause** | The process environment lacked `MAX_CONTRACTS_HARD_CAP=1`. Every bracket returned `NO_FILL` with `MAX_CONTRACTS_HARD_CAP is missing or empty; refusing to trade` (`config/settings.py`). |
| **Identity** | Matched the pin: manifest `6c9d5ab3c29a7aeade90e95c84e0128795afc1bdd19aa165e18d364760b7f43d`, 15m `461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339`, 5m `a08d909274fa5a06e9234b1c0975977d91a00b1f749c0560fd6d41859410c1e3`. |
| **Mismatches** | 1569 cells. All are exit fields. Zero mismatches on date, direction, trigger, stop, or target. |
| **NEXT** | Do not rerun this attempt. A separate change must refuse a missing cap before scoring. Do not merge from this checkpoint. |

## Previous — 2026-11-09 AFS-0523 preregistration (source only, not authorized to run)

| Item | State |
|---|---|
| **Branch** | `cursor/322-repro-comparison-oct4` |
| **Scope** | Preregistered wrapper and Databento bar adapter. Frozen harness and `check_repro()` are not edited. Reproduction gate compares all 306 Oct 4 outcome records. Pre-2019 NQ results are labeled MNQ-equivalent research, not executable historical MNQ. Break-even is one shared win rate per fill model and slippage. Gap-open days are scored at the 10AM open and counted. DATA_GAP days are not scored. |
| **Runtime** | Not exercised. No vendor download. `RUN_AUTHORIZED` is false. |
| **NEXT** | Claude reviews the preregistration before any new data runs. Do not merge from this checkpoint. Inventory wording is a separate docs branch. |

## START HERE — 2026-11-09 read-only audit ENTRY_FILL_MODEL pin

Source only. Do not merge, deploy, restart, or install from this checkpoint.

| Item | State |
|---|---|
| **Branch** | `cursor/audit-expose-entry-fill-model` |
| **Scope** | `ops/grok_audit_runtime_pins.py` adds `ENTRY_FILL_MODEL` to the read-only audit `runtime-pins` allowlist. Process value or `<unset>` (absent at exec, not after `.env` load), plus the `config/settings.py` fallback. QA AFS-0526 R1–R6 are in this branch. No fill-model default change and no trading-path change. |
| **Install** | Not done. Root steps, including `--expect-sha256` and the sha-checked rollback, are in the PR body. The live dispatcher is unchanged until Imani runs them. |
| **Clock test** | `#1261` froze that 212R clock on `main`. This branch now contains that commit. The earlier failure is not expected on this head. |
| **NEXT** | Independent review. Imani installs only after review. Do not merge from this checkpoint. |

## Previous — 2026-11-09 AFS handoff

Docs-only coordination record. Claude breaker/QA, verified by ChatGPT. Source `main` at `d703232b7c9df3bf2b942f1437235dc5488a0890`. This checkpoint does not deploy, restart, merge, change strategy inventory, change risk rules, or change fill-model code. The 2026-11-06 baseline-reset procedure below is unchanged and is not closed by this handoff.

| Item | State |
|---|---|
| **Pine repainting audit** | PASS. `tradingview/risksentinel_context.pine`: confirmed-bar alerts; HTF `request.security` uses `[1]`/`[2]` with `lookahead_on`; confirmed pivots/BOS. |
| **VWAP anchor** | Resolved. Pine and replay reset at the CME trading day. The `RTH VWAP` comment in `research/futures_non_strat_coverage.py` is misleading and research-only. |
| **Fill model** | `main` still defaults `ENTRY_FILL_MODEL=market`. Legacy market edge is a fill artifact per `docs/ioc-faithful-baseline-622d-2026-07-06.md`. Operator chose option (a): flip to honest IOC eventually. HOLD until a spec and a test classification exist. |
| **WIP fill-model branch** | `claude/honest-default-entry-fill-model` @ `6fb01c9` (`6fb01c935b7591dde5e9340351a16658b3ce79ee`) is WIP / DO NOT MERGE. It breaks ~34 tests and could change server paper behavior if `ENTRY_FILL_MODEL` is unset. |
| **strategy_permission_gate** | Default and all listed strategies are `SHADOW_ONLY` in `risk_rules.yaml` (source-level). |
| **Six-micro TSMOM** | RETIRED Oct 18 (ABORTED/NOT_RUN, DO NOT REDO). |
| **Trend R3 variant shadow** | NOT_ACTIVATED. Design/holdout results are not in the repo (UNVERIFIED). |
| **Re-baseline** | Already done (DO NOT REDO). Only 4HR Re-Trigger (MNQ) and 60M 3-2-2 are PROMISING BUT UNPROVEN. Both are blocked by `RISK_GATES_REMOVE_EDGE` (account cap, R:R floor). 60M 3-2-2 is 32-0 with zero stop hits and needs falsification. This file does not change `docs/strategy-rules/Strategy_Inventory.md`. |
| **UNVERIFIED** | Server env and effective `ENTRY_FILL_MODEL`; deployed runtime enforcement; Tradovate connection; preflight state. Claude's 8,988 pass / 30 fail was not independently reproduced. |
| **NEXT** | Cursor: spec and test classification only (no fix). Claude: falsify 60M 3-2-2 and 4HR concentration. Grok: independent review only. New strategy research: HOLD. Operator: confirm account size. Do not merge the WIP fill-model branch. |

## Merged — 2026-11-06 baseline-reset procedure

Source merged in #1257 at `d703232b`. Do not deploy, restart, reset a box, or place a demo order from this note.

| Item | State |
|---|---|
| **Branch** | `cursor/baseline-reset-procedure-6c72` (PR #1257), merged |
| **Scope** | Nonce claim and outcome appends hold an exclusive flock on the store lock file across the ledger read, the check, the append, and the fsync. |
| **Runbook** | `docs/ops/baseline_reset_runbook.md` |
| **Runtime** | Not exercised. No server was contacted. |
| **Tests** | `python -B -m pytest -q` after the nonce lock: 9018 passed, 8 skipped, exit 0. The 8-process spawn test then passed on its own in 27.61s. |
| **NEXT** | Operator GO is still required before any host command in the runbook. |

## grok-ops deploy lane (source only, PR #1258)

Code and docs only. Do not install the account, deploy, or restart from this change.

| Item | State |
|---|---|
| **Branch** | `cursor/grok-ops-deploy-lane-40d9` (PR #1258), merged with `main` at `8089073` (#1259 and #1261). Do not merge this PR or install from this note. |
| **Scope** | Restricted `grok-ops` forced-command user. Wrappers `cd /` and use `python3 -P`. Promote and baseline-reset pin `/root/afs-releases/<target-sha>` after `release-complete/<sha>` matches the manifest fingerprint and `repo.commit`, and the GO token fingerprint matches that digest. Promote, verify, and rollback refuse a git-less tree before the token is consumed. `scripts/atomic_release.sh`, `scripts/deploy_lock.sh`, and `baseline_reset.*` stay byte-identical to `main`. Install doc: `docs/grok-ops-deploy-lane.md`. |
| **Dependency** | `baseline-reset` calls `scripts/ops/baseline_reset.sh` from merged #1257 (`d703232b`). The lane argv is `reset --target-sha --release-fingerprint --ticket --approval --signature --status-file --shared-dir`. The approval and signature files are passed through from `/root/afs-maint/`. The lane does not sign them. A missing script still exits 78 and does not consume a token. |
| **Runtime** | Not installed. The October HOLD still governs the box. |
| **NEXT** | QA re-review of AFS-0528 (H1, M2, M3, L4) on this branch. Do not merge or install from this note. A git-less promote still needs a separate Imani-approved change to `scripts/atomic_release.sh`. |

## Previous — 2026-11-06 AFS-0500 submission-evidence follow-up

Paper/demo source only. Do not deploy, restart, or merge from an agent. Do not push `fix/order-state-durability`.

| Item | State |
|---|---|
| **Branch** | `fix/order-state-durability-followup` (PR #1256) |
| **Scope** | Empty `/order/list` must not clear AMBIGUOUS. A filled order is adopted only with a matching position and a working protective stop. Startup reconciliation runs with a broker before entries are accepted. |
| **Runtime** | Not exercised. The October HOLD below still governs the box. |
| **NEXT** | Grok reviews again after CI. Imani decides whether to merge. Do not set live trading and do not retry an unresolved client id. |

## Merged — 2026-11-06 options scanner SQLite lock fix

Source merged in #1255. Observer only. Do not deploy or restart from that merge.

| Item | State |
|---|---|
| **Branch** | `fix/options-db-lock-2026-11-06` |
| **Scope** | `ScanStorage` uses a 30s connect timeout, `busy_timeout=30000`, and WAL. Writes retry lock/busy errors. `resolve_open_candidates` isolates one candidate failure. |
| **Ops note** | WAL adds `-wal` and `-shm` beside `options_scanner.sqlite`. Those files stay with the data file, outside the release tree and integrity manifest. |
| **NEXT** | A later Ops GO is required before the running scanner uses this. |

## Merged — 2026-11-06 source-only Tradovate submission durability

Paper/demo source only. PR #1254 is merged at `0341f8be`. Do not deploy, restart, or redo that branch.

| Item | State |
|---|---|
| **Branch** | `fix/order-state-durability` |
| **Scope** | Durable pre-send AMBIGUOUS submissions and UNKNOWN / RECONCILE_REQUIRED instead of a local cancel when an entry or cancel cannot be read. `AFS_ORDER_SUBMISSION_DIR` or `LOG_DIR/order_submissions`. |
| **Runtime** | Not exercised. The October HOLD below still governs the box. |
| **NEXT** | Follow-up review is AFS-0500 on PR #1256. Do not set live trading and do not retry an unresolved client id. |

## Merged — 2026-11-05 source-only trend variant shadow (no deploy)

Source merged. `VARIANTS_START` stays unset. Paper/shadow forward tracking only. Runtime HOLD below is unchanged. Do not deploy, restart, enable a schedule, or redo the implementation.

| Item | State |
|---|---|
| **Branch** | `shadow/trend-variants-va-vb` |
| **Scope** | CONTROL_R3_v2 + V-A + V-B + V-C at two-sided alpha 0.0167. `VARIANTS_START` is unset, so the driver prints `NOT_ACTIVATED` and writes nothing. |
| **Frozen sources** | Byte-identical under `research/trend_r3_forward/` at commit `f2170a9dd1e674a7a868354f286610c976b1f6bc`. Do not edit those five files. |
| **Tracker** | `35f21644f7f9dff8ca6739071e8c9473fd9837cc` adds the four arms, tests, and the research-tree import exclusion. |
| **Authority** | Operator GO AFS-0480 / AFS-0485. QA AFS-0479 and AFS-0484. This file does not change strategy status. |

## Archived checkpoint — source `3964389` / operator VPS snapshot 2026-10-20; HOLD / NO RUN

Read this block and `AGENTS.md` before the dated archive. Adapted from the operator-provided `agent-work-state_2026-10-19.md` checkpoint and the 2026-10-20 Cursor read-only findings. **Repository facts were checked in GitHub; VPS observations are attributed to the operator's Cursor report and have NOT been independently verified here.** No private P&L totals are included in this checkpoint.

| Item | Current source / operator-reported state |
|---|---|
| **GitHub `main`** | Verified `3964389265cd2700d500ce26546a4b4fab0656f4`. Source-only #1236, #1238, #1239 and #1240 merged on Oct 20, following #1231–#1235. These merges do not establish any installed VPS change. |
| **Runtime release / integrity** | Cursor read-only report: futures release `94a71bd`, **integrity FAIL** due to exactly `scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc`. Cursor attributes creation to a 2026-10-11 read-only audit running live-release venv Python without `-B`; manifest-listed source reportedly stayed unchanged. **Cause reported by Cursor (AFS-0313), NOT independently verified**; integrity still FAIL as last reported, and independent post-recut PASS is required. |
| **Service restart risk** | Cursor reports `futures-bot` currently running in DEMO/live-off posture, no orders or positions; a restart could fail the enforced integrity gate. Do not restart or remove the stray file under this source-only work. |
| **Integrity repair plan** | **QA-passed v9 immutable recut of `94a71bd` this weekend**, subject to separate operator GO, followed by independent release-integrity verification and a full drift-gate pass. **DO NOT delete `__pycache__` or its `.pyc` in place** in the existing release. No deployment, build, promote, restart, repin, timer/service mutation or `apt upgrade` authorized by this documentation change. |
| **Other masked monitoring** | Cursor reports the drift gate stopped at the futures check and skipped later options-scanner/pin checks; manual checks passed separately. Weekly review Discord failures, scanner database locks and watcher swap pressure remain operational evidence tasks. #1231/#1232 are GitHub-only fixes; #1233 is a merged documentation handoff, not installed Discord consolidation. |
| **Lifetime finance** | Source-only reporters #1234/#1235 merged. Cursor reports original journals lack joinable order IDs and execution-mode stamps; **verified since-inception futures totals are UNKNOWN**. Options opening sides are unstamped; actual net-after-fee results UNKNOWN. Keep futures actual broker-path, hypothetical shadow and options ACTIVE evidence strictly separate. #1236 adds stricter per-ticker/CLOSE mode labels and is MERGED at `cf7f4be`. Grok independent review PASS is operator-reported (AFS-0316, AFS-0318 and subsequent re-review); reporter completeness remains distinct from verified broker/account financial history. |
| **Futures forward** | Cursor reports Track B bars flowing but forward ledger stale; independently count qualifying setups before claiming collection. 4HR/3-2-2 observer evidence accumulating but prereg gates not met. No source merge implies an edge. |
| **Six-micro TSMOM** | **RETIRED Oct 18 — ABORTED/NOT_RUN, 0/40**. Retirement is **DONE / DO NOT REDO** and is **not a strategy failure**. The disabled timer is consistent with retirement. **No operator decision is pending to restart it.** |
| **Options forward** | Options 122 forward proof remains blocked by its older installed collector pin and missing binding evidence. No repin, retroactive binding, data backfill or new scanner experiment. |
| **Source-only draft PRs** | #1236, #1238, #1239 and #1240 **MERGED (source only)**; #1237 **CLOSED WITHOUT MERGE**, superseded by #1240. New draft #1241 (top-level integrity scanner gap) is **BLOCKED FROM MERGE until after the weekend v9 immutable recut and independent integrity PASS**; draft #1242 accumulates drift-gate failures. Both new code PRs require exact-head CI and Grok independent review. #1239 is not approved for deployment before the weekend fix. |
| **Outstanding research / open PRs** | #1217 MNQ forward-seal guard remains on hold; #1226/#1225/#1230 competing hygiene/research docs require distinct review. Do not reopen sealed MNQ evidence or infer validated futures/options profitability. |
| **Verdict and next** | **HOLD / NO RUN**. Friday's read-only prep gate precedes any separate operator GO for the weekend **v9 immutable recut**. Independent verifier must confirm a clean recut and **complete gate traversal** before any release/restart discussion. #1241 may be reviewed source-only beforehand but must not merge before the recut. Do not delete `.pyc` in place. |

**AFS-0315 VPS access:** Cursor's use of the documented `ssh hetzner` **root read-only** precedent is **operator-accepted**; no dedicated Cursor VPS account exists. Never run Python in a release directory without `-B` (even when intending to read only). This approval does not authorize release mutation, cleanup, deployment, or restart.

**Follow-ups, not immediate edits:** verify live mode on the futures **CLOSE/OUTCOME** as well as entry (covered by separately reviewed #1236); source-only draft #1241 covers synthetic fake-release tests for new unlisted top-level Python package directories; do not merge it before the weekend v9 recut, and do not alter the installed integrity gate or manifests.

**Single checkpoint index:** `docs/afs-repo-readonly-closeout-2026-10-19.md` on merged main via #1240 contains the complete original 23-PR review table, reporting overlap matrix and private VPS evidence checklist. #1237 was closed without merge as superseded.

**Historical checkpoints below are retained as evidence, not current work orders.** The source SHA, demo/timer state and action list in the 2026-10-11 section are superseded by this attributed October 20 checkpoint.

## Archived checkpoint — 2026-10-11

### START HERE (2026-10-11, superseded) — source `4d56333` / #1222 merged; box checkpoint remains 2026-10-11 ~14:50Z (HOLD wide-stop)

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
