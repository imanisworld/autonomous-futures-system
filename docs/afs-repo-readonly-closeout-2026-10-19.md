# AFS repo-source closeout + VPS evidence handoff (2026-10-19/20)

**Verdict: AUDIT ONLY / SOURCE WORK IN DRAFT / VPS HOLD.**

Scope: a bounded GitHub snapshot, not a live or broker verification. GitHub `main` at this review is `4db26a112ea5c2f045d9853ed6819b22f6b9f1a7`. Read the actual box before making claims about deployed releases, collector state, watchdog delivery, security or profitability. The unmanifested Python-bytecode release-integrity incident has **no independently verified resolution**. **Never deploy, build, promote, repin, restart, toggle a timer, mutate a database, or cut over Discord** under this plan. Source-only PR creation grants no merge approval. Even after proven integrity restoration, runtime change requires a separate explicit operator GO.

## Completed in source (do not redo)

- #1231 removed retired companion-daily census expectation; #1232 reduced false recovery notifications; #1233 merged a staged Discord handoff. Last known merged squash heads `852b014`, `3354d3e`, `6ffb151`.
- #1234 merged a strictly local gross lifetime/options-by-epoch journal report; #1235 added source/provenance classification and descriptive per-ticker/cohort comparisons. Last known squash heads `c35ae8d` and `4db26a1`.
- The per-ticker error after #1235 (unstamped futures rows could still show clean ticker dollars) has a separate **draft-only** fix in #1236. Merge and deployment are separate decisions.
- Existing options daily, cash/equity and diagnostics reports should be **reused**, not replaced. Research and sealed forward gates remain untouched.

## Open PR inventory — original 23, plus this work's two draft PRs

The original review had **23 open PRs**. A subsequent GitHub check found **25** after creating source-only draft #1236 (ticker-label fix) and #1237 (this docs closeout); neither of those two has been merged or independently approved.

**Categories:** KEEP/HOLD = distinct source or review value, no merge authority; CONFLICT/RECONCILE = overlapping documentation or differing verdicts; DEFER = deliberate unapproved expansion/privileged operation; CLOSE CANDIDATE = operator may archive after preserving unique evidence and confirming no active dependency. No PR has been closed, rebased, merged or deleted by this audit. Titles and file footprints were checked, but the full semantic diffs have not all received independent code review.

| PR | Disposition | Evidence / minimal decision |
|---|---|---|
| #1230 Polygon 66-symbol cross-check | **KEEP / REVIEW** | Contains unique frozen Polygon cross-check artifacts and a negative result; conflicts in interpretation with earlier #1222 research summary. Verify method/source and approve scientific conclusion separately before touching authoritative verdicts. |
| #1226 hygiene + Polygon retirement | **CONFLICT / RECONCILE** | Edits agent state, futures/options handoffs, operator command guide, hygiene tracker and trial ledger. Large overlap with #1225, #1220 and this checkpoint; preserve unique commands/research first, do not merge as-is. |
| #1225 options discovery closeout | **CONFLICT / RECONCILE** | Four docs overlap #1226/#1230 and existing options-next-actions. Candidate supersession after comparison, not asserted yet. |
| #1221 causal Signa observer reads | **KEEP / HOLD** | Observer-only source and tests; needs independent causal/as-of validation and exact-head CI. It must never become an unreviewed futures entry gate. |
| #1220 research state + TODO | **CONFLICT / RECONCILE** | Updates agent state, operator TODO, Strategy Inventory and research note; preserve unique evidence/verdicts before considering closure. |
| #1217 MNQ research seal guard | **KEEP / PRIORITY REVIEW** | Addresses actual previously reported post-June-28 MNQ sealed-data contamination risk. Independent exact-head review required; no sealed outcomes may be re-tested. |
| #1207 day/night session-capped reporting | **KEEP / HOLD** | Alters existing futures/options daily and weekly reporters; must reconcile overlapping metric authority and protect weekly unique fills/restarts before merging. |
| #1206 scanner memory maintenance | **DEFER / PRIVILEGED** | Installs permission/maintenance tooling and root-approved MemoryMax mutation; not part of read-only evidence work. Risky scope overlaps #1205. |
| #1205 restricted Cursor memory maintenance | **DEFER / PRIVILEGED** | Overlapping MemoryMax maintenance proposal; avoid creating two competing privileged paths. Separate operator security decision needed. |
| #1204 #1198 source/proof handoff | **CONFLICT / RECONCILE** | Two docs touch futures handoff and deployment-plan authority; overlap #1203 and later source-only checkpoint. |
| #1203 VPS proof requirements | **CONFLICT / RECONCILE** | Two docs touch operator TODO and deployment plan; overlap #1204/#1197. Do not stack stale deploy plans. |
| #1201 net-edge objective in TODO | **CLOSE CANDIDATE (CONDITIONAL)** | Very small docs-only objective; verify the same objective is already explicit in current Strategy Inventory and this TODO before closing. |
| #1199 offline authority-history | **KEEP / HOLD** | Distinct security verification concept, but no verified approver store or approved runtime authority use; no runtime wiring. |
| #1197 Cursor VPS readiness audit | **CLOSE CANDIDATE (CONDITIONAL)** | Dated Oct 8 read-only findings; archive if all nonduplicate evidence is preserved and updated VPS truth obtained; historical record remains accessible. |
| #1154 options forward-proof readiness | **KEEP / BLOCKED** | Large stacked proof/readiness implementation, prerequisite #1150 and scanner source-to-runtime proofs must be separately reconciled. No Phase 2 automation activation. |
| #1140 options daily-cap audit | **CLOSE CANDIDATE (CONDITIONAL)** | Documentation-only dated cap audit; archive only after verifying unique facts already preserved in options authority. |
| #1139 prospective path-v0.2 capture | **KEEP / BLOCKED** | Dormant collector/timer/manifest path; no eligible start, no install or seal activation. |
| #1071 options epoch/filter-reason auditor | **KEEP / HOLD** | Unique filter-reason counterfactual analysis. #1234 reused epoch boundaries but did not replace the reason-specific audit. Requires rebase/source reconciliation and separate review. |
| #1069 SPXW paper lane | **DEFER / EXPANSION** | Separate SPXW/0DTE lane and lifecycle; no verified current need or forward prerequisites. Do not enable/merge by default. |
| #1067 66-symbol scanner universe | **DEFER / EXPANSION** | Scanner runtime coverage expansion; the existing 66-symbol offline research screen is **not** deployed watchlist approval. |
| #1037 public exposure closeout | **KEEP / CONTAINMENT HOLD** | Distinct public-repo operational exposure/governance question. No confirmed-secret claim and no history rewrite without separate authority. |
| #1026 read-only scanner audit wrappers | **KEEP / HOLD** | Uninstalled operator scripts. Needs caller/security comparison to existing read-only tools and any on-box authorization; not automatically safe to install. |
| #994 MNQ ORB Stage-A exploratory | **CLOSE CANDIDATE (CONDITIONAL)** | Frozen negative Stage A/selection-correction result; keep frozen artifacts/trial ID before archiving abandoned implementation. No Stage B earned. |

