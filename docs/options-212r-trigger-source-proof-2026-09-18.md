# 212R prospective trigger-source proof — 2026-09-18

## Verdict

**PROVEN FOR OBSERVATION / NOT SIP-EQUIVALENT / PAPER-EVIDENCE ONLY.**

A timely read-only source exists for prospective 212R trigger observation without waiting for the 15-minute Alpaca SIP entitlement delay: Public's real-time equity quote surface plus its DAY/WEEK chart data.

This proof does **not** authorize a trade, change V1, deploy a collector, or claim Public chart bars are byte-identical to consolidated SIP.

## Capture

Probe: `scripts/options_212r_trigger_source_probe.py`

Frozen report: `data/options_212r_trigger_source_probe_2026_09_18/report.json`

Capture window: **2026-09-18T18:30:17.537925Z to 18:30:37.147112Z** during RTH.

Universe: primary 20.

### Real-time equity quotes

All 20 Public equity quote responses carried last, bid, and ask timestamps.

At capture:

- maximum last-trade age: **14.201 seconds**;
- maximum bid age: **3.201 seconds**;
- maximum ask age: **3.201 seconds**;
- 20/20 had last, bid, and ask timestamps;
- 0/20 had a timestamp later than that ticker's quote-response receipt time.

Quote age is measured against each ticker's own response-receipt timestamp, not against one run-start timestamp. The earlier single-clock calculation could produce negative ages for later sequential requests and is not used in this proof.

The quote request uses only Public's read-only market-data endpoint. No account/trading/order endpoint is used.

### Public chart bars

Public's `DAY` chart returned regular-session **5-minute** bars during the active session. It also emits a live/partial row; the new pure normalizer refuses to round that row into a canonical bar and admits only grid-aligned intervals whose close is <= the decision timestamp.

Public's `WEEK` chart returned regular-session **30-minute** history. Session-close synthetic points and incomplete current intervals are excluded rather than treated as completed bars.

These rules live in `alert_ranker/public_chart_bars.py` and are fail-closed on malformed timestamps, duplicate completed bars, or invalid OHLC geometry.

## Delayed SIP reconciliation

The same capture compared Public 5-minute data aggregated into session-aligned 30-minute bars against Alpaca consolidated SIP once the same intervals were old enough to clear the 16-minute entitlement boundary.

Across the primary 20:

- comparable completed 30-minute bars: **180**;
- exact Public-vs-SIP high/low: **112/180 (62.22%)**;
- maximum observed high/low absolute difference: **$0.005**;
- comparable Strat scenario pairs: **160**;
- same Strat scenario: **158/160 (98.75%)**.

Therefore Public is sufficiently timely to support an **observation-only prospective trigger lane**, but it must remain explicitly labeled as its own source. It is **not** legitimate to call the Public bars SIP or silently mix their levels with the frozen SIP historical population.

The two scenario disagreements in this capture occurred at exact/equality-sensitive boundaries; that is precisely why source identity and later SIP reconciliation must be retained.

## Alpaca entitlement boundary

A same-session read-only probe against the existing Alpaca credentials confirmed recent consolidated SIP 1m/5m/30m requests fail with `provider_entitlement` (`subscription does not permit querying recent SIP data`). IEX was timely, but IEX is a different feed and is not promoted to canonical Strat evidence by this result.

## Consequence for 212R

The prospective lane can now be designed without reproducing the old 30m-close + 15-minute first-sight defect:

1. use Public chart bars as the **prospective observation source**;
2. freeze only complete grid-aligned 5m/30m bars at each decision boundary;
3. detect the first 5-minute bucket that proves the inside-bar boundary break;
4. capture the Public real-time underlying quote and option chain immediately after detection;
5. preserve the exact `OPTIONS_PAPER_V1` selector inputs and replay parity;
6. retain Public source identity on every level and timestamp;
7. once SIP becomes available, reconcile the observed Public setup/trigger against consolidated SIP as a separate evidence field;
8. source disagreement is evidence, not something to silently normalize away.

This remains Phase 1 observation only: no trade alert, ACTIVE risk reservation, order ticket, broker submission, or live execution.
