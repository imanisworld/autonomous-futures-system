# Futures — Current State Handoff

_As of 2026-09-24 (runtime and current decisions: see the 2026-09-24 blocks in `docs/futures-current-status-2026-09-22.md`). This is the long futures handoff. Historical audit docs remain evidence records, but they do not override the latest dated current-status file. Repository state is not proof of VPS/deployment state; verify the box separately before claiming anything is running._

## Repository/runtime refresh — 2026-09-24

**Verified repository main during this refresh:** `08b2cffebc9dcc6d2c36565b85f30fe4220069d3` after PR #1009. **Verified deployed futures release from the operator audit:** `cddef4a4b6583f31007268789545c7be24345d75` (#1007), deployed at 13:19Z with PID 3185879 and no deploy lock remaining. Repository state still does not prove runtime state; this block records the verified split rather than inferring deployment from merge status.

- **#1003** — merged as `d6358e2614a356ea28b534d7a5d6de7afc873c57`. Pine advisory label fix for VWAP rejection; advisory/chart-side only.
- **#1007** — merged as `cddef4a4b6583f31007268789545c7be24345d75` and is the current deployed futures release. Adds once-per-day operator alerts when the demo lane stops or recovers unprotected (FI-9).
- **#1008** — merged as `58ab077c5d06aa2304381d7062edc4bf930606e9`, **not deployed in `cddef4a`**. Rejects non-finite/non-positive OHLC and out-of-order bars (FI-5a/5b/FI-6). This changes the trading data path and therefore requires a separate deployment GO before promotion.
- **#1009** — merged as `08b2cffebc9dcc6d2c36565b85f30fe4220069d3`, **not deployed in `cddef4a`**. Research-only cumulative RTH VWAP correction; no runtime, strategy, risk, or broker path touched.
- **#1010** — open, not merged. Adds `overnight_high`, `overnight_low`, and `rth_open` payload fields so shadow overnight-sweep and gap-fill observers can receive the data they already expect. Merge is not deployment; TradingView alert recreation remains a separate operator step.
- Fault-injection work #993 through #1008 is now represented in source history; do not treat merged source as deployed unless the release SHA includes it.
- Draft research PRs **#990** and **#994** remain open. Do not close either merely on the assumption that one supersedes the other; compare their preregistration/hypothesis scope first.

**Current safe posture:** no deployment is authorized by this documentation refresh. In particular, do not deploy #1008/#1009 as a side effect. No strategy, risk, broker, scheduler, webhook, or execution logic is changed by this PR.

## Repository refresh — 2026-09-23

**Repository main at this refresh:** `061b5ec25d5809cc13373a932260956f2d9b6a5d` after PR #955. **This refresh did not re-verify the VPS.** The last verified runtime identity remains the dated operational baseline below until the box is checked again; do not infer that merged source is deployed.

- **#952 Tradovate response safety:** merged as `d4147e9738386f769debfb8fbfaca2dbc5b79932`. Malformed `placeOSO` outcomes are treated as UNKNOWN/AMBIGUOUS and cannot blindly re-fire; explicit broker rejections remain retryable; stop replacement now requires broker read-back before local protective state advances; liquidation still requires independent flat-position confirmation. **Merged source only; not deployment proof and no real-money authority.**
- **#953 corrected MCL production-detector rerun:** merged as `e75c8d088f6536811efaa2e42b770f42bfb41114`. Corrected MCL-specific roll construction restored parity to 98.7% setup reproduction / 99.7% geometry among reproduced setups, with roll-switch trade dates excluded as contaminated. 15m CONTROL failed; 60m WIDE is **PROMISING BUT UNPROVEN**; 4H WIDE is **REJECT**. **No fresh MCL forward test is justified.**
- **MGC 4H forward campaign:** remains frozen **PROMISING BUT UNPROVEN / WAIT**. Collection begins 2026-09-24 18:00 ET under the merged prereg/evaluator. No tuning, peeking, restart, or execution authority.
- **#955 closed-market decision-alert suppression:** merged as `061b5ec25d5809cc13373a932260956f2d9b6a5d`. MNQ/MES decision-channel Discord notifications now use the strict fail-closed product-session guard for reviewed holiday/special-session cases. This changes notification delivery only; it does not gate signals, evidence, risk, broker submission, collectors, or execution. **Not deployed by this merge.**
- **Index roll provenance:** operator-captured TradingView Contract Switch markers now prove the 2026 switch **dates**: MNQ/MES 2026-06-15 and 2026-09-15; M2K 2026-06-16 and 2026-09-16. Exact intraday/UTC switch timing remains unresolved. Generic 3-day/8-day scheduler rules are not TradingView authority; do not generalize an MCL roll rule to equity-index contracts. See `docs/equity-index-tradingview-roll-provenance-2026-09-23.md`.
- **Cleanup:** #940 (FTFC labels) and #945 (cross-market paper admission) were closed without merge as stale implementations. #950 architecture-gap review was reconciled and merged as `f6f7a80afce87676e716ca4ea7e04c0e47e95167`; it grants no implementation or execution authority.

**Current safe posture:** keep live execution disabled, keep existing forward evidence lanes frozen, do not expand instruments, and do not deploy the new Tradovate or notification changes without a separate runtime proof gate.

