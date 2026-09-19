# Futures Current Status — 2026-09-18

This is the concise operator-facing source of truth for the futures system as of the end of the 2026-09-18 work session. Historical audit documents remain evidence records; where an older summary conflicts with this file, this file governs current status unless a later dated document explicitly supersedes it.

## Verdict

**PAPER / SHADOW / DEMO EVIDENCE ONLY. NO LIVE EXECUTION APPROVED.**

### Late-session reconciliation — through audit base `3fbd20c`

- #771 merged: 3-2-2 First Live journal/state persistence repaired.
- #775 merged: Daily 2-2 CME holiday trading-day identity repaired.
- #776 merged: Miyagi completed-hour lookahead repaired. The 2026-09-19 causal/mechanics audit then found a separate replay defect: the trigger-touch 5m bar is excluded from immediate stop/T1 resolution. On the frozen corrected population, MNQ 2024-09-18 flips from TARGET win to same-trigger-bar pessimistic STOP; the 2-tick MNQ result falls from +$552.83 / PF 3.223 to **+$425.33 / PF 2.322**. Miyagi remains unproven/parked pending replay correction and evidence regeneration.
- #778 merged: Daily 2-2 independent epoch identity, fail-closed state loading, and epoch/SHA provenance repaired. CI and CodeQL green; **not deployed**.
- Generic 2-1-2/1-2-2 does not need the same decision-close IOC formula repair: its shared 15m paper/replay state machine reconstructs a causal pre-armed next-bar stop fill. **Operational execution parity is still absent** because no broker order is actually armed ahead of the watched 15m bar; a late live-broker substitute is correctly refused.
- Daily 2-2 timing audit is now complete. The activation baseline reproduced exactly (34 fills, +$13,885.18, PF 2.0171, max DD 25.1528%). Under current CME-day identity the completed-close variant remains positive (34 fills, +$13,571.68, PF 1.9482), but all 34 fills occurred only after 2–202 ticks of favorable close-vs-planned-entry retracement (median 33). A preregistered true-touch variant produced 0 admissible fills at 1/2/3 adverse entry ticks because fixed planned 2R plus strict actual-fill R:R >=2 is incompatible with any adverse touch slippage. Completed-close and first-touch must be treated as different strategies.
- Runtime was re-verified unchanged on immutable `ac2b117ec1f9`: release integrity OK (1,310 files), Tradovate demo, live trading false, one-contract hard cap, shadow schedule. No deploy or restart occurred.
- No natural 4HR/3-2-2 1m observer event exists yet. That item remains **WAITING FOR NATURAL EVIDENCE**.

The system is materially safer and more testable than it was at the start of the week, but no strategy is validated. The strongest current strategy lead is MNQ 4HR Re-Trigger, and even that remains **PROMISING BUT UNPROVEN** after correcting its entry-timing realism.

Active deployed futures release verified on the box:
- release: `ac2b117ec1f98f1470fc54330a4befc913ff15f2`
- service CWD matches that exact release
- `LIVE_TRADING_ENABLED=false`
- `TRADOVATE_ENV=demo`
- `SCHEDULE_MODE=always_on_shadow`
- `EXIT_MODE=static`
- `MAX_CONTRACTS_HARD_CAP=1`
- `FIVE_MIN_FEED_ENABLED=true`
- `ONE_MIN_TRIGGER_ENABLED=true`
- `ONE_MIN_322_OBSERVER_ENABLED=true`
- matching proof pin `EXPECTED_PROOF_ONE_MIN_322_OBSERVER_ENABLED=true`
- live-box drift guard: **OK**, no missing pins, no unpinned overrides, no mismatches

Repository `main` is ahead of the deployed futures release. Repository state must not be used as proof of deployed state. The VPS is intentionally pinned to `ac2b117ec1f98f1470fc54330a4befc913ff15f2`, a minimal runtime release built from prior live `6d5b224aa5c208cad0f1d39c09eda13c6171b98b` plus only #759's append-only 1m observer-response evidence hook. Do not deploy newer `main` merely to catch up.

