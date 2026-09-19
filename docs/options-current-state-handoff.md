# Options — Current State Handoff

_As of 2026-09-18. This is the single current-state authority for the options lane._

Historical dated notes and old/closed PRs are provenance only. They do not override this file. Operational deployment proof lives in `docs/options-paper-v1-deployment-checklist.md`; diagnostic definitions live in `docs/options-v1-diagnostics.md`; the read-only coverage evidence lane (observer, reducer, outcome study, after-close collector) is described in `docs/options-coverage-observer.md`.

## Current verdict

**DEPLOYED / SMOKE PROVEN / V1 EVIDENCE EPOCH RECORDED (`899a524` + 2026-09-15T16:50:00Z, cohort `V1-EPOCH-1`) / STRATEGY EDGE NOT PROVEN.**

Epoch record: `docs/options_v1_evidence_epoch.json`. **Cohort `V1-EPOCH-2` (`UNIVERSE_EXPANSION_6_TO_20`) started 2026-09-16T16:47:46Z** on the first clean RTH cycle after the watchlist grew from 6 to 20 symbols (deployed release `62546883`, scanner code identical to `899a524`; rules, risk, cadence and Signa authority unchanged). `V1-EPOCH-1` (six symbols, 2026-09-15T16:50:00Z to 2026-09-16T16:47:46Z) is retained inside the record. Rows before an epoch's start belong to the previous cohort, not to it. Rules in force: #570 late-entry guard, #571 Daily-lane timing + 1R target floor, #575 ENTRY_LATE episode block (counterfactual preservation). Any rule change starts a new cohort.

The options scanner has its **own service-specific immutable release**, independent of the futures bot. Current options-scanner production release: **`58f1c50583d8bb747c0b221eabb75af376b10ecc`**. It is the minimized v3 selector-evidence release built from prior scanner base `3b9770d8fed4ad1825cc325bab536ffea618a94e`: production `paper_v1.py`, market-data policy, scanner config, risk rules, and webhook runtime remain byte-identical to that base, while append-only production-selector evidence/replay is enabled. Post-restart proof showed healthy advisory-only operation, Public read-only provider, `order_supported=false`, account endpoints forbidden, unchanged production SQLite path, preserved aggregate open planned risk, and no broker/order activity.

The futures bot is separately pinned and must not be conflated with the options-scanner release. Service-aware drift monitoring verifies each pinned release independently.

Repository `main` now includes the options research/evidence chain through the controlled target/horizon work in **#747/#750**, the all-arm IEX-provisional source-policy validation in **#756**, and the three-session first-sight persistence decision in **#761**. #710/#712/#715 production-selector evidence/replay is deployed only through the curated scanner release above; unrelated `main` changes, including the dedicated 212R collector, its recent-SIP entitlement preflight, the IEX-provisional research study, and the later controls, are **not** deployed to the scanner.

Trigger-time and 212R research has advanced beyond the old completed-bar clock: #717/#719 add the causal trigger model and continuation fail-closed guards; #721/#722 freeze/reproduce the trigger-time SIP study; #724/#726 bind source-defined 212R geometry to the exact frozen 81; #733 resolves the exact nanosecond SIP price-forming trigger-cross trade for **81/81** rows with frozen 5-minute OHLC reproduced **81/81** and a byte-identical repeat proof; #738 proves on the frozen AMZN case that the exact causal trigger price can traverse the real scanner `context.price -> normalized price -> OPTIONS_PAPER_V1` path with production replay parity.

#730 builds the dedicated **observation-only prospective 212R collector**. #739 corrects its evidence clock and moves first-break detection into the active `WATCHING` window: a pre-armed setup resolves the first strict-through Alpaca SIP boundary break before the 5-minute bar closes, reuses the same 212 family/geometry rules, measures selector-capture lag from the exact SIP crossing, preserves immutable raw SIP-window hashes, and fails closed on missing timing/quote/parity evidence. An end-to-end `run()` test proves the WATCHING branch reaches selector evidence before bar close. The collector is **merged but not deployed or scheduled**. Its CLI intentionally has no default capture-lag threshold.

