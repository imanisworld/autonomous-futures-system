# Six-micro daily time-series momentum — forward preregistration (2026-10-10)

trial_id: T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01

**PAPER LEDGER ONLY. NO DEMO ROUTE. NO LIVE ORDER. NO DEPLOY.** This
replaces `T-2026-10-10-prereg-equity-index-daily-tsmom-forward-2026-10-10-01`
before that trial collected or scored anything. Operator amendment AFS-0242,
2026-10-10. A later PASS is not a demo or live GO.

The rule text below is frozen as of the commit that adds this file and its
`PLANNED` ledger line. MNQ bars from **2026-06-29 through 2027-01-29** may be
used only as inputs and outcomes of this frozen rule. They must not be used
to change the lookback, the hold, the costs, the symbol list, or the entry
time.

## 1. Why this replaces the four-market text

The registration at `3fc8709` named MES, MNQ, M2K, and MYM. MYM is not in
`config/futures_contracts.py`. The operator amended the universe to the six
micros that module already prices: **MNQ, MES, M2K, MGC, MCL, MBT**.

Month-end, cross-market momentum, and VIX were tested in Strategy Lab and
failed. That campaign is 414 tests. Those three ideas are not this study and
are not rerun here.

MGC, MCL, and MBT still have no adopted continuous-roll schedule in
`sources/polygon_client.py`. This study does not invent one. Their paper
fills use the dated ticker on the supplied bar. A contract change without
the old contract's 09:30 open is not a fill.

The blinded MGC 4H wide forward study is a different trial. This ledger does
not read it.

## 2. Hypothesis

**H1.** From the first eligible session, one frozen rule on the six roots
has, at the single look in §7:

- at least 40 completed round-turns, counting every root together;
- net profit after the frozen costs;
- both chronological halves of those round-turns net positive;
- no one symbol contributing 60% or more of a positive basket net.

Fail any one of those and the study is dead. There is no second look.

## 3. The one variant

`variant_set.count` is 1. The variant id is `six-micro-daily-tsmom-60-20-rth-v1`.

| Piece | Frozen value |
|---|---|
| Symbols | MNQ, MES, M2K, MGC, MCL, MBT. MYM is excluded. |
| Signal | Sign of `close[t] / close[t-60] - 1` on completed session closes. Positive is long, negative is short, zero is flat. |
| Entry | Next eligible session's 09:30 America/New_York open. |
| Exit | The 09:30 open on which the signal from data through the prior close has flipped or is zero, or the 09:30 open of the 20th completed session after entry, whichever is first. |
| After any exit | Flat. That same open is not a new entry. |
| Size | 1 micro per symbol. One position per symbol. No adds. |
| Target / stop | None. No 2R target. No tick-cap stop. |
| Positions across symbols | Independent. Every completed round-turn on any of the six counts toward 40. |

## 4. Prices, costs, and contracts

- Dollar conversion uses `config.futures_contracts.tick_size` and `tick_value` only. No handwritten tick table.
- Commission is $1.48 per side. Slippage is 2 ticks adverse on the entry fill and 2 ticks adverse on the exit fill.
- MNQ, MES, and M2K must match `sources.polygon_client.front_contract(root, session_date, roll_days=8)`. Any other ticker is not a fill.
- MGC, MCL, and MBT use the dated ticker on the bar. There is no volume-front choice in this ledger.
- A contract change exits on the old contract's 09:30 open. If that price is missing, the position stays unresolved and that session is not a round-turn.
- Missing open or close: no fill and no substituted bar.

## 5. What counts

- First eligible entry session: **2026-10-12**.
- Sessions through **2026-10-09** may be stored as warm-up closes. They are not round-turns and they have no P&L.
- The single look is the session that completes the 40th round-turn across the six roots. Include every round-turn that completes on that session.
- If 40 round-turns have not completed by **2028-10-12**, append `ABORTED` / `NOT_RUN`.
- Halves: sort by exit session, then by MNQ, MES, M2K, MGC, MCL, MBT. The first `floor(n/2)` are half 1.
- Concentration, only when basket net is positive: each symbol's net divided by basket net must be strictly under 0.60.

## 6. Paper ledger

Collection code is `research/six_micro_daily_tsmom_paper.py`. It appends
paper round-turns to a caller-chosen journal under `logs/` (gitignored). It
does not import a broker, does not submit a demo order, and does not write
`docs/research-evidence/`. The VPS does not run it until a separate deploy
GO. This registration does not grant that GO.

## 7. Evidence boundary

The future single-look result belongs only at
`docs/research-evidence/T-2026-10-10-prereg-six-micro-daily-tsmom-forward-2026-10-10-01/`.
Do not create that directory in the registration commit.

## 8. Explicit non-actions

No historical score. No demo route. No live order. No inventory edit. No
deploy. No retune after the registration commit. No use of the MNQ seal
window to edit this rule. No rerun of the failed month-end, cross-market
momentum, or VIX tests.