Cleanup snapshot immediately before the Daily timing audit: repository audit base `3fbd20c`, local `main` was clean and matched origin, and there were **no open PRs**. Completed temporary futures worktrees were pruned. The earlier dirty local work recovered after its stash ref disappeared remains permanently anchored at `recovery/pre-clean-main-20260918-post754` commit `fe4da8d0a9432aeb3c3fe28d57522731e2623a0a`; do not delete that recovery branch until its preserved work is intentionally reconciled.

## What is good / high confidence

### Runtime and execution safety

High confidence:
- live trading is disabled;
- the deployed broker environment is DEMO;
- the 1m lane has no live broker authority;
- collection-only roots remain non-executable by construction;
- max 1 contract hard cap is loaded;
- the global three-trades/day safety cap remains defined;
- stop-gap realism exists for already-resting stops;
- invalid brackets and invalid payloads fail closed;
- deployment is through immutable release SHA + build/verify/promote;
- the proof-critical 1m enable flag is pinned by the live-box guard.

No evidence currently supports accidental real-money execution through the tested paths.

### 1m response-proof deployment — #759

A forward-proof defect was found before the first natural 4HR/3-2-2 observer event: the observer event files retained the trigger event but not the exact `process_alert()` response fields needed to prove that the 1m observation produced no fill/risk/execution authority.

#759 adds an append-only `tf1m/observer_response_audit_<date>.jsonl` row only when a 4HR or 3-2-2 observer event exists. It persists the payload/event identity plus actual response `decision`, `fill_is_none`, `risk_is_none`, `execution_reachable`, and resolution. A write failure is fail-soft and cannot alter alert handling.

The deployed release `ac2b117ec1f98f1470fc54330a4befc913ff15f2` is deliberately minimal: prior live `6d5b224aa5c208cad0f1d39c09eda13c6171b98b` plus exactly four #759 files. No risk, strategy, execution, config, or `webhook/runner.py` file changed. Exact candidate proof: **6,193 passed / 7 skipped**, immutable integrity **1,310/1,310 files**, isolated verify with broker forced to paper, and post-promote live-box guard PASS. Tradovate remained DEMO/flat; all captured campaign-state hashes were byte-identical across the restart; no response-audit row was fabricated by restart. The first real row still requires a natural observer event.

### Watcher journal-stall correction — #760

Post-#759 monitoring exposed a separate read-only watcher defect: `journal_not_advancing` used the newest top-level `bars_*.jsonl` across every root even though M2K/MGC/MCL/MBT are collection-only and intentionally never advance the main decision journal. A fresh collection-only MBT 15m bar therefore created a false stall.

#760 scopes that causal check to MNQ/MES decision-path 15m files only. Current-main watcher tests passed **148/148**. Because the VPS watcher intentionally trails unrelated newer presentation/triage code, only the proven one-hunk fix was backported to the exact live watcher source; installed watcher SHA-256 is `7d29872d1c85814ec10da50bd3152a57c1eea6873d89c0b1c86fbf8f9ee96563` with a pre-change backup retained. The watcher restart did **not** restart futures-bot. It then recorded the `ac2b117ec1f9` restart as sanctioned, cleared the false stall/release blockers, and returned to **zero BLOCKED findings**.

### Collector-census off-session correction — #764

A separate read-only reporting defect was proven after the Friday session: `collector_census` claimed futures heartbeat files were DEAD/ABSENT solely because the market was closed and the UTC date had rolled. It could also label an intentionally carried Daily 2-2 swing as exposed to stale 5m bars over the weekend.

#764 makes only the cadence-driven futures heartbeat rows CME-session aware. Closed-session silence is reported as `OFF_SESSION`; normal fail-closed freshness resumes when the session reopens, and expected off-session swing carry is not labeled stale-feed exposure. **80 adjacent census/report/watcher tests passed.** This is merged monitoring code only; it does not require a futures-bot or watcher restart because the live watcher already has its own causal session-safe checks.

### Feed coverage

All six configured futures roots now deliver authenticated 1m TradingView data and write isolated `tf1m/` bars:

- MNQ
- MES
- M2K
- MGC
- MCL
- MBT