> **CURRENT OPERATOR SUMMARY — 2026-09-22:** read `docs/futures-current-status-2026-09-22.md` first. The accepted futures runtime baseline is now `aba8324dee1ad67e4b6a9c97e12c22b11490c3e6` (release `aba8324dee1a-20260922-000127`), deployed 2026-09-22 04:01:46 UTC under operator approval and verified active with release integrity OK, PID `1495829`, and `NRestarts=0`. PR #909 is the immediate deployment reason: a PAPER-ONLY MES 1-2-2 evidence-isolation fix preventing the isolated lane from consuming shared range-arm state. The active release is current-main at that merge, not a two-file curated release; future comparisons start from `aba8324...`. Options, the 1-2-2 prospective collector, and the curated paper-collection reporter remain independently pinned as recorded in the 2026-09-22 current-status file.
>
> Older text below is retained for provenance. Where it conflicts with `docs/futures-current-status-2026-09-22.md`, the 2026-09-22 file governs.
>
> **PR #915 combined-portfolio research — COMPLETED RUN / AUDIT ONLY:** branch `research/mnq-combined-portfolio-audit-20260922` completed its combined-portfolio run at `5a9f14b`. Earlier scratch runs at `62c9207` and `e46cfd5` are superseded. This closes the older "no combined run exists" gap, but it does **not** promote or demote any strategy and grants no paper, DEMO, live, broker, risk, or deployment authority. Treat the result as research evidence only; operational ledgers remain separate unless a later explicitly approved change says otherwise.
>
> **#912 sustained-trend v1 offline recovery — AUDIT ONLY:** the original successful JSON was recovered, its corpus fingerprints/23,533-of-23,533 causal coverage verified, and one exact frozen-head reconstruction reproduced all ten saved summaries before new diagnostics were accepted. The support ledger contains 1,815 events / 36 fills / 34 terminal trades / 102 stop-cap rejects. The same identities remain positive at 2- and 3-tick adverse-entry stress with both halves positive, but PF stays below the frozen 1.94 hurdle and terminal n remains 34. Winner/loss pre-entry differences are modest and overlapping. Classification remains **PROMISING BUT UNPROVEN / WAIT**; postmortem finding is **POSSIBLE EDGE — MECHANISM UNCLEAR**. One shallower-pullback/arm discovery hypothesis is retained for untouched/prospective testing only and is not authorized for implementation. #911 inverse work was deferred because a valid causal mirrored-SHORT comparator requires a separate research campaign. Evidence provenance: `/root/afs-offline-912-recovery-20260922/`; never treat that path as a runtime dependency.
>
> **PREVIOUS OPERATOR SUMMARY — 2026-09-20 POST-MESSAGE-DEPLOY:** Verified deployed futures release after the latest pass is `c7798d4993d1ecfd872313cfc5c84da2cda6625d` with release integrity **1,394/1,394**. The deployed delta from the prior futures release `ed1212b2552f0bfd990d4f4f80d2e05dd5c3d22c` is presentation/read-only/reporting only: standardized Discord operator cards, options scanner card formatting, read-only Signa storage reporting, public terms/privacy wording, and docs/tests. The box remains `LIVE_TRADING_ENABLED=false`, `BROKER=tradovate`, and futures `/health` is `ok=true`; options-scanner is also pinned to `c7798d4993d1ecfd872313cfc5c84da2cda6625d`, healthy, advisory-only, Public read-only, with `order_supported=false`, `account_endpoints_forbidden=true`, scheduler running, and `signa_context_pull_enabled=true`. No files changed under execution, risk, broker, strategy, config, webhook runner, or journal runtime paths, and no strategy/risk/order/execution logic changed.
>
> **4HR continuation treatment — #798:** PR #798 (`780b5ea`) is merged and deployed. Future natural MNQ 4HR 1m observer events carry read-only `4hr_prearmed_4h22_continuation_v1` metadata using completed ET-wall-clock 4H sequence context. Historical corrected pre-armed scoring shows the treatment subset improved the broad control (+$1,444.58 / PF 2.032 at 1 tick; +$1,402.58 / PF 1.984 at 3 ticks; both halves positive), but top-three-month concentration remains about 94–96%. Classification: **PROMISING BUT UNPROVEN / OBSERVATION ONLY**. It has no paper-fill, DEMO, live, stop/target, risk, or replacement authority.
>
> **Shared Signa snapshots — #803/#805:** PR #803 (`a7e0c16`) added the passive `signa_snapshots` store, and PR #805 (`4550d22`) routes options Signa pulls through it before writing options-specific interpretation rows. Shared snapshots prevent duplicate proxy pulls and let options/futures reference the same raw evidence by `snapshot_id`. This layer is evidence infrastructure only and has no trade authority.
>
> **Futures Signa Context Lane v2 — #807:** PR #807 (`a023202`) is merged and deployed. Futures journal context still includes `context.signa_futures_context`, now with shared snapshot references when available: `snapshot_ids`, `snapshot_refs`, `snapshot_status`, and per-observation `snapshot_id` / `snapshot_age_seconds`. It maps futures to proxies (MNQ→QQQ, MES→SPY, M2K→IWM, MYM→DIA, MGC→GLD, MCL→USO+XLE, MBT→BTC) and records tags such as aligned/conflict/mixed/incomplete/missing/error/stale. It is explicitly observation-only: `gate_authoritative=false`, `broker_evaluated=false`, `risk_evaluated=false`, `trade_authorized=false`, `execution_authority=false`. Signa is **not** a futures entry trigger, blocking gate, ranking system, or risk/routing authority.
>
> **Current safe next step:** seed or verify the shared snapshot store with a controlled read-only Signa pull for `QQQ, SPY, IWM, DIA, TLT, VIX, GLD, USO, XLE`, then audit the first future natural futures setup for causal timing, 4H continuation metadata when applicable, Signa v2 `snapshot_ids` / `snapshot_refs` / `snapshot_status`, and zero execution-authority leakage. Do not retune, expand instruments, loosen risk, add Signa gates, or replace the broad 4HR lane because the tags now exist.
>
> The long reconciliation/history below is retained for provenance. Where it conflicts with the current operator summary or `docs/futures-current-status-2026-09-22.md`, the 2026-09-22 summary governs.

