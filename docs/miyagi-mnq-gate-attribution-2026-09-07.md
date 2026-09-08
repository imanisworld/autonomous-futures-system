# 12HR Miyagi MNQ Gate Attribution

Status: **AUDIT ONLY — no strategy, configuration, permission, risk, or deployment changes.**

## Reproduction boundary

The canonical research artifact contains 8 MNQ candidates from 2024-08-22
through 2026-02-11. Miyagi remains research-only on the current branch:
`strat_12hr_miyagi` is not implemented in `strategy/signal_engine.py` and is
not an enabled runtime concept. Therefore a genuine full-engine journal replay
cannot be produced without inventing strategy logic. The audit reports the
current structural gate funnel and realistic IOC ceiling instead.

Execution assumptions for the IOC ceiling: one MNQ contract, 32-tick IOC
tolerance, 1/2/3 adverse ticks, $1.48 round-trip commission, pessimistic
same-bar resolution.

## Candidate-level attribution

| Date/time UTC | Direction | Stop ticks | R:R | Market state | Risk blockers | Signal blocker |
|---|---|---:|---:|---|---|---|
| 2024-08-22 14:05 | SHORT | 479.5 | 0.335 | RANGE_BOUND | R:R, stop width | Not trending |
| 2024-08-23 14:50 | SHORT | 564.0 | 0.504 | RANGE_BOUND | R:R, stop width | Not trending |
| 2024-09-18 13:30 | SHORT | 139.5 | 0.828 | RANGE_BOUND | R:R, stop width | Not trending |
| 2024-10-11 14:10 | LONG | 429.0 | 0.448 | RANGE_BOUND | R:R, stop width | Not trending |
| 2025-02-27 14:40 | SHORT | 643.5 | 0.720 | TRENDING | R:R, stop width | None |
| 2025-03-21 16:55 | LONG | 805.0 | 0.282 | TRENDING | R:R, stop width | None |
| 2025-05-16 13:35 | SHORT | 393.5 | 0.550 | RANGE_BOUND | R:R, stop width | Not trending |
| 2026-02-11 15:00 | SHORT | 754.5 | 0.363 | RANGE_BOUND | R:R, stop width | Not trending |

Structural attribution:

- 8/8 fail `rr_below_minimum`.
- 8/8 fail `stop_too_wide` against the 120-tick MNQ cap.
- 6/8 fail `MARKET_CONDITION_NOT_TRENDING`.
- 0/8 survive current structural risk gates.
- All candidates are confluence A/A+; confluence is not the failure point.

## Realistic IOC ceiling

This is the all-candidate diagnostic ceiling, not a production-survivor result,
because the structural survivor population is empty.

| Adverse slippage | Fills | No-fills | Wins / losses | Net | PF | Max DD | H1 / H2 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 5 | 3 | 4 / 1 | +$266.60 | 2.357 | $196.48 | -$58.44 / +$325.04 |
| 2 ticks | 5 | 3 | 4 / 1 | +$263.60 | 2.335 | $197.48 | -$60.44 / +$324.04 |
| 3 ticks | 5 | 3 | 4 / 1 | +$260.60 | 2.313 | $198.48 | -$62.44 / +$323.04 |

The documented plan-price bracket was +$525.91, PF 2.849, with H1 negative
(-$56.42) and H2 positive (+$582.33). The realistic IOC ceiling stays positive
but also fails chronological-half stability and is concentrated in the later
half. It does not rescue the zero-candidate production funnel.

## Verdict

**First failure point: current structural risk gates.** Miyagi MNQ has useful
directional and bracket evidence in this thin population, but every candidate
requires both substandard R:R and a stop wider than the account's 120-tick cap.
No full-engine executable result exists because the strategy is not wired into
the current engine; no runtime implementation was added for this audit.