At the post-deploy proof check, each root had a fresh 1m payload and an active `tf1m` file. Forward-observer baseline after activation: MNQ recorded 201 one-minute bars on 2026-09-18 from 17:38Z through 20:59Z. That began after the 4HR 09:30–11:00 ET and 3-2-2 10:00–11:00 ET observer windows, so zero 4HR/3-2-2 event files on that date is expected rather than evidence of a dead observer. Prior observer release `6d5b224` passed the targeted 1m isolation suite; current minimal release `ac2b117` additionally passed the exact full suite (**6,193 passed / 7 skipped**), and the 2026-09-18 futures journal has **0 TRADE rows** / no 1m execution rows. The first eligible natural observer session is the next trading day.

Role separation is deliberate:
- MNQ deployed behavior: 1m may observe an already-armed 4HR trigger;
- MNQ deployed behavior: the isolated 3-2-2 First Live observer is active behind pinned `ONE_MIN_322_OBSERVER_ENABLED=true`; it remains observation-only with no paper-fill or broker authority;
- MES: 1m context collection;
- M2K/MGC/MCL/MBT: strictly observation-only 1m collection;
- 1m itself does not create a strategy setup. The active 3-2-2 observer arms only from the completed canonical 5m 7AM/8AM/9AM structure and remains evidence-only.

### Backtest / replay infrastructure

High confidence for the scopes that have been explicitly audited:
- realistic same-bar ambiguity is pessimistic;
- stop-first treatment is used where price path is unknowable;
- commission/slippage stress is available;
- 4HR can now be compared under old trigger-backfill, completed-5m IOC, and causal pre-armed-touch entry models;
- causal completed-1H stop anchoring has been tested for 4HR;
- the structural-level R5/R6/R7 corpus/feature/attestation chain is reproducible;
- exact replay can be trusted for frozen, explicitly admitted scopes after parity checks;
- #751 makes Backtest→DEMO data identity fail closed by mechanically checking hash-pinned C14 session identity, frozen corpus file hashes, coverage counts, and explicit gap ledgers;
- #754 adds hash-bound per-outcome gap proof: counted resolved outcomes must bind strategy-specific dependency start, replay signal/entry/resolution timestamps, replay journal identity, code SHA, and frozen manifest; any declared-gap overlap or missing/mismatched proof fails closed.

#763 turns the preserved R5 entry-conditioning note into a deterministic hash-gated reproduction. It verifies exact MNQ/MES candidate and corpus hashes, freezes the recovered 16-bar/fill semantics, and reproduces the preserved headline values byte-identically. #768 then completes the preregistered one-variable ORB false-break entry-architecture A/B on that exact frozen population. Holding the original absolute stop/target fixed and changing only the entry from the later resting retouch to the completed signal-bar close made both instruments worse: MNQ **+0.0111R/all → -0.0596R/all**, MES **-0.0416R/all → -0.0847R/all**; both chronological halves were negative under signal-close on both instruments, and 1/2/3 adverse-tick stress worsened the result. All five preregistered support gates failed. Classification: **NO RUNTIME CHANGE / MIXED_OR_UNSUPPORTED**. The entry-conditioning diagnosis is real, but signal-close is rejected as the fix. The separate 2-2 entry-conditioning finding remains unanswered and requires its own preregistration if reopened.

Do **not** generalize this to “all backtests are trustworthy.” Fidelity is still strategy-specific, and older studies without the required historical timestamps/dependency-window proof remain unproven unless rerun or backfilled from sufficient raw data.

## What is not good / still uncertain

### No strategy is validated

There is no futures strategy that currently meets the full validation standard of:
- multiple-month stability;
- realistic executable fills;
- identical live/replay semantics;
- sufficient sample;
- controlled drawdown;
- slippage survival;
- independent forward confirmation.

Paper wins and one-off positive studies remain insufficient.

### 4HR timing defect changed the interpretation

The old 4HR historical study was too optimistic on entry timing.

Documented rule:
- the retrigger is a **level touch / break**;
- no completed 5m close is required by the strategy rule.

Old implementation:
- inferred the intrabar touch from the completed 5m bar;
- then historical replay could fill back at the original trigger price;
- forward paper/DEMO instead used current decision-time price + IOC tolerance.

That mismatch explains a large portion of the “late” / “unmarketable” behavior.

Controlled MNQ A/B, same 81 canonical candidates:

