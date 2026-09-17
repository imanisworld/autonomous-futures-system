# C14 Holiday / Daily-Session Anchor Proof Plan — 2026-09-17

**Status:** AUDIT / OFFLINE PROOF ONLY. VWAP remains `NOT_ADMITTED`. No runtime change, Pine change, tolerance change, strategy change, outcome read, R5 step, deploy, or restart is authorized.

## Proven starting fact

On ordinary sessions the repo's shared replay convention — one daily reset at the 18:00 ET CME equity-futures boundary — matches the observed Pine VWAP closely. On the 2026-09-08 trading day after the Labor Day holiday, however, both MNQ and MES diverged immediately despite no bar gap. Pine `ta.vwap` did not reset at the mechanical Monday 18:00 ET reopen, and Pine HOD/LOD showed the same daily-boundary behavior.

Therefore C14 is not a VWAP arithmetic defect. The defect class is:

> **Replay trading-day identity is derived from a mechanical 18:00 ET date rule, while TradingView/Pine daily-session identity can differ around exchange holiday transitions.**

## External documentation check

TradingView's current Pine documentation supports the **class** of this root cause:

- exchanges define a default session for every symbol;
- Pine time/session functions use the symbol's exchange session information when no explicit session is supplied;
- `time_tradingday` is specifically defined for overnight symbols: on intraday bars it identifies the trading day of the session the bar belongs to, even when the session opened on the prior calendar day;
- TradingView's time FAQ recommends `time_tradingday` when calendar calculations must remain correct across sessions that span days;
- `timeframe.change("1D")` detects the opening of a new TradingView daily bar, so it is a useful independent Pine-side boundary oracle;
- chart/session calculations therefore need not equal an arbitrary fixed wall-clock rollover.

Sources checked 2026-09-17:

- TradingView Pine Script, `Concepts / Time`: https://www.tradingview.com/pine-script-docs/concepts/time/
- TradingView Pine Script, `FAQ / Times, dates, and sessions`: https://www.tradingview.com/pine-script-docs/faq/times-dates-and-sessions/
- TradingView Pine Script, `Concepts / Sessions`: https://www.tradingview.com/pine-script-docs/concepts/sessions/
- CME holiday/trading-hours calendar: https://www.cmegroup.com/trading-hours.html

These documents still do **not** state a universal implementation rule for unanchored `ta.vwap`, nor do they prove that every Pine daily construct shares one exact holiday transition rule. Real boundary fixtures remain mandatory before code.

## Stronger candidate rule after the documentation check

The leading generalized hypothesis is now narrower than merely "holiday aware":

> **Replay daily-session identity should follow the exchange/trading-feed trade-date identity used by the TradingView daily bar, not `civil_date + fixed 18:00 ET rollover`.**

Why this fits the known Labor Day evidence:

- CME's 2026 calendar marks Labor Day as a holiday span covering September 6–8 and documents holiday/weekend handling in terms of the following business **trade date** rather than ordinary civil-date rollover.
- CME's own holiday-calendar explanation distinguishes the Sunday restart, the holiday early halt, and the Monday-night restart through Tuesday close as separate market sessions around a Monday holiday.
- The repo's observed TradingView/Pine data treated the relevant Monday-evening reopen as a continuation of the September 8 daily identity: neither Pine VWAP nor HOD/LOD reset there.
- A mechanical `18:00 ET => new day` rule cannot represent that case because it has no exchange trade-date/calendar state.

This is a **candidate generalized rule**, not yet an implementation authorization. The next fixture must directly record Pine `time_tradingday` and `timeframe.change("1D")` around the known Labor Day case and at least one additional holiday. If those Pine-side identities do not explain the observed VWAP/HOD/LOD boundary, reject this candidate rule rather than fitting the code to one holiday.

## Hypotheses

### H1 — exchange trade-date identity

TradingView's daily/session boundary follows the symbol's exchange/feed trading-day identity, not a bare local-clock 18:00 transition on every civil day.

### H2 — VWAP is only one consumer

If H1 is correct, the same corrected daily-session identity must explain VWAP, HOD/LOD and any other construct using Pine daily-session rollover. A fix that repairs only VWAP is insufficient.

### H3 — ordinary sessions must not change

Any accepted correction must reproduce the current ordinary-day behavior exactly. Holiday correctness may not be bought by changing normal-session definitions.

## Evidence required before code

Build a read-only boundary table from real Pine/TradingView evidence and the exchange calendar. Minimum cases:

1. ordinary weekday transition;
2. ordinary Sunday reopen / weekend transition;
3. the known 2026 Labor Day transition;
4. at least one additional full exchange holiday where the adjacent futures session is observable;
5. at least one early-close session if Pine daily identity differs or could plausibly differ there.

For every case record:

- last bar before the candidate boundary;
- first bar after it;
- Pine `time_tradingday`;
- Pine `timeframe.change("1D")` (or the exact equivalent daily-bar identity already emitted by the study fixture);
- whether `ta.vwap` resets;
- whether Pine HOD/LOD resets;
- replay `detect_day_boundaries()` result;
- bar-gap status;
- exchange session/calendar/trade-date status;
- agreement/disagreement classification.

One historical anomaly is not enough to infer a generalized rule.

## Acceptance criteria for the rule

A proposed shared daily-session rule is admissible only if:

- it explains all observed boundary cases without instrument-specific/date-specific exceptions;
- Pine `time_tradingday` / daily-bar identity agrees with the proposed replay day key on every accepted fixture;
- ordinary weekday and weekend behavior is unchanged;
- the Labor Day case changes in the direction Pine actually showed;
- VWAP and HOD/LOD use the same daily-session identity;
- missing-bar behavior remains separately classified as C16 and is not disguised as C14;
- the rule can be computed causally from exchange calendar/session facts available at the bar timestamp;
- deterministic replay is preserved.

If no generalized rule satisfies these criteria, C14 remains `BLOCKED` and VWAP stays `NOT_ADMITTED`.

## Implementation boundary after proof

Only after the rule is proven:

1. place the correction in a shared trading-day/session-anchor helper rather than a `vwap_*` strategy;
2. make replay/corpus daily constructs consume that shared helper;
3. do **not** add `if holiday == ...` one-off patches;
4. do not change VWAP arithmetic, thresholds, setup predicates, brackets, or tolerances;
5. add regression fixtures for ordinary weekday, weekend, known holiday, and additional verified holiday/early-close case;
6. rerun P3 VWAP/HOD/LOD parity and any affected replay parity before reconsidering admission.

A safe implementation shape, **only if the fixtures prove H1**, is an exchange-calendar-backed `trading_day_key(ts, product)` helper whose key corresponds to the same trade-date identity observed from Pine. The helper must not infer a new day solely because ET crossed 18:00.

## Required verdict after the proof run

Return exactly one:

- `C14 FIX PROVEN`
- `C14 ROOT CAUSE PROVEN — GENERAL RULE NOT YET PROVEN`
- `C14 BLOCKED — INSUFFICIENT EXTERNAL SESSION EVIDENCE`

No C14 result authorizes VWAP strategy promotion or R5.