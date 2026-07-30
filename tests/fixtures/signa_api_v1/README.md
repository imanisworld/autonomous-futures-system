# Signa API v1 contract fixtures

Captured live from `GET https://app.getsigna.ai/api/v1/signal` on 2026-07-29
(`api_version v1`, `engine_version v3.1`), HTTP 200, symbol `SPY`, at
`tf=1d`, `tf=4h`, `tf=1h`.

These are **contract** fixtures: their job is to pin the real response *shape*
so the parser cannot drift back to a shape the API never returned. They contain
public market data only — no credentials, no account identifiers, no PII.

## What these fixtures prove

**The request parameter is `tf`, not `timeframe`.** `timeframe`, `interval`,
`resolution`, and `symbol` are all silently ignored by the server, which then
falls back to `1d`. Verified `tf` values: `1d 4h 1h 30m 15m 5m 1w 1m 1mo`
(`daily` and `D` alias to `1d`).

**Three independent surfaces, which routinely disagree.** In `signa_spy_1d.json`:

| Surface | Grade | Direction | Per `meta.signal_sources` |
|---|---|---|---|
| `engine` | `B` | `BULLISH` | nightly 30+ model consensus, matches in-app Action Card |
| `signa`  | `C` | `HOLD` (action) | undocumented |
| `data`   | —   | `WAIT` | live single-pass technical analysis |

**`engine` is timeframe-invariant.** Across all three fixtures `engine` is
identical (`B` / `BULLISH` / score 81) because it is a once-nightly consensus.
Only `signa` and `data` vary by timeframe. Any code that reads `engine` and
labels the result "4H" or "1H" is reporting a daily value under a false label.

**`signal_timestamp` timestamps the engine, not the live data.** It is identical
across all three fixtures.

## Rules these fixtures exist to enforce

- Never collapse `engine.score`, `engine.confidence`, `data.confidence`, and
  `signa.conviction` into one number. They are four different measurements.
- Never pick a winner between `engine.grade` and `signa.grade`. Record both,
  plus `signa_grade_conflict`, and leave the system decision untouched.
- Preserve `A+` verbatim. Never truncate a grade to its first character.
- Signa is **observational metadata only**. Nothing in these fixtures may
  approve, reject, block, or alter a setup, entry, stop, target, or status.