Old trigger-price backfill:
- 80 fills;
- +$2,886.60 at 1 tick.

Completed-5m close + 8-tick IOC:
- 37 fills;
- 44 no-fill/excluded;
- +$1,266.24;
- very uneven halves.

Pre-armed stop-touch:
- 80 fills;
- +$1,414.60 at 1 tick;
- +$1,354.60 at 2 ticks;
- +$1,294.60 at 3 ticks;
- both chronological halves positive at 3 ticks.

Conclusion:
- the old +$2.8k headline is not the executable headline;
- 4HR still survives a more realistic pre-armed touch model;
- therefore 4HR remains **PROMISING BUT UNPROVEN**, not validated and not broken solely because the old backfill was optimistic.

Two hour-boundary candidates also proved that detecting the trigger at the later 5m close can select a 1H stop candle that was not yet complete at the actual trigger time. The pre-armed model fixes that timing boundary.

### Other armed-trigger strategies

60M 3-2-2 First Live received its own frozen-population timing A/B on 2026-09-18.

Result:
- exact 34-candidate parity between the accepted research detector and canonical state machine;
- completed-5m close was more than the 32-tick IOC tolerance adversely detached on **13/34 (38.24%)** candidates;
- causal pre-armed First Live model at 3 adverse ticks: **33 fills / 33 resolved wins / 1 bracket-invalid no-fill, +$2,709.66**;
- H1 **+$1,366.34** and H2 **+$1,343.32**;
- LONG **+$1,754.34**, SHORT **+$955.32**;
- zero same-trigger-bar both-stop-and-target ambiguities.

Timing classification: **TIMING EDGE SURVIVES / PROMISING BUT UNPROVEN.**

This does not remove the separate current-account blocker: the historical 3-2-2 population remains incompatible with the account's stop-width and R:R architecture, and n=34 is still thin.

Additional timing reconciliation:
- 12HR Miyagi's completed-hour lookahead was repaired by #776, but `docs/futures-causal-mechanics-audit-2026-09-19.md` proves a second replay mismatch: the current replay suppresses stop/T1 resolution on the trigger-touch bar. The corrected trigger-bar A/B remains positive but weaker and still extremely thin; no runtime promotion follows.
- generic 2-1-2 / 1-2-2 does **not** share the completed-close IOC formula defect: the shared 15m paper/replay state machine reconstructs a pre-armed next-bar stop fill. It still lacks operational execution parity because the real runner never arms that order before the watched bar; non-Paper submission correctly fails closed.

No armed-trigger family inherits another family's timing result automatically.

Close-confirmed strategies such as ORB/VWAP families must not be converted to touch-entry merely because 1m data now exists.

### Daily 2-2 entry identity — audited

The separately preregistered Daily timing audit reproduced the original activation baseline exactly, then separated two entry architectures.

**Completed-close / favorable-pullback architecture:**
- current CME-day identity: 34 fills, **+$13,571.68**, PF **1.9482**;
- both chronological halves positive;
- max DD **26.6323%**;
- all 34 fills occurred after the completed trigger-bar close moved **2–202 ticks favorably** from the structural planned entry, median **33 ticks favorable**.

**Immediate first-touch architecture under unchanged rules:**
- 210 current-identity structural continuation days;
- 124 prior-bar-context-approved first-break opportunities;
- **0 admissible fills at 1, 2, or 3 adverse entry ticks**;
- every otherwise eligible one-tick touch failed ACTUAL_RR_BELOW_2.

Cause: the target is fixed at exactly 2R from the planned entry while the lane also demands actual-fill R:R >=2. Any adverse true-touch slippage makes actual R:R strictly <2.

Ruling:
- completed-close Daily 2-2 remains **PROMISING BUT UNPROVEN / PAPER ONLY**;
- true-touch Daily 2-2 under current target/R:R rules is **BROKEN / ZERO ADMISSIBLE FILLS**;
- the 34-fill historical result must not be described as first-touch evidence;
- choosing a different target/R:R/fill contract requires a separate preregistered strategy decision.

Audit: docs/daily22-trigger-timing-ab-2026-09-18.md.

## 4HR — current evidence picture

### Canonical MNQ 4HR

Classification: **PROMISING BUT UNPROVEN / PAPER ONLY.**