## 2026-09-18 reconciliation

**Repository main at this reconciliation:** `f98a02b4fc82148a85d476fc5265be234185bf56`. Futures runtime fixes #663, #670, #691, and the global risk-policy correction #703 are merged on `main`; later main-only work must not be inferred as deployed.

**Verified VPS release:** `/root/afs-releases/3715eb89b1f5a3d645fc2a8969c3e4d8cf2c42fd`, exact commit `3715eb89b1f5a3d645fc2a8969c3e4d8cf2c42fd`, active from 2026-09-18 14:45Z. It is the prior curated `088012983c3d` release plus exactly one runtime file, `risk_rules.yaml`, carrying the #703 policy delta. Release integrity passed for 1,073 source files. The deployed risk rules are version `1.2.2`, with global `max_drawdown_percent=0.30`, `max_trades_per_day=3`, `max_consecutive_losses=9999`, `circuit_breaker_losses=0`, and `early_session_loss_floor=0`; `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, and `SCHEDULE_MODE=always_on_shadow` remain unchanged.

Verified futures-bot runtime facts:
- `LIVE_TRADING_ENABLED=false`;
- `SCHEDULE_MODE=always_on_shadow`;
- `WIDE_STOP_LEDGER_MODE=paper_sim`;
- the separately guarded wide-stop **Tradovate DEMO** route is armed: `WIDE_STOP_LEDGER_EXECUTION_ROUTE=tradovate_demo` and `WIDE_STOP_DEMO_EXECUTION_ENABLED=true`, with matching proof pins;
- `MES_122_PAPER_MODE=paper_sim`;
- `CROSS_INSTRUMENT_OBSERVATION=cross_instrument_observation_v1`;
- `ASIA_D_EMA_PAPER_MODE=paper_sim`.

Therefore the box must not be summarized as globally paper-only: **live execution is disabled, but the isolated wide-stop DEMO route is armed.** Paper evidence and Tradovate DEMO evidence remain separate. **2026-09-24: the operator confirmed this DEMO arming is intentional** (approved Tradovate demo execution, not live). See the wide-stop DEMO section in `docs/futures-current-status-2026-09-22.md`.

**#644 is merged** as `455037c`: the Backtest -> DEMO qualification gate is now on `main`. It is an acceptance gate, not strategy proof.

**Transition 400t/30m:** final classification **WAIT — FAILS REQUIRED SLIPPAGE ROBUSTNESS**. Baseline isolated evidence was positive, but the full population breached the 20% hypothetical-ledger drawdown stop under required 2-tick and 3-tick adverse entry+exit stress; the 2-tick second half was also negative. Do not rescue it by increasing the ledger, weakening drawdown/slippage, or tuning intermediate stops. PR #659 is archival research, not a deployment candidate.

**#663 source-ticker provenance remains deployed in `3715eb89b1f5`.** Future MNQ/MES BarHistory writes preserve the incoming `MNQ1!` / `MES1!` source ticker as additive provenance while root identity remains MNQ/MES. Historical rows are not rewritten, and continuous-symbol metadata still does **not** prove dated-contract identity.

