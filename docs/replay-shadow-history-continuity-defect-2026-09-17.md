# Replay shadow-history continuity defect — 2026-09-17

**Mode:** AUDIT / OFFLINE FIX ONLY. No runtime, deployment, risk, strategy, config, `.env`, broker, or R5/outcome change.

## Proven mismatch

Live records the current authoritative decision bar and calls `BarHistory.recent(instrument, 8)`. `BarHistory.recent` spans the current calendar-day file plus up to two prior calendar-day files (`lookback_days=3`). Therefore the first bars after a UTC day-file boundary can legitimately receive the prior file's tail.

Replay instead appends to `_research_bars` but calls `self._research_bars.clear()` at the start of every `ReplayEngine.run()` call. The structural-level R4 parity audit measured the effect: most firing misses for `impulse_first_pullback_observed` and `trend_consolidation_break_observed` occurred at replay day-file warm-up boundaries.

## Required repair

Persist the per-instrument maxlen-8 shadow history across sequential `run()` day files, while filtering it at read time to the same `BarHistory.recent(..., lookback_days=3)` calendar-day bound. This prevents both false day-boundary resets and stale history leakage across long gaps or out-of-order replay calls.

## Proof gate

The regression test must demonstrate:

1. a 23:45 UTC bar remains visible to the 00:00 UTC bar in the next day file;
2. a bar three calendar days old is excluded;
3. full CI remains green;
4. no runtime/deployment path is changed.

After merge, R4 firing parity must be rerun before R5. Passing the original prereg gate does not waive this rerun because the replay input state changed.
