# Prereg — futures RTH ORB_BREAKOUT_LONG geometry + fill study (`fng-v0.1`)

**Status: RESEARCH ONLY. Frozen before the first geometry run.**

Source population is the merged `fns-v0.1` observer from #881. That observer found
`ORB_BREAKOUT_LONG` was the only structural family above the post-hoc all-bar drift
control in all four MNQ/MES × calendar-half cells. This study asks whether that
directional behaviour survives causal fills, explicit geometry, costs, and pessimistic
bar resolution.

Nothing in this file authorizes a shadow lane, paper lane, Webull mirror, Tradovate
route, risk-rule change, or strategy promotion.

## Population identity

- Instruments: **MNQ and MES**, reported separately.
- Data: the same Polygon 5m RTH corpus and session exclusions as `fns-v0.1`.
- Signal: first event of each `ORB_BREAKOUT_LONG` episode only.
- ORB definition: first six completed RTH 5m bars, 09:30–10:00 America/New_York.
- Trigger: first completed 5m bar with prior close <= frozen RTH ORB high and current
  close > frozen RTH ORB high.
- The legacy payload `orb_high` is not substituted. #881 proved it agrees with this
  RTH ORB only about one third of sessions.
- Calendar split remains the frozen #881 split: H1 < 2025-09-01, H2 >= 2025-09-01.
- Roll/incomplete-session exclusions remain identical to #881.

## Causal entry clock

The signal does not exist until the trigger bar is complete. Therefore the primary
arrival price is the **next 5m bar open**. The study never fills at the earlier ORB
boundary or at the trigger bar close after the fact.

Two fill-cost stresses are fixed:

| label | entry/market slippage | stop slippage | target slippage |
|---|---:|---:|---:|
| base | 1 tick adverse | 1 tick adverse | 0 (resting limit) |
| stress | 2 ticks adverse | 2 ticks adverse | 0 (resting limit) |

Commission: **$1.24 round turn per contract**, matching the existing futures research
cost convention. Quantity: exactly **1 contract**.

If there is no next RTH bar, the candidate is `NO_DATA`.

## Frozen geometry cells

All cells are long-only and use the actual next-bar-open decision price as the entry
anchor. Targets are defined from the unslipped decision price; adverse entry slippage
therefore reduces achieved R:R rather than being hidden by target re-anchoring.

### G1 — canonical-offset stop
- stop = RTH ORB high − configured ORB stop offset
- MNQ offset = 48 ticks
- MES offset = 16 ticks
- target = decision entry + 2.2 × (decision entry − stop)

Purpose: test the repository's existing ORB offset/2.2R geometry on the **new frozen
RTH ORB population**, without fantasy fills at the ORB boundary.

### G2 — trigger-bar structural stop
- stop = trigger-bar low − 1 tick
- target = decision entry + 2.0 × (decision entry − stop)

Purpose: local structure, fixed before outcomes.

### G3 — ORB-midpoint structural stop
- stop = midpoint(ORB high, ORB low) − 1 tick
- target = decision entry + 2.0 × (decision entry − stop)

Purpose: broader opening-range structure, fixed before outcomes.

No other stop/target cell may be added after results are seen and described as
pre-registered.

## Risk feasibility

The study reports, but does not silently discard, stop widths against current global
caps:

- MNQ: 120 ticks
- MES: 60 ticks

A separate **risk-feasible** view excludes cells whose planned stop width exceeds the
instrument cap. The raw geometry view remains visible so the cap cannot manufacture a
better result.

No position sizing beyond 1 contract. No daily-loss or three-trades/day filtering is
applied to the primary single-trade research rows; a sequential account-feasibility
view may be reported separately and must be labelled secondary.

## Resolution

Use the real `PaperBroker` formulas with:

- `pessimistic_both_hit=True`;
- one contract;
- target as resting limit;
- adverse slippage on entry and stop;
- entry bar included in stop/target resolution because the trade enters at that bar's
  open;
- RTH-only; if neither stop nor target resolves by 16:00 ET, flatten at the final RTH
  close with one additional adverse exit slippage tick count equal to the cell's
  slippage stress.

The research runner must hard-disable the Webull futures mirror in-process before
constructing `PaperBroker`.

## Required outputs

For every instrument × geometry × slippage cell:

- candidates, fills, no-data, bracket-invalid;
- stop ticks median / p90 / max and count over current cap;
- resolved count;
- W/L/BE;
- net P&L after commission;
- expectancy;
- profit factor;
- max drawdown;
- H1 and H2: n, net, expectancy, PF;
- monthly concentration: largest positive month / total positive-month profit;
- entry-detachment from ORB high in ticks: median / p90.

Also report the identity crosswalk:
- share of frozen RTH ORB event sessions where payload `orb_high` equals frozen ORB;
- share of events whose trigger bar would also satisfy the repository's legacy
  payload-ORB boundary on that same bar when the payload value exists.

## Pre-registered pass rule

A geometry is **PROMISING BUT UNPROVEN** only if all of the following hold:

1. both MNQ and MES have >= 100 resolved trades in each half;
2. net P&L > 0 and PF > 1.10 in both halves on both instruments at **base** slippage;
3. net P&L remains > 0 in both halves on both instruments at **stress** slippage;
4. max drawdown is reported and no result depends on excluding stop-cap failures;
5. top positive month contributes < 60% of total positive-month profit;
6. no material identity/causality defect is found.

Failing any item means **WAIT / REJECT for promotion**. A strong single-instrument result
may be recorded for research but does not pass this cross-instrument gate.

This study does not overturn prior legacy ORB findings unless it proves the population
identity is materially different and the new population survives these causal mechanics.
