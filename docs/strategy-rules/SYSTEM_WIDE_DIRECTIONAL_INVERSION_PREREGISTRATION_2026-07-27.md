# System-wide directional inversion audit — preregistration

Date frozen: 2026-07-27

This is a research-only counterfactual. It must not contact the deployed box,
change runtime configuration, change sizing, or alter the frozen #359 forward
epoch.

## Frozen evidence

- Lane B population: the 490 trades frozen by
  `LANE_B_MNQ_CLOSE_MOMENTUM_PREREGISTRATION_2026-07-27.md`.
- System code posture: commit `74b1407`, whose executable strategy, replay,
  execution, risk, and configuration files are unchanged from the #358
  evidence-generation commit `b86eec690b7917f067d2daabcc9477584da451f0`.
- Corrected corpus: `data/replay_corpus_v1_market_condition_fixed`, 626 files,
  SHA-256 tree digest
  `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4`.
- Range: 2025-07-24 through 2026-07-23.
- Instruments: MES and MNQ.
- Commission: $1.48 per resolved round trip.
- Fill posture: one adverse tick, pessimistic stop-before-target when both are
  touched in one bar, static exits, 20% maximum-drawdown breaker.
- Supported entry models: canonical IOC limit, aggressive market,
  8-tick marketable IOC limit, and causal one-next-bar stop-market. StopLimit
  remains excluded because the frozen PaperBroker does not model it.

## Direction-only transform

For every technically defined approved order:

1. Preserve candidate time, strategy, instrument, session, planned entry,
   contracts, setup qualification, and execution model.
2. Change `LONG` to `SHORT` and `SHORT` to `LONG`.
3. Let `S = abs(entry - original_stop)` and
   `T = abs(original_target - entry)`.
4. For an inverse LONG, use `stop = entry - S` and
   `target = entry + T`.
5. For an inverse SHORT, use `stop = entry + S` and
   `target = entry - T`.
6. Preserve all other order metadata and risk distances. Do not alter
   thresholds, sessions, permissions, candidate ranking, or max-trades/day.
7. Re-run the applicable frozen fill model. An opposite IOC or stop entry is
   not assumed to fill. Subsequent bars resolve the mirrored bracket causally.

## Two non-interchangeable system analyses

### A. Trade-level inversion

Re-run each original #358 arm solely to capture its exact approved attempts
and order geometry. Independently mirror every captured attempt and replay it
from its original decision bar through the actual later bars. Each attempt is
resolved without letting its counterfactual P&L change later candidate
availability. This is the pure directional diagnostic.

The attempt count must equal the published #358 attempt count for each mode.
Original rerun summaries must reconcile to the published #358 evidence before
the inverse is accepted.

### B. System-path inversion

Run the frozen engine chronologically from the beginning while applying the
direction-only transform immediately before PaperBroker execution. Inverted
fills and outcomes update the rolling account balance and risk state normally.
The maximum-drawdown breaker and all ordinary position/max-trade gates may
therefore change later candidate availability. Candidate detectors, ranking,
permissions, and setup rules remain frozen.

## Lane B transform

Use the exact 490 frozen rows. Preserve entry and exit timestamps and prices
and flip only exposure. For raw prices, inverse gross must equal the negative
of original gross, subject only to numerical rounding. Adverse slippage and
commission are then charged again to the inverse, so inverse net is not the
negative of original net.

Report the original requested slices and 1/2/3/4-tick adverse-slippage
sensitivity without adding filters.

## Acceptance and classification

An inverse is not called an edge merely because aggregate P&L becomes
positive. A credible inverted edge must be positive after costs, positive in
both H1 and H2, positive across multiple chronological periods, not collapse
by direction, not depend on a handful of giant winners, and have sufficient
sample size.

Every strategy receives exactly one classification:

1. ORIGINAL EDGE
2. INVERTED EDGE
3. NEITHER HAS EDGE
4. AMBIGUOUS
5. DIRECTION-MAPPING BUG FOUND

The semantics audit independently traces bullish/bearish direction through
setup construction, 2U/2D conversion, reclaim/rejection concepts, order-side
math, and replay/runtime execution. A strategy-level loss is not evidence of a
mapping bug.

## Prohibited changes

No deployment, box access, #359 edit, runtime/config edit, sizing change,
parameter search, filter search, detector modification, strategy permission
change, or production-strategy conversion is authorized by this audit.
