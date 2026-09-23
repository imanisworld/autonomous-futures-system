# MGC 4H Wide-Geometry Forward Observation — Preregistration (2026-09-23)

**OBSERVATION / RESEARCH ONLY. NO EXECUTION AUTHORITY.** This campaign
places no orders, changes no collector, strategy, risk rule, broker path,
deployment or VPS, and adds no live or paper lane. It is scored **offline**
from Polygon bars fetched after the fact, like prereg #929. A PASS makes MGC
4H wide eligible for a *separate* proposal under the standing change rule
(prereg, evidence, staged rollback, explicit operator GO). It is never a GO
by itself.

## 1. Why this exists (what was seen before registration)

`docs/production-detector-geometry-test-2026-09-23.md` (#946) tested the
production setup detectors in three configurations across six markets. The
operator approved exactly one narrow forward test on 2026-09-23.

- **MGC 4H wide** was the only configuration that passed. 2026 results:
  - n = 438 trades on 174 days, +$23,778, PF 1.306;
  - both halves positive;
  - above random-direction (1.28) and random-time (1.19);
  - better than the 15m control.
- **The pass is marginal:**
  - PF 1.306 vs a 1.30 bar;
  - 8 wide configurations were tried;
  - the three best days were 53% of net, and January alone was 45% of net.
- **Nothing observed on or before the scoring start below is scored.** The
  2024-09 → 2026-09-22 results are motivation, not evidence.

## 2. Hypothesis

**H1.** On forward MGC data it has never seen, the production structural
setups signalled on 4-hour bars clear every criterion in §6 when traded as
follows:
- entry at the detector's entry price;
- stop at 2 × ATR(14);
- a trailing exit after +1R;
- 1 contract and $4.00 costs.

## 3. Frozen rules

### Data

- Polygon futures 15m aggregates for dated MGC contracts (months G, J, M, Q,
  V, Z), fetched fresh on every run.
- **Front contract per UTC day:** the dated contract with the highest volume
  on the previous UTC day. It never rolls backwards to an earlier expiry.
- Bars come only from that day's front contract, with no back-adjustment and
  no filling.
- **Settlement:** a CME observation day is used only once it ended at least
  24 h before the fetch. The raw bars get a SHA-256 hash.

### Candles and signals

- 15m candles come from the unchanged `scripts/polygon_to_replay.derive_candles`.
- **4H candles:** session-anchored buckets of the same 15m bars (18:00 ET
  roll, 240-minute buckets), enriched by the same `derive_candles`.
- **Warm-up:** 90 calendar days before the scoring start.
- **Detectors:** the production functions, unchanged.
  - `strategy.shadow_setups.evaluate_shadow_setups(state, recent_bars, config,
    include_canonical_observers=False)`, where `recent_bars` is the last 8
    4H bars (current included) within 3 calendar days.
  - The canonical 2-1-2 / 1-2-2 state machine,
    `execution.cross_instrument_observation._strat_212_122_candidate`.
  - Both run on `MarketState` from
    `replay.replay_engine.ReplayEngine._market_state_from_candle`.
  - Only the structural-outcome populations configured for MGC in
    `config/cross_instrument_observation.json` are kept.

### Trade geometry

- **Entry:** the detector's entry price. Fill when a later 15m bar's range
  touches it; a detector-declared `filled_on_signal_bar` is honored.
- **Stop:** entry ∓ 2 × `reconstructed_atr14` of the 4H signal candle. No
  ATR means no trade.
- **Trail:** once the best favorable excursion reaches +1R, from the next
  15m bar the stop becomes max(entry, best − 1R) for longs (mirror for
  shorts). There is no fixed target.
- **Stop first:** within a bar, the stop is checked before the excursion
  updates. A stop touched on the fill bar is a loss.
- **Expiry:** at the observation-day roll, at that day's last 15m close.
- The path is always read from 15m bars after the 4H signal bar closes.
- **Cost:** $4.00 per trade (commission plus 2 ticks). It may be raised,
  never lowered.

### Gap days (never filled)

- An observation day is a gap day if it is:
  - a regular weekday missing any 15m slot between 18:00 ET and 17:00 ET;
  - or a reduced-schedule day (the exchange-holiday calendar used by #929
    §9.3) with an internal hole.
- A trade whose signal bar or path touches a gap day is `VOID_GAP_DAY`.
- If more than 10% of observation days in the scoring window are gap days at
  the look, H1 is `INSUFFICIENT_DATA`.

## 4. Scoring window

- **Scoring start: the first 4H bar at or after 2026-09-24T22:00:00Z** (the
  CME session opening 18:00 ET Thursday 2026-09-24).
- A trade counts only if its signal bar starts at or after that instant.
- **Deadline:** 2027-06-30.

## 5. Blind until the look

Before the look, only operational counts may be read:
- trades by setup;
- terminal and void trades;
- observation days;
- gap days;
- pipeline health.

No net P&L, PF, win rate or drawdown may be read.

**Single look,** when **both** of these hold:
- ≥ **150** terminal trades;
- ≥ **60** observation days.

That pace is about 3 months, based on about 50 trades a month in 2026. If
the deadline arrives first, the look happens then and H1 is
`INSUFFICIENT_SAMPLE` unless the minimum is met.

**Step 0 before the look (data parity).**
- Rerun the #946 parity gate for MGC over the scoring window: production
  15m detectors on the Polygon bars vs the live observation lane's MGC
  CANDIDATE rows.
- It needs ≥ 90% reproduced and ≥ 95% of those within 1 tick on entry,
  stop and target.
- If it fails, the result is `BLOCKED`, and no P&L is read.

## 6. Decision (all required for PASS)

| # | Criterion |
|---|---|
| 1 | ≥ 150 terminal trades on ≥ 60 observation days |
| 2 | Net after costs > $0 |
| 3 | PF after costs ≥ **1.30** |
| 4 | Both chronological halves (by trade count) net > $0 |
| 5 | The three best observation days contribute ≤ **50%** of net |
| 6 | Max drawdown ≤ **$6,000** (about 1.1 × the 2026 in-sample drawdown) |
| 7 | PF above the 95th percentile of **random direction** (500 draws: each trade's bracket mirrored at random) |
| 8 | PF above the 95th percentile of **random time** (200 draws: random 4H bars in the scoring window, entry at the bar's close, the same direction mix, 2×ATR stop, trail, expiry and cost) |

**Outcomes:**
- **PASS** (all eight) → `FORWARD_EVIDENCE`: eligible for a separate
  proposal.
- **FAIL** (any of 2–8) → `REJECTED`. The configuration is closed; no retune
  or re-run on the same data.
- **`INSUFFICIENT_SAMPLE`** or **`INSUFFICIENT_DATA`** as defined above.

**Reported, never gating:**
- the long/short split;
- the per-setup split;
- the monthly net;
- the same numbers for the 15m control and 60m wide over the same window.

## 7. Not allowed

- Changing any rule, threshold, cost (except raising it), start, minimum or
  deadline.
- Reading P&L before the look, or looking twice.
- Adding markets, timeframes or setups.
- Filling or backfilling bars, or switching data vendors.
- Treating any result as runtime authority.
- Adding a live or paper collector lane for this campaign.

## 8. Ownership

Registered by Claude (auditor lane) on operator approval, 2026-09-23. The
evaluator lands as `research/mgc_4h_wide_forward.py` +
`scripts/mgc_4h_wide_forward.py`. It needs the local Polygon key and runs on
the Mac; the box has no key.

**No proof, no run.**