**Concrete resolution order:** review high-priority source safety #1217, then one conflict review for #1230/#1225/#1226/#1220; park privileged maintenance and new options instruments. Do not spend deployment time on PR count reduction.

## Reporter coverage and non-interchangeable authority

| Existing source / tool | What it measures | Does **not** prove | Overlap / action |
|---|---|---|---|
| `ops/lifetime_performance_report.py` | Offline, exact futures order-ID joined actual-price gross per ticker/change; options ACTIVE gross per underlying/epoch, source/provenance warnings | Authentic complete archive, broker-realized net, fees, historical buy-only guarantee or real account equity | Canonical **private snapshot gross accounting** after historical source census; #1236 correctness fix pending |
| `ops/shadow_daily_pnl_report.py` | One-day hypothetical SHADOW_OUTCOME by strategy/market; defined MNQ/MES adverse fill/cost assumptions; final late-row pass | Tradable broker fills; real cash/account P&L | Preserve daily shadow context; never sum its hypothetical dollars with lifetime paper figures |
| `ops/options_daily_pnl_report.py` | Daily ACTIVE scanner statuses and recorded `pnl_dollars`, plus cumulative convenience figure, blocks and alerts | Repriced financial correctness, full prices, commissions, or completeness: current `pnl_dollars or 0` hides missing values | Cross-check counts with lifetime; label this summary convenience only pending reprice |
| `scripts/weekly_review.py` | Weekly approved/outcome/fill/cancel/win statistics, reported dollars, errors/breakers/restarts | Complete scanner ACTIVE option ledger: it reads old `options_companion` table; journal totals not independently repriced | **Do not delete.** Historical unique weekly fills/breakers/restarts require retention/reconciliation; October 9/16 posts previously reported failures, delivery unverified |
| `scripts/paper_collection_report.py` | EOD/EOW row counts for futures journals and scanner SQLite, collector census, coverage/freshness, independent futures/options Discord destinations | Option financial outcomes or fee-adjusted P&L (mostly counts), identity/epoch verification | Keep as health/coverage authority; reconcile weekly and collector frequencies |
| `ops/evidence_report.py` | Per-lane/strategy/instrument evidence inventory and claim classifications | Time-proven broker trade ledger or comprehensive lifetime P&L | Preserve for why-no-trade/evidence counts, separate from finance |
| `alert_ranker/account_equity.py` | Options V1 cash/mark-to-market scenario equity and drawdown with explicit starting balances | Actual broker equity or fees for a real account | Run only against coherent copy, label **scenario**; do not rebrand as verified dollars |
| `alert_ranker/v1_diagnostics.py` | Options source row diagnostics and fee/slippage stress scenarios | Actual broker fee schedule, independent full-population costs | Preserve as sensitivity/why-loss explanations, not canonical realized net |
| PR #1207 (draft) | Proposed day/night session cap in three reporters | A validated merged reporting policy | Review overlap/authority; no opportunistic merge |

