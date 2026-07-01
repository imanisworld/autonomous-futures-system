# July 1, 2026 — Countertrend Signals and Phantom Fills

## What happened

- The live/demo engine approved four shorts: three MES `vwap_hold` signals and
  one MNQ `orb_rejection`.
- Tradovate confirmed no filled positions. All four journal-open positions were
  cleared about 20 minutes later by reconciliation as broker-flat phantoms.
- The day finished with zero counted fills and $0 realized P&L.

## Direction evidence

- Signa direction was `WAIT` on three approved shorts and missing on the fourth.
- The current Signa responses after the incident were:
  - QQQ: grade A, score 69, action `BUY`, normalized direction `WAIT`.
  - ES: grade A, score 95, action `HOLD`, normalized direction `WAIT`.
- The old Signa gate treated grade A/B as `PASS` without checking
  `daily_direction`; therefore `PASS` did not mean directional agreement.
- July 1 shadow observations counted 71 SHORT and 38 LONG candidates. At each
  of the four approved trade timestamps, the attached shadow candidates also
  pointed SHORT.
- Shadow candidates are observations only and currently have no resolved
  outcome ledger. Counts do not establish profitability.
- June 30 recorded five LONG shadow candidates. June 29 recorded none.
- TradingView screenshots show bullish daily structure and a broader 15-minute
  sequence of higher highs/higher lows. The engine elevated local 15-minute
  pullbacks (`trend=DOWN`, below VWAP) over the bullish higher-timeframe
  structure.

## Deployment findings

- The production repository is heavily dirty and divergent. Its Git HEAD cannot
  be treated as an accurate release identifier because changes have been copied
  directly into the working tree.
- Contrary to the initial diagnosis, last night's runner update was present and
  loaded. `webhook/runner.py` was updated at 00:47 UTC, followed by multiple
  service restarts.
- The remaining execution hole is the Tradovate entry-status `unknown` path:
  unreadable/read-lagged status can fall through as open, after which the
  reconciler discovers that the broker is flat.
- Order-placement/status INFO logs were suppressed, so the journal proves the
  four phantoms but cannot prove which broker response produced `unknown`.

## Changes deployed to demo

- `STRICT_DIRECTIONAL_ALIGNMENT=true`
- `BLOCK_RESTRICTED_REGIME=true`
- Missing, mixed, failed, or opposing HTF context now blocks entries.
- Daily `UP` blocks proposed SHORT entries; daily `DOWN` blocks proposed LONG
  entries.
- `RESTRICTED` regimes are observation-only in the demo deployment.
- Dashboard quality-gate status reports `HTF: strict (fail-closed)`.
- Real-money trading remains disabled.
- Tradovate reliability supervisor was `HEALTHY` and ready after restart.
- No open/phantom position remained after deployment.

Deployment backup on the host:

`/root/autonomous-futures-system/.deploy-bak/20260701-strict-direction`

Published implementation branch:

`codex/strict-direction-and-fill-safety`

Local verification:

- 99 focused decision/rule tests passed.
- 1,268 broader tests passed, 1 skipped, and 2 unrelated wall-clock-sensitive
  five-minute-feed tests were deselected.

## Required follow-up

1. Never journal `TRADE` until broker fill or position is positively confirmed.
2. Treat Tradovate status `unknown` as pending/unconfirmed, never open.
3. On confirmation timeout, cancel the OSO and journal `CANCELLED`/`REJECTED`.
4. Elevate order IDs and lifecycle transitions to durable audit records.
5. Separate Signa quality grade from directional agreement.
6. Persist Signa action and full daily/4H/1H/FTFC context in every decision.
7. Add a resolver and outcome ledger for structural shadow candidates.
8. Replace file-copy deployment with an immutable release manifest and refuse
   startup when the deployed source cannot be tied to an exact release.
