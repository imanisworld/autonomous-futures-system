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

## Research closure — MNQ sustained trend continuation v1

The frozen retrospective v1 screen is **CLOSED / WAIT**. At exact research head `cd50f423c77a87255f3b1b19160bb9566f902011`, coverage was complete (23,533/23,533 bars) and the run produced 36 fills / 34 terminal, +$576.18 net, PF 1.762, positive H1/H2, and $172.42 max drawdown. It failed the preregistered >=40 terminal-fill and PF >=1.94 gates. PR #912 remains the unmerged research record. Do not tune or rerun v1 on the same corpus; a materially different sustained-trend hypothesis requires a new preregistration.

### Offline evidence recovery / mechanism postmortem — 2026-09-22

**AUDIT ONLY. The frozen v1 gate result above is unchanged.** The original successful result JSON was recovered from the prior local scratch run, copied unchanged into isolated box storage, and verified against the frozen study/rules and corpus fingerprints (313 matched daily files per timeframe, 2025-07-24 through 2026-07-23, 23,533/23,533 causal coverage). Because the original aggregate report did not persist the trade/event ledger, one identity-verified reconstruction at the exact frozen head reproduced all ten saved summary sections before any new diagnostics were accepted. The recovered support ledger contains 1,815 detector events, 36 fills, 34 terminal trades, and 102 `STOP_CAP_REJECTED` events.

New diagnostics do **not** clear the preregistered gate. The same 36 setup identities remain positive under 2-tick (+$559.18, PF 1.7308; H1 +$397.04 / H2 +$162.14) and 3-tick (+$542.18, PF 1.7004; H1 +$383.54 / H2 +$158.64) adverse-entry stress, but PF remains below 1.94. Sixteen of 34 terminal trades reached the frozen 2R target; median MFE was about 1.759R. Winners tended toward larger arms and proportionally shallower pullbacks, while losers tended toward smaller arms and quicker triggers, but the distributions overlap substantially and do not establish an actionable filter. The 120-tick cap materially restricts participation (102 rejects versus 39 within-cap triggers; rejected median 228 ticks), but no rejected-trade counterfactual P&L was assigned and wider stops are **not** authorized.

Current classification: **PROMISING BUT UNPROVEN / WAIT; postmortem = POSSIBLE EDGE — MECHANISM UNCLEAR.** One discovery hypothesis is retained for a future untouched/prospective test only: smaller pullback depth relative to the sustained arm move may predict a higher probability of reaching the unchanged 2R target before the unchanged stop. This is not validated and is not authorized for implementation. Supporting evidence is preserved under `/root/afs-offline-912-recovery-20260922/`; that path is provenance only, never a runtime dependency.

The conditional #911 inverse follow-up was **not run**. A causal mirrored-SHORT comparator would require additional replay/research implementation beyond the cheap recovery pass, so #911 remains closed under its existing LONG results unless a separate research campaign is explicitly authorized.

## 2026-09-22 natural evidence update

### 1-2-2 prospective collector

The natural 13:00 UTC run and subsequent cycles exited successfully, but **today's evidence does not qualify**. Twelve new rows were all `COLLECTOR_ERROR` with `source_error:ReadTimeout`; there were 0 accepted setup rows, 0 denominator/rejected setup rows, and 0 SIP reconciliations. Classification: **HOLD — source availability blocked evidence collection**. Do not backfill, rerun, or tune the frozen `122-IEX-E1` policy to compensate.

### Signa #892 forward proof

Natural market-hours traffic produced 59 HTTP-200 snapshots and three scan `ReadTimeout` errors across successive checks. Context remained `observation_only=1` and `trade_authority=0`. No 429 was observed, so normal provider use is functioning, but the shared 429 cooldown path is **not yet naturally proven**. Do not force throttling merely to exercise it.

### Existing MNQ trend-family audit — #911

Frozen #911 audit at `e68e2da...` ran unchanged across all 313 canonical days. **No family cleared the preregistered research gate.**

- EMA pullback: 461 terminal, -$3,823.28, PF 0.8587; H2 negative.
- Impulse first pullback: 728 terminal, -$2,593.94, PF 0.8972; both halves negative.
- Strat 22 continuation: 758 terminal, +$3,530.16, PF 1.1065; H1 negative.
- Trend consolidation break: 484 terminal, -$4,765.82, PF 0.7487; both halves negative.

#911 is closed unmerged as **REJECT / no candidate**. #910 is also closed unmerged: its default-off paper cohort had no qualifying family to justify merge or activation. No session rescue, tuning, stop/target rewrite, or runtime integration is authorized from these results.

## Research update — MNQ combined portfolio audit #915

PR #915, branch `research/mnq-combined-portfolio-audit-20260922`, completed its combined-portfolio run at `5a9f14b`. Earlier scratch runs at `62c9207` and `e46cfd5` are superseded. The work is **UNIQUE / KEEP / AUDIT ONLY** and does not change runtime, broker, risk, strategy enablement, or deployment state.

Do not describe #915 as "not run" anymore. Completion of the run is not the same as accepting a strategy for promotion or validation; no paper, DEMO, live, broker, risk, or deployment authority follows from it.

## Journal delta — 2026-09-22 duplicate-work audit

Compared with the last proven September 20 journal check:
- duplicate order identities: **0 -> 0**;
- unmatched outcomes: **3 -> 3**;
- unmatched order-ID rows: **0**;
- orphan records: **0 -> 0**;
- naked-position flags: **0 -> 0**;
- new unresolved/open journal attempts since September 20: **0**.

The duplicate research work did not create a journal repair requirement.

## Known technical defects — current delta

**OPEN**
- None from the reconciled defect list as of this documentation pass.

**CONFIRMED FIXED REPO-SIDE — do not carry forward as open repo defects**
- Strategy Inventory taxonomy (#920 / `2e96e164624dc45996b45a2086b012c56ee5d43d`): evidence verdict and execution posture are now separate fields; `project_check daily` keeps the final evidence verdict as its safety classification while parsing posture separately, and the dashboard no longer infers execution authority from verdict text. Full CI passed before merge. This is **not deployed/restarted**; the current VPS status/dashboard remains on its existing release until a separately justified future release.
- normal PaperBroker vs ReplayEngine IOC entry-reference parity (#918 / `3e624693871cb725541e286b7feabf2633342228`): the normal webhook PaperBroker IOC path now supplies the causal decision-bar close, matching ReplayEngine; full CI passed before merge. This was repo-only and has **not** been deployed/restarted.
- promotion gate hard-blocker success semantics (#893 / `acadbf8`);
- zero/dead forward-campaign arm visibility (#582 / `964099c`);
- `project_check daily` critical-failure success semantics (#788/#790).

No deployment or restart is authorized merely to pick up #918/#920. Strategy/risk/broker/runtime execution behavior remains unchanged on the current box until a separately approved release.

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
