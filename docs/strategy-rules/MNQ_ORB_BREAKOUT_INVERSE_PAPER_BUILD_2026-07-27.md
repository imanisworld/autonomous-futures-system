# MNQ ORB Breakout inverse — paper-build contract

Status: **PROMISING BUT UNPROVEN — APPROVED FOR PAPER BUILD**

Research preregistration:
`eda2c3344304fe2f9daf74da6505acdf1256fad4`.

Research result:

- fixed population: 111 trades, +$745.72, PF 2.392;
- chronological path: 108 trades, +$664.66, PF 2.251.

This implementation does not tune or reinterpret that candidate.

## Runtime contract

Activation is paper-only:

`MNQ_ORB_BREAKOUT_INVERSE_MODE=paper_sim`

Valid values are `observe_only` and `paper_sim`. There is no demo or live
mode. `paper_sim` constructs the isolated `PaperBroker` directly and never
constructs an external broker.

The legacy `MNQ_ORB_BREAKOUT_PROOF_MODE` market-entry/runner lane must remain
`observe_only` while the inverse lane is active. Configuration validation
fails closed if both are active.

## Parity sequence

1. The normal completed 15-minute MNQ state enters `DecisionEngine`.
2. The existing `orb_breakout` detector, ranking, permissions, and shared
   gates produce the source signal unchanged.
3. Confluence and `RiskEngine` evaluate the source signal unchanged.
4. Dynamic sizing is recorded, then submitted quantity is forced to one.
5. Immediately before PaperBroker execution:
   - LONG becomes SHORT and SHORT becomes LONG;
   - planned entry is unchanged;
   - absolute stop and target distances are mirrored around planned entry.
6. Entry uses `ioc_limit`, the completed decision-bar close as current market,
   an eight-tick MNQ cap, and the frozen one adverse tick.
7. Resolution begins on later bars through the existing persistent journal
   position path, with static exits, no runner, no breakeven transform, and
   pessimistic stop-first handling when one bar touches both stop and target.

The authoritative confirmed `TRADE` journal row stores the submitted inverse
direction, actual fill entry, mirrored stop/target, and one contract. Its
`mnq_orb_breakout_inverse_audit` preserves both source and submitted geometry
plus recommended and submitted sizing.

## Required release pins

Any demo release activating this lane must include:

- `EXPECTED_PROOF_MNQ_ORB_BREAKOUT_INVERSE_MODE=paper_sim`;
- `EXPECTED_PROOF_MNQ_ORB_BREAKOUT_PROOF_MODE=observe_only`;
- the ordinary proof-critical pins for paper mode, broker, slippage,
  pessimistic ambiguity, exit mode, and timeframe.

The release manifest and live-box guard classify the activation variable as
proof-critical. A missing or mismatched pin fails verification.

## Non-authorization

This build does not authorize tuning, external broker routing, live trading,
or changing the frozen signal, session, sizing, IOC, bracket, cost, slippage,
or breaker contract.
