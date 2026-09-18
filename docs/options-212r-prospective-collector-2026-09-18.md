# 212R prospective trigger / option-evidence collector — 2026-09-18

## Status

**BUILT OFFLINE / OBSERVATION ONLY / NOT DEPLOYED / CURRENT REAL-TIME SIP ENTITLEMENT BLOCKED.**

This is the missing Phase-1 bridge between the proven 212R trigger-time model and the already-built `OPTIONS_PAPER_V1` prospective selector-evidence path.

It does not alert a trade, reserve ACTIVE risk, build an order ticket, call a broker, activate DEMO, or change the running options scanner.

## Source boundary

Collector v0.2 uses two read-only market-data sources with separate roles:

- **Public** supplies prospective chart structure, current underlying/option-chain evidence, and the production-selector inputs;
- **Alpaca consolidated SIP trades** resolve the exact strict-through underlying crossing timestamp inside the already-proven Public 5-minute trigger bucket.

The Public structure source remains separately labeled and is not relabeled as SIP.

Current deployment blocker discovered in isolated RTH testing: the configured Alpaca account rejects consolidated SIP trade queries inside the latest 15 minutes with HTTP 403 (`subscription does not permit querying recent SIP data`). Same-day SIP windows older than that restriction remain usable for historical/reconciliation work. Collector v0.3 therefore cannot honestly perform its intended real-time exact-SIP first-break detection on the current entitlement. IEX or delayed SIP must not be silently substituted and called equivalent evidence.

The collector uses the separately proven Public source:

- Public `DAY` regular-market 5m chart bars for causal lower-timeframe observation;
- Public `WEEK` regular-market 30m chart bars for recent history, including the immediately prior regular session needed for the opening-bar re-anchor;
- current-session 30m bars are rebuilt from completed Public 5m bars;
- Public real-time equity quote + option expiration/chain endpoints only after a qualifying reversal trigger reaches evidence capture.

Every structural level remains labeled `public_chart`. It is not relabeled as SIP.

Malformed, off-grid, partial, duplicate, or gapped 5m evidence fails closed. No live/partial chart row is rounded into a completed bar.

## Review-tightened timing boundary

Independent review tightened three timing details without changing the lane's observation-only scope:

- `ARMED` evidence is timestamped with the time that ticker's Public source payload was actually received, not one process-wide run-start timestamp. A later ticker in a serial 20-symbol pass cannot inherit an earlier observation time.
- The exact trigger clock is resolved from the first Alpaca SIP price-forming trade **strictly through** the proven Public trigger boundary. Equality prints do not count. The same trade-condition and strict-cross semantics used by the frozen 81-row historical trigger audit are reused here.
- The capture-lag gate is checked both before and after option/selector evidence and is measured from the **exact SIP crossing timestamp**, not from the five-minute bar close. Starting a chain request inside the window is not enough if it finishes after the pre-registered deadline.

This closes a defect in v0.1 where evidence captured one second after a five-minute close could appear one second late even when the actual trigger crossed several minutes earlier inside that bar. The numeric capture-lag threshold remains an operator policy decision; no value is approved by this review.

### Why v0.3 resolves during WATCHING

The v0.2 safety correction made latency honest but still waited for the five-minute bar to close. The frozen 81 proved that architecture was too late for a qualifying prospective lane:

- crossing-to-bar-close delay minimum: **11.716s**;
- median: **169.751s**;
- maximum: **299.821s**;
- only **11/81 (13.6%)** were within 60 seconds of bar close before any network/chain latency.

Collector v0.3 therefore inspects Alpaca SIP trades while a Public-source 212 setup is still `WATCHING`. On each collector cycle it queries only from the frozen watch start through that ticker's current source-observation time, determines which boundary was crossed first using the same strict price-forming trade rules as #733, and immediately runs the existing selector-evidence path when that first break is a valid reversal.

The completed-five-minute path remains only a fail-closed fallback for a missed observation cycle; it no longer defines the qualifying evidence clock. Actual capture quality is now governed by collector cadence + API/chain latency from the exact SIP crossing.

## No-hindsight requirement

A historical reconstruction is not allowed to become prospective evidence merely because the structure can be recognized later.

For selector evidence to be captured:

