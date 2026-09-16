# Options — Current State Handoff

_As of 2026-09-16. This is the single current-state authority for the options lane._

Historical dated notes and old/closed PRs are provenance only. They do not override this file. Operational deployment proof lives in `docs/options-paper-v1-deployment-checklist.md`; diagnostic definitions live in `docs/options-v1-diagnostics.md`; the read-only coverage evidence lane (observer, reducer, outcome study, after-close collector) is described in `docs/options-coverage-observer.md`.

## Current verdict

**DEPLOYED / SMOKE PROVEN / V1 EVIDENCE EPOCH RECORDED (`899a524` + 2026-09-15T16:50:00Z, cohort `V1-EPOCH-1`) / STRATEGY EDGE NOT PROVEN.**

Epoch record: `docs/options_v1_evidence_epoch.json`. **Cohort `V1-EPOCH-2` (`UNIVERSE_EXPANSION_6_TO_20`) started 2026-09-16T16:47:46Z** on the first clean RTH cycle after the watchlist grew from 6 to 20 symbols (deployed release `62546883`, scanner code identical to `899a524`; rules, risk, cadence and Signa authority unchanged). `V1-EPOCH-1` (six symbols, 2026-09-15T16:50:00Z to 2026-09-16T16:47:46Z) is retained inside the record. Rows before an epoch's start belong to the previous cohort, not to it. Rules in force: #570 late-entry guard, #571 Daily-lane timing + 1R target floor, #575 ENTRY_LATE episode block (counterfactual preservation). Any rule change starts a new cohort.

Deployed baseline on `main` and on the box: **`899a524aad82a66c80ba832ea5601430bdac7355`** (release dir `899a524aad82-20260914-203217`).

Final CI for that baseline: **4,921 passed / 7 skipped / 2 warnings**.

Do not claim the VPS is on this SHA until box-side deployment and the market-hours smoke prove it.

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
- policy id.

Missing evidence is flagged; it is not invented.

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

## Coverage evidence lane and read-only audits (2026-09-16)

Everything in this section is **observation only**. None of it changed the scanner, the policy, the universe, contracts, risk, or cadence, and none of it authorizes a change. Nothing here proves an edge.

### What now runs beside V1

- Observer `cov-v0.1` (#578), episode reducer `ep-v0.1` (#579), outcome study `out-v0.1` (#580) and the after-close collector `col-v0.1` (#581, `771b6cf`) form a read-only chain that classifies **every** directional 30m RTH bar of the observer universe into a Strat family each session and measures its same-session outcome in two views: mechanical (entry at the trigger) and **first-sight** (entry at the first scanner-visible price, close + about 18 minutes). First-sight is the decision-relevant view; the mechanical view is a frictionless upper bound.
- The collector is installed on the box as a pinned, write-protected release under `/root/afs-shared/coverage/` with its own timer (16:35 ET weekdays). It never touches the production tree, `.env`, the scanner, or the V1 database. Five sessions (2026-09-09 to 2026-09-15) were backfilled by a proven manual run; the first unattended firing is 2026-09-16 20:35Z and the lane is frozen until it is checked.
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

### Pre-registered prospective validation (staged, not yet started)

- Question: do the 2-1-2 reversal and 1-2-2 first-sight excesses persist on sessions after 2026-09-15 that were never used to find them? Inside break is counted passively with no dedicated lane.
- Method: the unchanged observer, reducer and outcome modules; the same first-sight view, matched baseline and ex-opening reporting; a read-only analysis script over the collector's aggregate output. No new detector, lane, timer, threshold, or production change.
- Status rule, fixed before any prospective data exists: `PERSISTING POSSIBLE SIGNAL` requires at least 30 prospective episodes, at least 3 sessions, both directions, and an ex-opening first-sight excess of at least +5 pp; otherwise `NO LONGER SHOWING EXCESS` or `INSUFFICIENT PROSPECTIVE SAMPLE`. Any drift in the collector's pinned commit, module versions, first-sight delay, or completeness stops the analysis as `METHODOLOGY / DATA BLOCKED`. The words validated, production-ready, approved, or trade are never outputs of this step.
- Only the prospective view drives decisions. The retrospective tables above are hypothesis-generating and are not re-scored.
- Expected first result after the 2026-09-16 collection: one session, therefore `INSUFFICIENT PROSPECTIVE SAMPLE`.

### Parked, not authorized

Target-geometry ablation on the full 2-1-2 continuation population, first-hour hourly-context audit, SPY/QQQ neutral-alignment ablation, legacy Signa timeout reliability audit, and option-chain snapshot retention. Each needs a separate operator instruction; none may run before the prospective work has sessions.

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

Steps 1–5 of the deployment checklist are complete (deployed, smoke proven, epochs `V1-EPOCH-1` and `V1-EPOCH-2` recorded). What remains:

1. Collect natural candidates on the 20-symbol universe without tuning V1. Any rule change starts a new cohort.
2. Let the after-close collector add one session per weekday; verify the first unattended firing (2026-09-16) before trusting the timer.
3. Re-run the pre-registered prospective family validation as sessions accrue and report its status only in the fixed vocabulary above.
4. Diagnose signal/timeframe → entry → stop → target/exit → filters → execution realism once V1 samples are useful.
5. Treat legacy Signa read timeouts as a separate reliability audit; they cannot alter a trade decision (scorer contribution 0, no branch on Signa state).

**No proof, no trade. No optimization before evidence.**
