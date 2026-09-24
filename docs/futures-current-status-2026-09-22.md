# Futures Current Status — 2026-09-22

This is the concise operator-facing source of truth for the futures system. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated current-status document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / GUARDED DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

Core rule remains: **No proof, no run.**

## MNQ inverse ORB — retired, not active (2026-09-24 read-only audit)

Operator decision 2026-09-24: the 2026-09-08 retirement stands. Do not reactivate.

| Field | Status |
|---|---|
| Inverse ORB status | **RETIRED / BROKEN** — retired 2026-09-08 by #517 (`212512e`); see `docs/inverse-orb-decision-time-replay-2026-09-08.md` |
| Deployment status | **NOT ACTIVE** — inactive by design, not config drift |
| Evidence status | **ZERO CURRENT VALID FORWARD EVIDENCE** — 0 inverse evaluations and 0 trades since either epoch; the old positive baselines (+$1,026.64 / PF 5.28 and +$745.72 / PF 2.39) are retired and must not be pooled |
| Env pin status | **ACCOUNTING ISOLATION ONLY / NOT ACTIVATION** |
| Safe action | **DO NOT REACTIVATE WITHOUT NEW PREREG, safety fixes, and replay/live parity proof** |

Why the lane cannot fire (verified at release `a7e6515`):

- `risk_rules.yaml` `disabled_concepts_per_instrument.MNQ` lists `orb_breakout`, so the MNQ executable set is empty ("No enabled strategy for MNQ").
- `strategy_permission_gate` sets `orb_breakout: SHADOW_ONLY`.
- `webhook/runner.py` runs the inverse lane only after an MNQ `orb_breakout` `TRADE` decision, which the two gates above prevent.
- `SCHEDULE_MODE=always_on_shadow` refuses every order, paper included.

Env pins that remain on the box and must not be read as activation:

- `MNQ_ORB_BREAKOUT_INVERSE_MODE=paper_sim` and `MNQ_ORB_BREAKOUT_INVERSE_EPOCH_START=2026-09-05T17:45:00Z` (with their `EXPECTED_PROOF_*` pins) are still read by code. `webhook/runner.py` scopes the **main runner's** daily state (daily loss, loss streak, balance, peak, drawdown) from that epoch. That is why the pin must not be removed or changed without a separate review; removing it would switch main-runner risk accounting back to all history.
- The box file `logs/mnq_orb_breakout_inverse_evidence_epoch.json` (epoch 2026-09-03T22:17:33Z, release `bbdb85e`) is obsolete. No code reads it, and by its own terms (release `bbdb85e`, `SCHEDULE_MODE=current`, `EXIT_MODE=runner_shadow`) it no longer describes the runtime. It is left in place; this note is the record.

Known safety gaps that would have to be fixed before any future reactivation (not fixed; not blocking while the lane is retired):

- An MNQ `orb_breakout` `TRADE` on a bar that is neither 15m nor a 5m retest skips the inverse block and uses the configured broker.
- An open inverse row missing its paper audit marker is resolved against Tradovate, and the day-only close path can call `flatten_position` outside the shadow gate.
- Paper/replay entry-fill parity is unverified for this lane.

Future futures work should focus on lanes that are live or paper-observable now, not inverse ORB:

- vwap_hold forward A/B (`forward_ab_2026_08_v1`)
- wide-stop demo lane (the only route currently armed to Tradovate demo)
- asia_d_ema paper lane (`asia_d_ema_2026_09_v1`)
- session_22c paper lane (`session_22c_paper_2026_09_v1`)

## Runtime update — 2026-09-24 02:43 UTC (read-only box check)

This block supersedes the 2026-09-23 runtime blocks below, which are kept as provenance. Nothing was changed by this check.