A 2026-09-18 isolated RTH cycle exposed an additional deployment blocker: the configured Alpaca account returned HTTP 403 with `subscription does not permit querying recent SIP data` for a still-live SPY watch window. Older same-day SIP windows succeeded only after aging beyond the recent-data restriction. #741 makes this an explicit fail-closed collector preflight, so current credentials still **cannot supply the real-time consolidated-SIP first-break clock required by collector v0.3**. #742 rejects IEX as SIP-equivalent on the old frozen 81. The later preregistered all-arm IEX study (`docs/options-212r-iex-provisional-results-2026-09-18.md`) now validates a narrower alternative: IEX is feasible only as a **miss-allowed provisional research clock with mandatory delayed-SIP reconciliation**. Across all 183 frozen 212 arms, IEX emitted 90 provisional reversals; delayed SIP confirmed 89, rejected 1 continuation-first false provisional, and IEX missed 2 of 91 SIP-authoritative reversals. Median confirmed lag was 3.501s but p95 was 156.273s and max 569.811s. Therefore IEX remains a distinct source policy, not an exact-SIP substitute, and no IEX collector is deployed or scheduled.

Current state is therefore: production selector-evidence capture is deployed for the running V1 scanner; 212R trigger-time/geometry/tick and prospective-collector code is merged; the dedicated 212R collector is not running; 212R remains research-only / `WAIT`.

## What is built

The current V1 path includes:

- real option-chain contract selection and exact-contract re-quotes;
- causal Strat setup proof;
- independent setup/timeframe populations;
- active vs rejected-filter counterfactual evidence;
- 1H and explicit `4H_RTH` observer cohorts;
- fail-closed runtime preflight;
- snapshot-path ambiguity telemetry;
- bankroll/drawdown/swing replay for $1,500 / $2,500 / $5,000;
- option + underlying MAE/MFE;
- entry-extension telemetry;
- fee/slippage stress overlays;
- evidence-quality and sample-confidence reporting.

Relevant merged work: #520, #526, #533, #536, #540, #541.

No broker auto-entry/order execution was added by this work.

## Backtest fidelity and selector authority — 2026-09-18

The production contract selector authority is **`OPTIONS_PAPER_V1`** in `alert_ranker.paper_v1`. The newer canonical selector under `options_manager.contracts` is deterministic and useful for reference/research, but it is **not** production authority and must not be used as though it reproduces the scanner.

#710 added append-only prospective decision-time selector evidence. #712 then added replay through the same pure `choose_expiration()` + `choose_contract()` functions used by production and made the evidence fail closed if retained-input replay diverges from the actual production choice.

Evidence schema v3 keeps the active scanner runtime minimal and production-authoritative. It preserves:

- exact byte-stable production-selector input + SHA-256;
- exact production selector source SHA-256;
- all expiration candidates + production-chosen expiration;
- underlying price/source/timestamp provenance;
- chain bid/ask plus side timestamps, volume, OI, delta, IV and source identity;
- actual production selection;
- exact production replay result + parity.

The canonical/reference selector is now **offline reference only** and is not imported by the runtime evidence modules. This removes the inactive `options_manager` selector package from the proposed scanner deployment dependency set.

Live branch proof on `476a6b3` used an isolated temp SQLite DB and a live SPY chain with no Discord send and no broker/order path: 282 chain rows were retained, all 282 carried bid/ask timestamps, OI, delta and IV, production selected `SPY261120C00775000`, retained-input production replay selected the same contract, and `production_replay_parity=true`. Runtime evidence modules also have an import guard proving they do not import `options_manager`.

This proves the **current forward capture/replay boundary for the observed capture**, not historical strategy results. #733 proves the exact causal underlying trigger-cross trade/time for all 81 frozen 212R rows, including byte-identical repeat evidence. #738 separately proves that the frozen AMZN trigger price can be supplied through the real scanner `context.price` override and produce retained-input production replay parity even when the provider snapshot price differs. Full historical 212R contract-selection replay nevertheless remains **DATA BLOCKED** because causal historical decision-time option Delta and contract-level open interest are still unproven for the frozen population. Do not synthesize them or substitute current snapshots.