The corrected pre-armed touch model is the current timing-realistic reference for entry-mechanism research. Existing wide-stop forward-paper evidence remains a separate campaign and must not be silently reinterpreted as if it had always used the new trigger semantics.

### Higher-timeframe structure attribution

Using the canonical 4HR trade population as an offline diagnostic:

Strongest adequately populated 4H context:
- 4H 2→2 continuation;
- n=29;
- +$2,085.08 under the original canonical trade outcomes;
- positive H1 and H2;
- survives 3-tick slippage in that attribution.

Recent 4H compression:
- 2+ inside bars among the last five completed 4H classifications;
- n=21;
- +$1,291.42;
- positive both halves;
- survives 3 ticks.

But:
- an immediate 4H 1→2 precursor was negative;
- an immediate 1H 1→2 precursor was worse;
- therefore “require 1→2” is **not** supported as a simple entry gate.

The small combination 2+ recent 4H inside bars + 4H 2→2 continuation was strong but only n=5. Classification: **WAIT — too small**.

### Supply / demand target geometry — superseded by independent zone audit

The earlier 4HR sample suggested that targets inside an opposing LC_ZONE looked
better than targets beyond it. That result is now **historical diagnostic only**.

A preregistered independent LC_ZONE audit on the mature MNQ/MES 15m corpus found:

- current live nearest-zone identity differs from strict completed-HTF semantics
  by up to **9.39%**;
- zone qualification itself is unstable because historical impulses are
  re-evaluated against a later rolling MTR: about **21–24%** of detected zone
  identities appeared retroactively after their formation window, and about
  **9–14%** of expected-alive identity rows were absent from qualification
  without a break;
- 2 existing 4HR target-geometry rows used a 1H zone before its defining
  impulse 1H candle completed;
- one of those rows changes from `BEYOND_ZONE` to `BEFORE_ZONE` under
  strict completed-HTF semantics.

The corrected reaction-quality test used completed HTF formation and
persistent-at-formation zone identity. On 3,643 real formations and 2,698
paired first touches:
- actual clean 0.5-MTR rejection: **86.43%**;
- matched controls: **90.51%**;
- uplift **−4.08 pp**, 95% CI **[−5.67, −2.41] pp**;
- MNQ −3.90 pp;
- MES −4.25 pp;
- 1H −3.36 pp;
- 4H −6.83 pp;
- supply approximately flat (−0.21 pp);
- demand materially worse (−8.53 pp).

Preregistered statistical classification: **NO EVIDENCE OF ZONE QUALITY**. Reviewer ruling: **REACTION-AREA QUALITY INCONCLUSIVE / CONTROL MATCH FAILURE** because the nearest-neighbor placebo pool had poor common support on the preregistered width/distance features. The negative comparison is retained but is not treated as proof that LC_ZONE is worse than a properly matched placebo.

The independent timing/identity failures above do not depend on the placebo design and are sufficient to block target-rule use.

Therefore:
- do **not** use the old 4HR inside/beyond-zone table to authorize target clipping;
- do **not** promote LC_ZONE v1 into a gate or target rule;
- do **not** run the planned canonical-target vs zone-clipped-target A/B under
  the current detector;
- any alternative zone definition is a new preregistered study, not a rescue
  of this result.

## What we are collecting now

### 1m TradingView context

All six roots:
- MNQ
- MES
- M2K
- MGC
- MCL
- MBT

Purpose:
- lower-latency trigger/context evidence;
- not a new signal engine;
- not permission to increase instrument scope.

### Existing forward evidence lanes

The previously active futures evidence campaigns remain conceptually distinct:
- MNQ wide-stop / 4HR family paper evidence;
- MNQ 3-2-2 evidence;
- MNQ Daily 2-2 continuation;
- MES 15m 1-2-2;
- cross-instrument observation;
- MNQ Asia D+EMA observational cohort.

Do not merge balances, epochs, or evidence populations across these lanes.

### Structural-level research

R5 candidate/outcome corpus is sealed and reproducible.
R6 features are frozen.
R7 independent attestation exists.
P8 exact outcomes show the broad structural population is net negative; no generic structural family is validated.
The consumed MES OOS holdout produced partial replication only and cannot be reused as fresh confirmation.

