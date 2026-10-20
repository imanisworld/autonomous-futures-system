# AFS Discord notification consolidation — staged operator handoff (2026-10-19 ET)

**Decision:** Source-only cleanup now. The operator's standing release-integrity gate is an absolute blocker: **NO DEPLOYMENT, SERVICE RESTART, RELEASE PROMOTION, OR VPS CUTOVER until the existing deployed release-integrity violation has been fixed and independently verified.** GitHub source merges do not waive that gate. Claude may conduct read-only VPS investigation in the meantime. No trading, broker, risk, evidence collection, release, secret, or service mutation is authorized by this note.

## Evidence and authority

- Basis: **ChatGPT's October 19 notification audit summary**, covering reported repository/VPS/Discord observations for October 6–19, 2026. **The counts and runtime observations in that summary have not been independently checked against raw VPS logs or Discord delivery records for this document.** The summary names futures release `94a71bdbb975`, options-scanner directory `47ae01acdbed`, and remote repository main `a04bd05b54ff` as of that audit, not as current verified runtime state. Re-read source, box, and routes before any later VPS action.
- **Unverified notification counts from ChatGPT's audit:** the audit reported **1,692** successful watcher sends during October 6–19 and specified **1,141** memory recoveries, **325** memory warnings, and **164** critical-memory alerts. The named categories sum to **1,630**, leaving a **62-message discrepancy** relative to the claimed total. These three categories may be non-exhaustive, but their relationship to the total was not demonstrated. **Do not treat 1,692, 1,630, or individual category counts as independently verified until raw logs are reconciled, duplicate stdout is excluded, and delivery records are checked.**
- **Unverified audit finding / hard-stop blocker:** ChatGPT's audit reported eight daily drift alarms (October 12–19) concerning an unmanifested Python bytecode file under the deployed release. Preserve the drift detector. Investigate the writer, compare the deployed release to its signed/expected manifest, correct the cause, and independently prove integrity restored. **No deployment, promotion, or service restart before that proof.** No automatic reinstall/deletion.
- Observed: six scheduled failures (Oct 12–19) of `afs-forward-lanes-daily-proof`, with no proven independent Discord escalation. The disabled six-micro timer and options binding failure must be interpreted correctly before alerting or changing any collector.
- Observed: seven options-scanner database-lock exceptions without verified delivery of corresponding error notices. Confirm severity and existing routing rather than assuming these events did or did not interrupt collection.
- Observed: legacy `scripts.weekly_review` jobs ran Oct 9/16 and each reported `posted to Discord: False`; its options component reads the old companion ledger. Compare unique weekly metrics/artifacts before removing the installed cron.
- Observed: retired options companion daily cron absent; its old log last changed Sep 23. The collector census nevertheless monitors `companion_daily.log` as live.
- Observed: options scanner alerting through `OPTIONS_SCANNER_DISCORD_WEBHOOK_URL` and paper P&L/collection reporting are actively delivering. Observation cards are edited in place; old creation timestamps are not dead-route proof.
- Audit proof has gaps: direct production webhook credential inspection and exact route aliasing were not approved. **No channel/webhook permanent deletion** until verified with redacted destination IDs and active-service configuration.

## Target: three destination feeds (logical routes remain independent)

| Feed | Content | Must preserve |
|---|---|---|
| `ops-alerts` | Failure, critical safety/resource, broker/ingestion, missing report/collector, release integrity, necessary recovery | Independent feed/flatness/health checks, error and legacy fallback routes, critical warnings even when reports fail |
| `futures-evidence` | Futures decisions and paper/advisory outcomes, upserted observations, daily preliminary/final corrections, EOD/EOW collection health | Distinguish actual/demo decisions from observer/counterfactual evidence; no change to risk/order authority |
| `options-evidence` | Current scanner alerts, paper P&L, observation, coverage/collection EOD/EOW summaries | Preserve provenance, current scanner delivery, collection vs economic P&L distinction |