The external Delta/OI source qualification is now explicit in `docs/options-historical-delta-oi-source-qualification-2026-09-18.md`: **ThetaData Standard is the preferred technical pilot candidate** because its documented historical first-order Greeks support sub-minute timing with option/underlying timestamps and its historical OI semantics are documented. ORATS one-minute intraday is the fallback candidate; Cboe DataShop Option Quote Intervals is an archival fallback. None is currently configured or authorized. Any alternate Delta source is a source-semantics change from current Public and therefore requires field-level provenance plus forward production-selector parity before it can retire the historical blocker. No purchase/integration was authorized.

## Frozen `OPTIONS_PAPER_V1` trade policy

- max planned risk per ACTIVE paper trade: **$300**;
- max aggregate ACTIVE planned risk: **$1,000**;
- no hard position-count cap; aggregate planned risk controls exposure;
- preferred DTE: **45+**;
- 14–44 DTE allowed with `DTE_EXCEPTION`;
- <14 DTE / weeklies excluded from V1;
- entry basis: **ask**;
- premium stop: **25% adverse from entry premium**;
- underlying invalidation required;
- no averaging down;
- Signa is observational only;
- GEX is optional context;
- missing critical setup/quote/risk data => `DATA_INVALID`.

Do not tune these rules during the first evidence epoch. A rule change creates a new policy/cohort.

## Strategy populations

### ACTIVE paper candidates

- **30m 2-1-2 continuation**
- **Daily 2-1-2 continuation**
- **Daily 2-2-2 continuation**
- **Daily 2-2-2 reversal**
- **Daily 3-2-2 continuation**
- **Daily 3-2-2 reversal**
- **Daily 3-2 developing** — WATCH only until actionable

Daily actionability is mechanical: previous-candle high/low break, with the opposite side as underlying invalidation.

### COUNTERFACTUAL / observation evidence

Mechanically valid signals rejected by promotion filters are preserved rather than discarded. These rows:

- never alert;
- never consume the ACTIVE $1,000 risk budget;
- remain distinct from ACTIVE candidates;
- use real-chain contract evidence when enough facts exist;
- re-quote the exact selected contract;
- are excluded from ACTIVE dashboard P&L/win rate.

Observer cohorts include rejected 30m/Daily signals plus **1H** and **`4H_RTH`** Strat observations. `4H_RTH` is session-anchored from causal 30m bars and must not be described as a native/vendor 4H candle.

## What every usable trade should preserve

At minimum:

- setup type + timeframe + direction;
- ACTIVE vs COUNTERFACTUAL lane;
- original filter/suppression reason;
- mechanical trigger, underlying invalidation, targets;
- exact expiration / strike / option symbol;
- DTE and DTE bucket;
- entry ask/bid, spread, volume, OI;
- Greeks/IV when actually supplied;
- premium stop + planned dollar risk;
- exact-contract marks through resolution;
- underlying snapshots through resolution;
- Signa/GEX state;
- ambiguity/path metadata;
- outcome and friction views;
- policy id;
- selector evidence id/hash;
- production selector source hash;
- production replay result + parity when the row is part of the prospective replay-evidence population.

Missing evidence is flagged; it is not invented. A production-replay mismatch is `DATA_BLOCKED`, not a usable backtest row.

## Diagnostics we will use to isolate each strategy

After enough trustworthy observations exist, answer these six questions separately:

1. **Signal / timeframe** — does the pattern itself predict direction; 30m vs 1H vs `4H_RTH` vs Daily where comparable?
2. **Entry** — are fills late/extended beyond the mechanical trigger?
3. **Stop** — is the 25% premium stop inside normal adverse excursion?
4. **Target / exit** — does MFE show targets/hold times are mismatched to the timeframe?
5. **Filters** — compare ACTIVE survivors with COUNTERFACTUAL rejected signals.
6. **Execution realism** — ask/bid path, spreads, friction stress, ambiguity, capital constraints, and swings.

The diagnostics report preserves:

- option MAE/MFE from executable bid marks relative to ask entry;
- underlying MAE/MFE from scheduled underlying snapshots;
- directional entry extension;
- quote age / observation gaps;
- missing Greeks/IV / provider errors;
- ambiguity flags;
- `HIGH / MEDIUM / LOW` evidence quality;
- sample count, win-rate interval, expectancy interval, profit factor, and drawdown.

Sample labels are reporting labels only:

