# C14 Pine Boundary Fixture Capture Protocol — 2026-09-17

**Status:** COMPLETED 2026-09-17 — fixtures captured and the candidate rule reached `PROVEN_FOR_IMPLEMENTATION_TEST`; implemented in PR #646 (see *Outcome* at the end). The protocol below is kept verbatim.

This protocol supplements `docs/c14-holiday-session-anchor-proof-plan-2026-09-17.md`. Its purpose is to make the remaining C14 evidence collection mechanical and non-interpretive.

## Question being tested

Candidate rule under test:

> Replay daily-session identity should follow the exchange/trading-feed trade-date identity used by TradingView/Pine daily bars, rather than declaring a new day solely because ET crosses 18:00.

The fixture run must be capable of **rejecting** that rule. It is not allowed to reinterpret a mismatch after seeing the result.

## Instruments and chart settings

Run the same fixture on **MNQ1! and MES1!** using the same TradingView data source and 15-minute chart convention used by the live system. Record the exact symbol string, exchange prefix if shown, chart timezone and fixture version with every export.

Do not mix continuous symbols, dated contracts or chart timezones inside one comparison table without explicitly separating them.

## Mandatory cases

Capture at least these five classes before any C14 code change:

1. **Ordinary weekday control** — a normal 18:00 ET reopen with no exchange holiday adjacent.
2. **Ordinary weekend control** — the normal Sunday reopen into a non-holiday Monday trade date.
3. **Known Labor Day case** — the 2026 transition already associated with the September 8 Pine/replay divergence.
4. **Independent full-holiday case** — one different CME equity-index futures holiday with observable bars on both sides of the relevant reopen/boundary.
5. **Early-close case** — one documented early-close trading day with observable adjacent bars.

If a selected case has missing feed bars around the proposed boundary, classify it `C16_GAP_CONTAMINATED` and replace it with another case for the C14 rule test. Do not use a data gap to prove or disprove C14.

## Exact Pine-side fields

For every 15-minute bar in a capture window beginning at least 90 minutes before and ending at least 120 minutes after each candidate boundary, export or visibly record:

- symbol / exchange identifier;
- bar `time`;
- bar `time_close`;
- OHLC;
- `time_tradingday`;
- `timeframe.change("1D")`;
- current daily-bar `time("1D")` or equivalent daily bar identity, if available in the fixture;
- unanchored/session VWAP value used by the original comparison;
- Boolean `vwap_reset_here` determined from the fixture itself, not inferred later;
- current HOD;
- current LOD;
- Boolean `hod_lod_reset_here`;
- chart timezone and syminfo timezone;
- a monotonic fixture row number.

The fixture must record raw timestamps, not only formatted local-clock strings.

## Boundary-row rule

For each case identify exactly one pair:

- `B_PRE`: final available 15-minute bar before the candidate replay reset;
- `B_POST`: first available 15-minute bar after it.

Also retain all surrounding bars in the capture window. The pair is an index into the raw capture, not a replacement for the raw evidence.

## Replay-side fields

For the same timestamps, derive without reading outcomes:

- replay bar timestamp and OHLC;
- existing replay day key / `detect_day_boundaries()` result;
- whether existing replay logic resets daily VWAP state at `B_POST`;
- whether existing replay logic resets HOD/LOD at `B_POST`;
- proposed exchange-trade-date key, if one can be computed causally from the frozen exchange calendar;
- bar-gap classification;
- exchange holiday / early-close status from the pinned calendar source.

Do not change replay code while collecting this table.

## Frozen comparison table

Produce one row per instrument × case with these columns:

| Field | Meaning |
|---|---|
| instrument | MNQ or MES |
| case_id | stable fixture id |
| case_class | weekday / weekend / labor_day / independent_holiday / early_close |
| B_PRE | timestamp |
| B_POST | timestamp |
| gap_contaminated | yes/no |
| pine_time_tradingday_pre | raw value |
| pine_time_tradingday_post | raw value |
| pine_daily_change_post | `timeframe.change("1D")` at B_POST |
| pine_daily_identity_changed | yes/no |
| pine_vwap_reset_post | yes/no |
| pine_hod_lod_reset_post | yes/no |
| replay_existing_day_changed | yes/no |
| proposed_trade_date_changed | yes/no/unknown |
| exchange_calendar_class | ordinary / holiday / early close |
| classification | AGREE / C14_MISMATCH / C16_GAP_CONTAMINATED / INSUFFICIENT |

No column may be silently omitted because it contradicts the candidate rule.

## Acceptance logic

The candidate exchange-trade-date rule is `PROVEN_FOR_IMPLEMENTATION_TEST` only if all of the following hold:

- both instruments agree on every non-gap fixture;
- ordinary weekday behavior remains identical to current replay;
- ordinary weekend behavior remains identical to current replay;
- Labor Day is explained by Pine daily identity / `time_tradingday`, not by a date-specific exception;
- the independent full-holiday fixture is explained by the same rule;
- the early-close fixture is explained by the same rule or demonstrates that no special boundary is required;
- Pine VWAP reset and Pine HOD/LOD reset are consistent with the same daily identity on every usable fixture;
- the proposed day key can be computed causally at bar time from exchange-calendar/session information;
- zero accepted fixture requires a symbol-specific or holiday-name-specific branch.

If any non-gap fixture contradicts the rule, verdict is `C14 ROOT CAUSE PROVEN — GENERAL RULE NOT YET PROVEN` and no implementation begins.

If required Pine fields cannot be recovered for at least the two controls, Labor Day and one independent holiday, verdict is `C14 BLOCKED — INSUFFICIENT EXTERNAL SESSION EVIDENCE`.

## Required evidence preservation

Preserve:

- raw Pine fixture export or screenshots/source artifact;
- fixture Pine source hash/version;
- TradingView symbol/timezone settings;
- replay-side extraction artifact;
- pinned exchange-calendar source/version or retrieval date;
- machine-readable comparison JSON/CSV if produced;
- human-readable report containing the frozen table above.

Do not overwrite the original Labor Day evidence or prior parity artifacts.

## What a successful proof authorizes

Only a successful fixture proof authorizes a **separate offline implementation PR** for a shared `trading_day_key(ts, product)`-style helper plus regression tests. It does not authorize strategy admission, R5, deployment or live execution.

## Outcome — 2026-09-17

| case class | fixture | result |
|---|---|---|
| ordinary weekday control | every Mon–Thu 18:00 ET in 2026-06-16 → 09-17 (both instruments) | AGREE (reset) |
| ordinary weekend control | every non-holiday Sunday 18:00 ET in the window, incl. 09-13 | AGREE (reset) |
| known Labor Day case | Sun 09-06 18:00 reset, **Mon 09-07 18:00 no reset**, Tue 09-08 18:00 reset | AGREE — explained by `time_tradingday`, no date-specific exception |
| independent full-holiday case | Juneteenth Fri 06-19 (MES) and observed Independence Day Fri 07-03 (both): Thu 18:00 reset, **Sun 18:00 no reset**, Mon 18:00 reset | AGREE — same rule |
| early-close case | the 13:00 ET holiday halts on 06-19 / 07-03 / 09-07 sit inside the holiday sessions above; no separate early-close-only trade date fell in the export window | no special boundary required |

Both instruments agree on every non-gap fixture; the only bar gaps are exchange halts (no `C16_GAP_CONTAMINATED`
rows). Machine-readable comparison: `tests/test_c14_pine_daily_identity.py` against
`tests/fixtures/c14_pine_daily_identity/` (verbatim export windows; provenance in its README).
Implementation and residuals: `docs/c14-holiday-session-anchor-proof-plan-2026-09-17.md` → *Outcome*.
