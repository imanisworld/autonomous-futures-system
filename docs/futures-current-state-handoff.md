# Futures — Current State Handoff

_As of 2026-09-09. This is the single current futures handoff. Historical audit docs remain evidence records, but they do not override this file. Repository state is not proof of VPS/deployment state; verify the box separately before claiming anything is running._

## Verdict

**PAPER ONLY / FORWARD EVIDENCE COLLECTION. NO LIVE OR EXTERNAL-BROKER EXECUTION IS APPROVED.**

Core rule: **No proof, no run.**

PR **#545** is merged to `main` as commit **`9caaaa3e2fbe5cb6ff20941229e3498794bcb6de`**. Its final merge-ref CI passed **4,952 tests, 7 skipped**. The repository now contains the clean PaperBroker-only three-lane collector, Daily persisted-state integrity guard, and this authoritative handoff.

**Nothing is claimed active on the VPS yet.** Deployment/environment/feed/epoch verification is the remaining activation gate.

## Current paper campaign

### 1. MNQ 4HR Re-Trigger

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE**
- canonical strategy detector; do not rewrite
- 1 MNQ contract
- isolated hypothetical starting ledger: **$4,000**
- maximum planned stop: **300 ticks / $150**
- minimum planned R:R: **1.0**
- static documented bracket
- decision-time **8-tick IOC** admission
- pessimistic same-bar resolution
- no promotion path

### 2. MNQ 60M 3-2-2 First Live

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE**
- canonical First Live detector; do not replace with generic 3-2-2 research logic
- 1 MNQ contract
- simulated starting capital: **$5,000 maximum**
- maximum planned stop: **600 ticks / $300**
- strategy-scoped R:R floor remains disabled for this evidence contract
- static documented bracket
- decision-time **8-tick IOC** admission
- pessimistic same-bar resolution
- no promotion path

The internal journal key `wide_stop_6k` is retained only for historical path continuity. **Its current simulated starting balance is $5,000. The name is not permission to assume $6,000 of capital.**

### 3. MNQ Daily 2-2 continuation

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE**
- Daily timeframe; causal first break only
- 1 MNQ contract
- isolated hypothetical starting ledger: **$5,000**
- natural prior-Daily-range stop; no stop tightening
- fixed **2R** target
- decision-time **8-tick IOC** admission
- 1 adverse entry tick
- actual fill R:R must remain **>= 2.0**
- actual fill-to-stop planned risk must remain **<= $1,750**
- one Daily swing position at a time
- positions may remain open across sessions; no day-strategy EOD flatten
- $1.48 round-turn commission
- pessimistic same-bar handling
- 20% and 25% drawdown warnings
- **30% hard paper halt**
- no external broker and no promotion path

Preregistered frozen-corpus IOC result used to justify forward paper collection:

- 34 non-overlapping trades
- net **+$13,885.18**
- PF **2.02**
- H1 positive / H2 positive
- 2024, 2025 and 2026 positive
- max historical drawdown **25.15%**
- the 30% hard halt was not reached

This is paper evidence, not a live-capital claim.

## Campaign-wide safety boundary

The paper router enforces a maximum of **3 new fills per trading day across this campaign**. The isolated day-strategy config is capped at one 4HR fill and one 3-2-2 fill; Daily uses one causal first-break opportunity and one open swing maximum. The real/global risk configuration remains untouched.

No campaign code may:

- change `risk_rules.yaml` to make these strategies fit the real book;
- add parked strategies to the active real-book `enabled_concepts`;
- submit to Tradovate or any other external broker;
- reuse a live/demo broker adapter as a shortcut for evidence collection;
- average down, omit the stop, or invent missing fills/outcomes;
- convert paper results into a promotion decision automatically.

## Execution route

The approved route is **PaperBroker only**.

`context/wide_stop_execution.py` is the route authority. Its valid route set is only `paper_sim`; stale values such as `tradovate_demo`, `tradovate`, or `live` resolve to disabled/no action.

The old external-broker branch/import was removed from the 5-minute campaign hook. Regression tests pin both the route selector and the absence of the old demo-runtime hook.

Any future external-broker route is a separate proposal requiring a new audit. It is not part of this campaign.

## What remains least certain

### A. The three ledgers are not one proven $5k portfolio

4HR uses a $4k isolated ledger; 3-2-2 and Daily each use their own $5k evidence ledger. This proves strategy-level survival under those assumptions. It **does not** prove all three can share one real $5,000 account simultaneously, because combined open risk, cross-strategy loss clustering, and combined drawdown have not been established.

