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
- **Generic “require 1→2” idea:** tested and not supported as a simple 4HR gate.
- **Simple “touch supply/demand = reverse” idea:** tested and not supported as a blanket rule.

## Current blockers

### 1. 4HR prospective trigger-time parity

Offline pre-armed touch survives stress, but the deployed 1m lane is evidence-only. We still need natural forward examples proving:
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

### 3. 3-2-2 trigger timing

The documented rule is “First Live” / no candle-close requirement, but the implementation can still infer a touch from completed 5m OHLC.
Need a frozen-population pre-armed-touch A/B analogous to 4HR before treating historical IOC evidence as final.

### 4. Miyagi timing and sample

Only reopen if strategically necessary.
Current sample is too small and timing parity is not proven under the new standard.

### 5. MES 1-2-2 remains thin

Forward accounting edge is slight and stronger slippage turns it negative.
Continue evidence; do not retune from this sample.

### 6. Cross-instrument evidence is observation only

M2K/MGC/MCL/MBT now have better 1m coverage, but this does not justify strategy expansion. Need enough observation history before defining or evaluating any execution candidate.

## Strategy state

- **MNQ 4HR Re-Trigger:** PROMISING BUT UNPROVEN / PAPER ONLY.
- **MES 4HR:** BROKEN / WAIT.
- **MNQ 3-2-2 First Live:** positive historical signal under old timing model but current real-account risk incompatible; trigger-timing audit pending.
- **Miyagi:** thin / parked; timing audit only if reopened.
- **Daily 2-2:** paper evidence lane; no live claim.
- **MES 15m 1-2-2:** PROMISING BUT UNPROVEN / thin.
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
- manifest/hash-backed structural research.

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

1. Let natural MNQ 1m armed-trigger evidence accumulate.
2. 3-2-2 trigger-timing A/B.
3. Keep LC_ZONE v1 / 4HR zone-target changes on HOLD; only reopen zone design under a new preregistration.
4. Reassess whether any strategy deserves paper-fill authority from 1m.
5. Continue passive six-root context collection.

No live expansion.
