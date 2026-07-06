# Operator Incident Note — Weekend -> Monday 2026-07-06

> **ADDENDUM (2026-07-06, post-root-cause — supersedes the "phantom" narrative below):**
> The Monday 10:45 ET MES case was **not a phantom and not a missed cancel path — it was a
> real, completed, WINNING trade that the reconciler erased from the journal.**
> The entry leg is a capped limit at plan + `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES=16`
> (cap 7577.75, not 7573.75); the market at ~7576.25 made it marketable and it
> **filled immediately** (`_entry_status="filled"` was correct). The position was
> legitimately open 14:45Z→~15:3xZ; the target 7588.75 filled between 15:30:14Z and
> 15:36:45Z (**demo account realized +$60.60**, verified via `/status/broker-account`;
> stop never touched). The 20-minute reconciler sweep then saw journal-open +
> broker-flat, and — because it never checked the persisted order ids for fills —
> booked `OUTCOME=CANCELLED $0 "phantom cleared"`, erasing the win from the journal,
> the daily P&L, and the go-live proof counter. Root cause = the reconciler treats
> "broker flat" as proof the entry never filled, and it can race the next 15m
> bar-driven resolve when an exit fills mid-interval. Fix: the reconciler now checks
> the entry order's fills first and books the real outcome through the order-id-scoped
> resolver; only zero-fill entries may be cleared as phantoms (`webhook/reconciler.py`).
> Corrections to the text below: "No fill occurred / No money moved" is **false**;
> Monday's true state was 1 filled winning trade misbooked as `CANCELLED`, so
> `trade_count=0 / today_pnl_dollars=0.0` reflects the *misbook*, not reality
> (broker: realized +$60.60). The three OTHER weekend attempts were genuinely
> unmarketable (cap below market) and cancelled cleanly — that part stands.

## Summary
The AFS box remained healthy from the Sunday, July 5, 2026 Globex open through Monday, July 6, 2026, but it recorded no counted filled trades during that window. The main operational issue was a half-completed 5-minute rollout: TradingView continued sending 5-minute alerts while the live decision path expected 15-minute inputs, creating sustained `TIMEFRAME_MISMATCH` rejects. Those alerts were blocked at the timeframe guard before normal strategy evaluation, so the impact was operational noise, rollout hygiene drift, and dashboard/status churn rather than degraded decision logic.

A second issue surfaced Monday morning: one MES order path appears to have missed the normal unfilled-cancel resolution flow and remained as a phantom journal-open position until reconciler cleanup. A third issue affected operator visibility: the daily health digest has been failing because protected status endpoints now return `401` to its unauthenticated localhost checks, and the digest currently has no Discord webhook configured, so even a fixed auth path would not restore delivery by itself.

## Confirmed State
- The box was healthy and running across Sunday, July 5, 2026 and Monday, July 6, 2026.
- Monday, July 6, 2026 ended with no counted filled trades: `trade_count=0`, `today_pnl_dollars=0.0`, and no open position at time of check.
- The system continued processing bars and journaling research evidence, including:
  - 61 `SHADOW_OUTCOME` rows on Sunday
  - 116 `SHADOW_OUTCOME` rows on Monday
- The live-box drift guard is correctly flagging a required mismatch:
  - `FIVE_MIN_FEED_ENABLED` runtime observed: `false`
  - `EXPECTED_PROOF_FIVE_MIN_FEED_ENABLED` expected: `true`
- Monday's no-trade/filter profile was dominated by:
  - `RANGE_BOUND`: 66
  - `REGIME_NOT_FULL`: 19
  - `CHOPPY`: 13
  - plus smaller counts for no-setup, entry-detached, and restricted-regime cases
- The 5-minute mismatch stream was active on Monday:
  - `TIMEFRAME_MISMATCH` count measured at 444 by `2026-07-06T18:20:00Z`
  - last seen at approximately `2026-07-06T18:20:00Z`
- The mismatch onset predates the weekend window:
  - first known onset: `2026-07-02T23:15Z`, immediately after the July 2 evening deploy

## Sunday, 2026-07-05
This section is operator-confirmed from privileged journal and service-log access, not public-status-only confirmation.

Sunday was quiet, but not signal-free. There was one executable signal:
- MNQ long @ 29956.5 at 22:45Z
- It ended as a clean unfilled/cancelled attempt
- This matters because "quiet" should not be read as "zero signals"