## What data we still need

For 4HR:
- prospective 1m armed-trigger evidence;
- actual trigger detachment versus prior 5m-close timing;
- forward fills under the causal trigger model;
- enough n to judge the 4H continuation / compression cells;
- no current LC_ZONE target A/B: the v1 detector failed independent quality validation.

For 3-2-2:
- timing A/B is complete and survives 3-tick stress;
- prospective First Live evidence under natural lower-latency observations;
- more sample, including losses;
- confirmation that a future lower-latency observer reproduces the same setup population rather than creating a new one.

For Miyagi:
- completed-hour lookahead is repaired;
- **trigger-bar replay semantics still require correction** before the historical headline is treated as execution-faithful evidence;
- after the causal trigger-bar A/B, MNQ at 2 ticks is 8 fills, 6W/2L, +$425.33, PF 2.322; H2 is still only one fill;
- much larger sample remains required.

For Daily 2-2:
- keep prospective completed-close/favorable-pullback evidence separate from any future first-touch population;
- decide the intended entry contract before any target/R:R/fill redesign;
- if first-touch is reopened, preregister the changed target/R:R/fill contract before testing it.

For MES 1-2-2:
- prospective evidence under realistic accounting;
- current edge is very thin and fails stronger slippage;
- treat current PaperBroker/replay results as **reconstructed pre-armed evidence**, not proof that the broker-connected runtime can capture the same entry; any execution promotion needs a separately proven pre-armed/lower-latency mechanism.

For M2K/MGC/MCL/MBT:
- enough 1m/5m/15m observation history before any strategy or execution expansion is considered.

## Can we backtest well now?

**Answer: yes for audited scopes; no as a blanket statement.**

What we can do well now:
- deterministic replay over known bar corpora;
- causal candidate reconstruction for audited detectors;
- pessimistic same-bar bracket resolution;
- slippage/commission stress;
- chronological halves;
- exact entry-model A/B for strategies whose trigger semantics are specified;
- causal completed-HTF context studies; LC_ZONE v1 itself is now explicitly blocked from target-rule use by the independent zone audit;
- frozen-manifest / hash-backed structural research.

What still blocks blanket trust:
- some strategies still have live/replay timing differences;
- some historical studies used optimistic decision-time fills;
- higher-timeframe/touch strategies need intrabar semantics checked individually;
- TradingView/Pine versus backend formula parity is not globally proven;
- continuous-symbol data is not the same thing as dated-contract identity;
- one strategy passing replay fidelity does not certify another.

Rule: **backtest credibility is per strategy and per execution model, never inherited globally.**

## Current confidence map

High confidence:
- runtime is not live;
- deployed 1m feed isolation;
- all six 1m feeds are arriving;
- collection-only roots cannot execute;
- pessimistic PaperBroker ambiguity;
- 4HR has a real 5m-close latency problem;
- old 4HR trigger-price backfill overstated executable results;
- pre-armed MNQ 4HR remains positive after 1/2/3-tick stress;
- broad generic structural family is not an edge.

Medium confidence / promising:
- MNQ 4HR itself;
- MNQ 60M 3-2-2 historical signal under corrected First Live timing;
- MNQ Daily 2-2 **completed-close / favorable-pullback** historical hypothesis, with entry identity explicitly separated from first-touch;
- 4H 2→2 continuation context;
- repeated recent 4H compression as context.

Broken / blocked mechanics:
- MNQ Daily 2-2 **first-touch** architecture under the current fixed planned 2R target + actual-fill R:R >=2 rule: zero admissible fills under 1/2/3 adverse entry ticks.

Low confidence / unresolved:
- any supply/demand target rule under LC_ZONE v1;
- whether a separately preregistered replacement zone construct would behave better;
- failed-2 / reversal discrimination at zones;
- prospective 3-2-2 First Live behavior beyond the consumed n=34 historical set;
- Miyagi;
- cross-instrument edge transfer;
- any live expectancy claim.

## Do not change yet

