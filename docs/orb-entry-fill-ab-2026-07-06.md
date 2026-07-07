# ORB Entry-Fill A/B — Workstream A Phase 1

Scope: stored ORB formed setups rejected by `ENTRY_DETACHED_FROM_PRICE` in the June 24 to July 1 audit window, replayed with signal formation unchanged. Local replay candles are available through 2026-06-26, so later audit/thread cases are identified as out of local replay coverage rather than inferred.

| model | cases | filled | no-fill | no-data | W | L | net $ | exp $ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| market | 20 | 20 | 0 | 0 | 19 | 1 | 1221.25 | 61.06 |
| ioc_limit | 20 | 0 | 20 | 0 | 0 | 0 | 0.00 | n/a |
| stop_market | 20 | 0 | 20 | 0 | 0 | 0 | 0.00 | n/a |

## Cases

| ts | inst | setup | market | ioc_limit | stop_market |
|---|---|---|---|---|---|
| 2026-06-24T17:30:00+00:00 | MES | orb_breakout SHORT @ 7437.5 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-24T11:45:00+00:00 | MNQ | orb_breakout LONG @ 29806.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-24T12:00:00+00:00 | MNQ | orb_breakout LONG @ 29806.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-24T12:15:00+00:00 | MNQ | orb_breakout LONG @ 29806.25 | LOSS -26.00 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-25T07:15:00+00:00 | MNQ | orb_breakout LONG @ 29844.0 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-25T08:00:00+00:00 | MNQ | orb_breakout LONG @ 29844.0 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-25T14:00:00+00:00 | MNQ | orb_breakout SHORT @ 29745.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-25T14:15:00+00:00 | MNQ | orb_breakout SHORT @ 29745.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-25T14:45:00+00:00 | MNQ | orb_breakout SHORT @ 29745.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T02:15:00+00:00 | MES | orb_breakout SHORT @ 7435.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T02:30:00+00:00 | MES | orb_breakout SHORT @ 7435.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T02:45:00+00:00 | MES | orb_breakout SHORT @ 7435.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T03:30:00+00:00 | MES | orb_breakout SHORT @ 7435.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T14:00:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T14:15:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T14:45:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T15:00:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T15:15:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T15:30:00+00:00 | MES | orb_breakout LONG @ 7388.75 | WIN +73.75 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |
| 2026-06-26T13:15:00+00:00 | MNQ | orb_breakout SHORT @ 29745.25 | WIN +54.50 | CANCELLED/ENTRY_NOT_FILLED +0.00 | CANCELLED/ENTRY_BRACKET_INVALID_AT_FILL +0.00 |

Notes:
- `market` is the legacy assumed-fill replay model.
- `ioc_limit` uses MES=16 and MNQ=32 tolerance ticks, matching the live-box defaults.
- `stop_market` is one-next-bar causal: gap-through fills use the next bar open; missing or non-triggering next bar cancels.
- Coverage caveat: the June 29 to July 1 audit rows and the July 2 missed-items thread are part of the review scope, but this repo snapshot only has local ORB detached replay journals/candles through June 26. Those later rows are therefore not inferred into the A/B totals.
