# Options 1-2-2 prospective causal collector — preregistration

Date: 2026-09-18

## Status

**AUTHORIZED FOR PHASE-1 OBSERVATION ONLY.**

This preregistration freezes the first prospective causal 1-2-2 collection epoch before any natural RTH row is collected by the new release.

It does not activate 1-2-2 in the running V1 scanner, reserve risk, emit a trade alert, create an order ticket, submit to a broker, enable DEMO/live execution, or define a strategy stop/target.

## Why this lane exists

The frozen three-session first-sight study classified 1-2-2 as `PERSISTING POSSIBLE SIGNAL` on 38 prospective episodes with +20.9 percentage points ex-opening first-sight 1-reference-range excess versus its matched baseline.

The separately preregistered 186-arm source audit then showed that IEX can support a distinct miss-allowed causal research clock with delayed SIP reconciliation:

- SIP authoritative reversals: 73;
- IEX provisional reversals: 71;
- confirmed same reversal: 69/71 = 97.18%;
- SIP reversal recall: 69/73 = 94.52%;
- false provisional reversals: 2/71 = 2.82%;
- missed SIP reversals: 4/73 = 5.48%;
- blocked rows: 0;
- negative-latency inconsistencies: 0.

IEX is **not SIP-equivalent**. Confirmed, missed, and rejected rows remain separate provenance.

## Frozen causal structure

A 1-2-2 watch is armed only when a completed directional `2` immediately follows an inside `1`.

- the directional 2 high/low are frozen before the next 30-minute watch begins;
- first strict break opposite the directional 2 = provisional 1-2-2 reversal;
- first strict break in the same direction = cancellation / continuation-first;
- equality does not count;
- simultaneous first crossings or unknown trade semantics fail closed;
- no later SIP information may create an IEX candidate retroactively.

The exact IEX price-forming trade timestamp is the prospective decision clock.

## Source policy

**Arm / chart source:** existing Public regular-session chart path.

**Provisional trigger source:** Alpaca IEX trades using the same strict-through and documented price-forming-trade semantics as the completed 186-arm audit.

**Authoritative delayed reconciliation:** Alpaca consolidated SIP trades after the entitlement delay has elapsed.

- IEX decision timestamps are never rewritten to SIP timestamps.
- rejected IEX provisionals remain recorded.
- SIP reversals missed by IEX remain explicit misses.
- source cohorts are never mixed with exact-SIP 212R evidence.

## Frozen collection policy — epoch `122-IEX-E1`

### Cadence

**Collector cadence: 60 seconds during NYSE RTH.**

Reason: the collector is a dedicated evidence observer, not the five-minute V1 scanner. A local read-only benchmark on the current 20-symbol Public DAY+WEEK source completed the full serial source pass in 8.971 seconds after hours (median ticker pair 0.415s, max 0.858s). That is mechanics evidence only, not an RTH latency guarantee.

Operational fail rule: the release must record actual cycle duration. If an RTH cycle exceeds 60 seconds, overlaps, or shows provider/rate-limit failure, the row is not silently treated as on-cadence evidence. Do not change cadence inside `122-IEX-E1`; a change requires a new preregistered epoch.

### Capture-lag eligibility

**Maximum exact-IEX-trigger to completed production-selector evidence lag: 120 seconds.**

The gate is checked both before selector capture and again at completed capture time. Rows over 120 seconds remain provenance but are `LATE / DATA_BLOCKED` for causal option evidence.

This threshold is intentionally about **IEX trigger -> selector evidence**, not IEX-vs-SIP latency. The long IEX-vs-SIP tail remains a separate source-quality measurement and is not erased by this gate.

No threshold tuning is allowed after seeing `122-IEX-E1` outcomes. Any future threshold change creates a new cohort.

### Delayed SIP reconciliation

Attempt reconciliation only after the watch window is at least **16 minutes old**. This is a one-minute safety margin beyond the documented/current recent-SIP entitlement restriction observed in the 212R lane.

A reconciliation attempt that is still entitlement-blocked remains pending; it is never synthesized.

## Option-selector evidence

For a timely, genuinely pre-armed IEX reversal, capture the existing production-authoritative `OPTIONS_PAPER_V1` evidence path unchanged:

- Public underlying quote and timestamp;
- available expirations;
- production-chosen expiration;
- full relevant option-chain inputs including bid/ask timestamps, volume, OI, Delta and IV;
- production selector input/source hash;
- actual production selection;
- retained-input production replay;
- `production_replay_parity`.

Missing/future/stale quotes, missing critical contract fields, replay mismatch, no valid selection, or capture after 120 seconds fail closed.

No ACTIVE risk reservation is performed because this is not a trade lane.

## Strategy geometry remains unresolved

The directional 2 opposite boundary is retained as **structural provenance only**. It is not relabeled as a proven options stop.

For this epoch:

- strategy stop: `UNRESOLVED`;
- strategy target: `UNRESOLVED`;
- runner policy: `UNRESOLVED`;
- futures fixed-2R convention: **not imported**.

Therefore this collector cannot produce strategy expectancy by itself.

## Frozen causal diagnostic endpoint

To prevent post-hoc outcome selection while strategy geometry remains unresolved, the first causal structural follow-up uses a **reference-range diagnostic**, not a strategy win/loss rule.

Reference range = frozen directional-2 high minus low.

From the exact IEX reversal timestamp, later analysis will report without tuning:

1. maximum favorable excursion through RTH close in reference-range units;
2. maximum adverse excursion through RTH close in reference-range units;
3. whether favorable excursion reaches +0.5, +1.0, +1.5, and +2.0 reference ranges;
4. fixed-horizon underlying returns at 5m, 15m, 30m, and RTH close;
5. fixed-horizon selected-option BID marks / returns when causal quote evidence exists.

The **+1.0 reference-range reach rate** is the primary structural continuity statistic because it preserves the scale of the earlier first-sight hypothesis while explicitly avoiding a claim that the opposite boundary is the strategy stop.

No strategy qualification threshold is created by this diagnostic. A later stop/target policy must be separately proven and preregistered before expectancy is calculated.

## Isolation requirements

The release must:

- write only to a dedicated append-only 1-2-2 evidence journal and raw-source directories;
- never write `options_scanner.sqlite`;
- import no broker/order submission path;
- reserve no risk and create no ACTIVE position;
- send no trade alert;
- expose no hidden order preparation;
- preserve every ARMED, provisional, cancellation, no-break, blocked, and reconciliation state needed to audit denominator completeness.

A static/import review and focused test must prove those constraints before deployment.

## First acceptance proof

The first acceptable natural RTH proof on the exact release is:

`ARMED before break -> IEX first-break reversal -> selector capture <=120s -> production replay parity -> delayed SIP reconciliation`

A continuation-first, no-break, late capture, rejected provisional, or IEX miss is still valid evidence about the source/cohort, but it is not a usable causal option-entry row.

## Promotion boundary

Even a successful collection epoch does not authorize trading. Before 1-2-2 can move beyond research, it still needs sufficient untouched forward rows, executable option marks, after-cost analysis, concentration/drawdown, strategy geometry, and final replay/forward parity.

**No proof, no trade.**
