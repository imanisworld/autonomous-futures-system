# C14 — Pine daily-identity fixtures (MES1! / MNQ1!, 15m)

Source: TradingView Desktop chart exports of the C14 diagnostic Pine study, captured
2026-09-17 by the operator (`CME_MINI_MES1!, 15.csv`, `CME_MINI_MNQ1!, 15 (2).csv`;
chart timezone America/New_York, `time` column is the bar OPEN in ET with offset).
Rows are copied verbatim for the listed windows; nothing is synthesised or edited.

Per-bar Pine fields (as emitted by the study on the same bar):

| column | Pine source |
|---|---|
| `daily_time` | `time("D")` — daily-bar identity; changes ⇒ new daily bar |
| `time_tradingday` | `time_tradingday` — exchange trade date (UTC midnight, ms) |
| `native_vwap` | `ta.vwap(hlc3)` (unanchored, session-reset by the exchange session) |
| `hod` / `lod` | running daily high / low, reset on `ta.change(time("D"))` |

Windows (ET, 15m bars, each starts on a Pine daily boundary so replay state is fully determined):

| window | span | what it proves |
|---|---|---|
| `labor_day` | Thu 09-03 18:00 → Wed 09-09 17:00 | Sun 09-06 18:00 RESET, Mon 09-07 (Labor Day) 18:00 **NO RESET**, Tue 09-08 18:00 RESET |
| `sunday_control` | Thu 09-10 18:00 → Tue 09-15 17:00 | ordinary Sun 09-13 18:00 RESET and weekday Mon 09-14 18:00 RESET |
| `independence_day` | Wed 07-01 18:00 → Tue 07-07 17:00 | Thu 07-02 18:00 RESET (→ trade date Mon 07-06), Fri 07-03 observed holiday, Sun 07-05 18:00 **NO RESET**, Mon 07-06 18:00 RESET |
| `juneteenth` (MES only; the MNQ export starts mid-day) | Wed 06-17 18:00 → Tue 06-23 17:00 | same shape as `independence_day` for Fri 06-19 |

Bar gaps inside the windows are the exchange halts only (17:00→18:00 daily, Fri 17:00→Sun 18:00,
holiday 13:00→18:00 / →Sun 18:00); no feed holes.
