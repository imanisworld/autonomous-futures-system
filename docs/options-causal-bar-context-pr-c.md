# PR C — causal options bar context

Phase 1 advisory and shadow infrastructure for the standalone options scanner.
No broker execution, no paper auto-entry, no provider switch, no deployment
change. The lane is **off by default**; enabling it is a separate operator
decision.

## Phase boundary — read this first

PR C supplies **causal structural context and mechanical setup observation**.
It does not supply a trade recommendation, and in its current state it cannot.

The shared strategy authority (`options_manager.scanner.scan_watchlist_strat_212`)
marks a 2-1-2 TRIGGERED only when entry, invalidation, targets, market context
and contract constraints are all proven. PR C derives the two mechanical
levels from the bars and supplies nothing else, so a confirmed 2-1-2 is
recorded as `INVALID / missing_target_1` with `setup_sequence_confirmed = true`
and remains **non-actionable**. That is intentional. It is the strategy layer
failing closed on inputs it was not given, which is what it is for.

**Do not loosen that gate to produce alerts.** The missing proof must arrive
through the canonical validation path — targets via the level finder, context
via the market validator, contracts via the contract validator — not by
relaxing what TRIGGERED means or by letting context, a caller-supplied
pattern, or a Signa grade stand in for it.

Delayed 30-minute consolidated bars also make this **Phase 1 shadow and
advisory evidence**, not a timely execution feed. The first usable bar of a
session arrives around 10:16 ET.

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
| Session completeness | Every calendar session inside the request window must be whole. A missing day, or one missing bar in a prior session, fails the context closed. |
| 30m | Native bars, filtered to the calendar-defined regular session. The only accepted canonical timeframe; any other configured value is refused. |
| 1h | Rebuilt from session-aligned 30m pairs. Native hourly bars are never used. |
| Daily | Rebuilt from completed regular sessions. The vendor daily bar is not used for Strat. |
| VWAP | Cumulative `Σ(vw·v)/Σv` over regular-session bars, reset each session. |
| EMA20 | Completed regular-session closes only; fewer than 20 bars is unavailable, not a smaller EMA. |
| SPY / QQQ | Identical rules to the scanned ticker; both required. |
| Setup authority | `options_manager.scanner.scan_watchlist_strat_212`. This lane classifies no setup of its own. |
| Actionability | TRIGGERED only. NO_TRADE, WATCH and INVALID all mean no alert. |
| Failure | Any missing, stale, incomplete, wrong-session or entitlement-blocked input yields WAIT with a named reason. |

### Why not the fresher single-venue feed

Measured over 65 sessions and 2,532 thirty-minute bars for AAPL, SPY and QQQ:
average prices agree (EMA20 within 0.1 basis points), but Strat bar-type
classification and prior-high/low break flags differ on **2.6% of bars** and
in **17–35% of sessions**, because those are threshold comparisons rather than
averages. One AAPL bar understated the true session high by about $1.00. A
16-minute lag costs nothing when measuring whether setups have edge; a 2.6%
classification error corrupts the measurement itself.

## Context is not a setup

The scorer credits any non-`N/A` `pattern` with +3, VWAP alignment with +2 and
trend alignment with +2, against a default alert threshold of 7. So if a bare
candle type were allowed to fill `pattern`, an ordinary 1, 2U, 2D or 3 candle
with price above VWAP and EMA20 would clear the threshold with no setup behind
it at all. It is not allowed to.

`candle_type`, `previous_candle_type` and `strat_sequence` are context and are
recorded under those names. `pattern` is filled only from a TRIGGERED verdict
of `options_manager.scanner.scan_watchlist_strat_212`, which is the same
authority the rest of the options system uses, reached through
`alert_ranker/setup_authority.py`. That module reimplements nothing: it
classifies the third bar back from a fourth, hands the strategy layer the two
mechanical levels that come straight out of the bars — the inside bar's high
and low — and returns the verdict unchanged.

