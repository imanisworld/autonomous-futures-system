# Post-fill execution audit — 2026-07-15

## Verdict

DEMO ONLY. Approve the post-fill validation fix for Tradovate demo deployment. Real-money execution remains disabled.

## Three-trade autopsy

All times are UTC. Commission, MAE, and MFE are missing from the production journals and are not inferred.

| Signal / decision | Strategy | Instrument | Requested / actual | Slippage | Stop / target | Planned risk / reward / R:R | Actual risk / reward / R:R | Old limit | Decision | Result |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 07-14 08:15 / 08:30:03 | orb_reclaim LONG | MNQ | 29603.50 / 29610.50 | 7.00 pts / 28 ticks | 29583.50 / 29653.50 | 20.00 / 50.00 / 2.50 | 27.00 / 43.00 / 1.59 | 32 ticks; not violated | FLATTEN — actual R:R below 2.0 | STOP, -$54.00 |
| 07-15 01:15 / 01:30:03 | pdh_reclaim LONG | MNQ | 29922.50 / 29923.25 | 0.75 pts / 3 ticks | 29915.50 / 29938.00 | 7.00 / 15.50 / 2.21 | 7.75 / 14.75 / 1.90 | 32 ticks; not violated | FLATTEN — actual R:R below 2.0 | STOP, -$15.50 |
| 07-15 09:00 / 09:15:18 | orb_reclaim LONG | MES | 7609.50 / 7609.75 | 0.25 pts / 1 tick | 7603.50 / 7624.50 | 6.00 / 15.00 / 2.50 | 6.25 / 14.75 / 2.36 | 16 ticks; not violated | ACCEPT | STOP, -$31.25 |

The first trade's submitted limit cap was 29611.50 under the old 32-tick rule. The preventive R:R cap is now 29606.75. The second trade's old cap was 29930.50; its R:R-preserving cap is 29923.00. The MES cap tightens from 7613.50 to 7610.50 and still permits its actual 7609.75 fill.

### Canceled MNQ order

- Signal / decision: 2026-07-14 15:00 / 15:15:04
- Strategy: orb_reclaim LONG
- Plan: entry 29815.50, stop 29795.50, target 29865.50, planned R:R 2.50
- Submitted type: Limit IOC; the exact submitted price is not journaled. The release/config formula derives 29823.50 under the old 32-tick rule.
- Outcome: canceled unfilled after 2.193 seconds; `NO_FILL_LIMIT_TOO_PASSIVE`; $0.00.
- Actual fill, slippage, actual risk/R:R, MAE, and MFE: missing/not applicable.
- Corrected R:R-preserving cap: 29818.75. The order would still be accepted only if filled at or better than that price.

## Root cause and execution path

1. `webhook.runner.process_alert` constructs the setup, rounds the requested bracket, and approves risk using the requested entry.
2. `TradovateBroker.execute_bracket` submits one atomic `placeOSO` request. Stop and target are structure-anchored and are therefore submitted before the fill and calculated from the requested entry.
3. Before this fix, the adapter created its local `Position` and returned `Fill(OPEN)` using `order.entry`, not the broker's actual entry fill.
4. `webhook.runner.process_alert` only replaced the requested entry with an actual fill for the special proof-paper path. Normal Tradovate trades journaled the requested entry as the open position.
5. `TradovateBroker.resolve_position` eventually recovered the actual entry from `/fill/list` when the trade closed, which corrected final P&L but was too late to protect entry economics.

The incorrect assumption was therefore present at both the broker-open boundary and normal open-position journaling: requested entry was treated as actual until resolution.

## Execution model

The audited strategies are all `anchored_structure`:

- orb_reclaim
- orb_breakout
- pdh_reclaim
- pdl_reclaim
- vwap_hold
- vwap_reclaim

Their stops and targets remain fixed to the approved structure. The fix never widens a stop or moves a target farther to manufacture R:R. No audited strategy currently uses a fill-relative bracket model.

## Fix

- Retrieve the actual entry fill by exact Tradovate entry order ID, quantity-weighted across partial fills.
- Apply one shared post-fill formula to runtime and explicit replay/parity studies.
- Recalculate actual stop distance, reward distance, R:R, dollar risk, adverse slippage, direction validity, and tick-grid validity.
- Keep a preventive IOC cap that cannot permit a fill below the configured minimum R:R.
- Keep protective OSO children active atomically through entry. The adapter cannot know an actual fill before submission without creating a naked-entry window, so it does not use an unprotected entry-first model.
- If validation fails, liquidate first while protection remains active, require a liquidation order ID, cancel remaining children, and verify the broker is flat.
- If liquidation or flat verification fails, retain an assumed-open local position, alert visibly, and block further entries.
- Journal requested entry, actual entry, every check, failed checks, liquidation evidence, realized flatten P&L when available, and final flat state.
- Surface the latest audit at `/status/today.post_fill_execution`.

## Replay comparison

Exact historical counterfactuals:

- First MNQ: old behavior held to stop for -$54.00; corrected behavior flattens immediately. Exact corrected P&L is missing because no historical immediate-flatten fill exists.
- Second MNQ: old behavior held to stop for -$15.50; corrected behavior flattens immediately. Exact corrected P&L is missing for the same reason.
- MES: accepted under both models; stop/target unchanged; P&L remains -$31.25 before unrecorded commission.
- Canceled MNQ: remains canceled with $0.00.

Broader same-day journal pairing, 30 externally executed trades:

- Accept: 9
- Flatten immediately: 21
- Rejected old winners: 7
- Rejected old losses: 14
- Old P&L attached to accepted trades: +$43.25
- Old terminal P&L attached to rejected trades: -$48.00
- Exact corrected expectancy and drawdown: missing because immediate-flatten prices and commissions were not recorded historically.

The broader result is a classification audit, not a fabricated P&L backtest. The reproducible reader is `scripts/post_fill_execution_audit.py`.

## Tests

- Focused execution/webhook/status suite: 283 passed.
- Full repository suite: 3,252 passed.
- Coverage includes requested-versus-actual separation, R:R, risk, slippage, geometry, tick sizes, long/short, MNQ/MES, controlled flatten, required liquidation identity, unconfirmed-flatten visibility, accepted brackets, shared runtime/replay math, proof isolation, and the existing real-money guards.

## Deployment and live verification

Pending at the time this report was written. Required sequence: confirm demo flat/no working orders, build and deploy one release, restart once, verify no restart-generated order, verify `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, demo execution active, and observe the next naturally occurring audited demo fill.
