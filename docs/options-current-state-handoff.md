# Options — Current State Handoff

_As of 2026-09-09. This is the single current-state authority for the options lane._

Historical dated notes and old/closed PRs are provenance only. They do not override this file. Operational deployment proof lives in `docs/options-paper-v1-deployment-checklist.md`; diagnostic definitions live in `docs/options-v1-diagnostics.md`.

## Current verdict

**BUILD READY / DEPLOYMENT NOT YET PROVEN / V1 EVIDENCE EPOCH NOT STARTED / STRATEGY EDGE NOT PROVEN.**

Options-ready baseline on `main`: **`9d008a0dcdcb69270d80b663c678b4522f27ebb6`**.

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

Use `docs/options-paper-v1-deployment-checklist.md`.

1. Deploy current reviewed `main` through the normal options deployment path.
2. Verify the explicit V1 runtime pins and keep old companion disabled.
3. Run one normal market-hours smoke.
4. Run both read-only smoke reports:
   - `python -m alert_ranker.account_equity ...`
   - `python -m alert_ranker.v1_diagnostics ...`
5. Record deployed SHA + smoke timestamp as the V1 evidence epoch.
6. Collect natural candidates without tuning V1.
7. Diagnose signal/timeframe → entry → stop → target/exit → filters → execution realism once samples are useful.

**No proof, no trade. No optimization before evidence.**