**Wide-stop gap-stop realism (#670) remains deployed in `3715eb89b1f5`.** The forward collector passes the actual 5m bar open into PaperBroker so an already-resting stop that gaps through is priced through the proven `STOP_GAP` path instead of the stale stop level. Long/short and ordinary-stop regressions passed; the exact minimal candidate passed **5,663 tests / 7 skipped**. Pre-deploy `wide_stop_4k` / `wide_stop_6k` paper ledgers were flat and had produced no fills/outcomes in this epoch, so no prior outcome required invalidation. #568 remains closed as superseded.

**Immediate action:** keep the current collection epochs intact and do **not** restart for #663/#670/#691/#703; all are already present in `3715eb89b1f5`. The dedicated 16:02 ET wide-stop DEMO EOD fallback timer remains active. Existing forward lanes continue under their frozen contracts. The global account drawdown floor is now 30% prospectively, but frozen lane-specific drawdown contracts remain unchanged for their active epochs.



**Adversarial runtime audit (2026-09-18): HOLD / PAPER ONLY / DEMO EVIDENCE ONLY.** The audit found no accidental real-money execution path in the tested routes. The public operational `/status/*` surface is locked at nginx: only `/status/public` remains unauthenticated, while other status endpoints require Basic Auth; localhost monitoring remains available. #691 resolves the wide-stop DEMO feed-independent EOD fallback gap and remains deployed in `3715eb89b1f5`. The no-revenge rule is evidence-defined: no manual/unsignaled re-entry, no averaging down, and every new entry must be a fresh reproducible signal that passes normal risk. Current evidence does not support a separate post-loss cooldown or consecutive-loss shutdown; 3 trades/day remains a provisional safety/isolation cap, not an optimized revenge rule. #703 is deployed prospectively with a **30% global account hard survival floor**; the prior 20% global setting is superseded for the shared account while frozen lane-specific thresholds remain unchanged. Runtime drawdown observed during the audit (~24.6%) now passes the deployed 30% `RiskEngine` check. See `docs/adversarial-runtime-audit-2026-09-18.md` and `docs/loss-sequence-risk-policy-audit-2026-09-18.md`.

**Server drift-gate false alarm fixed and made service-aware (#674 + #680):** the old VPS cron gate compared repository `main` directly to the curated release and red-alerted on intentional release differences. The installed `/root/bin/afs-drift-gate.sh` now treats the primary futures release manifest plus durable commit/fingerprint pins as red-alarm truth **and independently validates the active options-scanner release cwd/manifest**. Current `main` divergence is reported as `main-vs-primary-release` INFO only because service-specific releases may legitimately differ. At the 2026-09-18 #703 proof point: futures `3715eb89b1f5` integrity PASS; options scanner `3b9770d8fed4` was separately pinned; 25 main-vs-primary-release differences were INFO; exit 0. Those release pins are historical; current service pins are tracked in `docs/futures-current-status-2026-09-20.md` and `docs/options-current-state-handoff.md`. A fake wrong futures commit pin returns exit 1; an options-scanner release-integrity failure is covered by focused tests; `--seed` is refused with exit 64. Previous monitor backups: `/root/bin/afs-drift-gate.sh.pre-curated-20260918T121347Z` and `/root/bin/afs-drift-gate.sh.pre-service-aware-20260918T125455Z`. Installed script SHA-256: `cc4d9f5aeea8831d34182bca4305503841c64fc95447463529665f8f265d3b4f`. Cron remains `5 11 * * * /root/bin/afs-drift-gate.sh >/dev/null 2>&1`. No futures-bot restart was required.

**Main-vs-service release audit completed:** the original 19 informational differences were reviewed file-by-file. Two files genuinely belonged to the active options scanner — `alert_ranker/market_data.py` and `alert_ranker/paper_v1.py` — because #667 adds Public bid/ask timestamp + source provenance to the running advisory scanner. At that audit point they were deployed in the **separate options-scanner release** `3b9770d8fed4ad1825cc325bab536ffea618a94e`. That historical promotion was healthy, advisory-only, Public read-only, `order_supported=false`, preserved production SQLite state/risk, and did not restart futures-bot. The active scanner has since advanced through later service-specific releases; use `docs/options-current-state-handoff.md` for the current pin. `notifications/observation_notifier.py` is presentation-only and remains deferred; the other 16 original differences are intentionally offline/audit/replay/research/inactive-options-manager code and should stay off current VPS runtime. Exact ruling: `docs/vps-main-vs-service-release-audit-2026-09-18.md`.


**Deployment serialization note:** another session promoted candidate `117d9e80064b0175f9feb56b65623c72feb87b56` while this audit was still validating the independently built minimal candidate. That candidate carried byte-identical #663/#670 runtime blobs plus a docs delta. Because the promotion happened after an earlier release-state check and before the final promotion command, `c538e2bc` caused a second controlled futures-bot restart. No position/order existed at either promotion and no strategy/risk/broker mode changed, but this was a coordination miss. Future deploy procedure must re-read the live symlink/expected commit **immediately before** invoking promotion, even when the deploy lock was previously observed clear. Watcher was restarted/re-baselined after `c538e2bc`; no further restart is needed.

**Shared-epoch coupling discovered during #670 activation:** the initial attempt to start a fresh wide-stop epoch at `2026-09-18T11:41:33Z` caused the first natural 5m cycle to fail closed with `daily_swing_state_epoch_mismatch`. Daily 2-2 intentionally derives its persisted-state epoch from the same `WIDE_STOP_LEDGER_EPOCH_START`. Daily had real evidence under `2026-09-09T04:21:04+00:00`: balance **$4,406.02**, one resolved loss **−$593.98**, max drawdown **11.8796%**, and `position=null`. Resetting that campaign was rejected. The shared epoch/proof pin were restored to **`2026-09-09T04:21:04Z`** and the bot/watcher restarted. The next natural 5m bar (`bar_ts=2026-09-18T11:45:00Z`) processed with no epoch mismatch or wide-stop collection failure.

For #670 evidence, the explicit **gap-aware fill-model boundary is `2026-09-18T11:45:00Z`**, not the shared env epoch. There were zero wide-stop fills/outcomes before #670, so no realized economic outcomes are mixed across fill models. Do not reset Daily merely to create a cosmetic wide-stop epoch; any future wide-stop-only epoch reset must address this shared-epoch coupling first.

**Structural-level P8 OOS update (#645 merged as `1f4d901`):** the preregistered one-shot P-OOS-MES holdout is now **consumed**. H1 preserved sign and narrowly cleared its frozen effect floor (+0.0644 R vs +0.061 R); H3 preserved sign but missed its floor (+0.0618 R vs +0.098 R). Neither sign reversed, so K5 is not triggered, but the single H1≈H3 wick-reject finding is **not confirmed**. Classification remains **PROMISING BUT UNPROVEN / PARTIAL REPLICATION**. Do not rerun or tune against this holdout. The next confirmatory gate is the already-frozen P-OOS-PROSPECTIVE window after its calendar/sample threshold; **no new runtime collector, deployment, or restart is required**.

## Verdict

**LIVE DISABLED / FORWARD PAPER COLLECTION ACTIVE / GUARDED WIDE-STOP TRADOVATE DEMO ARMED. NO LIVE EXECUTION IS APPROVED.**

Core rule: **No proof, no run.**

PR **#545** is merged to `main` as commit **`9caaaa3e2fbe5cb6ff20941229e3498794bcb6de`**. Its final merge-ref CI passed **4,952 tests, 7 skipped**. The repository contains the clean PaperBroker-only MNQ three-lane collector, Daily persisted-state integrity guard, and this authoritative handoff.

**Four paper collections are now ACTIVE on the VPS** (two campaigns since 2026-09-09, two more
added 2026-09-16). Verified live on 2026-09-16:

- deployed release: **`8fd8b215063c83428fa15028ae76f0e7f6d25a8e`** (`main` at #595; the same release
  also carried #589, #590, #591 and the #582–#588 cross-instrument foundation);
- **MNQ three-lane campaign** (#545): `WIDE_STOP_LEDGER_MODE=paper_sim`, epoch **`2026-09-09T04:21:04Z`** — unchanged across every later restart;
- **MES 15m 1-2-2 lane** (#555): `MES_122_PAPER_MODE=paper_sim`, epoch **`2026-09-09T06:12:55Z`** — unchanged;
- **Cross-instrument observation campaign** (#582–#588, armed 2026-09-16 12:17Z):
  `CROSS_INSTRUMENT_OBSERVATION=cross_instrument_observation_v1`, epoch
  **`62546883+2026-09-16T12:17:19Z`**; collection-only, the added roots are non-executable by
  construction (see section 6);
- **MNQ Asia D+EMA forward paper cohort** (#595, activated 2026-09-16 18:48Z under a one-time
  operator waiver of the September 30 freeze): `ASIA_D_EMA_PAPER_MODE=paper_sim`, epoch
  **`2026-09-16T22:00:00Z`** (Day 1 = the Asian session of the night of 09-16/17), campaign id
  `asia_d_ema_2026_09_v1` (see section 5);
- `LIVE_TRADING_ENABLED=false`, `SCHEDULE_MODE=always_on_shadow`, `EXIT_MODE=static`, zero
  external-broker orders placed; the real-book decision stream and both 09-09 campaigns were
  proven byte-for-byte preserved across the 09-16 activation (env delta = the two Asia keys plus
  their proof pins and the release fingerprint; no evidence file shrank; one ~7 s restart gap
  between bars, no bar missed).

The **September 30 no-release / no-restart restriction is back in force** after the #595
activation. Everything below that is not one of these four collections is either research-only,
default-OFF in the repository, or explicitly HOLD.

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

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE / ENTRY ARCHITECTURE TIMING-SENSITIVE**
- Daily structural first break consumes the day's opportunity, but the active entry is **completed-5m close-confirmed**, not an immediate first-touch breakout
- 1 MNQ contract
- isolated hypothetical starting ledger: **$5,000**
- natural prior-Daily-range stop; no stop tightening
- fixed **2R** target from the planned structural entry
- decision-time **8-tick IOC** admission from the completed trigger-bar close
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

The 2026-09-18 timing audit reproduced the activation baseline exactly:
- 34 non-overlapping fills
- net **+$13,885.18**
- PF **2.0171**
- H1 **+$5,661.84** / H2 **+$8,223.34**
- 2024, 2025 and 2026 positive
- max historical drawdown **25.1528%**

Under the current #775 CME trading-day identity, the same completed-close architecture remains positive: 34 fills, **+$13,571.68**, PF **1.9482**, both halves positive, max DD **26.6323%**.

Critical interpretation: all 34 current-identity fills occurred only after the completed trigger-bar close moved **2–202 ticks favorably** from the planned breakout entry (median **33 ticks favorable**). A separately preregistered causal first-touch model produced **0 admissible fills at 1/2/3 adverse entry ticks** because the fixed planned 2R target plus strict actual-fill R:R >=2 rule is mathematically incompatible with any adverse true-touch slippage.

Therefore:
- the completed-close / favorable-pullback hypothesis remains **PROMISING BUT UNPROVEN / PAPER ONLY**;
- the immediate first-touch version under current rules is **BROKEN / ZERO ADMISSIBLE FILLS**;
- do not relabel the 34-trade historical result as first-touch evidence;
- do not change target or R:R rules without a separate preregistered rule decision.

Audit: `docs/daily22-trigger-timing-ab-2026-09-18.md`.

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

### 5. MNQ Asia-session D+EMA forward paper cohort (isolated lane, added 2026-09-16 by #595)

- status: **PROMISING BUT UNPROVEN / PAPER EVIDENCE** — an MNQ-specific, still-unproven effect
  pending prospective evidence
- module `context/asia_d_ema_paper_cohort.py`; hooked in `webhook/runner.py` after the MNQ Strat
  evidence leg, on authoritative MNQ **15m** bars only, in its own error boundary
- definition reproduces the archived offline producer **exactly** (parity test against 70 archived
  candidate rows in `tests/fixtures/asia_d_ema_parity.json`, fixture provenance hashed):
  candidate source = the runner's shadow candidates; cohort D = payload `market_condition !=
  TRENDING` AND structural condition not a structural trend; EMA alignment = candidate direction
  equals the payload trend direction; scope = MNQ, `session == "asian"`, strategies
  `ema_pullback_trend` and `strat_22_continuation_observed` only; persistent-geometry dedupe per
  observation day
- fill = canonical PaperBroker `ioc_limit` once at the decision-bar close, 1 adverse tick, 32-tick
  MNQ tolerance, pessimistic both-hit, original **1.0R** bracket, no breakeven, no runner;
  economics from `config/futures_contracts.contract_economics("MNQ")`
- resolution on strictly later 15m bars of the same CME observation day; an open position at the
  18:00 ET roll is **EXPIRED**, never carried
- **one open position at a time**; same-bar ties resolved in fixed (strategy, direction) order;
  a candidate arriving while busy is journaled `CANDIDATE_SKIPPED_BUSY`
- the ET hour of the decision bar is **logged, never filtered** (the 18–19 ET vs 20–02 ET split is
  a pre-registered secondary hypothesis, not a rule)
- crash-safe, idempotent persistence: write-ahead `pending_events` in `state.json`, deterministic
  `event_id`, recovery replays only non-durable events (fault-injection tests)
- writes only `logs/asia_d_ema_cohort/{evidence.jsonl,state.json}`; no broker interface lookup,
  no order route, no drawdown ledger — this cohort is an evidence stream, not a capital claim
- default **OFF** (`asia_d_ema_paper_mode="off"`); only the exact token `paper_sim` plus an
  offset-aware epoch activates it, anything else fails closed at config load

Historical basis (Asia-only audit of the archived D+EMA counterfactual, 2026-09-16): the
uncapped Asia population's PF 1.34 was ~5x stacked exposure; collapsed to one position the
07-13..08-31 stream is 107 trades / PF 1.46, robust to costs and to both halves, but the edge lives
in `ema_pullback_trend` + `strat_22_continuation` only and September collapsed flat. PF 1.46 sits
**inside the null band (p95 1.94)** — this is a narrowed, unproven candidate under prospective
observation. A population-delta proof showed the lane's broader input population adds exactly one
bar and zero candidates versus the offline producer; the two-strategy one-position streams are
identical in every field.

**Binding hypothesis wording (2026-09-16 ruling):** "MNQ Asian-session historical D+EMA cohort,
reproduced exactly; structural non-trend is frequently unevaluable during Asia because structural
history resets across the scheduled maintenance break." The structural clause of cohort D is
`INSUFFICIENT_DATA` for ~75% of Asian-session bars by design (the 45-minute contiguity rule in
`context/structural_regime.py` restarts its warm-up at the 17:00–18:00 ET break), so in Asia the
cohort is in practice mostly the non-TRENDING + EMA-alignment condition. Parity with the archive is
intact; the caveat is on the *claim*, not the code. Any break-aware structural analysis is a
separate diagnostic and must not change this lane's definition mid-epoch.

**MES replication: REJECTED (2026-09-16).** The same methodology on a preserved MES 5m corpus
(2026-06-22..09-16) found Asian, London and New York all BROKEN after the one-position collapse.
Cross-instrument transfer failed; MNQ Asia therefore has no MES replication. Never combine MNQ and
MES results, and do not search MES for another filter.

Review gate: **30 resolved trades over at least 10 observation days**, no pooling with the
historical stream, no pooling with any other campaign, one-position stream only.

### 6. Cross-instrument observation campaign (collection-only, armed 2026-09-16)

Not a trading lane. `cross_instrument_observation_v1` (#582–#588, #590–#591) records per-population
evidence for the added roots (M2K, MGC, MCL, MBT) alongside MNQ/MES under one epoch, with
continuity/roll/provenance quality gates. The added roots have no executable path: campaign-OFF
routing is pinned fail-closed (#587) and the roots are collection-only in the webhook transport.
Judge nothing from it yet; the feed-health CLI reads the epoch from the environment, so pass
`--epoch` explicitly when running it by hand.

## Campaign-wide safety boundary

The 5-minute paper router enforces a maximum of **3 new fills per trading day across the MNQ three-lane campaign**. The isolated day-strategy config is capped at one 4HR fill and one 3-2-2 fill; Daily uses one causal first-break opportunity and one open swing maximum. The real/global risk configuration remains untouched.

The **MES 1-2-2 lane is not under that router or that 3-fill cap** — it is a separate observer on the 15-minute webhook path, bounded instead by one open position at a time plus every ordinary gate evaluated against its own isolated config.

The **Asia D+EMA cohort is likewise outside the router and the cap** — it is bounded by its own
one-open-position rule and the same-observation-day horizon, and it holds no ledger, so there is
no drawdown halt to trip; the stop conditions are operational (feed loss, state/evidence
disagreement, any sign of a broker route), not P&L-based.

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

The **Asia D+EMA cohort has no route either**: the only execution object in its module is
`execution.paper_broker.PaperBroker`; there is no broker-interface lookup and no Tradovate
import, and the runner hook passes nothing that could reach one (asserted by an AST import/call
test). The cross-instrument roots are collection-only and cannot acquire a route without a new PR.

Any route beyond these two is a separate proposal requiring a new audit.

## What remains least certain

### 0. Highest-uncertainty items after the 2026-09-18 reconciliation

- **Strategy edge, not machinery.** Collection/execution-safety infrastructure is better proven than profitability. Transition is now WAIT after failing required slippage robustness; MES 1-2-2 is still extremely thin; the active forward lanes still need prospective sample.
- **Wide-stop DEMO evidence vs live safety.** Live trading is disabled, but a guarded Tradovate DEMO route is armed for the wide-stop family. DEMO evidence must remain separate from PaperBroker evidence and cannot be read as live readiness.
- **Deployed-release provenance.** The VPS is on curated release `c538e2b`, not repository `main`. Its runtime/test blobs are byte-identical to the independently verified `117d9e8` candidate; the concurrent second promotion was a deployment-serialization/process breach, not a runtime-code difference. Future releases need one declared deployment owner, an explicit allowlist, and a final live-symlink/expected-commit read immediately before promotion.
- **Contract/source identity.** #663 is deployed and naturally observed: new MNQ/MES BarHistory rows preserve `MNQ1!` / `MES1!` source tickers while retaining MNQ/MES root identity. This improves provenance but still does not prove dated-contract identity by itself; roll provenance remains an evidence limitation.
- **Forward restart/gap behavior.** #670 is deployed, but its attempted wide-stop-only epoch reset proved that the shared `WIDE_STOP_LEDGER_EPOCH_START` is also Daily 2-2's persisted-state epoch. The shared epoch was preserved and `2026-09-18T11:45:00Z` is the explicit gap-aware fill-model boundary. A natural forward `STOP_GAP` outcome has not yet occurred under the new model. Any future wide-stop-only epoch reset must address the coupling first.

### A. Combined-account research now exists; runtime ledgers remain separate

PR #915 at `5a9f14b` completed a research-only combined-account run under preregistered shared-capacity rules. This supersedes the older statement that combined open risk, cross-strategy interaction, and combined drawdown had not been studied at all. Completion of that research run is **not** strategy validation, promotion, or runtime authority.

**Current mitigation:** keep operational attribution and balances separate. Use #915 as research evidence only unless a later explicitly approved change alters runtime architecture. The MES 1-2-2 lane's $1,500 ledger remains separate and must not be pooled merely because combined research now exists.

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

### E. Whether the MNQ Asia D+EMA effect exists at all

The historical one-position stream (PF 1.46, 107 trades) is inside the null band, the edge is
concentrated in two strategies, September was flat, and the MES replication failed outright. The
cohort exists to answer this prospectively under the exact archived definition; nothing about it
may be retuned during the epoch, and the ET-hour split stays a logged secondary hypothesis.

### F. Structural regime is mostly blind during Asia by design

`INSUFFICIENT_DATA` covers ~34% of post-feature MNQ 15m rows, 100% of 18–21 ET and ~75% of the
Asian session, because structural history is cut at the last >45-minute gap and the 17:00–18:00 ET
maintenance break resets the warm-up every day. Zero data defects were found — this is a design
limitation, not an outage. It caveats every Asia-session claim that leans on the structural clause
(cohort D included) and it is **not** a reason to change the detector during collection.

### G. The MNQ 15m executable set is empty on purpose

Since #376 (2026-07-28) isolated the real book to `orb_breakout` and #517 (2026-09-08) retired
`orb_breakout`, no strategy is executable on MNQ 15m; every remaining family is observation-only.
The 2026-09-16 boundary audit found nothing disconnected and zero defects — the five executable
cases in the corpus were all `ENTRY_DETACHED_FROM_PRICE` as specified. **Ruling: HOLD this posture;
do not reverse #376.** Coverage audits of the 2026-07..09 corpus show the first loss of coverage is
"no aligned detector before the move" (67%) and "observation-only family" (23%), not gating —
so widening gates is not the lever, and no strategy promotion is authorized from these audits.

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
- **#582–#588** cross-instrument foundation, portability, observation transport, quality gates and fail-closed OFF routing merged; deployed OFF as `6254688` on 2026-09-16, armed by env the same day
- **#589** deterministic staleness fixtures; **#590** Discord observation route (optional, inert until its route is configured); **#591** failure/safety alerts on the error route
- **#595** Asia D+EMA forward paper cohort merged `8fd8b21` — six files, all additive (`context/asia_d_ema_paper_cohort.py`, `config/settings.py`, `webhook/runner.py`, `ops/live_box_guard.py`, one test module, one fixture); no `risk_rules.yaml`, `instruments.allowed` or `enabled_concepts` change; ships off by default

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

For the Asia D+EMA cohort, items 4-6 read: `ASIA_D_EMA_PAPER_MODE=paper_sim` exactly;
`ASIA_D_EMA_PAPER_EPOCH_START` an offset-aware timestamp (missing or naive → config load fails
closed); authoritative MNQ 15-minute bars actually arriving. Both variables are in
`PROOF_CRITICAL_RUNTIME_OVERRIDES` and need their matching `EXPECTED_PROOF_*` pins. Before any
re-activation or epoch reset also confirm `logs/asia_d_ema_cohort/state.json` carries no position
or pending events from the prior epoch.

What was verified live at Asia D+EMA activation (2026-09-16): deployed release = `main` at #595;
release switch performed inside a post-15m-bar window, so no authoritative bar fell into the
restart gap; first post-restart 15m bar processed by the new release created `state.json` with
`campaign_id=asia_d_ema_2026_09_v1`, no position and no pending events; every other campaign's
epoch and state unchanged; real-book decision stream unchanged; no external-broker order anywhere.

What was verified live at MES activation: lane `paper_sim` and active; epoch matching exactly;
realistic balance $1,500 with 0 resolved trades; journal writing under
`logs/hypothetical_ledger/mes_122_1500`; MES 15m alerts reaching the lane and producing genuine
gated decisions; 1 MES contract; no Tradovate route; real-book decisions byte-identical before and
after (MES still `NO_TRADE "not in allowed universe"`, real config unchanged at
`allowed_instruments=['MNQ']` / `enabled_concepts=['orb_breakout']`); the MNQ lanes' persisted
state byte-identical across the restart.

## Other strategy status — outside the four active lanes

- **12HR Miyagi:** shadow/research only; no fills.
- **Inverse ORB:** **RETIRED / BROKEN; NOT ACTIVE** since #517 (2026-09-08); the operator confirmed on 2026-09-24 that the retirement stands. Old positive headline retired after decision-time/bracket-geometry correction; do not revive from the invalid baseline. Zero current valid forward evidence. The `MNQ_ORB_BREAKOUT_INVERSE_*` env pins are accounting isolation only, not activation. See `docs/futures-current-status-2026-09-22.md`.
- **VWAP Hold:** corrected decision-time evidence negative; no promotion.
- **Transition reclaim:** separate repair investigation; not part of these four lanes.
- **ORB Reclaim / source ORB Breakout:** negative/weak corrected evidence; not part of this campaign. `orb_breakout` retired from the real book by #517, leaving the MNQ 15m executable set empty on purpose (section G).
- **MES D+EMA (Asian/London/New York):** REJECTED 2026-09-16 — no forward cohort; the MES precursor investigation is closed (HOLD / PAPER ONLY, representation hypothesis promising but unproven). Not another retrospective filter search.
- **BOS/MSS (#594):** HOLD; separate research question, not scheduled.
- **Shadow families in `strategy/shadow_setups.py`:** observation-only by design; they reach the journal and evidence files, never the DecisionEngine.
- **Context-permission-layer study (prereg 2026-07-16):** first formal review run 2026-09-16 —
  `docs/context-permission-first-review-2026-09-16.md`. 7,563 joined outcomes, 0 of 22 tests
  reach gate candidacy; supply/demand alignment, mid-range, freshness, opposing-zone, key-level,
  impulse and pair-agreement features do not discriminate. **No context gate is authorised.**
  One pipeline defect recorded (MES top-level `market_condition` null since #376) — post-09-30 fix.

## Superseded / historical PRs

These are not current execution authority:

- **#538** — superseded; external-broker work appeared on the branch; closed unmerged.
- **#543** — superseded by #545 after post-green external-broker commits appeared; closed unmerged.
- **#527** — initial Daily STRAT baseline harness; superseded by completed Daily isolation/IOC evidence; closed.
- **#534** — earlier handoff refresh; closed as superseded.
- **#539** — Daily cross-check documentation; historical evidence only; closed.
- **#476** — September 7 handoff; historical only; closed.

- **#596 / #598** — MES D+EMA precursor-audit and real-data reproduction branches; evidence only, ruled HOLD / PAPER ONLY; their preserved artifacts and checksums stay untouched.

Do not delete historical evidence files merely because they are old. Closed PRs and Git history remain the audit trail. Transition reclaim remains a separate open investigation because it is not a duplicate of this campaign. MES 1-2-2 is no longer separate — it is lane 4 above; the Asia D+EMA cohort is lane 5.

## Do not touch

- canonical 4HR detector/formula
- canonical 60M 3-2-2 First Live detector/formula
- Daily 2-2 stop/target/filter bundle without a new preregistered one-variable study
- global `risk_rules.yaml` merely to make these paper lanes pass
- real-book enabled strategy list
- canonical `strat_212_122` detector and the MES 1-2-2 entry/stop/2R-target bundle
- bracket-validity guard
- pessimistic same-bar handling
- the Asia D+EMA cohort definition, strategy pair, 1.0R bracket, one-position rule and same-day horizon during its epoch (parity with the archived producer is the point of the lane)
- `context/structural_regime.py` contiguity/warm-up rule while any Asia-session evidence is being collected
- the #376 real-book isolation (HOLD; reversal is a separate ruling)

## 2026-09-18 audit reconciliation — current override

This section supersedes the older statement below that all repository work is complete.

Verified since the prior handoff:

- Backtest fidelity Layer 1 (#652) merged; MNQ/MES v1.5 frozen replay rebuild passed manifest/hash and weekly-level semantic verification.
- C14-affected P3 parity rerun passed for every admitted structural level; VWAP/C16 remains diagnostic / not admitted.
- Tradovate runtime provenance was independently proven DEMO with live trading disabled; broker-fill calibration remains **INSUFFICIENT** because all 95 journals contain zero exact external fills/no-fills.
- M2K 2026-09-17 transport gap was audited: 11 missing 15m bars (13:30Z–16:00Z); contaminated terminal outcomes are automatically excluded by the deployed `DATA_GAP_CONTAMINATED` quality gate.
- MNQ/MES future BarHistory provenance now preserves incoming `MNQ1!` / `MES1!` source ticker metadata (#663), without rewriting history or proving dated-contract identity.
- **Roll-proof correction:** Sep-14 MNQ/MES evidence proves U6 before an intraday gap and Z6 from 22:00Z onward, but does not observe the switch boundary itself. The prior claim that the U6→Z6 switch was observed exactly at 22:00Z is superseded. Exact seam status is `NOT_OBSERVABLE` / `ROLL_PROVENANCE_UNKNOWN`.
- X0 v1.5.1 had a research-proof defect that could call a seam `FEED_CONFIRMED` across an evidence gap. v1.5.2 adds fail-closed continuity enforcement. This is research tooling only; no runtime path changes.
- Transition 400t/30m PR #659 is **closed unmerged**; the research classification remains WAIT / PAPER ONLY after failing required adverse-slippage robustness. Do not revive or deploy it for execution.

Deployment state during this reconciliation:

- futures service remains on release `94eb7d388c02b744eed5a3d3d36b14fa724f1781` (`94eb7d3` release directory), active since 2026-09-17 21:36Z;
- current preflight is not armed, reports `preflight_passed_not_armed`, zero open positions and zero working orders; live-box drift guard is healthy;
- runtime environment remains Tradovate DEMO, live trading disabled, `SCHEDULE_MODE=always_on_shadow`;
- the isolated wide-stop DEMO route is currently armed with matching proof pins, while its paper ledger remains `paper_sim`; this is not live authorization and DEMO evidence stays separate from paper evidence;
- no restart or deployment was performed for #652, #660, #662 or #663;
- do **not** restart merely to pick up research/docs/provenance changes. A future sanctioned release should reconcile the full `main` delta and be cut only when there is an operational reason.

## Safe next step

Repository work is **not** globally complete: the X0 gap-continuity proof correction must merge first. After that, remaining roll questions are observation-bound (MCL first live seam, MBT Sep-25 seam, MGC late-Nov seam) and broker-fill calibration is data-bound. No strategy activation, `.env` change, broker submission or runtime restart is authorized merely to manufacture evidence.

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
6. re-verify the box rather than trusting this file: a remembered SHA is not evidence;
7. review the Asia D+EMA cohort only at its gate (30 resolved / 10 days), on its own
   one-position stream, never pooled with the historical audit, the MNQ three-lane ledgers or MES;
   until then Day-N summaries are status, not evidence;
8. treat the cross-instrument campaign as feed/evidence-quality proof for now; no population there
   is a strategy candidate.