- `<20` priced closed rows: `INSUFFICIENT`;
- `20–49`: `EARLY`;
- `50+`: `REVIEWABLE`.

`REVIEWABLE` does not mean proven.

### Friction views

The canonical recorded fill view stays ask-entry / bid-exit. Analysis also reports:

1. `RECORDED_EXECUTABLE` — ask entry / bid exit, no added fee;
2. `FEE_STRESS_065` — $0.65 per contract per leg stress;
3. `FEE_065_PLUS_1C_SLIPPAGE` — same fee stress + $0.01/share adverse slippage on both legs.

These are sensitivity overlays only; they never rewrite the recorded V1 trade.

## Bankroll, drawdown, and swing study

Replay the same ACTIVE trade sequence through cash-only long-option accounts at:

- **$1,500** — starting test bankroll;
- **$2,500** — intermediate comparison;
- **$5,000** — current maximum allocation ceiling if evidence earns it.

**$5,000 is an allocation ceiling, not an acceptable drawdown.**

Measure ending equity, realized + unrealized P&L, peak-to-trough drawdown $/%, lowest equity, time underwater, max capital deployed, max planned risk, max concurrent positions, cash-only blocked trades, and minimum observed starting cash needed to fund the full sequence.

Swing positions remain open risk until closed. Track overnight holds, position-nights, max simultaneous overnight positions, observed overnight premium gaps, and open-position unrealized P&L.

Do not change trade rules merely to make a smaller bankroll look better.

## Resolution realism

The selected contract is re-quoted on the scheduled resolver cadence. The path between snapshots is not assumed known.

If premium stop and underlying target are both true on the same observed snapshot, keep pessimistic accounting but label the row explicitly:

- `resolution_ambiguity=AMBIGUOUS`
- `pessimistic_status=LOSS`
- `pessimistic_resolution_used=true`

Later analysis can include/exclude the ambiguous cohort explicitly.

## Shadow-row entry geometry (accounting repair, 2026-09-17)

A read-only audit of the epoch-2 shadow rows (export `afs_options_export_20260917`) found that **58 of 117** rows had been created with the live first-sight price already at or beyond `target_1`, and 12 more beyond the stop. All were COUNTERFACTUAL observer rows (H1/H4 timeframe-observation, market-alignment rejects, the 30m sequence counterfactual). The counterfactual contract branch never evaluated entry geometry (`paper_entry_remaining_rr` was null on 107 of 117 rows) and `resolve_open_setup` is a level-state test, so those rows resolved `target_hit` on the next resolver tick. Their option "outcome" was the bid/ask spread: 56 target-hit rows, +4 / −51 / 1, net −$790. Reproduced exactly from the snapshot. #575 was working as specified; it guards the ACTIVE path only.

