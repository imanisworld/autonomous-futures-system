# MES 1-2-2 full-engine proof: root cause of the max_drawdown rejections (2026-09-08)

**Verdict: EVIDENCE ONLY. No runtime, config, risk, strategy, or replay change.**

## Question

On current code the isolated #337 reproduction pass rejects 14 of the 33 canonical MES `strat_122`
rows (every row from 2026-03-13) with `max_drawdown` ("Account drawdown 22.0% exceeds max 20.0% from
peak $1,500.00"). PR #373 reproduced 33/33 on 2026-07-28. What changed?

## Answer in one line

**PR #464 (2026-09-05).** Before it, replay carried the previous day's New York ORB into Asian-session
bars; the first trade of the day set the engine's "ORB break played" flag against that stale range and
every later 2-1-2 / 1-2-2 candidate that day was silently skipped. #464 makes the ORB `undefined`
outside New York (live and replay alike), so those candidates now fire. They lose $606.25, the carried
paper balance crosses the gate's $1,200 floor on 2026-03-09, and the absorbing gate blocks every later
`strat_122` row.

## What is NOT the cause

| suspect | finding |
|---|---|
| `risk_engine._check_max_drawdown` | byte-identical to #373 head `d06d885b` |
| `max_drawdown_percent` | 0.20 at #373 and at HEAD |
| balance carry across day files | present since 2026-05-31, used identically by #373's own harness |
| `.env` | proof results byte-identical with and without it |
| #423 (2-1-2 reversal identity), #472 (5m routing), #508 (PaperBroker) | bisect: extra-trade count unchanged across each |

Note the gate's peak: `account_peak_balance` is never set on the replay path, so peak is
`max(balance, starting_balance=1500)`. In replay the rule is an absolute floor at $1,200, and blocked
entries cannot move the balance, so once crossed it never releases.

## Evidence chain

1. **Gate off (max_drawdown_percent=0), isolated pass:** all 33 canonical `strat_122` rows found,
   33/33 identical result and P&L. The `strat_122` trades themselves are unchanged.
2. **Equity path vs the #337 canonical MES rows (strat_212 + strat_122):** 258 canonical rows all
   matched; 250 identical P&L, 8 different (+$138.75 net); **39 current-only trades (36 strat_212,
   3 strat_122) net −$606.25.** Remove only those 39 and the balance never drops below $1,325.
   Scaling the six 2-contract fills to 1 contract only delays the crossing to 2026-06-29.
   The canonical path itself first drops below $1,200 on 2026-07-07, after the last canonical
   `strat_122` row (2026-06-19), which is why #373 passed.
3. **Bisect of the 39 extras, gate-off isolated pass at each commit:** 12 extras at `84dc739`
   (#423) and at `9201b36` (#452); 39 extras at `73bffb1` (#464) and at every later commit.
   First bad = **#464**.
4. **Hunk isolation:** #464 with only the six-line replay block (`elif candle.session != "new_york":
   orb_high/low = candle.high/low; orb_status = "undefined"`) removed reproduces the pre-#464
   population exactly (270 trades, 12 extras, 258/258 identical). The `previous_bar_high/low` fields
   #464 also added are not read by `strategy/strat_212_122.py`.
5. **Mechanism at the 27 bars that flip NO_TRADE → TRADE:** good run shows "No qualifying setup
   found" with an empty candidate list; 25/27 had an earlier same-UTC-day trade in the good run
   (asian 7, london 12, new_york 6). In `strategy/signal_engine.py`, a TRADE while `orb.status` is
   `above`/`below` sets `orb_break_long/short_played`, and later bars with the same status skip every
   strategy not in `_ORB_CONTINUATION_EXEMPT`, including `strat_212` and `strat_122`. The flag set
   by a stale Asian/London range also suppressed the New York candidates. Two bars (2025-09-17
   10:30Z, 2026-06-29 08:00Z) had no earlier trade and are left unexplained.

## Implication

The #337 canonical population, and everything built on it (#373's 16-trade / +$120 control, #514's
20-trade / +$522.50 counterfactual), was generated while a stale prior-day ORB range was suppressing
2-1-2 / 1-2-2 candidates after the first trade of each day. That suppression was a defect and #464
removed it in both live and replay. The proof harness therefore cannot reproduce `EXPECTED_CONTROL` or
`EXPECTED_TREATMENT` on current code, and fails closed as designed. Disabling the gate or restoring the
pre-#464 suppression would both be illegitimate controls. What to do with the MES 1-2-2 thread is a
separate decision.

A residual 12 extra trades (1 `strat_122`, net −$193.75) exist even before #464 relative to the #337
rows; they never reach the floor before July and were not root-caused.

## Files

- `scripts/mes_122_max_drawdown_root_cause_2026-09-08.json` (all numbers above, bisect table)
- `scripts/mes_122_drawdown_bisect_runner.py` (evidence tooling used for the bisect)
