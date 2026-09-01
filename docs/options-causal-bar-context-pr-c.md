# PR C — causal options bar context

Phase 1 advisory and shadow infrastructure for the standalone options scanner.
No broker execution, no paper auto-entry, no provider switch, no deployment
change. The lane is **off by default**; enabling it is a separate operator
decision.

## Why this exists

Every retained scheduled scan in the options journal recorded `UNKNOWN`
direction and score 0, because the scheduled path supplied a snapshot price
and nothing else: no VWAP, no EMA20, no candle structure. The scanner was not
disagreeing with the market, it was blind to it.

The 2026-09-01 VPS evidence pass established that the existing consolidated
equity bar entitlement can supply the missing structure, and measured exactly
how that provider fails. This PR encodes those measurements.

## Data policy

| Item | Rule |
| --- | --- |
| Source | Consolidated tape (`feed=sip`), `adjustment=raw`. Phase 1 advisory and shadow only. |
| Fallback | None. A single-venue feed is refused for setup decisions. |
| Lag buffer | `information_cutoff = now − OPTIONS_SIP_DELAY_BUFFER_SECONDS` (default 960s = 16 min). |
| Completed bars | Keep a bar only when `start + timeframe ≤ information_cutoff`. |
| Session authority | The market calendar. Never inferred from the presence of bars. |
| 30m | Native bars, filtered to the calendar-defined regular session. |
| 1h | Rebuilt from session-aligned 30m pairs. Native hourly bars are never used. |
| Daily | Rebuilt from completed regular sessions. The vendor daily bar is not used for Strat. |
| VWAP | Cumulative `Σ(vw·v)/Σv` over regular-session bars, reset each session. |
| EMA20 | Completed regular-session closes only; fewer than 20 bars is unavailable, not a smaller EMA. |
| SPY / QQQ | Identical rules to the scanned ticker; both required. |
| Failure | Any missing, stale, incomplete, wrong-session or entitlement-blocked input yields WAIT with a named reason. |

### Why not the fresher single-venue feed

Measured over 65 sessions and 2,532 thirty-minute bars for AAPL, SPY and QQQ:
average prices agree (EMA20 within 0.1 basis points), but Strat bar-type
classification and prior-high/low break flags differ on **2.6% of bars** and
in **17–35% of sessions**, because those are threshold comparisons rather than
averages. One AAPL bar understated the true session high by about $1.00. A
16-minute lag costs nothing when measuring whether setups have edge; a 2.6%
classification error corrupts the measurement itself.

## Provider behaviours defended against

Each of these returns HTTP 200 and fails silently:

1. **Omitting `end`** clamps to the entitlement boundary and returns data of
   undefined recency. `end` is mandatory; a missing cutoff raises.
2. **An unknown symbol** is omitted from the response with no error field. Any
   requested symbol that comes back empty raises `missing_symbol`.
3. **`limit` is shared across symbols** and fills them one at a time, so a
   single page can contain one symbol and nothing of the others. Pagination
   runs to token exhaustion and raises `pagination_truncated` if it cannot.

The entitlement boundary itself is exactly 15 minutes and fails the *whole*
multi-symbol request with HTTP 403, which is the one loud failure mode.

## Configuration

```env
OPTIONS_BAR_CONTEXT_ENABLED=false      # the single switch; default off
OPTIONS_BAR_CONTEXT_FEED=sip           # anything else is refused for setups
OPTIONS_BAR_CONTEXT_TIMEFRAME=30Min
OPTIONS_BAR_CONTEXT_LOOKBACK_DAYS=10
OPTIONS_SIP_DELAY_BUFFER_SECONDS=960   # 16 min: proven boundary + 1 min skew
ALPACA_ENDPOINT=https://paper-api.alpaca.markets/v2   # calendar host
```

`ALPACA_ENDPOINT` may or may not already end in `/v2`; both spellings resolve
to the same calendar URL.

With `OPTIONS_BAR_CONTEXT_ENABLED=false`, or with credentials absent, no
builder is constructed and the scanner behaves exactly as it did before.

## Suppression reasons

Structural failures are named rather than collapsed into
`score_below_threshold`:

`missing_inputs:vwap` · `missing_inputs:ema20` · `insufficient_history` ·
`stale_market_data` · `missing_bars` · `no_completed_bars` · `no_session_bars` ·
`no_session` · `session_not_started` · `provider_entitlement` ·
`missing_symbol:<sym>` · `pagination_truncated` · `provider_unavailable` ·
`provider_malformed` · `feed_not_consolidated` · `missing_context:spy` ·
`missing_context:qqq` · `calendar_unavailable` · `calendar_malformed`

## Telemetry recorded per scan

Feed identity, request as-of, information cutoff, configured delay buffer,
timeframe, session date and open/close, early-close flag, completed bar count,
latest completed bar start and close, VWAP, EMA20, prior candle high and low,
candle type, session-aligned hourly candle type, reconstructed daily candle
type, and SPY/QQQ availability.

## First-live-session acceptance check

Documentation only. Nothing here changes runtime behaviour, and none of it is
automated. Before trusting shadow output after an eventual deployment, watch
one full regular session and confirm:

- [ ] Actual bar availability lag during regular hours. This is the one
      unmeasured item: all evidence probing happened after the close.
- [ ] The configured buffer stays safely beyond the observed lag. Raise
      `OPTIONS_SIP_DELAY_BUFFER_SECONDS` if it does not.
- [ ] No bar enters context whose close is later than the information cutoff.
- [ ] The session calendar matches the live session, including the open and
      close actually observed.
- [ ] AAPL, SPY and QQQ bars all arrive; no symbol is silently absent.
- [ ] VWAP populates and resets at the session open.
- [ ] EMA20 populates and is not seeded from a short series.
- [ ] 30m context populates.
- [ ] Reconstructed session-aligned 1h context populates, and the opening
      candle carries no pre-market range.
- [ ] Missing or stale data produces WAIT with a named reason, never a score.

Expect no context in the first part of the session: with a 30-minute timeframe
and a 16-minute buffer, the first completed regular-session bar is only usable
from roughly 10:16 ET. Scans before that correctly report
`session_not_started` or `no_completed_bars`.

## Deliberately out of scope

- The hardcoded $1,000 aggregate-risk default. Known unapproved policy,
  separate cleanup, untouched here and not to be read as validated.
- The legacy GEX and Signa-pivot substitution paths outside the code this PR
  touches. This PR introduces no gamma context and depends on none; real GEX
  remains optional enrichment and its absence stays explicit.
- Three stale systemd timers on the box (`ibkr-watchdog`, `daily-digest`,
  `calendar-sync`). **FOLLOW-UP OUTSIDE PR C.** `calendar-sync` is the one
  that matters: it runs `git pull` and `systemctl restart futures-bot`, and
  has been failing at the `git pull` step since 2026-08-01.