TRIGGERED additionally requires proven targets, market context and contract
constraints. This lane supplies none of those and invents none of them, so a
genuine 2-1-2 reports as `INVALID / missing_target_1`, is recorded with
`setup_sequence_confirmed = true`, and cannot alert. Phase 1 is structurally
observe-only; that is a property of the wiring, not a convention.

Suppression reasons distinguish the cases:

| Verdict | Suppression reason |
| --- | --- |
| TRIGGERED | *(none — but unreachable in PR C)* |
| WATCH | `setup_forming` |
| Sequence real, proof incomplete | `setup_proof_incomplete:<reason_code>` |
| No sequence | `no_setup:<reason_code>` |

Only the 2-1-2 continuation is evaluated, because it is the only mechanical
setup the strategy layer implements. A 3-1-2 reports `no_setup:sequence_not_212`
rather than being approximated.

### Caller-supplied structure does not bypass the authority

A webhook may supply its own VWAP, EMA20 and pattern. With the lane off, that
path behaves exactly as it always did. With the lane on, those values keep
their precedence for scoring and display, but the setup authority is still
evaluated on the canonical bars and a generic alert still requires its
TRIGGERED verdict. Absent setup telemetry while the lane is on is not
permission: it fails closed as `setup_proof_missing`.

### The legacy Signa-only callout

`_maybe_send_candidate_alert` predates all of this: it needs only a Signa score
and grade, substitutes Signa pivots for gamma walls, defaults the regime to
`TRANSITION`, and posts to Discord independently of the scan's own decision.
A high Signa grade is not a setup, and a Phase 1 shadow campaign contaminated
by Signa-only callouts cannot measure the setups it exists to measure. While
the causal lane is on, that path fires only behind a TRIGGERED setup. The
function and its historical output are left intact for the later consolidation
pass.

## Every session used must be complete

The current session's gaps were always checked. Historical sessions are used
too — for EMA20, for previous-candle continuity, and for the reconstructed
daily candle — so they are held to the same bar. The calendar decides which
sessions must exist, which means a trading day that returned no bars at all is
still expected and still fails the check; a holiday is simply absent and is not
expected. A session that opened before the request window is excluded rather
than failed, because that truncation is ours.

The failing session is named in telemetry (`incomplete_session_date`,
`missing_bar_count`) so a provider gap is diagnosable rather than just fatal.

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
`provider_malformed` · `feed_not_consolidated` · `unsupported_timeframe` ·
`bar_context_unconfigured` · `missing_context:spy` · `missing_context:qqq` ·
`calendar_unavailable` · `calendar_malformed` · `setup_forming` ·
`setup_proof_incomplete:<reason_code>` · `no_setup:<reason_code>` ·
`setup_proof_missing`

`bar_context_unconfigured` is the enabled-but-broken case: the switch is on and
the builder could not be constructed, usually because credentials are missing.
It is reported rather than degraded into the intentional-OFF behaviour, and it
blocks alerts — an operator who asked for causal structure should not get blind
alerts because the wiring failed.

## Telemetry recorded per scan

Feed identity, request as-of, information cutoff, configured delay buffer,
timeframe, session date and open/close, early-close flag, completed bar count,
latest completed bar start and close, VWAP, EMA20, prior candle high and low,
candle type, previous candle type, Strat sequence, session-aligned hourly
candle type, reconstructed daily candle type, SPY/QQQ availability, the
incomplete session date and missing bar count when completeness failed, and the
setup verdict: status, reason code, direction, mechanical entry trigger,
invalidation, whether the sequence was confirmed, and the suppression reason.

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
- [ ] Prior sessions in the lookback window are complete; `missing_bars` with a
      named `incomplete_session_date` does not appear every scan.
- [ ] `setup_status` is populated, and no scan alerts without TRIGGERED.
- [ ] No Signa-only callout fires while the lane is on.

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
