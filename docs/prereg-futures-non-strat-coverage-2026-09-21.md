# Prereg — futures non-Strat level coverage (`fns-v0.1`), research-only

**Status: RESEARCH / READ-ONLY. Written 2026-09-21 before the first run.**
Nothing here is a strategy, a shadow lane, a paper lane, or a promotion
candidate. It reuses the frozen `ns-v0.1` predicates
(`alert_ranker/non_strat_coverage.py`) on futures 5m bars to answer one
coverage question: *how often do the same mechanically-defined level
interactions occur on MNQ/MES RTH, and what does the underlying do
afterwards?* — measured as MFE/MAE, with **no stop/target geometry**.

## Priors this run must not pretend away

- `docs/mnq-structural-level-5m-study-2026-07-13.md` — MNQ PDH/PDL + ORB
  break/retest/reclaim/rejection with fixed targets was **REJECTED**
  (negative in both walk-forward halves; runner variant fails 2-tick
  slippage). This run is not a re-test of that edge and cannot revive it.
- Structural-level P1–P8 / R5–R7 (MNQ/MES, 2026-09-16/17): level attribution
  is "a filter, not an edge"; outcomes sealed.
- Null baseline for any later PF claim: p95 1.94 / max-of-500 2.55.
- Contract roll: `ROLL_PROVENANCE_UNKNOWN` for the Polygon continuous series;
  no roll awareness in code (memory 2026-09-15).

## Frozen definitions

| element | rule |
|---|---|
| bars | `data/replay_polygon_5m/{MNQ,MES}/<SYM>_<date>.jsonl` (gitignored, Polygon 5m, 2024-07-02 → 2026-06-26), rows with `session == "new_york"` |
| RTH window | 09:30–16:00 **America/New_York** (cash session; the replay's `session` label is fixed in UTC and does not follow DST, so it is not used as the window), 78 bars; a session with fewer than 78 bars in that window is **skipped** (whole-session rule, as `cov-v0.1`) |
| PDH / PDL | prior RTH session's high/low over the same window (identical to `ns-v0.1`); the payload's `previous_day_high/low` is a different (Globex-day) level and is reported only as an agreement rate |
| ORB | first six RTH 5m bars (09:30–10:00 ET), frozen; the payload's `orb_high/low` is a different object and is reported only as an agreement rate |
| VWAP | cumulative over RTH bars only (`ns-v0.1` predicate); the payload's `vwap` field is not used |
| history for EMA20 / volume ratio | prior RTH sessions only (lookback 4 sessions) |
| SPY/QQQ alignment | not available → `market_aligned = None` for every row (no substitute index) |
| roll exclusion | sessions in the Mon–Fri week containing the 3rd Friday of Mar/Jun/Sep/Dec, plus the next session, are excluded (count reported) |
| outcomes | `measure_outcomes` as `ns-v0.1`: 15m/30m/60m/EOD close return, MFE, MAE in bps from the event close; EOD = 16:00 ET |
| identity | `observer_id = FUTURES_NON_STRAT_COVERAGE`, `observer_version = fns-v0.1`; rows never pooled with `ns-v0.1` |

## What is reported (fixed before running)

Per instrument, per family: events, episodes, sessions with ≥1 episode;
**episode-level** (first event of each episode) median and mean of 60m and
EOD close return, median MFE, median MAE; share of episodes with
MFE ≥ 2 × MAE at 60m (a shape statistic, not a win rate). Plus: sessions
skipped (incomplete / roll), and the PDH/PDL and ORB agreement rates between
the frozen definitions and the payload fields.

No PF, no win rate, no R multiples, no null-baseline comparison — there are no
trades, so none of those are defined here.

## What would make this worth a next step (also fixed now)

Only a family whose episode-level 60m **median** close return is positive on
**both** instruments **and** in both calendar halves (2024-07→2025-08,
2025-09→2026-06) with ≥ 100 episodes per half is a candidate for a prereg'd
geometry study. Anything else is recorded and closed.

## Runner

`python research/futures_non_strat_coverage.py --instrument MNQ --out logs/fns_v01`
(read-only; writes JSON + Markdown under `logs/`, gitignored). Not on the box,
not scheduled, imports nothing from `strategy/`, `execution/`, `engine/`.