Do not:
- enable live trading;
- give 1m bars setup-discovery authority;
- let collection-only instruments enter DecisionEngine/RiskEngine/broker paths;
- replace 5m/15m feeds with 1m;
- change global risk caps to rescue a strategy;
- convert close-confirmed ORB/VWAP rules into touch triggers;
- tune target/stop/zone thresholds off the small diagnostic cells;
- change Daily 2-2 target placement, actual-fill R:R floor, or fill model to rescue first-touch without a separate preregistered rule decision;
- call 4HR validated.

## Late 2026-09-18 addendum — mechanical fix + parity/state audits

See `docs/futures-causal-parity-and-state-audits-2026-09-18.md`. Summary: the 3-2-2 First Live `DailyState` journal/restore omission is fixed in PR #771 (not deployed); 3-2-2 remains 0/33 compatible with the account stop-width / R:R caps under corrected pre-armed timing; #778 independently fixes Daily 2-2 epoch identity/state provenance repo-side (not deployed; preserve the existing epoch on any future sanctioned release); generic 2-1-2/1-2-2 does not share the decision-close IOC defect; replay 4H bars are UTC-anchored while TradingView 4H bars are CME-session-anchored — no decision authority today (HTF gate off, `require_htf_alignment` false) but a blocker for ever enabling that gate with `htf_direction_source=payload`. The subsequent Daily 2-2 timing audit closes its previously unmeasured entry-identity question: completed-close/favorable-pullback evidence reproduces, while true-touch under unchanged target/R:R rules has zero admissible realistic fills.

## Safe next work order

1. **Preserve collection epochs / no further runtime churn** — #759 is deployed in minimal release `ac2b117ec1f9` and #760 is installed only in the read-only watcher. No further futures-bot or watcher restart is required now.
2. **Daily 2-2 rule identity decision — research only, no code change yet** — explicitly choose whether the strategy is the currently evidenced completed-close/favorable-pullback contract or whether a distinct first-touch breakout strategy is worth reopening. The current true-touch target/R:R contract is mechanically broken; do not rescue it by silent tuning.
3. **4HR prospective 1m evidence** — verify trigger touch timing, stop anchor, dedupe, and the persistent response fields `fill_is_none`, `risk_is_none`, `execution_reachable` on natural signals. This is the direct forward check for the previously proven completed-5m late-entry defect.
4. **3-2-2 prospective 1m evidence** — timing survives the offline causal model; now collect natural First Live arms/touches and the same persistent response proof under `docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md`. No paper-fill discussion before the preregistered mechanism threshold.
5. **Refine only from proven mechanism failures** — if forward evidence shows stale arm state, wrong trigger timestamp, wrong completed-1H stop anchor, duplicate trigger handling, or another live/replay timing mismatch, isolate and repair that exact defect. Do not tune targets/stops/risk merely because P&L is weak.
6. **LC_ZONE v1 is HOLD** — do not tune or rescue the failed quality audit. Reopen zone design only under a new preregistration.
7. **Fix Miyagi replay identity before any further trust/promotion work** — #776 fixed completed-hour lookahead, but the 2026-09-19 causal/mechanics audit proves the trigger-touch bar is still wrongly excluded from immediate stop/T1 resolution. Correct that research replay, regenerate the frozen evidence, and keep Miyagi parked; no runtime wiring or deployment is justified.
8. Continue passive evidence collection; do not expand instruments or execution scope. Keep Daily completed-close evidence in its existing epoch and do not mix it with any future first-touch study.
9. **Next natural execution-mechanism checkpoint = first 4HR/3-2-2 1m observer event.** On that event, audit arm timing → true touch → completed-1H stop anchor → dedupe → durable response proof → zero execution leakage. If it exposes a concrete mechanism defect, repair only that defect. If it passes, keep collecting.

## Bottom line

The system is now substantially better at answering **why a trade was late, why a historical fill was optimistic, and whether a trigger was actually executable**.

The important change in confidence is not “we found a winning bot.” It is:

- we can now distinguish setup logic from trigger timing;
- we have a lower-latency evidence feed;
- we caught and quantified a real historical-fill optimism defect;
- MNQ 4HR and 60M 3-2-2 both survive their own causal trigger-timing corrections;
- neither corrected historical result overrides sample-size, forward-proof, or account-risk blockers;
- and the system remains contained while we gather proof.

**No proof, no run.**