**Repair (#648, deployed as `94eb7d3`), accounting only:**

- every shadow row stores `paper_entry_remaining_rr` and `paper_entry_geometry` at creation (`AHEAD`, `TARGET_CONSUMED_AT_ENTRY`, `STOP_CONSUMED_AT_ENTRY`, same comparisons as the ACTIVE late-entry guard);
- a consumed row resolves into that state with no chain call and no P&L; rows written before the field existed derive it from their stored first-sight inputs;
- the two states are closed non-outcomes: excluded from closed/wins/losses/win rate/P&L (`ShadowJournalSummary.entry_consumed`), `NOT_AN_OUTCOME` in diagnostics;
- unchanged: #575, the ACTIVE 1.0 R:R guard, target finding, contract selection, DTE, premium stop, risk caps, coverage evidence.

Re-running the epoch-2 snapshot through the corrected classification: 84 WIN / 23 LOSS / 10 OPEN → 28 WIN / 11 LOSS / 8 OPEN / 58 target-consumed / 12 stop-consumed; net −$1,821 → −$764 on 39 resolved AHEAD rows. Zero consumed rows remain WIN or LOSS. Live acceptance on fresh rows is verified read-only on the first session after deployment.

**What the clean population says (47 AHEAD rows, descriptive only, no tuning).** The accounting repair exposed three separable questions, and the target-geometry and higher-timeframe horizon controls are now both complete:

1. **Target geometry is shallow, but target width alone does not rescue the sample.** `target_1` sits a median 0.52 R from the actual entry and 0.17 R on the rows that reach it. The controlled geometry test (`docs/options-shadow-target-geometry-controlled-2026-09-18.md`) holds entry, stop, premium stop, contract, ASK/BID cost basis and same-session horizon fixed. Forced-horizon P&L on the exact 47 `AHEAD` rows is **-$840** at recorded `target_1`, **-$783** at 1.5R, and **-$748** at 2R; censoring rises from **24/47** to **33/47** and **35/47**. Wider targets help slightly but all three variants remain negative.
2. **Option translation still matters.** On the original 22 rows where the underlying reached `target_1`, the option netted −$48 (+8 / −13). A target hit is not automatically profitable after ASK-entry/BID-exit translation.
3. **Horizon compatibility is timeframe-specific in this sample.** The controlled horizon test (`docs/options-shadow-horizon-compatibility-2026-09-18.md`) changes only the hold boundary for the 24 Daily/`4H_RTH` rows: same-session close versus exactly one additional RTH session. Combined P&L changes only **-$571 → -$539** while censoring falls **15 → 5**. Daily improves **-$358 → +$137** on 10 rows / 7 structural keys, but `4H_RTH` worsens **-$213 → -$676** on 14 rows. This supports no blanket higher-timeframe hold extension; the Daily result is suggestive only and too small/non-independent for a rule change.

Neither control is a 212R-specific options expectancy test, and neither authorizes V1 tuning.

**Research order (updated):** (1) no V1 tuning; (2) target-width and one-session Daily/4H horizon controls are complete; (3) accumulate more clean higher-timeframe evidence before considering any Daily overnight-hold policy, and do not apply a blanket extension to `4H_RTH`; (4) for 212R, the recent-SIP entitlement blocker remains for exact-SIP collection, while the separate IEX-provisional + delayed-SIP policy is now validated offline as a miss-allowed research source only; do not deploy or merge its rows into the exact-SIP cohort without a separately authorized prospective release; (5) retain the historical Delta/OI blocker.

## Coverage evidence lane and read-only audits (2026-09-16)

Everything in this section is **observation only**. None of it changed the scanner, the policy, the universe, contracts, risk, or cadence, and none of it authorizes a change. Nothing here proves an edge.

### What now runs beside V1

- Observer `cov-v0.1` (#578), episode reducer `ep-v0.1` (#579), outcome study `out-v0.1` (#580) and the after-close collector `col-v0.1` (#581, `771b6cf`) form a read-only chain that classifies **every** directional 30m RTH bar of the observer universe into a Strat family each session and measures its same-session outcome in two views: mechanical (entry at the trigger) and **first-sight** (entry at the first scanner-visible price, close + about 18 minutes). First-sight is the decision-relevant view; the mechanical view is a frictionless upper bound.
- The collector is installed on the box as a pinned, write-protected release under `/root/afs-shared/coverage/` with its own timer (16:45 ET weekdays). It never touches the production tree, `.env`, the scanner, or the V1 database. Five sessions (2026-09-09 to 2026-09-15) were backfilled by a proven manual run. The first unattended firing (2026-09-16, then at 16:35 ET) failed: the observer prices first sight from 5-minute bars ending at close + 26 min and the data plan refuses bars younger than 15 min, so every symbol returned an entitlement error and 1,410 events were stored without prices. #606 moved the timer to 16:45 ET and made completeness require a price on every in-session first sight, so an unpriced session is re-observed instead of skipped. #607 fixed a second latent bug (the two dead tickers on the allow-list were reported as fatal provider errors) and stamps `observer_repair` on any session whose observer ran more than once. The measurement modules are byte-identical to the original pin; only collector scheduling and completeness changed.
- Sessions 2026-09-09 to 2026-09-15 are the **retrospective** partition and are byte-frozen. Every later session is the **prospective** partition. The two are never merged.

### Audit 1 — missed-signal discovery (ruled)

- **Proven:** signal scarcity and coverage gaps. The 30m lane evaluates only 2-1-2 continuation, which is 59 of 835 structural episodes (7.1%) on the 20-symbol universe over five sessions. Market alignment then rejects 48 of 59 and target geometry 58 of 59, so no 30m episode has ever become ACTIVE. Every ACTIVE row in the sample came from the Daily 2-2-2 lanes. The `hourly=missing` context on the 15:00Z bar is a design blind spot, not an outage.
- **Not proven:** that profitable trades are being missed. The 43-move sample used to find blind spots is outcome-selected and must never be quoted as a win rate. H1/H4 counterfactual win/loss counts are counts, not expectancy.

### Audit 2 — opportunity coverage and attrition (closed, corrected v2)

- Objective denominator: moves with MFE of at least 1.5 ATR14 within four 30m bars and at least 0.75 ATR net at bar four, deduplicated. That gives 191 opportunities across the 20 symbols and sessions 2026-09-09 to 2026-09-16.
- Represented by an active detector: 33 (17.3%). Never represented: 158, of which 118 are unsupported-family structure. Matched non-move controls were represented 20.7% of the time, so the current detectors do not discriminate opportunities from non-opportunities.
- Strict funnel among the 33 represented: market-context gate 12, observer-only H1/H4 lanes 9, late 6, target geometry 2, hourly missing 1, actionable 2.
- Reading rule: the "never represented" share is a retrospective system-coverage measure over five sessions, not a live miss rate for the 20-symbol universe, and the 118 unsupported structures come from an outcome-selected population. Coverage problem proven; profitable coverage not proven.

### Audit 3 — outcome-independent unsupported-family validation (closed)

Population: every directional 30m bar (944 bars, 835 episodes, zero missing rows) on the 20 symbols over the five retrospective sessions, so no outcome selection. Baseline: other families, same symbol, direction and clock bucket. Opening-bar mechanical numbers are a gap artifact (the session opened through the trigger) and every table is read ex-opening, first-sight view.

| Family | n | First-sight 1R vs matched baseline | Status |
|---|---|---|---|
| 2-1-2 reversal | 81 | +11.9 pp | POSSIBLE SIGNAL |
| 1-2-2 | 60 | +12.1 pp | POSSIBLE SIGNAL (descriptive n) |
| 2-2-2 reversal | 176 | +0.8 pp | NO EDGE |
| 2-2-2 continuation | 197 | −11.4 pp | NO EDGE |
| outside bar | 80 | −15 pp | NO EDGE |
| 2-2 continuation | 83 | +1.7 pp (negative on the 148-symbol corpus) | NO EDGE |
| 3-2-2 reversal, 3-1-2, inside break, 3-2-2 continuation | 21–29 each | — | INSUFFICIENT |

Operator ruling: most missing families are coverage, not edge, so **no broad detector expansion**. 2-1-2 reversal is the strongest follow-up candidate, 1-2-2 the same pattern at a descriptive sample size. 2-2-2 continuation and outside bar are not to be pursued. Nothing is production-ready; no expectancy, P&L, or contract claim is made.

### Pre-registered prospective validation (three-session threshold reached)

- Question: do the 2-1-2 reversal and 1-2-2 first-sight excesses persist on sessions after 2026-09-15 that were never used to find them? Inside break is counted passively with no dedicated lane.
- Method: the unchanged observer, reducer and outcome modules; the same first-sight view, matched baseline and ex-opening reporting; a read-only analysis script over the collector's aggregate output. No new detector, lane, timer, threshold, or production change.
- Status rule, fixed before any prospective data exists: `PERSISTING POSSIBLE SIGNAL` requires at least 30 prospective episodes, at least 3 sessions, both directions, and an ex-opening first-sight excess of at least +5 pp; otherwise `NO LONGER SHOWING EXCESS` or `INSUFFICIENT PROSPECTIVE SAMPLE`. Any drift in the collector's pinned commit, module versions, first-sight delay, or completeness stops the analysis as `METHODOLOGY / DATA BLOCKED`. The words validated, production-ready, approved, or trade are never outputs of this step.
- Only the prospective view drives decisions. The retrospective tables above are hypothesis-generating and are not re-scored.
- First prospective session, 2026-09-16 (collected 21:09Z after the repair, binding sidecar carries `observer_repair`: run 6 unpriced at 20:35Z, run 7 priced at 21:03Z, same rules; usable as strategy evidence because setup selection was prospective and the outcome rule was frozen beforehand):

| Family | prospective n | L / S | first-sight 1R | matched baseline | diff | ex-opening diff | status |
|---|---|---|---|---|---|---|---|
| 2-1-2 reversal | 10 | 5 / 5 | 30.0% | 33.3% | −3.3 pp | −3.3 pp | INSUFFICIENT PROSPECTIVE SAMPLE |
| 1-2-2 | 15 | 7 / 8 | 60.0% | 50.0% | +10.0 pp | +3.8 pp | INSUFFICIENT PROSPECTIVE SAMPLE |
| inside break (passive) | 3 | — | — | — | — | — | counted only |

- Retrospective hashes were verified unchanged after the repair. The retrospective tables are not re-scored.
- **Automation proven 2026-09-17.** The 20:45Z firing (pinned `58d6c5f`) completed unattended in 59 s: observer step "ran", no repair flag, 148/150 observable, 1,450 events all priced, 1,243 episodes, aggregate of seven sessions, every retrospective hash unchanged, independent SIP bar count equal to the event count per symbol. All six checks passed.
- Second prospective session, 2026-09-17, two sessions total (still short of the three-session minimum):

| Family | prospective n | L / S | first-sight 1R | matched baseline | diff | ex-opening diff | status |
|---|---|---|---|---|---|---|---|
| 2-1-2 reversal | 29 | 17 / 12 | 42.9% | 53.9% | −11.0 pp | −4.3 pp | INSUFFICIENT PROSPECTIVE SAMPLE |
| 1-2-2 | 28 | 13 / 15 | 42.9% | 14.4% | +28.5 pp | +23.0 pp | INSUFFICIENT PROSPECTIVE SAMPLE |
| inside break (passive) | 6 | — | — | — | — | — | counted only |

  The 2-1-2 reversal excess had not reappeared through two sessions; the 1-2-2 excess was large but still below the pre-registered sample threshold.

- **Third prospective session / first decision point, 2026-09-18.** The collector completed cleanly and the frozen validation script passed its methodology checks: sessions agree across events/outcomes, first-sight delay remains 17.9 min, no missing rows, no provider errors, and the collector source SHA remains in the allowed set.

| Family | prospective n | L / S | first-sight 1R | matched baseline | diff | ex-opening diff | status |
|---|---:|---:|---:|---:|---:|---:|---|
| 2-1-2 reversal | 47 | 26 / 21 | 34.9% | 40.4% | −5.5 pp | **−4.2 pp** | **NO LONGER SHOWING EXCESS** |
| 1-2-2 | 38 | 16 / 22 | 45.9% | 20.8% | +25.1 pp | **+20.9 pp** | **PERSISTING POSSIBLE SIGNAL** |
| inside break (passive) | 7 | — | — | — | — | — | PASSIVE_ONLY |

  This closes the original three-session persistence question under the pre-registered first-sight framework. It does **not** make 212R a rejected option strategy, because the separate corrected 212R lane uses a causal trigger clock and different option-side evidence boundary. It does mean the retrospective family-level first-sight excess did not persist prospectively. Conversely, 1-2-2 preserved the defined excess threshold and is the only one of the two follow-up families that remains a possible structural signal under this specific study. See `docs/options-prospective-family-validation-2026-09-18.md`.

### Daily evidence rollups (reporting only)

`scripts/paper_collection_report.py` (#603, corrected in #604, cards #608, EOW registry #609, census cleanup #610) posts read-only EOD and EOW rollups of the futures journal, the options scanner database and the collector census to two dedicated Discord routes and writes a JSON artifact per run. It runs from a pinned copy under `/root/afs-shared/paper_collection/` with its own oneshot timers (EOD 17:10 ET weekdays, EOW Friday 17:20 ET) because the production release predates it and no trading-service restart was sanctioned for reporting; it is absorbed into the production tree at the next release that already needs a restart. It changes nothing and is not evidence authority: shadow-journal status counts are row states, not option P&L.

### Parked, not authorized

Target-geometry ablation on the full 2-1-2 continuation population, first-hour hourly-context audit, SPY/QQQ neutral-alignment ablation, legacy Signa timeout reliability audit, and option-chain snapshot retention. Each still needs a separate operator instruction. The three-session prospective precondition is now satisfied, but that does **not** authorize any of these parked studies automatically.

## Retired / superseded options clutter

- **`options_companion` is not the V1 collector. Keep it disabled with `OPTIONS_COMPANION_ENABLED=false`.** Its old short-DTE/stop logic must not leak into this campaign.
- The generic/manual `options_manager` exception surface is not V1 evidence authority. It must not be used to inject <14 DTE rows.
- **PR #446 (`claude/options-data-health`) is superseded for the current V1 path and is not a deployment prerequisite.** It targeted the older standalone data-health / companion-provider path. Any future provider-health defect should be fixed narrowly in the active `alert_ranker` provider/collector rather than reviving that stale branch.
- Historical options PRs/docs remain provenance only. Do not merge/reopen them merely because they contain an older version of a now-completed idea.

## Completed — do not redo without new evidence

- scanner/advisory plumbing + SQLite persistence;
- real-chain contract selection;
- 2099/far-future expiration protection;
- premium-stop + ACTIVE aggregate-risk accounting;
- causal setup proof + fail-closed alerting;
- multi-setup Daily collection;
- rejected-filter counterfactual collection;
- 1H / `4H_RTH` observer cohorts;
- ACTIVE/counterfactual risk + dashboard separation;
- resolution ambiguity telemetry;
- V1 runtime preflight;
- bankroll/drawdown/swing replay;
- MAE/MFE, entry extension, friction stress, and evidence-quality diagnostics.

The remaining uncertainty is primarily **operational proof + strategy evidence**, not another speculative feature build.

## Next action

There are now three separate evidence tracks and they must not be conflated:

1. **Running V1 scanner:** selector-evidence/replay is already deployed on scanner release `58f1c505...`. Continue natural V1 collection without tuning; do not redeploy unrelated `main` changes merely to absorb research code.
2. **Dedicated 212R prospective evidence:** exact-SIP collector v0.3 is merged in #739 but **not deployed or scheduled**, and current Alpaca entitlement still blocks real-time consolidated SIP. The alternative-source validation step is now complete offline: the preregistered 183-arm audit supports IEX only as a separate miss-allowed provisional clock with delayed-SIP reconciliation (89/90 provisional reversals confirmed; 2/91 SIP reversals missed; one false provisional rejected; long timing tail). If this source policy is pursued prospectively, the next gates are explicit operator authorization, a separately versioned observation-only IEX/reconciliation release, and pre-registered IEX-to-selector capture lag + collector cadence. The first acceptance proof must be a natural RTH `ARMED -> IEX provisional break -> decision-time selector evidence -> delayed SIP reconciliation` row with no scanner/risk/broker mutation. Do not deploy SIP collector v0.3 by silently swapping its source.
3. **Historical 212R option replay:** exact frozen trigger time/price and the production context-price path are proven (#733/#738), but exact historical selector/fill replay remains **DATA BLOCKED** on causal historical Delta and contract-level OI. Do not purchase data, synthesize analytics, or use current snapshots without separate authorization/evidence.

212R market-context policy, source-target/runner management, and numeric slippage/stress qualification also remain explicit policy decisions; none is authorized by the collector build.

Existing V1 collection continues without tuning. Steps 1–5 of the original deployment checklist remain complete (deployed, smoke proven, epochs `V1-EPOCH-1` and `V1-EPOCH-2` recorded). Also continue to:

1. Collect natural candidates on the 20-symbol universe without tuning V1. Any rule change starts a new cohort.
2. Let the after-close collector continue adding sessions under the frozen methodology and six-point integrity check. The original three-session decision threshold has now been reached; additional sessions are monitoring evidence, not permission to re-score the retrospective discovery sample.
3. Preserve the first prospective classification: 2-1-2 reversal = `NO LONGER SHOWING EXCESS`; 1-2-2 = `PERSISTING POSSIBLE SIGNAL`. Any 1-2-2 strategy follow-up must be a new separately authorized causal/option-side study, not an automatic V1 detector expansion.
4. Treat the controlled target-geometry and Daily/4H horizon studies as complete; do not rerun or tune from them without a new pre-registered question.
5. Treat legacy Signa read timeouts as a separate reliability audit; they cannot alter a trade decision (scorer contribution 0, no branch on Signa state).

**No proof, no trade. No optimization before evidence.**
