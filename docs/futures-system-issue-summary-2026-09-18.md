# Futures System Issue Summary — 2026-09-18

## Verdict

**HOLD LIVE / CONTINUE PAPER-SHADOW EVIDENCE.**

There is no current blocker to passive 1m/5m/15m evidence collection. There are still blockers to treating any strategy as validated or granting new execution authority.

## Closed / materially improved

- **Accidental live-route concern:** tested posture remains live-disabled; no proven real-money escape in the audited paths.
- **1m authentication:** fixed at TradingView alert snapshot level without weakening webhook authentication.
- **1m feed routing:** MNQ/MES plus M2K/MGC/MCL/MBT all now write isolated `tf1m` bars.
- **Collection-only isolation:** M2K/MGC/MCL/MBT remain `OBSERVATION_ONLY`; structural tests forbid execution/risk imports in the observation transport.
- **4HR “late entry” mechanism:** proven. The documented touch was being inferred from completed 5m OHLC.
- **4HR optimistic historical entry:** quantified. Old trigger-price backfill is retired as the executable headline.
- **4HR hour-boundary stop timing:** proven and corrected in the pre-armed offline model; stop must come from the last 1H candle complete at the true trigger time.
- **3-2-2 First Live timing:** offline mechanism audit complete; causal pre-armed timing survives 3-tick stress with both halves positive. This closes the timing question only; sample and account-risk blockers remain.
- **Generic “require 1→2” idea:** tested and not supported as a simple 4HR gate.
- **Simple “touch supply/demand = reverse” idea:** tested and not supported as a blanket rule.
- **Backtest data-proof gate:** #751 removes bare-boolean trust for session identity/feed integrity by checking hash-pinned C14 fixtures, frozen replay-file hashes, coverage counts, and explicit gap ledgers.
- **Resolved-outcome gap proof:** #754 binds each counted validation outcome to a strategy-specific dependency start plus replay signal/entry/resolution timestamps and hash-bound journals; any declared-gap overlap or missing/mismatched proof fails closed.
- **1m observer response-proof persistence:** #759 is deployed in minimal release `ac2b117ec1f9`. Natural 4HR/3-2-2 observer events now have an append-only place to retain the actual runner response needed for execution-isolation proof; restart itself creates no row.
- **Watcher false journal stall:** #760 limits the main-journal causal bar clock to MNQ/MES decision-path 15m files. The exact live-source one-hunk backport is installed at SHA `7d29872d1c85`; watcher has zero BLOCKED findings and the #759 restart is recorded as sanctioned.
- **Collector-census off-session false failures:** #764 adds `OFF_SESSION` handling for cadence-driven CME equity futures heartbeats and prevents expected weekend Daily 2-2 carry from being mislabeled as stale-feed exposure. **80 adjacent tests passed.** This is read-only monitoring/reporting code and does not require a runtime restart.
- **R5 entry-conditioning reproduction + ORB A/B:** #763 deterministically reproduces the preserved MNQ/MES R5 audit from exact hash-gated inputs. #768 then completes the preregistered ORB false-break entry-architecture A/B. Signal-close is **rejected as a fix**: MNQ +0.0111R/all → -0.0596R/all; MES -0.0416R/all → -0.0847R/all; both halves negative on both instruments; 1/2/3-tick stress worsens further. All five preregistered support gates failed. **NO RUNTIME CHANGE / MIXED_OR_UNSUPPORTED.** Further ORB redesign would be a new strategy hypothesis; the separate 2-2 entry-conditioning question remains untested.
- **2026-09-19 causal/mechanics audit:** Miyagi has a second proven replay defect after the completed-hour fix: its trigger-touch bar is excluded from immediate stop/T1 resolution. One frozen MNQ row flips WIN→LOSS; at 2 ticks MNQ falls from +$552.83 / PF 3.223 to **+$425.33 / PF 2.322**. Generic futures 2-1-2/1-2-2 has causal reconstructed paper math but **no operational pre-armed broker parity**; late non-Paper submission correctly fails closed. No new HTF lookahead leak was found.

## Current blockers

### 1. 4HR prospective trigger-time parity

Offline pre-armed touch survives stress, but the deployed 1m lane is evidence-only. The 2026-09-18 forward baseline contains no 4HR/3-2-2 observer events because MNQ 1m collection began at 17:38Z / 13:38 ET, after both observer windows had ended; this is expected and does not indicate collector failure. Current minimal deployed release `ac2b117ec1f9` passed the exact full suite (**6,193 passed / 7 skipped**) and now persists the runner-response proof on future natural observer events; the 2026-09-18 futures journal still has zero TRADE rows. We still need natural forward examples proving:
- correct armed state;
- 1m touch time;
- dedupe;
- correct prior completed-1H stop;
- no stale 5m-close dependence;
- no unauthorized paper/broker action.

Until then, 1m does not get paper-fill authority.