**Session/day boundary:** `ops/shadow_daily_pnl_report.py` groups by `candidate_day` and may read resolved rows from later UTC journal files; `ops/options_daily_pnl_report.py` groups option opens/closes by `America/New_York` and `resolved_at`. Treat their same-labeled calendar dates and day/night categories as different populations until UTC/ET/DST normalization and unique trade/episode identity are proved. The still-draft #1207 must not silently combine them.

**Actual coverage discrepancies:** The weekly review's option loader queries retired `options_companion` rather than `options_shadow_journal`; `options_daily_pnl_report.py` coerces missing P&L to zero in its all-time/daily summaries. Neither historical channel is an authoritative lifetime profit figure. Existing weekly health/errors data have no complete drop-in replacement; do not disable a failed sender until those fields and delivery receipts are preserved.

## Source-only release-integrity review

- `ops/release_integrity.py` checks hashed manifest files, fingerprint pin consistency, unexpected `__pycache__` and risky first-party root files, import trees derived from the manifest, and unrecorded venv site hooks. Its explicit state is FAIL or UNPINNED if requirements fail, rather than success.
- `ops/release_ci_proof.py` requires exact commit SHA, latest successful `github-actions` checks and re-fetching live CI before promoting. `scripts/atomic_release.sh` provides build/verify/promote separation, `PYTHONDONTWRITEBYTECODE=1` in key release commands, release-history and watcher preflight, and fake-box tests in `tests/test_atomic_release_script.py`.
- **Potential source coverage blind spot needing targeted regression before calling it a confirmed defect:** `_runtime_extras` only recursively scans fixed or manifest-derived import roots. An entirely new *unlisted* top-level Python package with `__init__.py` would not be scanned as a directory by the root-only file loop. Test on a synthetic fake release that such a package is refused; if not, propose the smallest safe directory-selection fix after assessing allowed top-level dirs, without touching deployed release trees.
- **Runtime incident status remains UNVERIFIED.** The report of repeated unexpected Python bytecode needs read-only evidence of path, timestamps, hash/source, running interpreter, effective unit `PYTHONDONTWRITEBYTECODE`/`-B`, cron/one-shot tasks, scanner and watcher paths, service restarts, and journal integrity. No production file deletion or automatic cache cleanup is authorized. Source safeguards do not prove an installed unit uses them.

## One focused private VPS evidence packet (for Claude/Cursor)

Use authorized **read-only access** only; protect credentials, environment values, account IDs, strategy returns, private journals and unsealed results. The snapshot needs these six numbered records, each with as-of UTC, source type, version and explicit PASS/FAIL/UNKNOWN. **No raw secrets/P&L in GitHub or Discord.** Do not run ad hoc Python on a release tree unless safe no-bytecode posture is verified (e.g. explicit `PYTHONDONTWRITEBYTECODE=1`), and even a read-only script must not modify archives or services.

1. **Release identity + integrity:** canonical live symlink/current and previous hashes, read-only release-manifest check result, all extra/missing/mismatched files, offending .pyc metadata, effective watcher/daemon units (redacted), env fingerprint pins and lock status, drift alarms since last clean day. A new release, repin, permission change or restart is forbidden.
2. **Futures financial population:** all original `journal_YYYY-MM-DD.jsonl` files, UTC first/last event dates, missing days/rotations, checksum/path provenance, duplicates, `paper_order_id` entry/exit join, actual fill prices, dated contracts, account mode/provenance and known commissions. Confirm every approved decision and terminal state has a reconciled disposition. Keep shadow/outcomes separate and account-specific source authority explicit.
3. **Options financial population:** consistent read-only scanner SQLite snapshot **including WAL**, `options_shadow_journal` ACTIVE vs COUNTERFACTUAL and unknown lanes, contract/underlying, timestamp/id cohort boundaries and installed epoch release, opening BUY vs SELL vs unstamped, ask-in/bid-out executable price and quantity, missing/terminal exits, options costs. Compare independently recomputed amounts to existing scanner `pnl_dollars`; a mismatch blocks completion.
4. **Forward evidence and broker safety:** exact collector service/timer state and release pin, Track B/futures and options observations by recorded and valid session, 1m vs 5m identity/parity, contract max, orders/positions, demo/live-off and daily loss/session gating. **Do not inspect sealed MNQ/MGC outcomes**, reset starts, or backfill. Missing evidence stays UNKNOWN.
5. **Monitoring and Discord:** effective watcher and collectors, health/why-no-trade logs, weekly-review artifact and post receipts, forward-proof service failures and intended disabled-timer behavior, options DB-lock error severity, alert and critical fallback routes, delivery IDs rather than just stdout `sent`. Reconcile Oct 19 watcher 1,692-vs-1,630 discrepancy; no channel migration.
6. **Readiness decision:** blockers each with owner, evidence source, smallest corrective PR/operational action, independent verification and rollback criteria. State exactly what can be concluded today; default **HOLD**. Any desired runtime mutation goes into a **new** explicit operator-GO request after integrity is restored; this packet authorizes none.

**Definition of done:** one reviewed GitHub correctness/docs result and one trustworthy historical/runtime evidence snapshot with a finite discrepancy ledger. No new strategy, broker route, database, collector, or dashboard needed. No proof, no run.
