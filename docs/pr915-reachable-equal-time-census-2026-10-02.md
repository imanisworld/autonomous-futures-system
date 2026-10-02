# #915 reachable equal-time census — 2026-10-02

Capacity structure only. No account-admission overlay. No P&L, profit factor,
drawdown, or win/loss.

The raw fillable-event census is 280 pairs whose `exit_ts` equals another
event's `eligible_fill_ts`. Frozen `replay_portfolio` then keeps one open
position and at most three fills per observation day. A prior position frees
that slot only when its exit is strictly earlier than the candidate fill.

This census keeps a pair only when all of the following are true:

- the exiting event was accepted by that capacity replay
- its `exit_ts` equals the candidate `eligible_fill_ts`
- the candidate's observation day is still under the three-fill cap
- no earlier candidate at that same timestamp would already have taken the
  slot if the exiting position were free

The capacity replay accepts 488 fills, the published #915 combined-replay
count. Of the busy skips at an exact exit timestamp, 16 would still miss
because the day cap is full, and 2 sit behind an earlier candidate at the
same timestamp. Those 18 are not reachable.

## Result

Reachable count: **47**.

| Exiting family | Candidate family | Count |
|---|---|---|
| `ASIA_D_EMA` | `ASIA_D_EMA` | 45 |
| `DAILY_22_COMPLETED_CLOSE` | `ASIA_D_EMA` | 1 |
| `ASIA_D_EMA` | `DAILY_22_COMPLETED_CLOSE` | 1 |

The 47 rows, with accepted source id, collision timestamp, exiting family,
candidate family, and candidate source id, are in
`research/artifacts/pr915-reachable-equal-time-collisions-5a9f14b.json`.

The count is greater than 0. Equal timestamps were not ordered. The account
draft stays `SAME_TIMESTAMP_ORDER_BLOCKED` for these 47 cases. The other 233
raw pairs do not need an ordering rule.
