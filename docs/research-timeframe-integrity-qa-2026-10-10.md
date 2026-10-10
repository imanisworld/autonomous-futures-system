# Timeframe construction QA for new futures research

**Source-only / offline. No trading or deployment authority.**

## Reproduced failure modes

The existing session resampling implementation `research/mgc_4h_wide_forward.py::resample_session` emits the final bucket whether or not it contains the required number of source candles. It also aggregates OHLC without retaining the originating dated contract per source bar, even though the upstream front selection can change at UTC midnight. The final approximately three hours of a normal 23-hour futures session therefore can be labeled `240m`; a switch of front contract during a 4-hour session bucket cannot be rejected by that existing resampler.

This is a **correctness concern requiring independent review**, not a claim that existing registered research P&L or an official strategy verdict must change. Do not read or rescore any sealed outcomes to investigate it.

## Isolated fix

`research/timeframe_integrity.py::confirmed_session_bars` is a standalone converter for **new research only**. It supports verified source 15-minute candles aggregated into 5/15/30/60/240/720-minute windows from declared 5m or 15m source candles, anchored at **18:00 America/New_York**. It requires dated-contract provenance, exact contiguous input at every declared 5- or 15-minute slot, a complete window, valid OHLCV, unique increasing timestamps, and forbids contract mixing. Maintenance-hour rows are excluded. Each output supplies `ts`, `close_ts`, `session_start_ts`, `timeframe_minutes` and `ticker`. Partial/gap/roll buckets are counted and rejected, not interpolated.

Use `close_ts` (not `ts`) to prove a higher-timeframe signal was available to an order, and enter no earlier than an actual executable price *after* that time. Resampling alone does not establish causal entries or profitable fills.

**Important:** This does **not** overwrite, alter, or import into the frozen MGC forward evaluator, since changing its aggregation mid-trial would silently change the preregistered study identity. A separate independently reviewed correctness decision and new prospective study identity would be required for a scorer change. No runtime collectors or current strategy permissions are touched.

## Verification

`pytest -q tests/test_timeframe_integrity.py`

The 30 focused tests cover supported periods, closed-bar timestamps, New York DST shifts, UTC-vs-session alignment, missing source intervals, incomplete last buckets, contract rolls, invalid price/source metadata, and the daily maintenance break. Two additional diagnostics deliberately compare the legacy frozen resampler against the new, strict path on synthetic incomplete/cross-contract candles; no live study data is opened. GitHub exact-head full CI and independent review are required before relying on the new path.

This PR is **not** an edge discovery result or permission to trade. If the validation changes measured signals, the earlier unguarded result is not grandfathered into evidence.

**Daily bars** are deliberately unsupported by the generic clock-sized resampler: the regular CME Globex day lasts approximately 23 trading hours and holiday calendars can shorten it further. Treating 1D as an ordinary 1,440-minute bucket would be another false time-frame identity. A daily-bar contract needs a separate explicit exchange-session calendar and verification.
