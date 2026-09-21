# Counterfactual — Sunday-reopen window, 8 Sundays (2026-08-02 → 2026-09-20)

**Evidence only. No runtime, rule, or config change. Operator-requested after the
2026-09-20 MNQ reopen breakout went untraded (see
`counterfactual-gates-off-2026-09-21.md`).**

## Question

The 2026-09-20 miss looked like a labelling problem: at a fresh Sunday reopen there
is no trend history yet, so the engine labels the market RANGE_BOUND and the
`require_trending_condition` gate blocks the breakout long. Is that a *pattern* —
would letting non-TRENDING MNQ setups through in the Sunday-reopen window have made
money — or was tonight a one-off?

## Simple answer

**One-off. The gate was right on 6 of 8 Sundays.** Every non-TRENDING MNQ setup
the engine logged in the reopen window, taken 1 contract net of costs, lost −$445
over eight Sundays — *including* tonight's +$394. The RANGE_BOUND longs specifically
(tonight's type) went 2 for 9, −$302. And the setups the gate *allows* (TRENDING)
did worse still: 2 for 11, −$747. The Sunday reopen is a bad window for this
system regardless of label. Nothing here supports loosening the gate; if anything
it supports **not trading the first three hours of the Sunday reopen at all** —
a candidate rule for the post-2026-09-30 review, not proposed here.

## Method

- **Window:** Sunday 22:00Z → Monday 01:00Z (first 3h of the CME Globex reopen),
  every Sunday from 2026-08-02 to 2026-09-20 (8 Sundays).
- **Candidates:** the live engine's own `shadow_candidates` on MNQ decision rows in
  `/root/afs-shared/logs/journal_YYYY-MM-DD.jsonl` — the geometry (entry / stop /
  target / direction) the engine attached to each NO_TRADE decision. 68 candidates.
  No new detection logic; nothing re-derived from OHLCV. (The
  `cross_instrument_observation_v1` campaign only started 2026-09-16 and therefore
  contains exactly one Sunday — it cannot answer this on its own.)
- **Resolution:** against the box's own `bars_MNQ_*.jsonl` (15m, same feed the
  engine consumed). Honest fill = entry price touched within the next 8 bars,
  otherwise NOT_FILLED. Then first-touch of stop or target over a 48-bar (12h)
  horizon; **stop wins ties** on a bar that touches both; open at horizon end marks
  to close (TIMEOUT). 1 contract, MNQ $2/pt.
- **Costs:** 1 tick (0.25 pt) slippage each side + $1.48 commission each side —
  the campaign's proven constants (`execution/forward_evidence_campaign.py`).
- **Excluded:** 18 NOT_FILLED candidates (price never returned to the entry). 50
  resolved: 11 WIN, 37 LOSS, 2 TIMEOUT.
- Snapshot 2026-09-21 02:45Z, read-only. Box release `5c91602`.

## Result — MNQ, Sunday-reopen window, 1 contract net of costs

| Label at decision time | Dir | n | Wins | Win % | Net $ |
|---|---|---|---|---|---|
| RANGE_BOUND | LONG | 9 | 2 | 22% | **−302** |
| RANGE_BOUND | SHORT | 3 | 0 | 0% | −276 |
| CHOPPY | LONG | 8 | 2 | 25% | −177 |
| CHOPPY | SHORT | 9 | 3 | 33% | +203 |
| DEAD | LONG | 7 | 3 | 43% | +172 |
| DEAD | SHORT | 3 | 1 | 33% | −64 |
| TRENDING | LONG | 7 | 2 | 29% | −280 |
| TRENDING | SHORT | 4 | 0 | 0% | −467 |

Rolled up:

| Bucket | n | Wins | Win % | Net $ |
|---|---|---|---|---|
| Gate-blocked (not TRENDING) | 39 | 11 | 28% | **−445** |
| Gate-allowed (TRENDING; then blocked by regime gate) | 11 | 2 | 18% | **−747** |
| All reopen-window setups | 50 | 13 | 26% | −1,192 |

By Sunday, gate-blocked setups only:

| Sunday | n | Wins | Net $ |
|---|---|---|---|
| 08-02 | 4 | 0 | −446 |
| 08-09/10 | 11 | 2 | −516 |
| 08-16/17 | 7 | 1 | −139 |
| 08-23/24 | 5 | 1 | −177 |
| 08-30/31 | 0 | — | — (only TRENDING setups that night) |
| 09-06/07 | 3 | 1 | −63 |
| 09-13/14 | 6 | 3 | +504 |
| 09-20 | 3 | 3 | **+394** |

Two winning Sundays out of eight; the other six lost. Tonight is the best Sunday
in the sample and still does not pull the bucket positive.

## What this does and does not say

- It **does** say: on this system's own candidates and its own bars, the
  RANGE_BOUND block at the Sunday reopen has been net-correct over two months.
  Tonight's miss is real and painful and is also the exception.
- It **does** say: TRENDING-labelled setups at the reopen were the worst bucket.
  They were all also blocked by the regime gate (`REGIME_NOT_FULL`), so the
  system never took them — but the market-condition gate alone would have let
  them through.
- It **does not** say the label is well-calibrated at the reopen. Both TRENDING
  and RANGE_BOUND lost; the honest reading is "the first 3h after a Sunday reopen
  are hostile to every setup family here", not "the label knows".
- n is small (50 resolved, 8 Sundays). This closes the *loosen-the-gate* question
  for now; it does not by itself justify a new *time-of-week* rule.
- Journal candidates only exist on bars where the engine wrote a NO_TRADE row with
  shadow geometry; bars lost to the 2026-09-21 stall (00:00–00:45Z, 01:30Z) have
  no candidates and are simply absent.

## Follow-ups (not actioned)

1. Post-09-30: evaluate a "no new entries Sun 22:00Z–Mon 01:00Z" rule on the full
   tape, all conditions, before proposing it.
2. Add the Sunday-reopen split as a line in the daily gate-condition Discord
   report once the cross-instrument campaign has ≥4 Sundays.

## Reproduction

Inputs are on the box (`journal_2026-08-*.jsonl`, `journal_2026-09-*.jsonl`,
`bars_MNQ_2026-0[89]-*.jsonl`). Resolver (~60 lines, stdlib only):

```python
# collect: MNQ rows with shadow_candidates whose ts is Sun>=22:00Z or Mon<01:00Z
# resolve(c): first bar after c.ts; fill if low<=entry<=high within 8 bars;
#   then per bar: LONG hit_stop=low<=stop, hit_target=high>=target (SHORT mirrored);
#   stop checked before target; 48-bar horizon; TIMEOUT marks to close.
# pnl: WIN=(reward-0.5)*2-2.96, LOSS=-(risk+0.5)*2-2.96, TIMEOUT=(move-0.5)*2-2.96
```

Full script and the resolved rows (`sun_res.json`) are in the 2026-09-21 session
scratchpad; re-running the snippet above against the same files reproduces the
tables exactly.
