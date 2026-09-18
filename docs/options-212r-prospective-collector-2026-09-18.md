# 212R prospective trigger / option-evidence collector — 2026-09-18

## Status

**BUILT OFFLINE / OBSERVATION ONLY / NOT DEPLOYED.**

This is the missing Phase-1 bridge between the proven 212R trigger-time model and the already-built `OPTIONS_PAPER_V1` prospective selector-evidence path.

It does not alert a trade, reserve ACTIVE risk, build an order ticket, call a broker, activate DEMO, or change the running options scanner.

## Source boundary

The collector uses the separately proven Public source:

- Public `DAY` regular-market 5m chart bars for causal lower-timeframe observation;
- Public `WEEK` regular-market 30m chart bars for current-week history;
- current-session 30m bars are rebuilt from completed Public 5m bars;
- Public real-time equity quote + option expiration/chain endpoints only after a qualifying reversal trigger reaches evidence capture.

Every structural level remains labeled `public_chart`. It is not relabeled as SIP.

Malformed, off-grid, partial, duplicate, or gapped 5m evidence fails closed. No live/partial chart row is rounded into a completed bar.

## No-hindsight requirement

A historical reconstruction is not allowed to become prospective evidence merely because the structure can be recognized later.

For selector evidence to be captured:

1. the 212 setup must already have an append-only `ARMED` record;
2. that arm record timestamp must be no later than the start of the first 5m bucket that eventually proves the break;
3. the trigger must be `STRAT_212_REVERSAL`;
4. source magnitude must still remain at the trigger (`TARGET_CONSUMED_AT_ENTRY` is blocked);
5. the evidence capture must occur within a caller-supplied, pre-registered maximum lag after that 5m bar becomes complete.

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
- later outside-bar transition.

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

Market context is intentionally recorded as `DEFERRED_SIP_RECONCILIATION` in v0.1.

Reason: the frozen context formula uses VWAP. Public chart bars do not expose the same VWAP input, and inventing one from OHLC would violate the evidence rules. Context can be reconstructed later from consolidated SIP using only data timestamped at or before the trigger boundary once the 15-minute entitlement delay has elapsed.

No alignment variant is used as an entry gate by this collector.

## Isolated journal

Default path: `logs/options_212r_prospective.jsonl`.

The journal is append-only and independent of `options_scanner.sqlite`. It stores `ARMED` and terminal `RESOLUTION` records. Malformed existing journal rows fail closed instead of being skipped.

## Independent review corrections

Pre-merge review tightened three causal boundaries before this collector can be trusted prospectively:

1. **Per-ticker observation time.** The ARMED timestamp now uses the time that ticker's Public source payload was actually received, not the collector run-start time. A slow 20-symbol pass therefore cannot claim a setup was observed before a trigger merely because the overall process started earlier.
2. **RTH opening re-anchor.** The 09:30 ET watch bar can use only the immediately preceding NYSE session's final completed 30m bar as its precursor. Older/stale sessions fail closed. The history loader now reaches across the prior week so Monday/opening setups are not silently dropped.
3. **Final selector-capture deadline.** The capture-lag gate is checked both before the option requests and again against the selector evidence's actual `captured_at` timestamp. A request that starts inside the window but finishes late is `DATA_BLOCKED`.

## Current RTH smoke

The original read-only dry run at 2026-09-18T18:29:57Z reconstructed 14 212R reversals and 21 terminal 212 setup resolutions. After the causal-boundary review above, a second primary-20 run at approximately 18:37Z reconstructed 15 212R reversals and 24 terminal resolutions; the additional set included a TLT 09:30 ET opening trigger that the pre-review session-gap logic would have skipped.

All **15/15** reversal option captures were blocked as `no_proven_pretrigger_arm`; zero current chains were accepted as historical decision-time evidence. The review run used a 60-second lag value only to exercise the mechanics. **It is not the operator's pre-registered production evidence policy.**

That is the intended no-hindsight behavior.

## Still required before deployment

- independent review + CI;
- explicit operator choice of the prospective capture-lag limit and timer cadence;
- a service-specific collector release/location that cannot mutate scanner/risk/broker state;
- first live `ARMED -> TRIGGERED` proof during RTH;
- delayed SIP context/source reconciliation;
- only then accumulation of option-side outcome evidence.

No proof, no trade.
