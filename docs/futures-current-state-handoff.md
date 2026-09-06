# Futures — Current State Handoff

_As of 2026-09-06. This is the single current futures handoff. Do not recreate completed audits, redo merged fixes, or reopen strategy work unless new evidence proves a defect._

## Verdict

**PAPER ONLY / COLLECTION CONTINUES / NO IMMEDIATE REPAIR REQUIRED.**

The current futures system is not fully strategy-validated, but the active paper collection/runtime lane has no newly confirmed blocker from the latest audit. Recent work closed the real Pine ORB parity defects, reduced VPS memory pressure, and merged the Tradovate exact-account routing guard into the repository. The remaining Tradovate step is operational activation later, not more development.

## Current repo vs deployed runtime

- Repository `main`: `48037967ff40a722d247f0cc22be474d06d28b4a` (`48037967`) after merged PR #374.
- Running `futures-bot` remains on the previously proven deployed release `73bffb1`; do not assume repo `main` changes are on the bot until a separate pinned deploy is performed.
- The watcher-only #466 rollout did not redeploy `futures-bot`.
- Latest verified bot state from the current maintenance pass: service healthy, health endpoint 200, no restart/crash-loop issue reported.
- Evidence epoch remains the current paper-collection epoch; do not create a new epoch merely because `main` advanced.

## Completed September fixes — do not redo

### Pine / TradingView

PR #467 — stale ORB bracket state:

- Fixed Pine carrying an old NY ORB bracket after the canonical runtime/replay ORB had expired/reset.
- Pine reset/expiry now follows the canonical ORB lifecycle instead of leaving the prior bracket alive until the next open.
- No entry, target, filter, risk, or broker behavior was tuned.

PR #468 — ORB stop parity:

- Fixed Pine `orb_breakout` advisory stop offsets to match backend `risk_rules.yaml`.
- MNQ ORB stop offset: 48 ticks.
- MES ORB stop offset: 16 ticks.
- 4HR Re-Trigger and other strategy brackets were intentionally left unchanged.
- Current-main CI passed before merge.

Operational TradingView refresh is complete:

- Latest `tradingview/risksentinel_context.pine` was copied into TradingView after #467/#468.
- Script was saved/compiled.
- Active alert was recreated so TradingView uses the updated Pine snapshot.
- No VPS deploy is required for those Pine-only fixes.

### CHOPPY live/replay parity

Already fixed on current repo code before this maintenance pass. The explicit replay regression populates `window_direction` and proves the effective-condition parity behavior. No new CHOPPY patch was made. Do not reopen it without a new reproducible failure.

### Tradovate exact account routing — repository fix complete

PR #374 was refreshed onto current main, re-audited, tested, and merged as `48037967`.

The merged guard:

- adds optional `TRADOVATE_EXPECTED_ACCOUNT_ID`;
- searches the full Tradovate `/account/list` for the exact pinned id rather than trusting `accounts[0]`;
- uses the same selector in normal account resolution and the reliability heartbeat;
- fails closed if the pinned account is absent, duplicated/ambiguous, unresolved, or malformed;
- fails closed before order submission if the pinned account balance cannot be verified as positive;
- adds the account-routing cancellations to the existing no-fill taxonomy.

Current-main merge-candidate CI: **4660 passed, 6 skipped, 0 failures**.

**Important:** this code is merged but not yet deployed to the running bot, and `TRADOVATE_EXPECTED_ACCOUNT_ID` is not yet set on the VPS. That is intentionally deferred. No Tradovate reconnect, credential recreation, or account setup is required. Later, read the bot's existing intended demo account id from the VPS/Tradovate account list, pin that same id, deploy the already-merged code, and verify it. Do not guess an account id.

## VPS memory / IB Gateway

The earlier VPS memory pressure was real, but the current maintenance pass removed the largest unnecessary resident process from the active box state.

IB Gateway status now:

- `ibgateway` container is stopped/exited.
- Container restart policy is `no`, so Docker daemon restarts or host reboots will not automatically bring it back.
- Container and image were preserved; this was not an uninstall.
- `futures-bot` was not touched by the stop/restart-policy change.

Observed memory improvement after stopping IB Gateway:

- RAM used: about 957 MB -> 651 MB.
- RAM available: about 957 MB -> 1262 MB.
- Swap used: about 914 MB -> 726 MB.

Do not recreate the prior IB dependency audit or restart the container unless a real consumer is identified later.

## Watcher state

The current watcher work is already deployed and should not be rewritten.

- #466 is watcher-only and includes release-identity/rebaseline handling.
- Watcher remained healthy after the IB Gateway change; latest reported tick in this pass was OK with nothing blocked and no warnings.
- The next real futures release should provide the natural proof of the watcher self-rebaseline path. That is a proof item, not a reason for another watcher code change now.

## Strategy evidence classifications

`docs/strategy-rules/Strategy_Inventory.md` remains the evidence source of truth.

These are evidence outcomes, not software bugs to "fix" by weakening risk or tuning rules:

- ORB Reclaim current/first_cross — **BROKEN — negative evidence**.
- ORB Reclaim V4-R — **WAIT**.
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT EXECUTABLE FORM**.
- 4HR Re-Trigger MES — **BROKEN / WAIT**.
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**.
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**.
- ORB Breakout inverted evidence lane — **PROMISING BUT UNPROVEN**.
- VWAP Hold MNQ NY — **PROMISING BUT UNPROVEN**.
- MES `strat_122` — **WAIT**.

Do not widen stops, loosen risk gates, or change entry/target/filter logic to make the broken forms pass. A changed strategy is a new variant and requires preregistration and new evidence.

## Current safety posture

- Paper only.
- No live broker execution is authorized.
- Active isolated futures lane remains MNQ-first.
- Max 3 trades/day for the isolated lane.
- Daily loss and drawdown survival controls remain in force.
- No averaging down.
- Bracket/stop requirements remain in force.
- Do not fabricate signals, force traffic, or tune strategy parameters to manufacture evidence.
- Do not deploy merely because repository `main` moved.

## Open items that are NOT current defects

- Tradovate account pin activation on the VPS — later operational step; repo code already fixed.
- Watcher self-rebaseline proof — wait for the next real pinned release.
- First-bars check — intentionally unscheduled/deferred; do not recreate an automation for it unless explicitly requested.
- PR #463 memory-entry/deploy gate — deliberate HOLD/policy item; do not merge just because memory is currently healthier.
- Options-lane PRs/evidence are separate from this futures handoff; do not mix their work into futures maintenance.

## Smallest safe next step

**Stop changing futures code for now and continue natural paper evidence collection.**

When a real futures deployment is next justified, separately:

1. read/confirm the existing intended Tradovate demo account id;
2. set `TRADOVATE_EXPECTED_ACCOUNT_ID` to that exact id;
3. deploy the exact reviewed commit through the normal pinned release path;
4. verify paper/demo mode, exact account routing, watcher rebaseline, health, and first natural post-deploy evidence;
5. stop again unless new evidence proves another defect.
