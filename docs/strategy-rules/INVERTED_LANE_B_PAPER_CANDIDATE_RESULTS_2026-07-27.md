# Inverted Lane B paper-candidate validation

## VERDICT

**PROMISING BUT UNPROVEN — KEEP RESEARCHING.**

The exact frozen inverse reproduced with zero differences. A genuinely
untouched post-baseline extension also passed: **19 trades, +$645.38 net,
+$33.97 expectancy, PF 1.988**. Both directions and both halves of that small
extension were positive.

That is encouraging, but it is not enough to promote the rule into a paper
runtime lane yet:

- untouched N is only 19;
- 2025-Q3 and 2026-Q2 are negative;
- 5 of 23 rolling three-month windows are negative;
- the latest rolling three-month and six-month windows are negative;
- the combined equity remains in an unrecovered terminal drawdown;
- the current runtime cannot reproduce the rule without new, explicitly
  isolated timing/state/risk plumbing.

No deployment or runtime implementation is included.

## FROZEN RULE IDENTITY

The candidate remains the exact direction inverse of the committed literal
Lane B rule:

- signal at 15:30 ET from prior eligible 16:00 close through current 15:25
  bar close;
- inverse SHORT when the return is positive, inverse LONG otherwise;
- entry at the 15:30 five-minute bar open;
- exit at the 15:55 bar close (16:00 ET);
- one MNQ contract;
- no threshold, stop, target, trend/VWAP/ORB/volatility filter, or optimization;
- unchanged full-session eligibility and missing/shortened-session handling;
- $1.48 round-trip commission and one adverse tick per side at baseline.

The protocol was committed before extension results as `853206b`.

## REPRODUCTION RESULTS

Every required field reconciled exactly to the frozen inversion audit.

| Metric | Frozen target | Reproduction | Difference |
|---|---:|---:|---:|
| Trades | 490 | 490 | 0 |
| Gross | +$4,858.50 | +$4,858.50 | $0.00 |
| Net | +$3,643.30 | +$3,643.30 | $0.00 |
| Expectancy | +$7.4353 | +$7.4353 | $0.0000 |
| PF | 1.1941 | 1.1941 | 0.0000 |
| H1 / H2 | +$3,474.90 / +$168.40 | +$3,474.90 / +$168.40 | $0.00 / $0.00 |
| LONG / SHORT | +$2,838.44 / +$804.86 | +$2,838.44 / +$804.86 | $0.00 / $0.00 |
| Four periods | +$402.94 / +$3,071.96 / +$120.44 / +$47.96 | Exact | $0.00 each |
| Untouched final 25% | +$47.96 | +$47.96 | $0.00 |
| 1/2/3/4 ticks | +$3,643.30 / +$3,153.30 / +$2,663.30 / +$2,173.30 | Exact | $0.00 each |
| Top 1 / top 5 | $546.52 / $2,194.10 | Exact | $0.00 |
| Net without top 5 | +$1,449.20 | +$1,449.20 | $0.00 |

Failure to reproduce would have forced HOLD. It did not.

## NEW UNTOUCHED OOS DATA