Reuse/rename existing Discord channels where possible. A three-feed target does **not** mean collapsing all required logical route names into one environment variable, disabling route validation, or combining bot risk/execution paths.

### Channels and reports

- **Retain until cutover proven:** `afs-watcher`, `afs-drift-gate`, `futures-signals`, `observation`, `observation-options`, `read-only-daily-pass`, `paper-collection`, `daily-report`, `paper-collection-options`, `afs-heart-beat`. Silence/volume improvements need first/last-mile proofs.
- **Historical/retirement candidates:** `options` (empty), `premarket` (empty), old `options-signals`, old `rh-scanner-options`, old options-category `error`, plus `welcome-channel` if operator confirms ownership. Do not touch unrelated Discord/community channels.
- **Retire failed legacy weekly *publication* only after preserving its useful unique futures metrics.** Source comparison: `scripts.weekly_review` computes actual journal OUTCOME fill/cancel counts, fill rate, win/loss counts, win rate, outcome dollars by instrument, and weekly server error/breaker/restart counts. `scripts.paper_collection_report` instead reports decision/shadow-observation counts and collection health, and `ops.shadow_daily_pnl_report` reports theoretical shadow economics. Neither is an interchangeable fill-rate/actual-outcome weekly replacement. Its options companion P&L is obsolete, and two recent Discord posts failed. Preserve its historical artifacts, choose a truthful current-journal weekly replacement or explicitly retain archive-only access to the unique fields, **then** retire the installed cron/post. Do not delete active options P&L.
- **Remove retired companion-daily census expectation**, not the companion's history and not live options collectors.
- **Cut repeat noise:** avoid WARNING/RECOVERED threshold flapping without weakening critical alerts; reduce unchanged weekend reports and shadow advisory deluges only once meaningful corrections are preserved.
- Preserve separately configured safety routes `DISCORD_ROUTE_ERROR`, `DISCORD_ROUTE_HEARTBEAT`, `DISCORD_ROUTE_SIGNAL`, `DISCORD_WEBHOOK_URL` and scanner/companion legacy variants as appropriate until every sender is mapped and tested. `config/notification_routes.yaml` is incomplete as an inventory: some senders resolve environment webhooks directly.

## Source-only work / review queue

