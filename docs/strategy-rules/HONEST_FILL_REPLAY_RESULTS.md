# Honest Fill Replay Results

**Research date:** 2026-07-23  
**Instrument:** MNQ, one contract  
**Scope:** reconciled 4HR Re-Trigger, 12HR Miyagi, and 60M 3-2-2 signals only

## Shared execution contract

- One-shot Limit-IOC; no retry or chase
- MNQ IOC adverse cap: 32 ticks (8 points) from the trigger
- Completed five-minute crossing/touch bar close is the market proxy at order arrival
- Two ticks adverse slippage on entry and exit in the base case
- $1.24 round-trip commission per fill
- Bracket evaluation begins on the next five-minute bar after a non-gap decision
- Stop wins when stop and target are both touched in the same eligible bar
- A fixed stop that is non-protective after the actual IOC fill fails closed
- Unresolved positions exit at the 15:55 ET bar close with adverse slippage
- Walk-forward halves split at the exact calendar midpoint of the full signal range

The 15:55 exit is a replay assumption because the rules do not specify overnight
carry. The research engines are not wired into live execution.

## Base case

| Strategy | Signals | Fills | W/L | Net P&L | Exp/signal | Exp/fill | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 4HR Re-Trigger | 94 | 41 | 23/18 | $1,960.16 | $20.85 | $47.81 | 2.33 | $411.18 |
| 12HR Miyagi | 13 | 3 | 2/1 | $59.28 | $4.56 | $19.76 | 1.30 | $197.24 |
| 60M 3-2-2 | 32 | 20 | 17/3 | $1,537.70 | $48.05 | $76.88 | 8.00 | $167.24 |

## Stability

| Strategy | H1 net | H2 net | LONG net | SHORT net | Net at 1/2/3/4 ticks |
|---|---:|---:|---:|---:|---|
| 4HR Re-Trigger | $230.46 | $1,729.70 | $890.18 | $1,069.98 | $2,001.16 / $1,960.16 / $1,919.16 / $1,878.16 |
| 12HR Miyagi | -$115.48 | $174.76 | $0.00 (0 fills) | $59.28 | $62.28 / $59.28 / $56.28 / $53.28 |
| 60M 3-2-2 | $1,086.88 | $450.82 | $1,108.36 | $429.34 | $1,557.70 / $1,537.70 / $1,517.70 / $1,498.20 |

## Decision

- **4HR Re-Trigger — PROMISING BUT UNPROVEN.** Positive in both halves and
  directions, but H2 accounts for most of the profit and only 41 signals filled.
- **12HR Miyagi — WAIT.** Three fills cannot support an edge claim; H1 is negative
  and LONG has no filled observations.
- **60M 3-2-2 — PROMISING BUT UNPROVEN.** Strongest replay of the three and
  positive in every requested split, but only 20 signals filled and no historical
  gap-open entry was observed.

These results retire the prior performance figures built from superseded or
incomplete rule sets. They do not change configuration, execution, or deployment.
