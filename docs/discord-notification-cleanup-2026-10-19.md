# AFS Discord notification consolidation — staged operator handoff (2026-10-19 ET)

**Decision:** Source-only cleanup now, separately approved VPS/Discord cutover later. No trading, broker, risk, evidence collection, release, secret, or service mutation is authorized by this note.

## Evidence and authority

- Basis: read-only repository, VPS and Discord audit, Oct 6–19, 2026; current-state checks Oct 19 evening. The audit found the active futures release `94a71bdbb975`, options scanner directory `47ae01acdbed`, and remote repository main `a04bd05b54ff` **at the time of that audit**. Re-read all three before any later VPS action.
- Observed: 1,692 successful watcher sends (1,141 recovery, 325 WARNING, 164 critical-memory) in the 14-day period. These are send counts, not proof all incidents were benign.
- Observed: Oct 12–19: eight daily drift alarms concerning a real unmanifested Python bytecode file under the deployed release. Preserve the drift detector. Investigate the writer, release-state integrity and fix method separately; no automatic reinstall/deletion.
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
- **Retire failed legacy weekly *publication* only after proving unique weekly fields are present elsewhere or archived.** Do not erase old artifacts or disable the current options paper P&L report.
- **Remove retired companion-daily census expectation**, not the companion's history and not live options collectors.
- **Cut repeat noise:** avoid WARNING/RECOVERED threshold flapping without weakening critical alerts; reduce unchanged weekend reports and shadow advisory deluges only once meaningful corrections are preserved.
- Preserve separately configured safety routes `DISCORD_ROUTE_ERROR`, `DISCORD_ROUTE_HEARTBEAT`, `DISCORD_ROUTE_SIGNAL`, `DISCORD_WEBHOOK_URL` and scanner/companion legacy variants as appropriate until every sender is mapped and tested. `config/notification_routes.yaml` is incomplete as an inventory: some senders resolve environment webhooks directly.

## Source-only work / review queue

1. [Draft PR #1231](https://github.com/imanisworld/autonomous-futures-system/pull/1231): retire obsolete `companion daily` census registration, regression test; **installed pinned paper-reporter unaffected** until separate proof + repin.
2. [Draft PR #1232](https://github.com/imanisworld/autonomous-futures-system/pull/1232): confirmation before dynamic/fixed memory WARNING recovery cards, tests; **watcher independently installed** and unaffected until separately approved update.
3. Continue evaluation of `scripts.weekly_review` against active weekly paper-collection, shadow P&L, options P&L. No premature removal of a unique evidence field.
4. No forward-proof failure escalation change until latest proof JSON/service state distinguishes real breaks from disabled-by-design six-micro and source-binding mismatches; no automatic enabling of collectors.
5. No DB-lock alarm addition until errors are classified for actionable failure vs normal busy retry and existing message/health coverage is checked.

## Claude handoff — only after source PR review and operator GO

**Stage A — READ ONLY / no mutation:**
- Establish exact authorized VPS identity, deployed source SHA, dedicated installed watcher/paper reporter hashes, live-safe settings, service health, and paper-only/no-order posture without printing credentials.
- Enumerate *all* active senders and actual webhook destination IDs using irreversible redacted fingerprints. Include systemd, cron, nested script fallbacks, `DISCORD_ROUTE_*`, legacy `DISCORD_WEBHOOK_URL`, `DISCORD_OPTIONS_*`, and `OPTIONS_SCANNER_DISCORD_WEBHOOK_URL`.
- Produce source -> effective env -> channel -> active schedule -> last successful delivery -> missing/failed delivery -> unique coverage map. Verify upsert edit activity by message edit times. Distinguish present code from installed activation.
- Independently confirm weekly-review field coverage, retired companion census source/pin, watcher memory warning recurrence and severity, integrity-alarm writer, forward-proof failing invariants, scanner lock exceptions and current escalation.
- Output a precise no-execution cutover/rollback procedure and unresolved blockers; **STOP**.

**Stage B — controlled operations, separately approved:**
- Require exact reviewed commits, passing targeted and required CI, approved source/installed pin diff, no active broker positions/orders when a service restart is proposed, and a proven rollback copy/mapping.
- Stage redacted webhook mapping to three destinations without deleting original channel history or changing trading/collection code. Preserve required routes and legacy safety fallbacks.
- Apply only the authorized reporter re-pin / watcher update / cron retirement / webhook configuration changes, one independent component at a time. Do not bundle with futures strategy release or options scanner release.
- Verify error/failure/recovery delivery and correct destination under controlled tests, including critical-memory, release-drift, broker/ingestion, feed-gap, collector outage, and final-versus-preliminary corrections. Ensure Discord outages cannot interrupt the system or block trading safety logic.
- Observe a complete weekday/weekend reporting cycle with real natural deliveries and compare artifact/event counts before and after. Check missed messages, webhook 2xx responses, fallbacks, service PIDs/restart counts and persistence of collection/evidence epochs.
- Only after verified coverage and operator approval: archive unused channel destinations, never delete evidence or Discord history, and document exact rollback commands and retained integrations.

## Acceptance criteria / definition of done

- Three comprehensible active Discord destination feeds; all critical logical routes verified end to end, including fallback routes.
- No false retired companion DEAD report. Historical artifacts still accessible.
- No repeat recovery flood attributable to single-check memory threshold flapping. Genuine recurring critical incidents remain visible and are investigated.
- Missing forward-proof and actionable scanner failures have observable durable records plus proven human-visible escalation; benign suppressed noise is explicitly documented.
- One daily and one weekly evidence summary per system, with retained signal granularity and any distinct provisional/final P&L corrections.
- Retired senders do not run, and every remaining scheduled sender has an owner, purpose and delivery receipt.
- One complete natural weekly reporting cycle of evidence, independently reviewed; no live execution, contract/risk change, strategy edit, or unsanctioned service change.

**Status:** HANDOFF PREPARED / NO VPS ACTION. Source PRs are separately reviewable, not evidence of deployment.