### 2. LC_ZONE quality / 4HR target geometry

The preregistered independent LC_ZONE audit does **not** support the current v1
zone detector as a superior reaction-area detector.

Corrected audit findings:
- live current-vs-completed HTF nearest-zone identity disagreement reaches
  **9.39%**;
- rolling-MTR qualification makes historical zone identities appear/disappear
  without a break;
- 2 existing 4HR geometry rows used a 1H zone before the defining impulse 1H
  candle completed;
- one affected row changes from `BEYOND_ZONE` to `BEFORE_ZONE` when corrected;
- on 2,698 paired first touches, actual clean 0.5-MTR rejection was 86.43% vs
  90.51% for matched controls;
- uplift **−4.08 pp**, 95% CI **[−5.67, −2.41] pp**;
- supply was approximately flat, demand materially worse.

Preregistered statistical classification: **NO EVIDENCE OF ZONE QUALITY**. Reviewer ruling: **INCONCLUSIVE / CONTROL MATCH FAILURE** because the placebo matching did not achieve adequate common support. The negative result is preserved but is not promoted into a claim that the zone concept is worse than random.

The timing/identity instability is independently proven and sufficient to block target-rule validation.

The formerly planned 4HR zone-clipped-target A/B is **BLOCKED / DO NOT RUN**
under LC_ZONE v1. Any replacement zone definition requires a new preregistration.

### 3. 3-2-2 trigger timing — CLOSED AS AN OFFLINE MECHANISM QUESTION

The frozen 34-candidate First Live A/B is complete.

- completed-5m close exceeded the 32-tick adverse IOC tolerance on **13/34 (38.24%)** candidates;
- pre-armed First Live at 3 adverse ticks: **33 fills / 33 resolved wins / 1 bracket-invalid no-fill**;
- net **+$2,709.66**;
- H1 **+$1,366.34** / H2 **+$1,343.32**;
- zero same-trigger-bar both-stop-and-target ambiguities.

Classification: **TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN.**

Still open:
- prospective confirmation;
- more sample including losses;
- current real-account stop-width/R:R incompatibility.

The timing audit does not authorize an execution path or risk-policy change.

### 4. Miyagi trigger-bar replay identity

The completed-hour lookahead and the separate trigger-bar replay mismatch are now **fixed repo-side**. Regenerated frozen evidence changes MNQ 2024-09-18 from the old later TARGET win to the correct pessimistic same-trigger-bar STOP loss. At 2 ticks MNQ is 8 fills, 6W/2L, +$425.33, PF 2.322; H2 remains only one fill. Runtime remains parked and no deployment follows.

### 5. MES 1-2-2 remains thin and lacks operational execution parity

Forward accounting edge is slight and stronger slippage turns it negative. The 2026-09-19 pre-arm feasibility audit proves the exact bracket is first final at the completed 15m arm-bar close, which is the same instant the watched bar opens. Therefore exact pre-open execution parity is **not feasible as the same strategy identity**. Paper/replay remains causal reconstructed counterfactual evidence; non-Paper late submission fails closed. Continue evidence only as paper/observation, not execution proof.

### 6. Cross-instrument evidence is observation only

M2K/MGC/MCL/MBT now have better 1m coverage, but this does not justify strategy expansion. Need enough observation history before defining or evaluating any execution candidate.

## Strategy state

- **MNQ 4HR Re-Trigger:** PROMISING BUT UNPROVEN / PAPER ONLY.
- **MES 4HR:** BROKEN / WAIT.
- **MNQ 3-2-2 First Live:** **PROMISING BUT UNPROVEN**; corrected pre-armed timing survives 3-tick stress with both halves positive, but n=34 is thin and current real-account stop/R:R architecture remains incompatible.
- **Miyagi:** **PROMISING BUT UNPROVEN / PARKED / REPLAY DEFECT FIXED REPO-SIDE**; #776 repaired completed-hour lookahead and the 2026-09-19 follow-up fixes trigger-bar stop/T1 suppression, gap-open semantics, and fail-closed bracket validation. Frozen evidence regenerated successfully; current-account risk incompatibility and thin sample remain.
- **Daily 2-2 completed-close / favorable-pullback:** PROMISING BUT UNPROVEN / PAPER ONLY; activation baseline reproduced exactly and current-CME-day variant remains positive, but the 34 fills are not first-touch evidence.
- **Daily 2-2 first-touch under current rules:** BROKEN / ZERO ADMISSIBLE FILLS at 1/2/3 adverse entry ticks because fixed planned 2R + actual-fill R:R >=2 is mechanically incompatible with adverse touch slippage.
- **MES 15m 1-2-2:** PROMISING BUT UNPROVEN / thin / **PAPER EVIDENCE ONLY / EXACT PRE-OPEN PARITY NOT FEASIBLE AS SAME STRATEGY**; the final arm-bar geometry is only known at the same instant the watched bar opens. A future 1m lane may study post-arm attainability observation-only, but cannot retroactively validate next-bar-open counterfactual fills.
- **Transition:** WAIT / fails required slippage robustness.
- **ORB Reclaim current:** BROKEN.
- **ORB Breakout:** BROKEN.
- **Inverse ORB:** BROKEN under corrected decision-time geometry; old positive baselines retired.
- **VWAP Hold:** BROKEN under honest decision-time fill reference.
- **Structural-level broad family:** BROKEN as standalone generic edge; narrow hypotheses remain research-only.

