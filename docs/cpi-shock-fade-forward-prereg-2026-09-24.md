# CPI Shock-Fade Forward Shadow Preregistration — 2026-09-24

## Status

**RESEARCH / SHADOW ONLY. NOT PAPER-ACTIVE. NOT LIVE-ACTIVE.**

This document freezes the first forward-test version of the MNQ CPI shock-fade candidate before the next CPI release.

The active trading lanes are not modified by this study. No broker submission, order preparation, execution routing, risk-budget consumption, strategy gate, or live decision may depend on this research collector.

## Frozen hypothesis

On scheduled U.S. CPI release mornings, the initial 5-minute MNQ move after 08:30 ET partially reverses before the U.S. cash open.

## Frozen rule — v1

Instrument: **MNQ only**

Event: **official BLS CPI release at 08:30 ET**

Signal:
1. Record the 08:30 ET 1-minute bar open.
2. Record the 08:35 ET 1-minute bar open.
3. If 08:35 open > 08:30 open, shadow direction = **SHORT**.
4. If 08:35 open < 08:30 open, shadow direction = **LONG**.
5. If equal, record **NO_SIGNAL**.

Entry proxy: **08:40 ET 1-minute bar open**

Exit proxy: **09:30 ET 1-minute bar open**

No threshold.
No stop.
No target.
No trailing logic.
No GEX.
No Signa.
No Strat filter.
No regime filter.
No volatility filter.
No discretionary override.
No direction asymmetry.
No parameter tuning during this version.

## Official forward CPI dates currently frozen

BLS release calendar verified 2026-09-24:

- 2026-10-14 at 08:30 ET
- 2026-11-10 at 08:30 ET
- 2026-12-10 at 08:30 ET

Any later CPI date requires an explicit calendar update before it is eligible for the forward sample.

## Discovery evidence — not forward proof

The discovery population contains 23 CPI releases from 2024-10-10 through 2026-09-11.

The complete 1-minute replay of the frozen 08:40 → 09:30 rule produced:

- 23 events
- 78.3% gross winners
- +$45.35 average gross per 1 MNQ
- +$1,043 cumulative gross
- H1: +$29.50 average
- H2: +$59.88 average
- median gross result: +$54.50
- gross profit factor: 3.84
- max sequential gross drawdown: -$132
- worst completed trade: -$132
- average MAE: $69.54
- worst MAE: $146.50
- average MFE: $106.22

Latency sensitivity:

- 08:40 entry: +$45.35 average
- 08:41 entry: +$42.48 average
- 08:42 entry: +$48.89 average
- 08:45 entry: +$40.30 average

Concentration check:

- remove best 3 events: +$29.73 average across remaining 20
- remaining win rate: 75%

Calendar slices:

- 2024: 3 events, -$5.50 average
- 2025: 11 events, +$36.64 average
- 2026 through September: 9 events, +$72.94 average

Fixed-dollar execution stress:

- $5 total friction per trade: +$40.35 average, PF 3.36
- $10 total friction per trade: +$35.35 average, PF 2.92
- $20 total friction per trade: +$25.35 average, PF 2.17

These figures are discovery evidence only. They do not authorize paper or live trading.

## Known execution limitation

The current historical futures entitlement does not provide historical bid/ask quotes or tick trades for these events.

Therefore:

- 08:40 and 09:30 bar opens are **fill proxies**
- fill quality is **not verified**
- bid/ask spread is **not observed**
- queue position is **not observed**
- slippage is stress-tested rather than reconstructed

The forward collector must preserve these limitations in every record.

## Forward evidence contract

Each eligible CPI release must produce one immutable append-only record containing:

- event date
- contract
- 08:30 open
- 08:35 open
- frozen shadow direction
- 08:40 entry proxy
- 09:30 exit proxy
- gross points / dollars
- MAE
- MFE
- $5 / $10 / $20 friction stress
- bar source
- explicit bid/ask availability flag
- explicit fill-verification flag
- safety flags proving observation-only status

A missing required bar means **NO EVIDENCE**, not an inferred value.

Duplicate event records are rejected.

## Forward gate

No paper activation is permitted from discovery results alone.

A review may occur only after at least **6 consecutive eligible post-prereg CPI releases** have been captured without rule changes or data substitution.

At that review:

1. aggregate result after $10 total friction must remain positive;
2. both chronological halves of the forward sample must be non-negative;
3. the result must not depend on one event for more than 50% of total forward P&L;
4. all required bars must be present and timestamp-valid;
5. no execution-quality limitation may be hidden;
6. any rule modification creates a new version and resets the forward sample.

Passing these conditions permits **review only**, not automatic promotion.

## Safety boundary

The collector is intentionally isolated under `research/`.

It imports the existing read-only Polygon/Massive futures bar client and does not import broker, execution, risk, webhook, scheduler, strategy-routing, or order code.

Its output cannot authorize a trade.

**No proof, no promotion.**
