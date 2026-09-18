# 1m Armed Trigger / Context Lane — 2026-09-18

## Verdict

**DEPLOYED / PAPER-SHADOW EVIDENCE ONLY / NO LIVE EXECUTION AUTHORITY.**

Purpose:
- reduce trigger-latency observation for already-authorized MNQ 4HR setups;
- collect lower-timeframe context for all configured futures roots;
- keep setup discovery, strategy authorization, risk approval, and broker submission separate from 1m ingestion.

Verified active release:
`a6913c06750dbe9e67ea6ba0120ac43841f1fdc9`

Verified loaded posture:
- `LIVE_TRADING_ENABLED=false`
- `TRADOVATE_ENV=demo`
- `SCHEDULE_MODE=always_on_shadow`
- `EXIT_MODE=static`
- `MAX_CONTRACTS_HARD_CAP=1`
- `ONE_MIN_TRIGGER_ENABLED=true`
- live-box drift guard: **OK**

## Architecture

Generic 1m storage:
- `context/one_min_feed.py`
- writes isolated `tf1m/` BarHistory only;
- imports no DecisionEngine, RiskEngine, PaperBroker, or Tradovate code.

MNQ 4HR trigger observer:
- `context/one_min_trigger.py`
- can observe a trigger only when persisted MNQ 4HR state is already `ARMED`;
- cannot create or arm the setup;
- uses a genuinely completed prior 1H stop anchor;
- deduplicates one event per armed setup;
- records `trade_authorized=false`;
- records `external_broker=false`.

MNQ 3-2-2 First Live observer — **repository build only / not activated on the VPS**:
- `context/one_min_322_observer.py`;
- dedicated proof-critical flag `ONE_MIN_322_OBSERVER_ENABLED`, default OFF;
- requires the generic `ONE_MIN_TRIGGER_ENABLED` flag as well;
- maintains isolated observer state under `tf1m/322_first_live/`, never executable `DailyState`;
- arms only from the canonical completed 7AM/8AM/9AM 5m setup at the 10:00 ET boundary;
- refuses to arm unless all 36 required 5m bars from 07:00 through 09:55 are present;
- 1m can only observe the first strict break of that pre-armed level;
- equality at the trigger is not a break;
- records `trade_authorized=false`, `paper_fill_authorized=false`, and `external_broker=false`;
- imports no DecisionEngine, RiskEngine, PaperBroker, or broker adapter;
- activation requires a separate deploy/pin decision.

Main runner:
- authenticated 1m MNQ/MES inputs are intercepted before the ordinary strategy/risk/broker path;
- result is context/evidence only.

Collection-only observation transport:
- M2K/MGC/MCL/MBT remain on the dedicated `OBSERVATION_ONLY` route;
- their 1m bars are stored through the neutral 1m feed helper;
- they never enter DecisionEngine, RiskEngine, PaperBroker, or Tradovate because of this patch.

Existing 5m and 15m feeds remain in place. 1m supplements them; it does not replace them.

## Verification

Initial MNQ armed-trigger lane:
- focused 1m + 5m tests: 24/24 passed;
- surrounding webhook/timeframe isolation: 138/138 passed;
- full current-base suite before deployment: 6,093 passed / 7 skipped.

Collection-only 1m extension:
- focused observation / 1m / webhook suite: **175/175 passed**;
- structural isolation guard proves observation modules do not import execution/risk engines;
- full repo suite: **6,099 passed / 7 skipped**;
- PR #725 CI / analysis / CodeQL: green.

3-2-2 First Live observer build:
- preregistration frozen before implementation;
- focused 1m / 5m / 3-2-2 / live-box-guard suite: **77/77 passed**;
- static isolation test proves the observer module imports no DecisionEngine,
  RiskEngine, PaperBroker, or broker adapter;
- full repository suite: **6,186 passed / 7 skipped**;
- `ONE_MIN_322_OBSERVER_ENABLED` is proof-critical and default OFF;
- **not activated or deployed by this build**.

Atomic release path:
- merged SHA release built/verified/promoted through the immutable release flow;
- post-activation CWD matched exact deployed SHA;
- proof-critical `ONE_MIN_TRIGGER_ENABLED` pin loaded and guard-clean.

## Live feed proof

Post-deploy, fresh authenticated 1m payloads and `tf1m` files were verified for all six roots:

- MNQ ✅
- MES ✅
- M2K ✅
- MGC ✅
- MCL ✅
- MBT ✅

At the final natural-minute proof:
- all six latest payloads reported `timeframe=1`;
- all six had active `bars_<ROOT>_2026-09-18.jsonl` files.

TradingView authentication issue encountered during activation:
- the six newly-created 1m alerts initially returned 401 because their alert snapshot lacked the Pine body-auth secret;
- copying the existing body-auth input and recreating the alerts changed them to HTTP 200;
- server authentication was not weakened.

## What this lane does not prove

It does not prove:
- that 1m execution improves every strategy;
- that 1m should discover setups;
- that all armed-trigger families should use identical intrabar semantics;
- that collection-only instruments are eligible for trading;
- that MNQ 4HR is validated;
- that a 1m bar provides exact tick-by-tick price path inside the minute.

For MNQ 4HR, 1m is currently evidence infrastructure. Any move from trigger observation to paper-fill authority requires its own controlled proof and explicit authorization.

## Current safe next step

Keep the lane running as evidence-only.

Use natural 1m MNQ events to compare:
- armed trigger level;
- actual 1m touch timing;
- prior 5m-close decision timing;
- detachment at the old decision point;
- correct completed-1H stop anchor;
- duplicate/replay behavior.

Do not extend execution authority to M2K/MGC/MCL/MBT.
