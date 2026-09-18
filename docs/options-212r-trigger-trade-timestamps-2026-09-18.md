# 212R exact trigger-trade timestamp audit — 2026-09-18

## Verdict

**PROVEN FOR THE FROZEN 81 TRIGGER-CROSSING CLOCK / 212R REMAINS WAIT.**

This audit resolves the first causal underlying **SIP price-forming trade through the frozen 2-1-2 reversal trigger** for every row in the exact frozen 81-event population. It does not prove historical option selection, option fills, expectancy, or strategy edge.

## Frozen evidence identity

Underlying observer:

- version: `cov-v0.1`
- SHA-256: `edba1ab55e859357f38db843c48b9942e685c1b148db82e2cefafaca632df286`

Causal trigger-bar snapshot:

- snapshot: `OPTIONS_TRIGGER_BAR_SNAPSHOT / trigger-bars-v0.1`
- Alpaca consolidated SIP, primary 20, 2026-09-09 through 2026-09-15
- manifest SHA-256: `8d9cd8a4674827abc11bf60805d53ca812c738bb28417b672a10e3f98790dc1b`

Exact 212R geometry membership and trigger buckets come from the SHA-bound #724/#726 audit.

## Method

For each exact frozen 212R row:

1. use the previously frozen first-cross 5-minute trigger bucket;
2. fetch Alpaca consolidated SIP historical trades for **only that five-minute window**;
3. preserve nanosecond SIP timestamps without float conversion;
4. apply the documented minute-bar price-field trade-condition rules;
5. fail closed on unknown tapes or unknown condition codes;
6. require the accepted price-forming trades to reconstruct the frozen 5-minute **open, high, low, and close exactly**;
7. require a strict break of the trigger boundary:
   - LONG: first eligible trade with price **> trigger**;
   - SHORT: first eligible trade with price **< trigger**;
8. preserve the first eligible crossing trade as the causal trigger-time evidence.

An equality print at the trigger does not count as a boundary break.

## Result

Frozen rows expected: **81**

Frozen rows resolved: **81/81**

Frozen trigger-bar OHLC reproduced exactly from eligible SIP trades: **81/81**

Raw SIP trade rows retained: **353,456**

Unique five-minute trade windows: **81**

First crossing offset from the start of the already-proven five-minute trigger bucket:

- minimum: **0.178917261 s**
- median: **130.249476936 s**
- maximum: **288.283787331 s**

First crossing price:

- exact equality with trigger: **0/81**
- strict through-level cross: **81/81**

First-cross tapes:

- Tape A: **22**
- Tape B: **8**
- Tape C: **51**

First-cross condition counts:

- regular-sale blank condition: **30**
- `@`: **51**
- `F`: **32**

## Concrete AMZN proof

Frozen AMZN LONG 212R event on 2026-09-09:

- watched 30m bar: `2026-09-09T14:30:00Z`
- proven first-cross 5m bucket: `2026-09-09T14:45:00Z`
- trigger: **252.65**
- invalidation: **251.52**
- source magnitude target: **254.69**
- frozen 5m OHLC: **252.475 / 252.69 / 252.10 / 252.2931**

Eligible historical SIP trades reproduce that OHLC exactly.

The first price-forming trade strictly through the LONG trigger is:

- timestamp: **2026-09-09T14:46:52.595127004Z**
- price: **252.66**
- size: **100**
- exchange: **Z**
- conditions: `@`, `F`
- tape: **C**

The immediately preceding eligible trade was exactly **252.65**, so the audit correctly waits for the next strict-through print.

A separately observed SIP NBBO immediately around that AMZN crossing was **252.65 bid / 252.66 ask**. NBBO retention is not part of this v0.1 artifact and is not claimed for all 81 rows.

## Preserved artifacts

Repository artifacts:

- `data/options_trigger_trade_timestamp_audit_2026_09_18/manifest.json`
  - SHA-256: `29a1ecdcc052348708583818da1334f098af9425da69d9612f0c6e0b0003e103`
- `data/options_trigger_trade_timestamp_audit_2026_09_18/events.jsonl`
  - SHA-256: `58718220328645e7f87aaa6826fd20fadf9f45e75a36ed920b4242635c9d42f6`
- `data/options_trigger_trade_timestamp_audit_2026_09_18/repeat_proof.json`
  - SHA-256: `1abd263a7a7bcdc5e94cbe38227362c5ae9e12cdd8da4ad40067fa7f2c75e3ad`

Independent repeat run:

- primary manifest == repeat manifest: **byte-identical**;
- primary events == repeat events: **byte-identical**;
- raw file set: **81/81 same**;
- raw SIP files: **81/81 byte-identical**.

Raw read-only SIP evidence on the VPS:

`/root/afs-shared/research/backtest_fidelity_20260918/options_trigger_trade_timestamp_audit`

Raw evidence size: approximately **66 MB** across 81 files/windows.

The raw directory was copied from the isolated research run, both compact artifact hashes were re-verified, and the preserved evidence tree was made non-writable.

## What this clears

This clears the historical uncertainty around:

- which exact SIP price-forming trade first crossed each frozen 212R trigger;
- the nanosecond trigger-cross timestamp;
- the causal crossing-trade underlying price;
- whether that tick evidence is consistent with the already-frozen 5-minute trigger bar.

The production scanner accepts a caller-supplied `context.price` as normalized `price`, and that normalized price is passed to `OPTIONS_PAPER_V1` contract selection. Therefore this audit supplies a causal historical context-price candidate for replay.

## What remains blocked

Do **not** promote this into full historical selector parity yet.

Still unresolved:

- historical decision-time option **Delta** for the production selector;
- historical decision-time contract-level **open interest**;
- an explicit replay packet proving the corrected 212R historical lane feeds the crossing-trade price through the same `context.price -> normalized price -> OPTIONS_PAPER_V1` semantic path;
- historical option-chain population at the exact corrected trigger timestamps;
- historical option fills, after-cost expectancy, drawdown, and stress;
- final 212R market-context policy;
- final source-target / runner-management policy;
- pre-registered numeric slippage/stress qualification policy;
- untouched chronological / multi-month validation.

Massive historical option bid/ask remains useful, but current snapshots must not be used to synthesize past Delta or open interest.

## Safety

This was a read-only historical market-data audit.

No scanner activation, strategy activation, risk reservation, broker/account endpoint, order route, DEMO trade, or live trade was added or used.

**Current strategy classification: PROMISING BUT UNPROVEN / WAIT.**