## Backtest status

**Usable for audited scopes, not globally certified.**

Good:
- deterministic frozen-corpus replay;
- pessimistic same-bar handling;
- gap-stop realism;
- slippage/commission stress;
- chronological halves;
- causal HTF context/zone reconstruction;
- entry-model A/B;
- mechanically verified frozen-data/session identity for Backtest→DEMO qualification;
- hash-bound dependency→signal→entry→resolution gap checks for counted resolved outcomes;
- manifest/hash-backed structural research.

Older studies that did not record the required historical timestamps or dependency-window source remain unproven unless sufficient raw data exists to rerun/backfill them.

Still strategy-specific:
- live/replay timing parity;
- Pine/backend formula parity;
- exact intrabar trigger semantics;
- dated-contract identity;
- whether a strategy is close-confirmed or touch-triggered.

## Do not redo

Do not restart:
- generic 4HR detector audit;
- generic 4HR 1→2 gate test;
- simple supply/demand presence filter;
- Transition bracket diagnosis;
- ORB/VWAP optimistic-fill studies;
- broad structural standalone-family tests;
- 1m feed/auth/routing work that is now proven.

## Next work

1. **No deployment/restart first.** The verified futures box remains healthy at minimal release `ac2b117ec1f9`; the Daily timing audit is offline only and does not justify runtime churn.
2. **Daily 2-2 rule identity decision:** keep the evidenced completed-close/favorable-pullback strategy explicit, or separately preregister a materially different first-touch contract. Do not silently change target placement, R:R floor, or fill model to rescue first-touch.
3. Let natural MNQ 4HR 1m armed-trigger evidence accumulate and specifically test the previously proven late-entry mechanism: correct arm, true 1m touch time, correct prior-completed 1H stop anchor, dedupe, and no stale 5m-close dependence.
4. Continue the 3-2-2 First Live 1m observer under `docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md`; no paper-fill discussion before the preregistered per-strategy sample/safety gate.
5. Refine a strategy only when evidence isolates a concrete mechanism defect. Do not tune targets, stops, risk caps, or session filters merely to improve results.
6. Keep LC_ZONE v1 / 4HR zone-target changes on HOLD; only reopen zone design under a new preregistration.
7. **Miyagi replay fix complete repo-side** — use the regenerated 2026-09-19 evidence as the current headline; keep the strategy parked. No runtime wiring or deployment.
8. Continue passive six-root context collection. Keep Daily completed-close evidence in its existing epoch and do not mix it with any future first-touch study.
9. First natural 4HR/3-2-2 1m event remains the next forward execution-mechanism review.

Cleanup snapshot immediately before this audit: local/origin `main` were clean at audit base `3fbd20c`, there were no open PRs, completed temporary futures worktrees were pruned, and the recovered pre-clean local work remains anchored on branch `recovery/pre-clean-main-20260918-post754` at `fe4da8d0`.

No live expansion.

## 2026-09-20 safety-defect reconciliation

A current-main source audit supersedes the earlier open-defect queue. Six reported defects were already repaired and covered by current implementation/tests: promotion-gate fail-closed semantics; configured campaign arms remaining visible at zero candidates; normal PaperBroker/replay fill-setting parity; evidence classification being separate from execution authority/status; daily reconciliation failing on critical non-trade-chain blockers; and exact-account pinning/fail-closed selection for the guarded Tradovate DEMO route.

One real semantic defect remained: `max_daily_loss` was multiplied by the contract quantity of the *next proposed setup*. That allowed a larger next order to enlarge an already-consumed daily account-loss allowance. The 2026-09-20 repair makes the real-book `max_daily_loss: 150` a fixed account/day realized-loss breaker. Contract quantity remains governed independently by sizing/per-trade risk controls. The 30% global drawdown survival floor and three-trades/day ceiling are unchanged. Isolated wide-stop evidence ledgers retain their explicit lane-scoped $300/$600 daily limits and 20% drawdown floors; they do not mutate the real-book rule.

Focused verification after the repair: `tests/test_risk_engine.py`, `tests/test_runner_budget_observation.py`, and `tests/test_wide_stop_ledger_paper.py` — **150 passed**; `git diff --check` passed. This is a safety semantics correction only: no strategy parameter, stop/target/R:R rule, instrument universe, broker route, live-trading authority, or wide-stop ledger policy was changed.