1. the 212 setup must already have an append-only `ARMED` record;
2. once exact SIP crossing evidence exists, that arm record timestamp must be strictly earlier than the true crossing timestamp; equality or a later arm is hindsight and blocks the event;
3. the trigger must be `STRAT_212_REVERSAL`;
4. source magnitude must still remain at the trigger (`TARGET_CONSUMED_AT_ENTRY` is blocked);
5. an Alpaca SIP trade window for that proven bucket must resolve the first strict-through crossing without unknown trade semantics;
6. both the pre-selector gate and the final completed selector capture must fall within a caller-supplied, pre-registered maximum lag after that exact SIP crossing.

There is deliberately **no default capture-lag threshold**. The CLI requires `--max-capture-lag-seconds`, so deployment cannot silently invent the acceptance window.

Late-discovered historical/session triggers remain structural evidence but their current option chain is `DATA_BLOCKED`, never back-filled as decision-time evidence.

## Source geometry retained

For a valid 212R trigger the record preserves:

- inside-bar break trigger;
- opposite side of the inside bar as invalidation;
- parent 2 far extreme as source magnitude;
- source magnitude in R;
- whether source magnitude was already consumed at trigger;
- first crossing 5m bucket;
- exact first-break SIP trade provenance. Later opposite-side/outside evolution remains a later reconciliation field rather than being guessed at the live crossing.

No >=1R target-floor substitution is made.

## Production selector evidence

For a timely, pre-armed, unconsumed 212R trigger the collector reuses the existing production-authoritative path:

- Public underlying quote provenance;
- all expiration candidates;
- production-chosen expiration;
- full option-chain supplement with bid/ask side timestamps, volume, OI, delta and IV;
- exact `OPTIONS_PAPER_V1` production selector source hash/input bytes;
- actual production selection;
- retained-input production replay;
- `production_replay_parity`.

Evidence usability adds a strict timestamp check: selected option and underlying quote timestamps must exist, must not be future, and must be within the existing Public freshness limit.

No risk sizing or ACTIVE aggregate-risk accounting is performed by this collector because it is not a trade lane.

## Market context

Market context is intentionally recorded as `DEFERRED_SIP_RECONCILIATION` in v0.3.

Reason: the frozen context formula uses VWAP. Public chart bars do not expose the same VWAP input, and inventing one from OHLC would violate the evidence rules. Context can be reconstructed later from consolidated SIP using only data timestamped at or before the trigger boundary once the 15-minute entitlement delay has elapsed.

No alignment variant is used as an entry gate by this collector.

## Isolated evidence storage

Default journal: `logs/options_212r_prospective.jsonl`.

Default raw SIP window directory: `logs/options_212r_sip_trades/`.

Each qualifying reversal retains the exact crossing-trade metadata plus a SHA-256 of the canonical SIP trade window. Non-dry-run collection persists that raw window immutably; an existing path with different bytes fails closed as source drift.

The journal is append-only and independent of `options_scanner.sqlite`. It stores `ARMED` and terminal `RESOLUTION` records. Malformed existing journal rows fail closed instead of being skipped. Resolution records distinguish `capture_gate_eligible` (the trigger passed the pre-selector timing gate) from `option_evidence_usable` (the completed selector capture also passed final timing/freshness/parity checks).

Setup identity is stable by ticker + watch window + pattern. The exact Public boundary levels/reference direction are stored under a separate source fingerprint. If a completed Public bar is later revised and the same setup identity produces different frozen boundaries, the lane records source drift and fails the event closed instead of silently creating a second setup.

## Earlier no-hindsight RTH smoke

A pre-v0.3 read-only dry run over the primary 20 at 2026-09-18T18:29:57Z reconstructed 14 212R reversals and 21 total terminal 212 setup resolutions from the session.

Because the collector had **not** existed before those triggers, all 14 reversal option captures were correctly blocked for lack of a proven pre-trigger arm. Zero current option chains were misrepresented as historical decision-time evidence.

That is the intended no-hindsight behavior.

## Still required before deployment

- independent review + CI;
- resolve real-time first-break source access: either prove current credentials can query real-time consolidated SIP or explicitly validate a different prospective trigger source with later SIP reconciliation;
- explicit operator choice of the prospective capture-lag limit and timer cadence;
- a service-specific collector release/location that cannot mutate scanner/risk/broker state;
- first live `ARMED -> proven first break -> selector evidence` proof during RTH;
- delayed SIP context/source reconciliation;
- only then accumulation of option-side outcome evidence.

No proof, no trade.
