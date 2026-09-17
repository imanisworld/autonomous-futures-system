# C14 Holiday / Daily-Session Anchor Proof Plan — 2026-09-17

**Status:** CLOSED 2026-09-17 — verdict **`C14 FIX PROVEN`** (see *Outcome* at the end). VWAP remains `NOT_ADMITTED`. The plan below is kept verbatim as the pre-registered procedure.

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
## Outcome — 2026-09-17: `C14 FIX PROVEN`

- **Evidence:** operator-captured TradingView Pine diagnostic exports (MES1! and MNQ1!, 5m + 15m,
  2026-06-16 → 2026-09-17, per-bar `time("D")`, `time_tradingday`, `ta.vwap(hlc3)`, HOD, LOD).
  H1 held: Pine daily identity = the CME **trade date**. Labor Day (Mon 09-07 18:00 reopen → still
  09-08), Juneteenth (Fri 06-19 → Sun 06-21 18:00 no reset) and observed Independence Day
  (Fri 07-03 → Sun 07-05 18:00 no reset) are all explained by one rule with no date-specific branch;
  every ordinary weekday/Sunday boundary is unchanged. H2 held: VWAP and HOD/LOD reset on the same
  identity. H3 held: ordinary-session behaviour identical.
- **Rule:** `cme_trading_day(ts, instrument)` = ET date (+1 at/after 18:00 ET) advanced to the first
  weekday that is a CME equity-index trade date (rule-generated non-trade dates; Good Friday
  deliberately excluded). 0 mismatches vs Pine `time_tradingday` on 76,541 export rows.
- **Implementation:** PR #646, merged `bfcfc5209a8b345d980e758352b34e98f074e704` (reviewed head
  `7a702d14d13658e8298f1f23b04528d93e922a33`). Replay converters only; calendar applied to
  `MES/MNQ/M2K` only, other products keep the mechanical key; fixtures in
  `tests/fixtures/c14_pine_daily_identity/`, tests in `tests/test_c14_pine_daily_identity.py`.
- **Not done / still open:** frozen P-REPLAY corpus not rebuilt and P3 VWAP/HOD-LOD parity not rerun
  (needs its own go); `context/location_context._trading_day` (live, observation-only) still
  mechanical — fenced in `docs/c14-live-replay-daily-identity-fence-2026-09-17.md`; un-fixtured
  holidays (MLK, Presidents', Memorial, Thanksgiving, Christmas, New Year's) and unscheduled closures
  remain an evidence limitation. VWAP admission is unchanged (`NOT_ADMITTED`).
