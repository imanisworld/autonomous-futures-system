# Futures Current Status — 2026-09-22

This is the concise operator-facing source of truth for the futures system. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated current-status document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / GUARDED DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

Core rule remains: **No proof, no run.**

## Verified runtime — 2026-09-22 morning preflight

- futures service: active;
- PID: `1495829`;
- `NRestarts=0`;
- deployed release: `aba8324dee1a-20260922-000127`;
- release commit: `aba8324dee1ad67e4b6a9c97e12c22b11490c3e6`;
- release integrity: **OK, 1,465 files**;
- previous/rollback release: `3a917abb...`;
- deployment time recorded on the box: **2026-09-22 04:01:46 UTC**;
- operator approval for the deployment is recorded in the project conversation;
- production SQLite `quick_check=ok`;
- failed relevant systemd units: **0**;
- deployment lock: absent.

The deployment is accepted as the current authorized futures baseline. Do not treat the move from `3a917abb...` to `aba8324...` as unexplained drift.

## Why futures moved

PR **#909 — Fix MES 1-2-2 range-arm state leakage** merged as `aba8324dee1a...`.

Confirmed defect: the isolated MES 1-2-2 lane re-entered `webhook.runner.process_alert()` before the authoritative MES pass while both paths shared module-global range-arm state. The isolated lane could consume a fresh `RANGE_BREAK_CLOSE` and leave the authoritative evidence lane with only `RANGE_BREAK_CLOSE_REPEAT`.

Fix: the isolated MES 1-2-2 config copy pins `range_observe_enabled=False`, preventing that re-entrant paper/evidence lane from touching shared range-arm state. The parent/authoritative config remains unchanged.

Classification: **PAPER ONLY / evidence-isolation fix**. It does not promote a strategy, change range logic, loosen risk, or add live execution authority.

## Release-scope note

The active futures release is current `main` at the #909 merge and is **35 commits ahead** of the prior `3a917abb...` release. Therefore this was not a two-file curated #909-only release.

Review of that delta found:

- no `risk_rules.yaml` change;
- no strategy-directory change;
- futures-active changes include fail-closed live-preflight / broker-state / drift-guard hardening plus the #909 evidence-isolation fix;
- many other commits in the range are docs, research, options-only, or reporting work.

The release is the accepted baseline; future audits should compare from `aba8324...`, not from `3a917abb...`.

## Companion services / evidence lanes

### Options scanner

- PID: `1317855`;
- `NRestarts=0`;
- release: `a6f79d79702e32afcda44b230f22d418db66d35b`;
- release integrity: **OK, 1,464 files**;
- sources non-writable;
- health: healthy;
- posture: advisory/read-only;
- scheduler: running;
- Signa pull: enabled.

Natural market-hours proof of the #892 Signa request-budget/backoff/reuse behavior is still required. Do not manually hammer the provider.

### 1-2-2 prospective collector

- timer: enabled/active;
- exact release: `36e73f1981850b66b043d849ce877c15bd1ab3e7`;
- release integrity: **1,346 files verified**;
- last service result: successful;
- next scheduled run at the morning preflight: **13:00 UTC**.

The prior missed window remains lost and must not be backfilled. The next legitimate gate is natural RTH evidence under the frozen `122-IEX-E1` policy.

### Paper-collection reporter

Current curated reporter pin:

`b60931a6a9f8-reporter-6512dc3578e9`

#899 is deployed / smoke-proven via Discord API read-back. The reporter overlay changes only `scripts/paper_collection_report.py`; its three pinned `ops/` dependencies remain byte-identical to the prior proven pin. Visual client inspection was not required for the deployment ruling.

## Current evidence gates

1. **1-2-2:** wait for the natural RTH chain:
   `ARMED -> IEX reversal -> selector capture <=120s -> production replay parity -> delayed SIP reconciliation`.
2. **Signa #892:** wait for natural market-hours scanner traffic and verify shared 429 circuit, cooldown, snapshot reuse, and truthful provider-health telemetry.
3. **PR #875:** remains **Draft**. Its branch is stale relative to current `main`; do not merge from the old head. It must first satisfy its source-data/market-hours gate, be rebuilt/refreshed from current `main`, have the exact diff re-reviewed, and rerun CI. Webull submission remains blocked pending the separate sandbox round-trip lifecycle proof.

## Do not touch

- live execution;
- risk loosening;
- broker submission outside already guarded DEMO evidence routes;
- strategy promotion from paper results;
- 1-2-2 stop/target/runner tuning before evidence;
- Signa as trade authority;
- backfilling missed prospective evidence;
- broad rewrites merely to make the repo match an older release narrative.

## Source-of-truth chain

Use this order when resuming work:

1. `docs/futures-operator-reader.md` — where to look;
2. this file — concise current operator/runtime status;
3. `docs/futures-current-state-handoff.md` — long provenance/history;
4. `docs/strategy-rules/Strategy_Inventory.md` — strategy classifications;
5. the box — final authority for what is actually running.

Repository `main` by itself is never proof of deployed VPS state.
