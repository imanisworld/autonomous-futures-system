# Prereg — options non-Strat underlying geometry study (`ong-v0.1`)

**Status: RESEARCH ONLY. Frozen before the first historical geometry run.**

This study takes the merged `ns-v0.1` structural event definitions and asks a narrower
question than option P&L:

> after a mechanically-defined non-Strat event is knowable at a completed 5-minute
> bar, does the underlying continue far enough to support a simple, causal bracket?

It does **not** estimate option-contract expectancy. There is no historical option
quote, IV, delta, spread, open-interest, theta, assignment, or exercise simulation in
this lane.

Nothing here authorizes #875's geometry registry, a forward paper candidate, Webull
submission, scanner alerts, or live execution.

## Frozen corpus

- Provider: consolidated SIP 5-minute equity bars through the existing Alpaca
  read-only historical bars client.
- Universe: the existing frozen 150-name coverage watchlist
  `research/coverage/options_watchlist_150.csv`.
- Historical study range: **2026-04-01 through 2026-09-18**, inclusive.
- Lookback transport begins 7 calendar days before the start solely to establish a
  prior complete RTH session.
- RTH session authority: `nyse_session_for()`; incomplete symbol-sessions are skipped.
- Event definitions: **exact merged `alert_ranker.non_strat_coverage ns-v0.1`**.
- One row per event episode: first event only.
- `SPY`/`QQQ` are fetched so the existing observer can record market alignment, but
  alignment is telemetry and is not a primary gate.
- Chronological split: H1 = 2026-04-01 through 2026-06-30; H2 = 2026-07-01 through
  2026-09-18.

The current 150-name universe introduces survivorship/current-universe bias. Results
must be labelled as setup screening, not a market-wide historical validation.

## Causal entry

The event does not exist until the trigger 5m bar is complete.

Entry decision price = the **next RTH 5m bar open**. The study never fills at the event
bar close or at the structural level after seeing the future.

Underlying slippage stresses:

| label | adverse entry | adverse stop/EOD exit | target |
|---|---:|---:|---:|
| base | $0.01 | $0.01 | resting limit, no slip |
| stress | $0.03 | $0.03 | resting limit, no slip |

These are not brokerage-cost claims. They are small adverse-price stresses on the
underlying geometry only.

## Frozen geometry cells

Both cells use a 2.0R target from the **unslipped next-bar-open decision price**.
Entry slippage therefore reduces achieved R rather than being hidden by target
re-anchoring.

### O1 — event-level invalidation
For LONG:
- stop = event `level_value - $0.01`

For SHORT:
- stop = event `level_value + $0.01`

Target = decision price ± 2.0 × planned risk.

### O2 — trigger-bar invalidation
For LONG:
- stop = trigger-bar low − $0.01

For SHORT:
- stop = trigger-bar high + $0.01

Target = decision price ± 2.0 × planned risk.

No ATR fitting, no ticker-specific stop optimization, and no family-specific target
tuning are allowed in `ong-v0.1`.

## Resolution

- entry bar is included because entry occurs at its open;
- if stop and target are both inside one 5m bar, **stop wins**;
- unresolved positions flatten at that session's final RTH close with the cell's
  adverse exit stress;
- no overnight carry;
- realized result is reported in R using the **planned decision-price risk** as the
  denominator, so entry/exit stress remains visible.

## Families

Every mechanically emitted `ns-v0.1` family is measured. A family is never dropped
because its result is weak.

Generic support-hold / resistance-rejection / arbitrary pullback-reclaim remain absent
because `ns-v0.1` deliberately has no frozen planned-level source for them.

## Required outputs

For each family × geometry × stress:

- episodes and resolved count;
- symbols and sessions represented;
- invalid/no-next-bar counts;
- mean R, median R, PF in R-space;
- target / stop / EOD counts;
- max drawdown in R;
- H1 and H2 n / mean R / PF;
- top positive ticker share of total positive ticker R;
- median / p90 decision-price distance from the event level;
- market-aligned / unaligned / unknown counts.

The aligned subset may be reported as secondary telemetry only. It cannot rescue a
family that fails the all-events primary gate.

## Pre-registered screening gate

A family/geometry pair is **PROMISING BUT UNPROVEN** only if:

1. >= 100 resolved episodes in H1 and >= 100 in H2;
2. H1 and H2 both have mean R > 0 and PF > 1.10 at **stress**;
3. total net R is positive at both base and stress;
4. top positive ticker contributes < 30% of total positive ticker R;
5. no causality, session-completeness, or event-identity defect is found.

Anything failing the gate is `WAIT` or `REJECT` for further promotion.

Even a passing family does **not** populate #875's geometry registry. Before that can
happen the forward observer must have at least five completed sessions, the exact
forward geometry must be separately preregistered, and actual option contract/risk
evidence must exist.
