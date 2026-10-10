# Equity-index daily time-series momentum — forward preregistration (2026-10-10)

trial_id: T-2026-10-10-prereg-equity-index-daily-tsmom-forward-2026-10-10-01

**RESEARCH / LOG ONLY. NO EXECUTION AUTHORITY.** This document freezes one
forward study. It places no orders, changes no strategy, risk rule, broker
path, collector, deployment, or Strategy Inventory row, and it authorizes no
historical score. A later PASS is not a demo or live GO.

Operator approval to register this exact rule: 2026-10-10. Nothing in the
windows below has been scored under this rule.

## 1. Why this exists (motivation, not evidence)

Intraday MNQ pattern searches through 2026-10-10 are closed. The standing
explanation is that a noise-sized stop and a 2R target do not match the
typical favorable excursion. Slow direction was the sign that kept appearing,
and a single-name daily-trend look was already parked because its holdout
profit was concentrated. That parked look is not this study.

This study asks one new question: on equity-index micros that already have a
causal quarterly roll chain, does the published 60-session sign of return,
entered at the next regular-session open and held in session-counts rather
than a 2R bracket, make money out of sample.

Related looks that are **not** this family and are **not** rescored here:

- Overnight long plus a 20-day filter, dead on clean data (AFS-0209 / AFS-0210).
- Daily trend on five micros, parked; holdout profit concentrated in gold
  (AFS-0214). Gold, crude, and bitcoin stay out.

## 2. Hypothesis

**H1.** From the first eligible session, one frozen rule on MES, MNQ, M2K,
and MYM has, at the single look in §7:

- at least 40 completed round-turns;
- net profit after the frozen costs;
- both chronological halves of those round-turns net positive;
- no one symbol contributing 60% or more of a positive basket net.

Fail any one of those and the study is dead. There is no second look and no
parameter change.

## 3. The one variant

`variant_set.count` is 1. The variant id is `eqidx-daily-tsmom-60-20-rth-v1`.

| Piece | Frozen value |
|---|---|
| Symbols | MES, MNQ, M2K, MYM. No other root. |
| Signal | Sign of `close[t] / close[t-60] - 1` on completed session closes. Positive is long, negative is short, zero is flat. |
| Entry | Next session's 09:30 America/New_York 1-minute open. |
| Exit | The 09:30 open on which the signal from data through the prior close has flipped or is zero, or the 09:30 open of the 20th completed session after entry, whichever is first. |
| After a time exit | Flat. The same open is not a new entry. The next entry can only be a later session. |
| Size | 1 micro per symbol. One position per symbol. No adds. |
| Target / stop | None. No 2R target. No 120-tick stop. No daily disaster stop. |
| Positions across symbols | Independent. A symbol does not wait on another symbol. |

The 20-session clock starts at the entry session and counts later completed
sessions for that symbol. A same-sign day does not reset it.

## 4. Session, calendar, and prices

- Time zone: `America/New_York`.
- Observation day: prior 18:00 ET through 17:00 ET, the same window as
  `research/prereg929_forward_corpus.obs_day_window`.
- A civil date in `context.cme_trading_day.cme_equity_index_non_trade_dates`
  is not a session for any of the four symbols, including MYM. Do not edit
  that set or `CME_EQUITY_INDEX_INSTRUMENTS` for this study.
- Session close price: close of the last 1-minute bar with a start strictly
  before 17:00 ET. On an early-close date (weekday day-after-Thanksgiving,
  December 24, or July 3, when that date is not a non-trade date), use the
  last 1-minute bar starting strictly before 13:00 ET.
- Entry and exit price: open of the first 1-minute bar starting at or after
  09:30 ET on that session date.
- Missing the required open bar or the required close bar makes that symbol
  flat for any action that needed the missing bar. Do not substitute a later
  bar, a daily aggregate, or another contract.
- Warm-up closes before the first eligible entry may be used only to form the
  60-session sign. They are not trades and they have no P&L.
- The signal for an entry on session D uses only closes of sessions strictly
  before D. The close of D-1 is known after that session ends; the fill is
  the next 09:30 open.

## 5. Contract and roll

- Source: Polygon 1-minute aggregates of the dated front contract.
- Front contract: `sources/polygon_client.front_contract(symbol, session_date, roll_days=8)`.
- Months are H/M/U/Z. No back-adjustment. No continuous vendor symbol.
- If the front contract for session D differs from the contract of the open
  position, exit that position at D's 09:30 open on the **old** contract.
  Do not open the new contract on that same open. The next entry, if the
  signal is still nonzero, is the following session's 09:30 open on the new
  front. A roll exit counts as a completed round-turn.

## 6. Costs and contract dollars

Commission is $1.48 per side, so $2.96 per round-turn. Slippage is 2 ticks
adverse to the position on the entry fill and 2 ticks adverse on the exit
fill.

Dollar conversion for this study only:

| Root | Tick size | Dollars per tick | Source |
|---|---|---|---|
| MES | 0.25 | 1.25 | `config/futures_contracts.py` |
| MNQ | 0.25 | 0.50 | `config/futures_contracts.py` |
| M2K | 0.10 | 0.50 | `config/futures_contracts.py` |
| MYM | 1.00 | 0.50 | CME micro Dow. **Not** in `config/futures_contracts.py` at registration. |

Do not add MYM to the runtime contract table as part of this registration.
The offline scorer uses the table above. If a later commit puts a different
MYM tick size or tick value into `config/futures_contracts.py`, this table
still wins for this trial.

P&L for a long round-turn is
`(exit_fill - entry_fill) / tick_size * dollars_per_tick - 2.96`.
Short is the mirror. `entry_fill` and `exit_fill` already include the 2-tick
adverse slippage.

## 7. What counts, and the single look

- First eligible entry session: **2026-10-12**.
- Sessions through **2026-10-09** are ineligible and are never backfilled.
- No entry before 2026-10-12 is a trade. Warm-up is not a result.
- A round-turn counts only when both its entry and its exit are on eligible
  sessions.
- The single look happens on the session that completes the 40th
  round-turn across the four symbols. Include every round-turn that completes
  on that session, even if the total finishes above 40.
- If 40 round-turns have not completed by the session of **2028-10-12**,
  append `ABORTED` / `NOT_RUN`. That deadline is not a pass and not a fail
  on P&L.
- Halves: sort completed round-turns by exit session, then by symbol order
  MES, MNQ, M2K, MYM. The first `floor(n/2)` are half 1. The rest are half 2.
- Concentration, evaluated only when basket net is positive: each symbol's
  net divided by basket net must be strictly under 0.60. A negative symbol
  net does not fail this check. If basket net is not positive, H1 has already
  failed on the net test.

There is no random-sign control in this trial. Adding one is a new prereg.

## 8. Evidence boundary

The future result, and only the future result, belongs at
`docs/research-evidence/T-2026-10-10-prereg-equity-index-daily-tsmom-forward-2026-10-10-01/`.
Do not create that directory before the look. Do not write a `COMPLETED`
ledger line in the registration commit.

Registration ledger line is `PLANNED` only.

## 9. Explicit non-actions

No historical backtest of this rule. No fetch for the purpose of scoring.
No order, demo route, paper ledger, risk-cap change, inventory edit, or
deploy. No reuse of the closed ORB, VWAP, Strat, overnight, event, or 4HR
grids. No change to the 60-session window, the 20-session hold, the symbol
list, or the cost model after any forward fill is seen.
