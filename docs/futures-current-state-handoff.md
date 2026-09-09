# Futures — Current State Handoff

_As of 2026-09-09. This is the single current futures handoff. Historical audit docs remain evidence records, but they do not override this file. Repository state is not proof of VPS/deployment state; verify the box separately before claiming anything is running._

## Verdict

**PAPER ONLY / FORWARD EVIDENCE COLLECTION. NO LIVE OR EXTERNAL-BROKER EXECUTION IS APPROVED.**

Core rule: **No proof, no run.**

PR **#545** is merged to `main` as commit **`9caaaa3e2fbe5cb6ff20941229e3498794bcb6de`**. Its final merge-ref CI passed **4,952 tests, 7 skipped**. The repository contains the clean PaperBroker-only MNQ three-lane collector, Daily persisted-state integrity guard, and this authoritative handoff.

**Both paper campaigns are now ACTIVE on the VPS.** Verified live on 2026-09-09:

- deployed release: **`08dd40a43505c97f9e021c7cdbf895613c46b524`** (contains #545, #547, #549, #551, #552, #553, #554, #555), promoted via `scripts/atomic_release.sh build/verify/promote`;
- **MNQ three-lane campaign** (#545): `WIDE_STOP_LEDGER_MODE=paper_sim`, epoch **`2026-09-09T04:21:04Z`**;
- **MES 15m 1-2-2 lane** (#555): `MES_122_PAPER_MODE=paper_sim`, epoch **`2026-09-09T06:12:55Z`**;
- `LIVE_TRADING_ENABLED=false`, `SCHEDULE_MODE=always_on_shadow`, zero external-broker orders placed.

Repository state is still not proof of box state — re-verify the box (`/proc/<pid>/cwd`, `/proc/<pid>/environ`) before asserting anything is running.

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

### 4. MES 15m 1-2-2 (isolated lane, added 2026-09-09 by #555)

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE**
- canonical `strat_212_122` detector; do not retune entry, stop, target or filters
- MES only, **15m only** (`expected_timeframe_minutes=15` pinned in the lane config, not inherited)
- 1 MES contract; all sizing ladders and win-streak scaling off
- isolated hypothetical starting ledger: **$1,500**
- original structural stop, fixed **2R** target, static exit, no breakeven trail, no runner
- swing holds allowed across sessions and weekends
- one open position at a time
- gap-aware stop fills; pessimistic same-bar handling
- $1.48 round-turn commission
- 20% / 25% drawdown warnings, **30% hard paper halt**
- no promotion path

**Judge this lane on its realistic ledger, not raw PaperBroker P&L.** PR #553 proved that
`strat_122` never routes through the broker's entry-fill machinery — `replay_engine.py` and
`webhook/runner.py` both open it via `restore_position()` at the causal entry, which applies no
adverse slippage, and same-bar `pre_resolved` trades exit via `force_resolve()` at the exact
structural price. Forward paper inherits that same optimism. The lane therefore charges, for
accounting: one adverse tick on every entry, one more on same-bar exits, plus commission.
`realistic_balance`, the warnings and the 30% halt all run off that ledger;
`raw_paper_balance_diagnostic_only` is kept for diagnostics and never drives the halt.

Preregistered in-sample result used to justify forward collection, at a genuine **1 adverse tick
per leg** (313-day corpus, 40 trades, 11W/29L): net **+$32.05**, PF **1.035**, max MTM drawdown
**$229.11**, population stable (0 bracket-invalid, 0 disappeared). At 2 ticks per leg it is
**negative** (−$57.95). The older **+$90.80 / PF 1.104** figure is the unslipped-entry number and
is **retired as a realism headline**; the still older 38-trade / +$27.51 result is superseded
outright. PF 1.035 sits far inside the null band (p95 1.94) — this is a thin candidate under
observation, not an established edge.

## Campaign-wide safety boundary

The 5-minute paper router enforces a maximum of **3 new fills per trading day across the MNQ three-lane campaign**. The isolated day-strategy config is capped at one 4HR fill and one 3-2-2 fill; Daily uses one causal first-break opportunity and one open swing maximum. The real/global risk configuration remains untouched.

The **MES 1-2-2 lane is not under that router or that 3-fill cap** — it is a separate observer on the 15-minute webhook path, bounded instead by one open position at a time plus every ordinary gate evaluated against its own isolated config.

No campaign code may:

- change `risk_rules.yaml` to make these strategies fit the real book;
- add parked strategies to the active real-book `enabled_concepts`;
- submit to Tradovate or any other external broker;
- reuse a live/demo broker adapter as a shortcut for evidence collection;
- average down, omit the stop, or invent missing fills/outcomes;
- convert paper results into a promotion decision automatically.

## Execution route

Paper collection (**PaperBroker**) is unconditional and always runs first, in its own error boundary.

`context/wide_stop_execution.py` is the route authority. Since #549/#551/#552 its valid route set is `paper_sim` (default) **and** `tradovate_demo`; any other value (`tradovate`, `live`, typos) resolves to `disabled`, which no longer suppresses paper collection.

`tradovate_demo` is an explicit, proof-pinned lane (`WIDE_STOP_LEDGER_EXECUTION_ROUTE` + `EXPECTED_PROOF_WIDE_STOP_LEDGER_EXECUTION_ROUTE`) that runs **additively** alongside paper for 4HR Re-Trigger and 60M 3-2-2 only. Daily 2-2 is never demo-eligible. Placing demo orders additionally requires the lane-local pair `WIDE_STOP_DEMO_EXECUTION_ENABLED` + `EXPECTED_PROOF_WIDE_STOP_DEMO_EXECUTION_ENABLED` and a session in `WIDE_STOP_DEMO_SESSIONS`; the route pair alone invokes the lane but permits no orders. The demo lane feeds its own lane-local schedule mode to `adaptive.execution_gate.order_placement_allowed` and never reads the box-wide `SCHEDULE_MODE`. Demo evidence is journaled under its own root, isolated from paper evidence.

Regression tests pin the route selector, the paper/demo coexistence in the 5-minute hook, and the demo journal isolation.

The **MES 1-2-2 lane has no route of its own and cannot acquire one.** It re-evaluates MES alerts on an isolated config *copy* that pins `paper_mode=True`; `webhook/runner.py` derives `simulate` from that, which selects `_paper_broker()` for execution and forces `_using_tradovate_position` False for the position's whole lifecycle. That holds regardless of the box's `BROKER` (currently `tradovate`) or `SCHEDULE_MODE`. A regression test asserts the non-paper broker constructor is never called even with `BROKER=tradovate`. The lane writes only to `logs/hypothetical_ledger/mes_122_1500`.

Any route beyond these two is a separate proposal requiring a new audit.

## What remains least certain

### A. The three MNQ ledgers are not one proven $5k portfolio

4HR uses a $4k isolated ledger; 3-2-2 and Daily each use their own $5k evidence ledger. This proves strategy-level survival under those assumptions. It **does not** prove all three can share one real $5,000 account simultaneously, because combined open risk, cross-strategy loss clustering, and combined drawdown have not been established.

**Current mitigation:** keep attribution and balances separate. Do not aggregate the three paper balances or call this a unified $5k portfolio. Any future shared-capital study must replay the three chronological signal streams through one balance/risk state. The MES 1-2-2 lane's $1,500 ledger is separate again and must not be pooled with these either.

### B. Daily multi-day restart/deploy behavior

Daily can hold overnight, so persisted swing state is proof-critical.

**Mitigation implemented:** `context/daily_22_state_integrity.py` runs before the Daily collector. Malformed, unreadable, wrong-epoch, impossible-bracket, or unexpectedly missing state with existing evidence fails closed rather than resetting to a fresh $5k flat ledger.

What remains unproven is real forward restart/deploy behavior on the VPS. That must be observed during collection.

### C. Five-minute feed completeness

Forward outcomes depend on the sequence of completed 5-minute bars. If bars are missing while a position is exposed, the system cannot safely infer that neither stop nor target was touched during the gap.

**Current mitigation:** a known material feed gap makes the affected outcome invalid/unresolved. Do not repair missing path data with later OHLC, MAE summaries, or optimistic assumptions. Complete automated exchange/session gap detection remains a forward-monitoring item.

### D. Whether MES 1-2-2 has an edge at all

At a realistic 1 adverse tick per leg the in-sample result is **+$32.05 / PF 1.035** over 40
trades, and **2 ticks per leg is negative**. PF 1.035 is far inside the null band (p95 1.94), and
n=40 cannot separate that from noise in either direction. The out-of-sample window was n=3
isolated / n=1 control — far too small to validate anything.

What #553 did settle is that this is no longer a *simulator* question: the population is stable
under realistic execution cost (0 bracket-invalid, 0 trades disappeared, no outcome flips at 1-3
ticks), and replay and forward paper share the same fill treatment. What remains open is simply
whether the edge exists.

**Current mitigation:** forward paper only, fixed 1 MES, no scaling, no parameter changes during
the epoch, judged on the realistic ledger, with a 30% hard halt. A materially larger independent
sample is required before the word "validated" is used.

## Repository verification completed

- #545 merged to `main`: `9caaaa3e2fbe5cb6ff20941229e3498794bcb6de`
- final merge-ref CI: **4,952 passed, 7 skipped**
- only valid campaign execution route at #545: `paper_sim` (the additive `tradovate_demo` lane arrived later via #549/#551/#552 — see Execution route)
- no external-broker runtime file in the #545 diff
- no `risk_rules.yaml` change
- Daily state-integrity guard present
- capital/role regression tests present
- Miyagi remains shadow-only
- superseded handoff/Daily PRs closed while Git history was retained
- **#547** paper/replay parity (gap-aware stop pricing; MES-only `strat_122` 8h swing exemption) merged `4112fe3`
- **#553** per-leg execution-realism gate merged `dae11f1` — evidence only, no runtime file touched
- **#555** isolated MES 1-2-2 lane merged `08dd40a` — no `risk_rules.yaml`, `instruments.allowed` or `enabled_concepts` change; ships off by default

## VPS activation gates — verified at activation, and required again for any re-activation

Both campaigns cleared these on 2026-09-09 (MNQ at `04:21:04Z`, MES 1-2-2 at `06:12:55Z`). Re-verify every item on the exact release being deployed before any future enable, re-enable, or epoch reset:

1. deployed release SHA is the intended `main` commit or a reviewed descendant containing #545;
2. `LIVE_TRADING_ENABLED=false` in effective runtime configuration;
3. campaign route resolves to **`paper_sim`** (paper-only collection) or, if the demo lane is intended, **`tradovate_demo`** with all four demo keys set and `demo_config_errors()` empty;
4. `WIDE_STOP_LEDGER_MODE=paper_sim` is explicitly set;
5. `WIDE_STOP_LEDGER_EPOCH_START` is a fresh offset-aware timestamp;
6. `FIVE_MIN_FEED_ENABLED=true` and the MNQ 5-minute stream is actually arriving;
7. proof-critical environment pins match the release;
8. no stale paper position/state exists from a prior accounting epoch;
9. the real strategy book and `risk_rules.yaml` remain unchanged by campaign activation;
10. paper journals/state directories are writable and isolated from the real book.

Missing proof on any item means **do not enable the campaign**.

For the MES 1-2-2 lane specifically, items 4-6 read: `MES_122_PAPER_MODE=paper_sim`;
`MES_122_PAPER_EPOCH_START` a fresh offset-aware timestamp (the lane fails closed without one, and
a naive timestamp is rejected at config load); MES 15-minute alerts actually arriving. Both
variables are in `PROOF_CRITICAL_RUNTIME_OVERRIDES`, so each needs its matching
`EXPECTED_PROOF_*` pin — an active but unpinned proof-critical override makes the box
irreproducible and degrades the box guard.

What was verified live at MES activation: lane `paper_sim` and active; epoch matching exactly;
realistic balance $1,500 with 0 resolved trades; journal writing under
`logs/hypothetical_ledger/mes_122_1500`; MES 15m alerts reaching the lane and producing genuine
gated decisions; 1 MES contract; no Tradovate route; real-book decisions byte-identical before and
after (MES still `NO_TRADE "not in allowed universe"`, real config unchanged at
`allowed_instruments=['MNQ']` / `enabled_concepts=['orb_breakout']`); the MNQ lanes' persisted
state byte-identical across the restart.

## Other strategy status — outside the four active lanes

- **12HR Miyagi:** shadow/research only; no fills.
- **Inverse ORB:** old positive headline retired after decision-time/bracket-geometry correction; do not revive from the invalid baseline.
- **VWAP Hold:** corrected decision-time evidence negative; no promotion.
- **Transition reclaim:** separate repair investigation; not part of these four lanes.
- **ORB Reclaim / source ORB Breakout:** negative/weak corrected evidence; not part of this campaign.

## Superseded / historical PRs

These are not current execution authority:

- **#538** — superseded; external-broker work appeared on the branch; closed unmerged.
- **#543** — superseded by #545 after post-green external-broker commits appeared; closed unmerged.
- **#527** — initial Daily STRAT baseline harness; superseded by completed Daily isolation/IOC evidence; closed.
- **#534** — earlier handoff refresh; closed as superseded.
- **#539** — Daily cross-check documentation; historical evidence only; closed.
- **#476** — September 7 handoff; historical only; closed.

Do not delete historical evidence files merely because they are old. Closed PRs and Git history remain the audit trail. Transition reclaim remains a separate open investigation because it is not a duplicate of this campaign. MES 1-2-2 is no longer separate — it is lane 4 above.

## Do not touch

- canonical 4HR detector/formula
- canonical 60M 3-2-2 First Live detector/formula
- Daily 2-2 stop/target/filter bundle without a new preregistered one-variable study
- global `risk_rules.yaml` merely to make these paper lanes pass
- real-book enabled strategy list
- canonical `strat_212_122` detector and the MES 1-2-2 entry/stop/2R-target bundle
- bracket-validity guard
- pessimistic same-bar handling

## Safe next step

**Repository work is complete and both campaigns are collecting.** The remaining work is
observation, not building.

Next:

1. let forward evidence accumulate; change nothing about strategy, sizing, stops, targets, filters
   or routes during an epoch;
2. watch restart/state integrity and 5-minute feed completeness for the MNQ lanes, and
   overnight/weekend carry behaviour for the two swing lanes (Daily 2-2 and MES 1-2-2);
3. judge MES 1-2-2 on its realistic 1-tick-per-leg ledger, never raw PaperBroker P&L; treat its
   $229.11 historical max MTM drawdown as a review reference, not a guaranteed limit;
4. keep every ledger separate — the three MNQ ledgers are not one proven $5k portfolio, and the
   MES $1,500 ledger is separate again;
5. do not promote based on historical/backtest results alone, and never convert paper results into
   a promotion decision automatically;
6. re-verify the box rather than trusting this file: a remembered SHA is not evidence.