The shadow resolver remained active and continued collecting evidence in the background.

## Monday, 2026-07-06
Monday was an active box with no counted filled trades.

Three MES entry attempts and one overnight signal path were observed, but all relevant live attempts ended unfilled. The key Monday issue was the 10:45 ET MES phantom-clear case:

- A real OSO was placed at the broker
- Entry order id: `522911742209`
- Limit price: 7573.75
- Market was already around 7576
- Price never traded back down to the limit
- No fill occurred
- No money moved
- The journal carried a phantom open position until reconciler cleanup at `15:36:45Z`

This looks like a single-path miss, not a systemic failure of every unfilled IOC:
- the clean unfilled-cancel path worked on three other weekend attempts
- confirmed clean paths occurred on:
  - Sunday 22:45Z MNQ
  - Monday 04:00Z
  - Monday 14:30Z

## What Was Affected
The impact was operational and observational more than strategic:

- Operational noise / rollout hygiene
  - 5-minute alerts were rejected before decision evaluation
  - this created heavy `CONFIG_BLOCKED` noise and red-banner conditions
- Visibility
  - the health digest's broker-status check now gets `401`
  - the digest also has no Discord webhook configured
  - result: alerting has been failing silently since at least July 3
- Safety-path confidence
  - one Monday MES attempt did not complete the expected unfilled IOC resolution flow before reconciler cleanup
- Research continuity
  - shadow and resolver evidence collection continued normally

## Important Clarification on Trade Counts
There is no bug implied by seeing `TRADE` rows while `trade_count` remains `0`.

The system's intended semantics:
- `TRADE` decisions can still be journaled
- if they later resolve to `OUTCOME=CANCELLED` because the IOC limit never filled
- they do not increment counted trades

So the correct phrasing is:
- no counted filled trades
- not "no trading activity at all"
- and not "trade-count discrepancy"

**Caveat added post-root-cause:** a `CANCELLED` outcome is only trustworthy when the
entry genuinely never filled. The 10:45 ET `CANCELLED` row was a reconciler misbook
of a completed winning trade (see addendum), so Monday's `trade_count=0` is an
artifact of that bug, not a faithful count. After the reconciler fix, `CANCELLED`
rows again mean exactly "entry never filled, not counted."

## Root Causes and Follow-Up Work
### Box-config work
1. Resolve the 5-minute rollout mismatch
   - Either complete the rollout by intentionally enabling the 5-minute path and aligning the proof pin
   - Or revert the proof expectation and stop the stray 5-minute alerts at the source
2. Fix TradingView alert configuration
   - Restore a clean 15-minute decision feed if 5-minute inputs are not intended live
3. Restore digest visibility
   - Fix auth for protected localhost status reads used by the digest
   - Also configure a real Discord route/webhook for digest delivery

### Root-cause work
4. ~~Investigate the Monday phantom-open path~~ **DONE — see addendum.** The order
   DID fill (capped limit was marketable); the trade completed at its target; the
   reconciler erased the win because it never checks entry fills before clearing.
   Fix implemented in `webhook/reconciler.py` (completed-trade guard +
   `entry_order_filled` on the Tradovate broker).

### Documentation-only clarification
5. Document `CANCELLED`-not-counted behavior
   - Make it explicit in operator-facing notes that canceled/unfilled IOC attempts do not consume counted trades

## Expected Outcome After Fixes
Success does not mean "more trades." Success means:

- 5-minute alert spam stops
- the box evaluates only intended 15-minute live inputs
- unfilled IOC attempts always resolve cleanly and promptly
- phantom journal-open states do not persist until reconciler cleanup
- status visibility returns
- the health digest can authenticate successfully and post to Discord

## Acceptance Criteria
- `TIMEFRAME_MISMATCH` stops climbing once the rollout/alert issue is corrected
- New live alerts are evaluated as intended 15-minute inputs rather than blocked misconfigurations
- A deliberately unfilled IOC attempt resolves cleanly without phantom carry
- The reconciler never books `CANCELLED` over a position whose entry order has fills —
  completed trades resolve to their real WIN/LOSS outcome even when the exit fills
  between 15m bar resolves
- The health digest can read protected status endpoints successfully
- The health digest also posts to Discord successfully
- Operator notes and dashboards consistently describe `CANCELLED` attempts as not-counted behavior