The committed baseline cache ends on 2026-06-26. A read-only Massive/Polygon
futures pull retrieved 24 separate post-baseline files covering the available
bars through 2026-07-24. Massive documents that its futures aggregates are
constructed from actual trades and that an interval with no trades produces no
bar ([Futures Aggregate Bars](https://massive.com/docs/rest/futures/aggregates?assetClass=futures&license=personal&name=futures_basic)).

Compatibility validation:

- 2,136 overlap bars independently downloaded for 2026-06-17 through
  2026-06-26;
- zero OHLCV mismatches against the frozen cache;
- OOS directory: `data/research_oos/inverted_lane_b_2026_07/MNQ`;
- 24 files, 5,472 bars;
- tree SHA-256:
  `5cd69692cdd9707ec3520d0498a1a666b611dd527c92e9611f8d1b21c6c4585e`;
- no old observation changed when the OOS directory was added.

An attempted pre-baseline vendor pull for 2024-05-15 through 2024-06-30
returned zero bars. No earlier sample was manufactured or substituted.

| Sample | Trades | Gross | Net | Exp/trade | PF | WR | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Old frozen | 490 | +$4,858.50 | +$3,643.30 | +$7.4353 | 1.194 | 51.43% | $1,320.96 |
| New untouched OOS | 19 | +$692.50 | +$645.38 | +$33.9674 | 1.988 | 63.16% | $224.46 |
| Combined | 509 | +$5,551.00 | +$4,288.68 | +$8.4257 | 1.221 | 51.87% | $1,462.90 |

The OOS result is reported independently before the combined result. N=19
prevents a validation claim.

## TEMPORAL STABILITY

### Calendar years

| Year | N | Net | PF |
|---|---:|---:|---:|
| 2024 | 123 | +$224.46 | 1.053 |
| 2025 | 246 | +$3,267.42 | 1.367 |
| 2026 through July 24 | 140 | +$796.80 | 1.127 |

### Calendar quarters

| Quarter | N | Net | PF |
|---|---:|---:|---:|
| 2024-Q3 | 61 | +$165.72 | 1.074 |
| 2024-Q4 | 62 | +$58.74 | 1.029 |
| 2025-Q1 | 59 | +$1,531.18 | 1.744 |
| 2025-Q2 | 62 | +$1,787.24 | 1.654 |
| 2025-Q3 | 63 | **-$367.74** | 0.786 |
| 2025-Q4 | 62 | +$316.74 | 1.133 |
| 2026-Q1 | 61 | +$900.22 | 1.459 |
| 2026-Q2 | 62 | **-$670.26** | 0.818 |
| 2026-Q3 partial | 17 | +$566.84 | 1.929 |

Rolling results:

- Three-month windows: 18 positive, 5 negative out of 23. Worst -$670.26;
  best +$2,869.26.
- Six-month windows: 18 positive, 2 negative out of 20. Worst -$51.00;
  best +$3,318.42.
- Latest three-month window through July: **-$44.84, PF 0.987**.
- Latest six-month window through July: **-$37.10, PF 0.994**.
- Earliest 126 trades: +$226.02, PF 1.052.
- Latest 126 trades: +$495.52, PF 1.083.

The July OOS rebound is positive, but it has not yet repaired the weak trailing
three- and six-month windows.

## H1 / H2

| Sample | H1 | H2 |
|---|---:|---:|
| Old frozen | +$3,474.90 | +$168.40 |
| New untouched OOS | +$314.68 | +$330.70 |
| Combined | +$3,311.58 | +$977.10 |

The combined midpoint moves because 19 new observations are appended; the old
frozen H1/H2 values remain separately preserved.

## LONG / SHORT

| Sample | LONG N / net / PF | SHORT N / net / PF |
|---|---:|---:|
| Old frozen | 222 / +$2,838.44 / 1.283 | 268 / +$804.86 / 1.092 |
| New untouched OOS | 10 / +$362.20 / 1.768 | 9 / +$283.18 / 2.561 |
| Combined | 232 / +$3,200.64 / 1.304 | 277 / +$1,088.04 / 1.122 |

There is no catastrophic direction dependence, although SHORT remains the
weaker historical side.

## COST SENSITIVITY

Combined 509-trade sample:

| Stress | Net | Exp/trade | PF | Max DD |
|---|---:|---:|---:|---:|
| 1 tick/side, $1.48 RT | +$4,288.68 | +$8.4257 | 1.221 | $1,462.90 |
| 2 ticks/side, $1.48 RT | +$3,779.68 | +$7.4257 | 1.192 | $1,492.90 |
| 3 ticks/side, $1.48 RT | +$3,270.68 | +$6.4257 | 1.164 | $1,522.90 |
| 4 ticks/side, $1.48 RT | +$2,761.68 | +$5.4257 | 1.137 | $1,552.90 |
| 1 tick/side, $2.00 RT | +$4,024.00 | +$7.9057 | 1.206 | $1,478.50 |

The rule has fixed entry/exit times and no stop or target, so there is no
same-bar stop/target ordering ambiguity.

## CONCENTRATION

### Old sample

- Top 1: $546.52; net without it: +$3,096.78.
- Top 5: $2,194.10; net without them: +$1,449.20.
- Top 10: $3,830.70; net without them: **-$187.40**.

### New untouched OOS

- Top 1: $230.02; net without it: +$415.36.
- Top 5: $815.60; net without them: -$170.22.
- Top 10: $1,260.20; net without them: -$614.82.

The OOS concentration numbers are not independently meaningful with only 19
trades and 12 winners.

### Combined

- Top 1: $546.52, 2.3% of winner dollars; net without it: +$3,742.16.
- Top 5: $2,194.10, 9.3% of winner dollars; net without them: +$2,094.58.
- Top 10: $3,830.70, 16.2% of winner dollars; net without them: +$457.98.

Combined profitability survives removal of the top ten, but only narrowly
relative to the full +$4,288.68.

## DRAWDOWN / LOSING STREAK

Combined:

- largest loss: -$455.98;
- longest losing streak: 9;
- maximum drawdown: $1,462.90;
- max-drawdown peak: 2026-05-18;
- max-drawdown trough: 2026-07-01;
- longest observed recovery/underwater duration: 98 trading observations,
  143 calendar days;
- terminal drawdown: **unrecovered** as of 2026-07-24;
- current underwater duration: 46 observations, 67 calendar days.

The OOS extension improved equity by $645.38 but did not regain the May peak.

## RUNTIME PARITY AUDIT

### Feed and timing

- The current authoritative decision timeframe is 15 minutes. The separate
  five-minute feed is environment-gated and defaults off.
- When enabled, ordinary five-minute bars are stored as context and do not
  discover new setups; only existing specifically allowlisted native lanes can
  act from them. The inverted Lane B identity does not exist.
- TradingView bar timestamps identify bar opens. A 15:25 bar-close alert
  arriving around 15:30 makes the signal causal, but a real/paper market order
  cannot be guaranteed to fill at the exact next bar open plus precisely one
  tick. Actual submission latency and fill deviation must be journaled and
  compared with the research model.
- CME states that MNQ trades on Globex nearly around the clock and identifies
  the product code and quarterly listing cycle
  ([Micro E-mini FAQ](https://www.cmegroup.com/articles/faqs/micro-e-mini-equity-index-futures-frequently-asked-questions.html)).
  The research rule nevertheless uses the 09:30–16:00 ET cash-market window;
  runtime must not substitute Globex session boundaries.

### Required state

A future lane needs dedicated persistent state containing:

- prior eligible full-session 15:55 close and its date;
- current 15:25 signal close and timestamp;
- computed return, original direction, and inverse direction;
- eligibility status plus exclusion reason;
- entry intent, observed 15:30 open, submit time, and actual simulated fill;
- fixed 16:00 exit obligation;
- contract/root and roll identity;
- idempotent signal/order identity across restart and resend.

The current generic rolling bar history is not sufficient: its normal
lookback is short and missing-bar backfill is not guaranteed. A holiday or
multi-day outage cannot be allowed to silently replace the previous eligible
close.

### Holiday and shortened sessions

Research excludes a day unless all required boundary bars exist. Runtime has no
equivalent exchange-calendar/short-session eligibility service in this path.
It must use an authoritative schedule plus observed-bar completeness and fail
closed. Massive exposes futures schedules in UTC and documents holiday/special
adjustments ([Futures API overview](https://massive.com/docs/rest/futures/overview)).

### Exit parity

The existing day-only helper recognizes the exact 15:55 five-minute bar and
can flatten at its close, but its strategy allowlist does not include this
candidate. The scheduled fallback is designed for broker positions, not a
standalone fixed-time research simulation. Missing 15:55 bars must produce an
explicit unresolved/invalid session, never a later substitute close.

### Gates and collision behavior

Passing this rule through the normal DecisionEngine would silently alter it:

- non-tradable/`TRENDING` market-condition gates;
- regime, direction, GEX, Signa, confluence, session-window, and strategy
  permission gates;
- ranked candidate selection;
- max-trades/day and existing open-position suppression.

The standard RiskEngine also requires a complete stop/target bracket and
minimum R:R. This rule intentionally has neither, so it cannot be represented
as an ordinary `TradeSetup` without falsifying its identity.

A later paper implementation therefore needs an isolated signal journal and
fixed-time paper simulator. Portfolio safety may still block simulated
execution, but the candidate signal must always be journaled separately so
blocked trades remain measurable. The policy for max-trades/day, existing MNQ
positions, and simultaneous 15:30 candidates must be frozen before build; any
choice changes executed-population parity.

### Instrument and roll parity

Research uses a stitched front-contract MNQ series with the repository's
frozen roll convention. Runtime may receive `MNQ1!` while broker instruments
are dated contracts. The candidate must preserve the MNQ root while journaling
the exact source and simulated contract and validating roll-date price
continuity. A source-symbol mismatch near roll can change both the signal
return and entry/exit P&L.

## PAPER-PROMOTION BLOCKERS

1. Untouched OOS N=19 is insufficient.
2. Latest rolling three- and six-month windows remain negative.
3. The terminal drawdown is not recovered.
4. There is no dedicated close-momentum signal/state identity in runtime.
5. Exact 15:30 research entry versus observable paper fill parity is unproven.
6. Five-minute feed completeness and shortened-session eligibility are not
   guaranteed.
7. The standard bracket/R:R risk path cannot express a stopless fixed-time
   trade.
8. Global gates and portfolio collisions would change the frozen population
   unless explicitly isolated and dual-journaled.
9. Contract-roll parity needs a frozen runtime policy.

## FORWARD PAPER PROMOTION GATES

| Gate | Status |
|---|---|
| Exact replay reproduction | PASS |
| Frozen rule | PASS |
| Positive realistic costs | PASS |
| Positive historical H1/H2 | PASS |
| Positive recent isolated OOS | PASS, but N=19 |
| Positive untouched extension | PASS, insufficient N |
| No severe winner concentration | CONDITIONAL; top-10 removal is thin |
| No direction dependence | PASS |
| No runtime/replay mismatch | **FAIL** |
| Clean causal implementation | **NOT BUILT** |
| Existing risk controls explicitly preserved | **UNRESOLVED** |

## RECOMMENDATION

**KEEP RESEARCHING.**

Continue prospective untouched collection under the frozen identity. Reassess
after a materially larger OOS sample and recovery of trailing three/six-month
stability. Only then preregister a separate paper-lane implementation design
that resolves timing, schedule, fixed-time exit, risk-adapter, and collision
semantics before writing runtime code.

ORB Breakout remains excluded and should receive a separate research proposal
if pursued.

No deployed box, #359 epoch, runtime configuration, strategy, sizing, or
execution setting was touched.
