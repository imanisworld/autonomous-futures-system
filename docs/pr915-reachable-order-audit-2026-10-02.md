# #915 reachable-collision ordering audit — 2026-10-02

Request provenance only. No account overlay. No P&L, profit factor, drawdown,
or win/loss.

The 47 reachable collisions are the input. Each row asks whether the exiting
fill and the next reachable candidate are one live bar request, and which one
that request processes first.

## What the code proves

`webhook/app.py` calls `process_alert` once per webhook payload. That function
is one bar-close alert.

Asia D+EMA events in the archived adapter are 15-minute bar closes. When an
accepted Asia exit close equals the next Asia candidate's fill close, both
stamps are the close of the same 15-minute bar, and that bar is strictly later
than the open position's own entry. `context/asia_d_ema_paper_cohort.py`
`process_bar` runs inside that alert. It clears an open cohort position on
the bar before it considers that bar's candidates. Those 45 rows are
`EXIT_BEFORE_CANDIDATE_PROVEN`. The cohort call does not reach
`RiskEngine.validate`.

The other two rows mix a 5-minute Daily 2-2 close with a 15-minute Asia close.
Those are separate payloads. Nothing records which payload arrived first.
Those rows are `DISTINCT_REQUEST_ORDER_UNKNOWN`.

## Result

| Classification | Count |
|---|---|
| `EXIT_BEFORE_CANDIDATE_PROVEN` | 45 |
| `DISTINCT_REQUEST_ORDER_UNKNOWN` | 2 |
| `CANDIDATE_BEFORE_EXIT_PROVEN` | 0 |
| `INSUFFICIENT_PROVENANCE` | 0 |

The two unresolved rows:

| Timestamp | Exiting source | Candidate source | Exit bar | Candidate bar |
|---|---|---|---|---|
| `2025-12-01T01:15:00+00:00` | `daily22:2025-12-01:2025-11-30T23:00:00+00:00` | `asia:2025-12-01T01:15:00+00:00:strat_22_continuation_observed:SHORT` | 5m bar starting `2025-12-01T01:10:00+00:00` | 15m bar starting `2025-12-01T01:00:00+00:00` |
| `2026-02-05T12:45:00+00:00` | `asia:2026-02-05T02:30:00+00:00:strat_22_continuation_observed:SHORT` | `daily22:2026-02-05:2026-02-05T12:40:00+00:00` | 15m bar starting `2026-02-05T12:30:00+00:00` | 5m bar starting `2026-02-05T12:40:00+00:00` |

All 47 rows are in
`research/artifacts/pr915-reachable-order-audit-5a9f14b.json`.

One unresolved reachable case is enough. The account draft stays
`SAME_TIMESTAMP_ORDER_BLOCKED`. This audit does not freeze an ordering rule
and does not authorize a scored run.