**Current mitigation:** keep attribution and balances separate. Do not aggregate the three paper balances or call this a unified $5k portfolio. Any future shared-capital study must replay the three chronological signal streams through one balance/risk state.

### B. Daily multi-day restart/deploy behavior

Daily can hold overnight, so persisted swing state is proof-critical.

**Mitigation implemented:** `context/daily_22_state_integrity.py` runs before the Daily collector. Malformed, unreadable, wrong-epoch, impossible-bracket, or unexpectedly missing state with existing evidence fails closed rather than resetting to a fresh $5k flat ledger.

What remains unproven is real forward restart/deploy behavior on the VPS. That must be observed during collection.

### C. Five-minute feed completeness

Forward outcomes depend on the sequence of completed 5-minute bars. If bars are missing while a position is exposed, the system cannot safely infer that neither stop nor target was touched during the gap.

**Current mitigation:** a known material feed gap makes the affected outcome invalid/unresolved. Do not repair missing path data with later OHLC, MAE summaries, or optimistic assumptions. Complete automated exchange/session gap detection remains a forward-monitoring item.

## Repository verification completed

- #545 merged to `main`: `9caaaa3e2fbe5cb6ff20941229e3498794bcb6de`
- final merge-ref CI: **4,952 passed, 7 skipped**
- only valid campaign execution route: `paper_sim`
- no external-broker runtime file in the #545 diff
- no `risk_rules.yaml` change
- Daily state-integrity guard present
- capital/role regression tests present
- Miyagi remains shadow-only
- superseded handoff/Daily PRs closed while Git history was retained

## VPS activation gates — all required

Before the paper campaign is enabled on the box, verify on the exact release being deployed:

1. deployed release SHA is the intended `main` commit or a reviewed descendant containing #545;
2. `LIVE_TRADING_ENABLED=false` in effective runtime configuration;
3. campaign route resolves to **`paper_sim`** only;
4. `WIDE_STOP_LEDGER_MODE=paper_sim` is explicitly set;
5. `WIDE_STOP_LEDGER_EPOCH_START` is a fresh offset-aware timestamp;
6. `FIVE_MIN_FEED_ENABLED=true` and the MNQ 5-minute stream is actually arriving;
7. proof-critical environment pins match the release;
8. no stale paper position/state exists from a prior accounting epoch;
9. the real strategy book and `risk_rules.yaml` remain unchanged by campaign activation;
10. paper journals/state directories are writable and isolated from the real book.

Missing proof on any item means **do not enable the campaign**.

## Other strategy status — separate from this campaign

- **12HR Miyagi:** shadow/research only; no fills.
- **Inverse ORB:** old positive headline retired after decision-time/bracket-geometry correction; do not revive from the invalid baseline.
- **VWAP Hold:** corrected decision-time evidence negative; no promotion.
- **Transition reclaim:** separate repair investigation; not part of these three lanes.
- **MES 1-2-2:** separate evidence/root-cause investigation; not part of these three lanes.
- **ORB Reclaim / source ORB Breakout:** negative/weak corrected evidence; not part of this campaign.

## Superseded / historical PRs

These are not current execution authority:

- **#538** — superseded; external-broker work appeared on the branch; closed unmerged.
- **#543** — superseded by #545 after post-green external-broker commits appeared; closed unmerged.
- **#527** — initial Daily STRAT baseline harness; superseded by completed Daily isolation/IOC evidence; closed.
- **#534** — earlier handoff refresh; closed as superseded.
- **#539** — Daily cross-check documentation; historical evidence only; closed.
- **#476** — September 7 handoff; historical only; closed.

Do not delete historical evidence files merely because they are old. Closed PRs and Git history remain the audit trail. Separate active investigations such as Transition and MES 1-2-2 remain open because they are not duplicates of this campaign.

## Do not touch

- canonical 4HR detector/formula
- canonical 60M 3-2-2 First Live detector/formula
- Daily 2-2 stop/target/filter bundle without a new preregistered one-variable study
- global `risk_rules.yaml` merely to make these paper lanes pass
- real-book enabled strategy list
- bracket-validity guard
- pessimistic same-bar handling

## Safe next step

**Repository work for this paper campaign is complete.**

Next:

1. verify the exact VPS release and all activation gates above;
2. enable only the PaperBroker campaign after those checks pass;
3. collect forward evidence, specifically watching restart/state integrity and 5-minute feed completeness;
4. keep the three ledgers separate until a chronological shared-$5k portfolio study proves otherwise;
5. do not promote based on historical/backtest results alone.
