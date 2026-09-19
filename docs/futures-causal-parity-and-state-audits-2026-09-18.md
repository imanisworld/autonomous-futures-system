# Futures — mechanical defect fix + causal/timing, account-feasibility, Daily 2-2 coupling and live↔replay parity audits — 2026-09-18

**Status:** AUDIT RECORD. Read-only except PR #771 (a journal-accounting repair). No strategy, risk, gate, threshold, execution, env or deploy change. Nothing here validates a strategy.

Operator request (2026-09-18 late): fix the 3-2-2 journal/state-persistence omission; finish causality/timing audits on the remaining higher-timeframe families; finish account-feasibility / risk-architecture checks (3-2-2 stop / R:R vs account rules); audit Daily 2-2 epoch/state coupling before treating its evidence as promotion-grade; do the broader live ↔ replay causal parity audit (HTF feature construction, timestamps, session boundaries, formula identity).

## 1. 3-2-2 First Live journal/state omission — FIXED in PR #771 (not deployed)

**Defect.** `DailyState.strat_322_first_live_state` (`risk/risk_engine.py`) carries the same persist/reconstruct contract as `four_hr_retrigger_state` and `strat_212_122_state`, but:

- `webhook/runner.py` wrote only `strat_4hr_retrigger` and `strat_212_122` into the journal `strategy_state` block at all four journal sites;
- `replay/replay_engine.py` did the same at its two sites;
- `JournalLogger.get_daily_state()` restored only those two.

An armed 3-2-2 First Live state was therefore silently dropped on every runner restart while its siblings survived. This is the omission noted in `docs/prereg-one-min-322-first-live-observer-2026-09-18.md`.