1. [Merged PR #1231](https://github.com/imanisworld/autonomous-futures-system/pull/1231) (`852b0141` squash): retire obsolete `companion daily` census registration, regression test; **installed pinned paper-reporter unaffected**; re-pin blocked by release-integrity hard stop.
2. [Merged PR #1232](https://github.com/imanisworld/autonomous-futures-system/pull/1232) (`3354d3e1` squash): confirmation before dynamic/fixed memory WARNING recovery cards, tests; **watcher independently installed** and unaffected; service restart/update blocked by release-integrity hard stop.
3. Continue evaluation of `scripts.weekly_review` against active weekly paper-collection, shadow P&L, options P&L. No premature removal of a unique evidence field.
4. No forward-proof failure escalation change until latest proof JSON/service state distinguishes real breaks from disabled-by-design six-micro and source-binding mismatches; no automatic enabling of collectors.
5. No DB-lock alarm addition until errors are classified for actionable failure vs normal busy retry and existing message/health coverage is checked.

## Claude handoff — read-only now; all deployment/restart actions blocked by unresolved release-integrity violation

**Stage A — READ ONLY / no mutation (allowed while integrity is unresolved):**
- Establish exact authorized VPS identity, deployed source SHA, dedicated installed watcher/paper reporter hashes, live-safe settings, service health, and paper-only/no-order posture without printing credentials.
- Enumerate *all* active senders and actual webhook destination IDs using irreversible redacted fingerprints. Include systemd, cron, nested script fallbacks, `DISCORD_ROUTE_*`, legacy `DISCORD_WEBHOOK_URL`, `DISCORD_OPTIONS_*`, and `OPTIONS_SCANNER_DISCORD_WEBHOOK_URL`.
- Produce source -> effective env -> channel -> active schedule -> last successful delivery -> missing/failed delivery -> unique coverage map. Verify upsert edit activity by message edit times. Distinguish present code from installed activation.
- Independently confirm weekly-review field coverage, retired companion census source/pin, watcher memory warning recurrence and severity, integrity-alarm writer, forward-proof failing invariants, scanner lock exceptions and current escalation. Reconcile the claimed watcher counts: 1,141 + 325 + 164 = 1,630, versus claimed 1,692, with 62 unaccounted for by those labels. Confirm whether these are mutually exclusive categories and compare against raw send/delivery records.
- Output a precise no-execution cutover/rollback procedure and unresolved blockers; **STOP**.

**Stage B — FUTURE ONLY; BLOCKED until the release-integrity violation has been fixed and independently verified, with separate operator GO:**

- **Hard gate:** Claude MUST STOP before any deploy, promote, repin, service restart, cron/timer modification, webhook/env configuration mutation, or Discord cutover while the deployed release-integrity violation remains unresolved. An operator's approval of source PRs is **not** approval to bypass the integrity gate. Read-only inspection and documentation may proceed; report findings and wait.
- Stage B may be considered only after a reviewer confirms the integrity root cause and remediation from fresh VPS release-manifest/provenance checks and records explicit **INTEGRITY RESTORED** evidence. If the proof is missing, conflicting, or stale, the decision remains **WAIT / NO RUN**.
- After the integrity hard gate has passed and a new explicit operator GO is provided: require exact reviewed commits, passing targeted and required CI, approved source/installed pin diff, no active broker positions/orders when a service restart is proposed, and a proven rollback copy/mapping.
- Stage redacted webhook mapping to three destinations without deleting original channel history or changing trading/collection code. Preserve required routes and legacy safety fallbacks.
- Apply only the authorized reporter re-pin / watcher update / cron retirement / webhook configuration changes, one independent component at a time. Do not bundle with futures strategy release or options scanner release.
- Verify error/failure/recovery delivery and correct destination under controlled tests, including critical-memory, release-drift, broker/ingestion, feed-gap, collector outage, and final-versus-preliminary corrections. Ensure Discord outages cannot interrupt the system or block trading safety logic.
- Observe a complete weekday/weekend reporting cycle with real natural deliveries and compare artifact/event counts before and after. Check missed messages, webhook 2xx responses, fallbacks, service PIDs/restart counts and persistence of collection/evidence epochs.
- Only after verified coverage and operator approval: archive unused channel destinations, never delete evidence or Discord history, and document exact rollback commands and retained integrations.

## Acceptance criteria / definition of done

- Independently verified resolution of the deployed release-integrity violation **before** any service restart or deployment; while unresolved the project remains **WAIT / NO RUN**.
- Three comprehensible active Discord destination feeds; all critical logical routes verified end to end, including fallback routes.
- No false retired companion DEAD report. Historical artifacts still accessible.
- No repeat recovery flood attributable to single-check memory threshold flapping. Genuine recurring critical incidents remain visible and are investigated.
- Missing forward-proof and actionable scanner failures have observable durable records plus proven human-visible escalation; benign suppressed noise is explicitly documented.
- One daily and one weekly evidence summary per system, with retained signal granularity and any distinct provisional/final P&L corrections.
- Retired senders do not run, and every remaining scheduled sender has an owner, purpose and delivery receipt.
- One complete natural weekly reporting cycle of evidence, independently reviewed; no live execution, contract/risk change, strategy edit, or unsanctioned service change.

**Status:** WAIT / INTEGRITY BLOCKER. GitHub source PRs #1231 and #1232 were merged, but installed VPS services are unchanged; #1233 remains documentation-only. **No deploy, restart, or VPS cutover until the integrity problem is fixed and independently verified.** Counts above are attributed to ChatGPT's audit and remain unverified.
