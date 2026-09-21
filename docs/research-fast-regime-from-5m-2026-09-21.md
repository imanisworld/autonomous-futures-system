# Research item — "Would the 5m/1m feed tell us it's TRENDING sooner?" (2026-09-21)

**Status: PREREGISTERED STUDY, NOT RUN. Evidence-only. No runtime, rule, or
config change proposed here. Any gate change that comes out of it is a
post-2026-09-30 item with its own GO.**

## The operator's question

> We were using the old rules for RANGE_BOUND — does it account for getting the
> 1-min bars? Wouldn't this help know if it is trending sooner?

## What the code does today (verified in source, not memory)

- The regime label on every 15m decision is **TradingView's**. The Pine script
  sends `market_condition` on the 15m alert and `SignalEngine._score_market_condition`
  trusts TRENDING / RANGE_BOUND verbatim (`strategy/signal_engine.py:1373`).
  We only intervene on CHOPPY (veto to RANGE_BOUND when there is directional
  structure) and never on DEAD.
- The **1m and 5m alerts never touch that label.** They are intercepted before
  the strategy/risk/broker path and land in isolated lanes:
  `context/five_min_feed.py` → `logs/tf5m/`, `context/one_min_feed.py` →
  `logs/tf1m/` (OHLCV only, no Pine label). They feed the 4HR armed-trigger
  observer and the 3-2-2 first-live observer — evidence, not regime.
- So: **no, the 1m bars do not inform "is it trending".** The regime is decided
  once per 15m bar from the 15m bar.

## Why "sooner" is not obviously better

The instinct is right that a 15m label is late: at 22:00Z Sunday there is no
trend history, the bar is labelled RANGE_BOUND, and the breakout goes untraded
(2026-09-20 miss, `counterfactual-gates-off-2026-09-21.md`). But the two
counterfactuals already on file cut the other way:

- Over 8 Sunday reopens, RANGE_BOUND longs were 2/9 for −$302 and **TRENDING
  setups were the worst bucket** (2/11, −$747) (`counterfactual-sunday-reopen-2026-09-21.md`).
- The daily gate-evidence cron (#852) reads RANGE_BOUND as net negative on 2026
  tape (n=58, −$95).

A faster TRENDING call that simply admits the same trades 10 minutes earlier
would buy more of the losing bucket sooner. The only version worth having is one
that is **selective**: it admits the RANGE_BOUND bars that were actually the
start of a move and keeps rejecting the ones that weren't. That is a testable
claim, and the data to test it is already on the box.

## Data inventory (box, read-only, 2026-09-21 03:30Z)

| Source | Depth | Notes |
|---|---|---|
| `logs/bars_MNQ_*.jsonl` (15m) | 93 days, since 2026-06-05 | what the engine consumed |
| `logs/tf5m/bars_MNQ_*.jsonl` (5m) | **63 days, since 2026-07-01, 13,851 bars** | OHLCV, no Pine label — enough |
| `logs/tf1m/bars_MNQ_*.jsonl` (1m) | 2 days, since 2026-09-18 17:38Z | **too shallow** — 1m is out of scope for this study |
| MNQ NO_TRADE rows with `shadow_candidates`, since 2026-08-01 | RANGE_BOUND 1,114 · CHOPPY 289 · DEAD 272 · TRENDING 767 | the engine's own entry/stop/target geometry |

Same feed, same candidates, same resolver as the two existing counterfactuals —
nothing re-derived, no new detector.

## Hypothesis (falsifiable)

**H1.** Among MNQ 15m decision bars labelled RANGE_BOUND (or CHOPPY) that carried
a candidate, a **fast-regime score computed only from the preceding 5m bars**
separates the candidates into a positive subset and a negative subset, such that
the "fast-TRENDING" subset, taken 1 contract net of costs, has profit factor above
the null baseline (p95 PF 1.94, `project_null_baseline`) **and** the remainder is
no better than the pooled RANGE_BOUND bucket.

**H0.** The fast-regime score does not separate outcomes; the fast-TRENDING
subset's PF is within the null band. → Regime stays 15m/Pine, item closed.

## Fast-regime score (fixed before looking at outcomes)

Computed from the **six 5m bars ending at the 15m decision bar's close** (30 min
of context, all *prior* to the decision — no look-ahead):

1. EMA(9) of 5m closes above EMA(21) (long) / below (short) — 1 point.
2. ≥ 4 of the 6 bars close in the candidate's direction — 1 point.
3. Net move over the 6 bars ≥ 1.0 × the 20-bar 5m ATR — 1 point.
4. Last 5m bar closes beyond the 15m bar's open in the candidate's direction — 1 point.

`fast_regime = TRENDING` iff score ≥ 3 and the direction agrees with the
candidate. Everything else is `NOT_TRENDING`. One definition, no tuning pass;
if it fails it fails.

## Method (identical to the existing counterfactuals)

- Candidates: MNQ NO_TRADE rows, `market_condition ∈ {RANGE_BOUND, CHOPPY}`,
  2026-08-01 → 2026-09-19 (excludes the Sunday-reopen window already studied and
  the post-stall days).
- Resolution against `bars_MNQ_*.jsonl` 15m: honest fill = entry touched within
  8 bars else NOT_FILLED; first touch of stop/target over 48 bars; **stop wins
  ties**; TIMEOUT marks to close. 1 contract, $2/pt.
- Costs: 1 tick slippage each side + $1.48 commission each side
  (`execution/forward_evidence_campaign.py` constants).
- Report per label × fast_regime × direction: n, wins, win %, net $, PF.
- Sample floors: ≥ 60 resolved candidates in the fast-TRENDING subset or the
  result is INSUFFICIENT_DATA, not a finding.
- Read-only. Snapshot the journal and bar files first (`ops/` audit style).

## Decision rule

| Outcome | Action |
|---|---|
| fast-TRENDING PF ≥ 1.94 **and** remainder PF < pooled RANGE_BOUND PF | Draft a `fast_regime` **context gate** (5m-derived, admits RANGE_BOUND only when fast-TRENDING). Own PR, own backtest replay on the parity corpus, post-09-30, own GO. Still paper. |
| fast-TRENDING PF in null band | Close. Regime stays Pine/15m. Note it in the closed-work index. |
| n < 60 | INSUFFICIENT_DATA. Re-run after 2026-10-31 with more 5m tape. |

## Explicitly out of scope

- 1m bars (2 days of data). Revisit when `tf1m/` has ≥ 30 trading days.
- Changing the Pine label, the TRENDING gate, or `REGIME_NOT_FULL`.
- Any live/demo order path. This is a journal-and-bars study.

## Cost to run

One read-only script (~150 lines, same shape as the reopen counterfactual),
~10 minutes of box CPU off-hours. Should be run **Monday after 16:15 ET** or
later, never during RTH on the single-worker bot.