- **Futures release `45290e03d201`** (`main` at #970), released 2026-09-24 02:17:26 UTC by another session.
  - PID `2975357`, active, `NRestarts=0`.
  - `/root/autonomous-futures-system` points to `/root/afs-releases/45290e03d201-20260923-221700`.
  - Release integrity **OK, 1,531 files**; `RELEASE_INTEGRITY_ENFORCED=true`; health 200.
- Release history since the 2026-09-23 block: `991df5f` (09-23 21:06Z, #952) → `a7e6515` (09-24 00:50Z, #963) → `45290e03` (09-24 02:17Z, #969 observe-only contract identity + #970 Pine `contract_hint`).
- Environment: `BROKER=tradovate`, `TRADOVATE_ENV=demo`, `LIVE_TRADING_ENABLED=false`, `SCHEDULE_MODE=always_on_shadow`, `EXIT_MODE=static`, `MAX_CONTRACTS_HARD_CAP=1`, `BLOCK_RESTRICTED_REGIME=false`.
- The wide-stop Tradovate DEMO route is armed on purpose (`WIDE_STOP_DEMO_EXECUTION_ENABLED=true`, route `tradovate_demo`); see "Wide-stop Tradovate DEMO route — intentionally armed" below. It has placed 0 demo orders; broker demo account flat.
- MNQ inverse ORB is retired and not active; see the section near the top of this file.

## Runtime update — 2026-09-23 03:50 UTC (read-only box check)

- **Futures release is now `799a89e2db2d`** (#932, plain-English Discord on every channel). It was released at 01:59:58 UTC by another session, so the service block below is one release behind.
  - PID `2151194`, active.
  - `/root/autonomous-futures-system` points to `/root/afs-releases/799a89e2db2d-20260922-215935`.
- **Options scanner:** `5b7be0f7` (`a6f79d7` + #932 cards), released 02:04:56 UTC.
- The #932 change is message text only; strategy, risk and broker behaviour are unchanged. Nothing else was changed by this check.

## Runtime reconciliation — 2026-09-23 01:35 UTC (read-only box check)

This section supersedes the morning-preflight runtime block below, which is kept as provenance. Checked via `/proc/<pid>/cwd`, `systemctl show`, `/root/afs-shared/release_history.txt`, and the process environment. Nothing was changed.

**Futures service**

- PID: `2119380`; `NRestarts=0`; active since **2026-09-23 01:25:28 UTC**;
- cwd / deployed release: `/root/afs-releases/b2420713b9cc-20260922-212508`;
- release commit: `b2420713b9cccf036a4b67865618985d94e7b42b` (= `main` at #930);
- environment: `LIVE_TRADING_ENABLED=false`, `SCHEDULE_MODE=always_on_shadow`, `EXIT_MODE=static`, `BROKER=tradovate`, `TRADOVATE_ENV=demo`, `EXPECTED_TRADOVATE_ACCOUNT_ID` pinned; `ENTRY_FILL_MODEL` unset;
- failed systemd units: **0**.

Release history since the `aba8324` baseline (from `release_history.txt`):

| UTC | release commit | adds |
|---|---|---|
| 09-22 23:47 | `902a99a` | `aba8324` + #924 only (curated) |
| 09-22 23:56 | `88e89e4` | `main` at #924, so also #917–#923 |
| 09-23 00:22 | `5fd4716` | + #925, #926 |
| 09-23 00:42 | `3a3d425` | + #927 |
| 09-23 00:51 | `42b9def` | + #928 |
| 09-23 01:25 | `b242071` | + #929 (docs), #930 |

Runtime effect of `aba8324..b242071` on the futures path:

- no `config/`, `risk/`, strategy, or broker order/cancel/flatten/sizing/account-selection change. The only `execution/` diff is the Tradovate session-alert Discord post body (#927);
- #918 IOC entry-reference fallback: deployed but **inert** — it only fires for `PaperBroker(entry_fill_model="ioc_limit")` with no proof/inverse market price, and the box default is `market` (`config/settings.py`), with no env or YAML override;
- #920: dashboard strategy table reads the separate execution-posture field (display only);
- #923: fail-closed product-session guard module is deployed but **unwired**;
- #924: decision-notification and feed-health gating no longer suppress 16:15–16:30 ET (CME removed that halt in 2021);
- #927/#928/#930: Discord alerts sent as cards, in plain English (presentation only).

**Approval record:** operator GO for releases `902a99a`, `88e89e4` (the first to carry #918/#920), `5fd4716`, `3a3d425` and `42b9def` is recorded in the private handoff log. The final `b242071` release (#930) had no handoff entry as of 2026-09-23 ~01:45 UTC. The "no deployment authorized merely to pick up #918/#920" statement below was accurate when written and is superseded by the approved `88e89e4` release.

**Python environment — pinned per release (2026-09-23 ~01:50 UTC):** `/root/autonomous-futures-system` is a symlink to the live release, not a git checkout. Every futures release's `.venv` used to be a symlink to one shared, mutable venv (`/root/afs-shared/.venv`), so a `pip install` there would change the dependencies of the running release and of every rollback target. An earlier revision of this note wrongly called it the "git-checkout venv".

- Live release `b2420713b9cc`: `.venv` now points to `.venv-release`, a byte-identical copy of the shared venv inside the release dir (every file matches by sha256, `pip freeze --all` is identical). Swapped atomically with no restart; pid `2119380` is unchanged, integrity OK (1,473 files), health 200. Its freeze is recorded in `/root/afs-shared/release-b2420713b9cccf036a4b67865618985d94e7b42b-dependencies.txt`.
- New releases: the operator's deploy script now copies the shared venv into each new release the same way. It refuses to switch if the copy's freeze differs, records the freeze per release, prints any dependency change against the previous release, and runs the staging integrity and import checks with the release's own Python.
- Older release dirs (3a3d425, both 42b9def dirs) still link to the shared venv, so they roll back unchanged.
- `/root/afs-shared/.venv` remains the dependency source: to change dependencies deliberately, install there, then cut a release.
- Known gap, unchanged: the shared venv has drifted from `requirements.lock` (uvicorn 0.48.0 vs 0.49.0, idna 3.17 vs 3.18), and the lock omits `requests`, which `requirements.txt` declares and `execution/tradovate_broker.py` imports. Do not rebuild a venv from `requirements.lock` until the lock is regenerated.

## Verified runtime — 2026-09-22 morning preflight (superseded, see above)

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
- exact release: **`db9bc7e2c00559fc969af7be0e2cb12b00a1454c`** (#922, drop-in `10-release.conf`, switched 2026-09-22 22:52 UTC; prior pin `36e73f1...` backed up as `options-122-prospective.10-release.conf.pre-922-20260922T225209Z`);
- last service run: **FAILED** 2026-09-22 20:59:01 UTC on the old `36e73f1` release with `journal_setup_fingerprint_drift_58` — the crash-loop #922 fixes (every run since 16:36 UTC);
- no run has executed on `db9bc7e2` yet. Its first proof is the next natural timer fire, **2026-09-23 13:00 UTC**: the existing journal loads unchanged, the cycle succeeds, and `journal_setup_fingerprint_drift_58` does not recur.

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

## Research update — 2026-09-23 Strat rules, higher timeframes, signal grading

All of this is research or reporting only. No runtime, strategy, risk or broker change.

- **#934 → #936 (merged `0f21ad6`): 15m TheStrat source rules. DOES NOT CLEAR, all 18 MNQ cells.**
  - The rules tested were the magnitude target, the entry-bar stop and the FTFC entry filter (price vs the month/week/day/hour opens).
  - The magnitude target never beat the observer's 2R.
  - FTFC improved 4 of 5 reversal baselines. The best cell was 2-2 reversal + FTFC: PF 1.24, n = 290, below the 1.94 bar, and not replicated on MES.
  - About 70% of signals are in FTFC "conflict", where most losses sit. Hammer/shooter PF 0.52.
- **#938 → #941 (merged `4c3b0b6`): Strat on 60m/4H/daily with an intrabar stop entry. DOES NOT CLEAR, all 54 MNQ cells.**
  - Family-wise direction-flip null: 60m 1.93, 4H 1.99, daily 1.36.
  - The best results were unfiltered 4H baselines (2-2 reversal, 2-2 continuation, 1-2-2): PF 1.12–1.41, inside the null.
  - FTFC hurt at 60m and on most 4H setups. Hammer-at-a-level was too rare (0–11 trades).
  - Only daily 2-2 reversal with magnitude + FTFC looked strong: PF 4.0 on 23 trades. It is prior-exposed (the Daily 2-2 lane), so it is forward-only context.
  - The integrity check caught a holiday-calendar bug before scoring. It was fixed with `context.cme_trading_day`: 461/461 daily parity.
- **No variants of either study may be run on the same data.**
- **#940 (DRAFT, not released): Strat FTFC labels on every observer row, plus a MNQ 2-2 reversal "lined-up only" tracker.**
  - It is a label view on the Discord cards and the Friday report. It filters nothing.
  - Opens are not roll-adjusted (a follow-up is needed before the December roll).
  - The release needs the futures release plus the `paper_collection/current` snapshot, and an **operator GO**.
- **#942 (merged `8b2700c`): observer signal grading, a forward test.**
  - The grade: A = FTFC aligned + New York session + not DEAD; B = aligned; C = conflict; D = against.
  - It is judged once on cost-adjusted live outcomes: when MNQ A ≥ 60 on ≥ 30 days and B/C/D ≥ 30 each, or at 2027-03-31.
  - Evaluator #943 (merged `7808fe8`) is counts-only until that look. Collection starts when #940 is live.

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
- Strategy Inventory taxonomy (#920 / `2e96e164624dc45996b45a2086b012c56ee5d43d`): evidence verdict and execution posture are now separate fields; `project_check daily` keeps the final evidence verdict as its safety classification while parsing posture separately, and the dashboard no longer infers execution authority from verdict text. Full CI passed before merge. **Deployed** since release `88e89e4` (2026-09-22 23:56 UTC); see "Runtime reconciliation" above.
- normal PaperBroker vs ReplayEngine IOC entry-reference parity (#918 / `3e624693871cb725541e286b7feabf2633342228`): the normal webhook PaperBroker IOC path now supplies the causal decision-bar close, matching ReplayEngine; full CI passed before merge. **Deployed** since release `88e89e4`, but inert on the box (entry fill model is `market`).
- promotion gate hard-blocker success semantics (#893 / `acadbf8`);
- zero/dead forward-campaign arm visibility (#582 / `964099c`);
- `project_check daily` critical-failure success semantics (#788/#790).

~~No deployment or restart is authorized merely to pick up #918/#920.~~ Superseded 2026-09-23: both reached the box in the operator-approved `88e89e4` release (see "Runtime reconciliation"). Strategy, risk, and broker order behavior are unchanged by them; any further trading-path change still needs its own approved release.

## Wide-stop Tradovate DEMO route — intentionally armed (operator confirmed 2026-09-24)

**Status: INTENTIONALLY ARMED FOR TRADOVATE DEMO EXECUTION. NOT LIVE.** The operator confirmed on 2026-09-24 that this lane is approved to place Tradovate **demo** orders. Keep the arm flags as they are; do not disarm, edit `.env`, or restart to change this without a new operator decision.

Verified read-only 2026-09-24 02:33 UTC (release `45290e03`, pid `2975357`):

| Condition | Value |
|---|---|
| Arm flags | `WIDE_STOP_DEMO_EXECUTION_ENABLED=true`, `EXPECTED_PROOF_WIDE_STOP_DEMO_EXECUTION_ENABLED=true` |
| Route | `WIDE_STOP_LEDGER_EXECUTION_ROUTE=tradovate_demo` (proof-pinned) |
| Broker environment | `TRADOVATE_ENV=demo`; `LIVE_TRADING_ENABLED=false` |
| Account pin | `TRADOVATE_EXPECTED_ACCOUNT_ID` set (the broker accepts only an exact single match) |
| Size | `MAX_CONTRACTS_HARD_CAP=1`; the lane itself always submits 1 contract |
| Broker state | flat (cached `/status/broker-account`); 0 working orders at the last preflight read |
| Lane state | `demo_state.json`: `pending=null`, `position=null` |

What the lane does:

- Strategies: MNQ **4HR Re-Trigger** (`wide_stop_4k`) and MNQ **60M 3-2-2 First Live** (`wide_stop_6k`) only.
- Entries: New York session only (`WIDE_STOP_DEMO_SESSIONS` default); detection 09:30–11:00 ET (4HR) and 10:00–11:00 ET (3-2-2); at most 3 slots per day and $450 planned risk per trade.
- By design the lane passes its own schedule mode to the execution gate and **does not follow the box-wide `SCHEDULE_MODE=always_on_shadow`**. `always_on_shadow` still blocks every other lane.
- Exits: the lane's own 15:55 ET flatten, plus the 16:02 ET `afs-wide-stop-demo-eod-fallback` timer. Both act only on an exactly matching lane position.

Order history: **0 demo orders so far.** The only two candidates (2026-09-11 3-2-2 and 2026-09-15 4HR) were blocked by `REGIME_RESTRICTED`. `BLOCK_RESTRICTED_REGIME=false` since 2026-09-21, so the next eligible candidate is expected to place a real demo order.

Next check: read-only reconciliation after the **first actual demo order**:
- `logs/tradovate_demo_evidence/hypothetical_ledger/tradovate_demo/demo_state.json` (`client_order_id`, `broker_order_ids`);
- `logs/tradovate_demo_evidence/hypothetical_ledger/{wide_stop_4k,wide_stop_6k}/journal_*.jsonl` (`CANDIDATE` / `OUTCOME` rows);
- cached broker status, and the matching paper-ledger row.

Known edge cases, not fixed (should-fix, not blocking while one pinned account is flat):
- pending-order reconcile can adopt any same-direction MNQ 1-lot from an unfiltered position list;
- working-order cancel does not filter by account when no account ID is resolved;
- a lane position that goes flat without a matching fill is never cleared automatically and blocks the next day until reconciled by hand.

## Current evidence gates

0. **Options 1-2-2 collector fix (#922, release `db9bc7e2`).**
   - Its last run (2026-09-22 20:59 UTC) failed with `journal_setup_fingerprint_drift_58`. That run predates the fix going live at 22:52 UTC.
   - The first run on the fix is the 13:00 UTC timer on 2026-09-23. Read-only checks are scheduled for 13:05, 13:20 and 13:45 UTC.
   - #940's release waits for this verdict and an operator GO.
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