**Fix (PR #771, branch `claude/322-state-journal-persistence`, commit `6129cfa`).** Add `strat_322_first_live` at all six write sites; restore it in `get_daily_state()` behind the same dict guard (legacy entries without the key yield an empty state as before); three pinning tests (round-trip restore, legacy-entry tolerance, structural guard that every journaled `strategy_state` block in runner/replay carries all three fields). Full suite in the branch worktree: **6226 passed / 7 skipped**; CI green (tests, CodeQL, both Analyze jobs).

**Blast radius.** None at runtime today: the state machine's own `trading_date` guard still discards any prior-day state, the wide-stop forward collector builds its own fresh `DailyState` from bars, and the 1m 3-2-2 observer keeps its state under `tf1m/` only. The fix only matters once merged and picked up by a sanctioned futures release; it is not a reason to restart.

## 2. Causality / timing audits — remaining higher-timeframe families

Status per family, entry mechanism as implemented, and what the 4HR/3-2-2 audits already established:

| family | entry mechanism in code | timing parity status | evidence |
|---|---|---|---|
| MNQ 4HR Re-Trigger | armed touch; live inferred touch from completed 5m OHLC | **AUDITED** — completed-5m inference overstated; causal pre-armed touch survives 3-tick stress | `docs/futures-current-status-2026-09-18.md` §4HR |
| MNQ 60M 3-2-2 First Live | armed First Live touch | **AUDITED** — 13/34 candidates detached >32 ticks at 5m close; pre-armed model survives | `docs/322-trigger-timing-ab-2026-09-18.md` |
| Strat 2-1-2 / 1-2-2 (MNQ, MES 15m) | next-bar boundary break, stop-order semantics on the *completed* 5m bar (`strategy/strat_212_122.py`: `entry_touched = high >= entry`; fill = open if gapped else boundary+1 tick; same-bar entry+stop fails closed) | **NOT PROVEN** — replay assumes a resting stop order fills at the boundary; live evaluates only after the 5m bar completes, so a live IOC at that close inherits the same detachment class the 4HR audit measured. No detachment distribution has been measured for this family. | code read; no A/B exists |
| Daily 2-2 (paper, `context/daily_22_swing_collector.py`) | first 5m break of prior-day high/low, detected only on the bar whose `ts == current_ts` (no backfill), then IOC at decision close with 32-tick tolerance and 1-tick slip (`_expected_ioc_fill`) | **NOT PROVEN, same class as 4HR** — trigger is recognised at 5m completion; detachment of the true touch from the decision close is not measured. Forward sample: 1 fill, 1 outcome. | box `swing_audit.jsonl` (7 rows) |
| 12HR Miyagi | research detector on 12h bars (`research/detector_12hr_miyagi.py`); shadow-only in the wide-stop ledger, cannot fill | **PARKED** — timing parity only if reopened (unchanged ruling) | `context/wide_stop_ledger_paper.py` |
| Asia D+EMA (MNQ paper cohort) | canonical IOC at decision-bar close, 1 adverse tick, 32-tick tolerance (`context/asia_d_ema_paper_cohort.py`) | **CONSISTENT BY CONSTRUCTION** with its own accepted model (close-confirmed, not touch); no touch-inference claim exists to audit | code read |
| Transition 400t/30m | close-confirmed | WAIT (slippage robustness), no touch claim | existing ruling |

Ruling implied by the code read, not new authority: the only families with an *un-audited touch-inference gap* are **generic 2-1-2/1-2-2** and **Daily 2-2**. Both use the same "recognise at 5m completion" pattern the 4HR audit proved optimistic. A frozen-population detachment A/B (decision close vs true first touch, per candidate) is the next mechanical audit for each, and neither may inherit the 4HR or 3-2-2 result.

## 3. Account-feasibility / risk architecture — 3-2-2 under the corrected timing

Computed from the frozen artifact `scripts/322_trigger_timing_ab_2026-09-18.json` (SHA-256 `f0bde574…`, per-candidate `ledger.1tick` rows: trigger, stop, target, pre-armed fill). Caps from `risk_rules.yaml` v1.2.2: `max_stop_ticks.MNQ=120`, `min_rr_ratio=2.0`, `min_target_points.MNQ=15`, `max_daily_loss=150` (1c), `max_drawdown_percent=0.30`, `starting_balance=1500`, MNQ 1 contract below $2,000.

| model | n | stop ticks median / p90 / max | > 120-tick cap | R:R median / max | R:R < 2.0 | 1c risk $ median / max | risk > $150 daily cap | target < 15 pts |
|---|---|---|---|---|---|---|---|---|
| plan (trigger price) | 34 | 472 / 764 / 1,471 | **34/34** | 0.24 / 1.19 | **34/34** | 236 / 736 | 32/34 | 11/34 |
| pre-armed touch (corrected timing) | 33 filled | 460 / 765 / 1,472 | **33/33** | 0.27 / 1.18 | **33/33** | 230 / 736 | 31/33 | 10/33 |

Candidates clearing every structural cap under the corrected timing: **0**. Three candidates carry a single-contract stop loss above 30% of the $1,500 starting balance.

Conclusion: the timing correction moves entries by ticks, not by the ~460-tick structural stop, so the account incompatibility is unchanged. 3-2-2 is executable only under a different risk architecture (wider stop cap, sub-1 R:R acceptance, larger balance), which is a policy decision with drawdown and concentration consequences — not something evidence from n=34 authorises. Status stays **HOLD / PARKED**; do not raise `max_stop_ticks` or lower `min_rr_ratio` to admit it.

## 4. Daily 2-2 epoch / state coupling

Code facts (`context/daily_22_swing_collector.py`, `context/daily_22_state_integrity.py`, `context/wide_stop_forward_router.py`):

- Daily's persisted state (`hypothetical_ledger/daily_22_5k/swing_state.json`) is keyed on the **shared** `WIDE_STOP_LEDGER_EPOCH_START`; there is no Daily-specific epoch variable.
- `_load_state()` silently returns a **fresh $5,000 ledger** on any epoch mismatch or unreadable file. The only thing that stops a silent reset is `assert_state_integrity()` in the forward router, which raises `daily_swing_state_epoch_mismatch` before the collector runs. That is the fail-closed that fired during the #670 activation and preserved the ledger.
- Daily is also gated by `wide_contract.evaluate(cfg).active`: turning the wide-stop lane off stops Daily's evidence collection too.
- Audit rows (`swing_audit.jsonl`) carry ledger balance/peak/drawdown per row but **no `epoch` field** and **no release SHA / code identity**. Attribution of a row to an epoch is by timestamp only.

Box state (read-only, 2026-09-18 ~23:00Z): epoch `2026-09-09T04:21:04+00:00`, balance $4,406.02, peak $5,000, max drawdown 11.88%, position null, 6 seen keys, halted false; audit 7 rows 09-10 → 09-18 (1 fill → STOP_HIT, 3 BLOCKED `OPEN_SWING_POSITION`, 1 `FIRST_BREAK_IS_22_REVERSAL_NOT_CONTINUATION`, 1 `REJECTED_RISK ACTUAL_RR_BELOW_2`). `.env`: `WIDE_STOP_LEDGER_EPOCH_START` and its proof pin both `2026-09-09T04:21:04Z`.

Promotion-grade assessment: **NOT YET.** Before Daily evidence can be treated as promotion-grade:

1. epoch identity must be stamped on every audit row (and ideally the release SHA), so a future epoch reset or a silent `_load_state` fallback cannot re-attribute rows;
2. the silent fresh-ledger fallback in `_load_state()` should not be reachable in production — today it is only masked by the router's integrity gate;
3. the Daily epoch should be decoupled from the wide-stop epoch (a Daily-specific `DAILY_22_EPOCH_START` with its own proof pin) so a wide-stop-only reset is possible without touching a live swing ledger (the #670 incident already required a manual revert for exactly this reason);
4. the sample is n=1 resolved; timing parity (§2) is unproven.

Items 1–3 are mechanical and separable from strategy evidence; each needs its own authorization because they change a running evidence lane's persistence format.

## 5. Live ↔ replay causal parity

### 5.1 HTF feature construction

| feature | live (TradingView → `webhook/state_builder.py`) | replay (`scripts/polygon_to_replay.py` / `scripts/csv_to_replay.py`) | parity |
|---|---|---|---|
| daily / 4H / 1H bar type + direction (`state.htf.*`) | Pine `request.security(…, [open[1], high[1], …], lookahead_on)` = last **closed** HTF bar; labels `two_up/two_down/inside_bar/outside_bar` | resampled from base bars: `resample(raw, 240)` uses **fixed UTC buckets** (`ts // 14400`); `htf_at()` exposes a row only after `ts − duration` (last closed bar); labels `1/2U/2D/3` | **4H bar identity DIVERGES**: TradingView anchors CME 4H bars to the 18:00 ET session open (18/22/02/06/10/14 ET), replay buckets fall on 20/00/04/08/12/16 ET (EDT). 1H is anchor-safe (whole-hour offset). Daily is aligned post-C14 (`resample_daily` uses `cme_trading_day`). Label vocabularies differ; consumers accept both (`strategy/regime_classifier.py`, `strategy/strat_classifier.py`). |
| daily / 4H direction when `htf_direction_source=live` | `context/live_direction.py`, UTC-anchored 4H windows (documented: "differs from TradingView's session-anchored 4H bars… keeps replay and live consistent") | same function | identical by construction |
| chart-TF bar types (`current/previous/two_bars_back`) | Pine `classify_bar(high, low, high[1], low[1])` | csv path: Pine's own exported labels via `bar_type_str`; polygon path: `classify_htf_bar` (same H/L break rule) | same formula; the csv converter has a built-in Pine-vs-derived mismatch checker (`csv_to_replay.py` ≈ L895–925) |
| 4HR / 3-2-2 armed state, 1H stop anchor | `recent_five_min()` from persisted 5m `bars_*.jsonl` (source = TradingView 5m payloads) → `advance_*` state machines | `_four_hr_bars` deque from the 5m corpus → the **same** `advance_*` functions | formula identical by construction; only the 5m *data source* differs (TradingView vs Polygon/CSV). The 34-candidate 3-2-2 cross-check (research detector vs canonical state machine, exact parity) is the existing proof for this path. |
| PDH/PDL/PDC, VWAP, HOD/LOD | Pine values in payload (Pine "D" = CME session day) | `cme_trading_day` (C14) | proven vs Pine `time_tradingday` (`docs/c14-live-replay-daily-identity-fence-2026-09-17.md`); `location_context._trading_day` mechanical divergence remains, no decision authority |

**Decision authority of the 4H divergence today: none.** The only decision consumer of `state.htf.*` is `DecisionEngine._check_htf_alignment`, which is active only when `htf_direction_mode` is `strict`/`prioritize` or `require_htf_alignment[instrument]` is true. Box: `HTF_DIRECTION_MODE=off`, `HTF_DIRECTION_SOURCE` unset (default `payload`), `require_htf_alignment` false for every instrument. The divergent 4H labels are journaled context/FTFC evidence only. It becomes a live/replay decision divergence the moment the HTF gate is enabled with `htf_direction_source=payload`; enabling it should require `htf_direction_source=live` (already UTC-consistent) or a session-anchored resampler.

### 5.2 Timestamps

- Pine emits `timestamp = time` = bar **open** (ms epoch); replay rows are timestamped at bar open (`csv_to_replay.py` L60/L648/L752). Both sides treat the bar as complete when processed (live: `barstate.isconfirmed`; replay: sequential candles). Consistent.
- Consequence already proven for touch strategies: any "touch" inferred from a completed bar is recognised at open+TF, never at the touch. That is the 4HR finding, and §2 marks 2-1-2/1-2-2 and Daily 2-2 as carrying the same unmeasured gap.
- `htf_at()`'s `ts − duration` rule and Pine's `[1]` both select the last closed HTF bar, so there is no HTF lookahead on either side given the same bar partition; the partition itself is the §5.1 issue.

### 5.3 Session boundaries

- `webhook/state_builder.detect_session` and `scripts/csv_to_replay.detect_session` are the same ET map (asian 18:00–02:59, london 03:00–09:29, new_york 09:30–16:59, off_hours 17:00–17:59); the diff is comments/variable names only.
- Daily identity: Pine "D" vs `cme_trading_day` — proven (C14) for MES/MNQ/M2K; other roots use the mechanical key. `daily_22_swing_collector._trading_day` and `location_context._trading_day` are mechanical and diverge on holiday transitions (C14 §2–3); the Daily 2-2 lane therefore inherits a holiday-transition identity risk that is not yet fenced.
- 4H partition: see §5.1 (session-anchored live vs UTC-anchored replay).

### 5.4 Formula identity

- Strat bar classification: same high/low break rule on all sides (Pine `classify_bar`, replay `classify_htf_bar`, backend `strategy/strat_classifier`); vocabularies differ (`two_up` vs `2U`) and are normalised by consumers. Direction for outside bars: Pine `c >= o ? UP : DOWN`, replay `close >= open` — identical.
- Bracket arithmetic for 4HR / 3-2-2 / 2-1-2 / 1-2-2 / Daily 2-2 lives in shared Python (`strategy/*.py`, `context/daily_22_swing_collector.py`) used by both live and replay — identical by construction.
- `market_condition` / `trend_strength`: replay reconstructs Pine's formula (`scripts/pine_market_condition.py`, engine-facing `market_condition`, legacy heuristic kept only as `legacy_market_condition`); parity report `docs/replay-market-condition-parity-report-2026-07-26.json`. Not re-audited here.
- Pine ↔ backend bracket parity remains strategy-specific and not globally proven (existing ruling; ORB stop-offset mismatch on record in `docs/orb-breakout-canonical-evidence-2026-07-26.md`).

## 6. What this authorises

Nothing. Findings that are mechanical and separable, each needing its own go:

1. merge #771 (3-2-2 state persistence) and pick it up at the next sanctioned release;
2. measure 2-1-2/1-2-2 and Daily 2-2 decision-close-vs-touch detachment on frozen populations before either family's fills are treated as executable evidence;
3. Daily 2-2: epoch stamping on audit rows, remove the silent fresh-ledger fallback, decouple the Daily epoch from the wide-stop epoch;
4. before ever enabling the HTF alignment gate on the box, either set `htf_direction_source=live` or replace the UTC 4H resampler with a session-anchored one and re-run parity.

Do not: tune stops/R:R to admit 3-2-2; reset the Daily ledger; restart for #771; treat the 4H label divergence as a current runtime defect.
