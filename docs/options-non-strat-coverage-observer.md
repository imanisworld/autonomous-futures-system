# Options non-Strat coverage observer (`ns-v0.1`)

**Status: PAPER/RESEARCH ONLY — PROMISING BUT UNPROVEN.**

This lane exists to answer the coverage question the Strat-only scanner cannot:
*which repeatable level interactions occur even when no Strat family qualifies?*

It is deliberately parallel to `cov-v0.1`. It does **not** redefine Strat,
change the production scanner, send alerts, select contracts, consume risk,
open paper positions, or touch broker/execution code.

## Boundary

Identity:

- `observer_id = OPTIONS_NON_STRAT_COVERAGE`
- `observer_version = ns-v0.1`
- timeframe: completed regular-session consolidated SIP `5Min` bars
- default database: `logs/options_non_strat_coverage.sqlite`
- default universe: the same pre-registered options coverage watchlist used by
  `scripts/options_coverage_observer.py`

The database is separate from `options_scanner.sqlite` and from the existing
Strat coverage observer.

## Why this lane exists

The active/advisory options scanner now has several Strat populations, but a
mechanically valid level setup can exist without being a Strat sequence. The
repository already names examples such as support-hold continuation,
pullback/reclaim, VWAP, PDH/PDL and ORB families. Until this lane, those ideas
were either fixture language, futures-side definitions, or explicitly outside
`cov-v0.1`.

This observer measures them without granting them trading authority.

## Frozen `ns-v0.1` raw families

| family | causal event definition |
|---|---|
| `VWAP_RECLAIM_LONG` | prior close <= prior cumulative session VWAP; current close > current cumulative session VWAP |
| `VWAP_FAILED_RECLAIM_SHORT` | the immediately prior bar was a reclaim; current close falls back below cumulative VWAP |
| `VWAP_TEST_HOLD_LONG` | already above VWAP; current low tests VWAP and closes back above |
| `VWAP_TEST_HOLD_SHORT` | already below VWAP; current high tests VWAP and closes back below |
| `PDH_RECLAIM_LONG` | prior close <= prior-session high; current close > PDH |
| `PDL_RECLAIM_SHORT` | prior close >= prior-session low; current close < PDL |
| `PDH_REJECTION_SHORT` | from inside/below, wick trades above PDH and closes back below |
| `PDL_REJECTION_LONG` | from inside/above, wick trades below PDL and closes back above |
| `PDH_BREAK_RETEST_LONG` | after a recorded PDH break, later bar tests PDH and closes above |
| `PDL_BREAK_RETEST_SHORT` | after a recorded PDL break, later bar tests PDL and closes below |
| `ORB_BREAKOUT_LONG/SHORT` | after first six 5m bars freeze the 30m OR, close crosses the appropriate boundary |
| `ORB_REJECTION_SHORT/LONG` | after OR is frozen, wick exceeds a boundary and closes back inside |
| `ORB_BREAK_RETEST_LONG/SHORT` | after a recorded OR break, later bar tests the broken boundary and closes on the breakout side |

"Reclaim" on PDH/PDL preserves the repository's existing strategy naming. This
observer records the actual crossing predicate so the evidence remains
unambiguous.

## Context recorded, never used as promotion authority

Each event stores:

- cumulative session VWAP;
- 5m EMA20 when enough causal history exists;
- relative volume versus the prior 20 completed **regular-session** 5m bars when available;
- SPY and QQQ trend using their own cumulative VWAP + EMA20;
- whether both index trends align with the event direction;
- the earliest SIP visibility time (`bar close + 960s`).

These fields are **telemetry**. An event is not dropped because alignment,
EMA, or relative volume is absent or adverse. That separation is intentional:
first measure the raw population, then test filters without selection bias.

## Known ns-v0.1 limits (frozen, not bugs to patch silently)

- Context history is rebuilt from regular-session slices of the prior
  sessions; pre-market and post-close provider bars are discarded. The 09:35
  bar therefore has a VWAP but may have no EMA20/volume ratio until enough
  RTH history exists.
- Bar 0 (09:30) is never evaluated as an event bar, so a gap that opens
  through PDH/PDL does not arm `*_BREAK_RETEST_*`; a later retest of that
  level is not observed. A crossing needs a completed prior 5m bar on the
  other side.
- `VWAP_TEST_HOLD_*` compares the bar's low/high with the cumulative VWAP as
  of the bar's **close**, not the VWAP at the moment of the extreme.
- The `--sqlite` path is refused, by name and by schema, when it is the V1
  scanner database (same guard as the Strat collector).

## Episodes

Raw events remain stored. For summary counts, contiguous 5m events with the
same symbol, family, direction and level name share one episode id. A gap,
family change, direction change or level-name change starts a new episode.

No outcome-selected dedupe is allowed.

## Outcomes

`ns-v0.1` does not invent stop/target geometry.

Instead it measures the underlying from the event bar's close using **later
completed 5m bars only**:

- 15m directional close return, MFE and MAE;
- 30m directional close return, MFE and MAE;
- 60m directional close return, MFE and MAE;
- EOD directional close return, MFE and MAE.

All values are basis points from the event close. This is coverage/behavior
evidence, **not option expectancy** and not a promotion test.

## Explicitly blocked in `ns-v0.1`

Generic "support hold", "resistance rejection" and "pullback + reclaim" are **not** implemented
against guessed swing levels. The existing fixture language says those need a
planned-level source. Until that source is frozen and reproducible, these
families remain blocked rather than approximated.

Also absent by design:

- option-chain selection;
- option P/L;
- alerting/Discord;
- scanner score changes;
- risk-budget consumption;
- paper broker entry;
- Webull/Tradovate routing;
- live execution;
- promotion decisions.

## Run

After a completed NYSE session:

```bash
python scripts/options_non_strat_coverage.py --date 2026-09-18 --dry-run
python scripts/options_non_strat_coverage.py --date 2026-09-18
python scripts/options_non_strat_coverage.py --report 2026-09-18
```

Requires the same Alpaca consolidated-bar credentials used by the existing
coverage observer.

No box install, timer or release activation is part of this PR. Those are
separate operator actions after CI and a controlled dry-run prove the lane.

## Acceptance gate before any deployment

1. Tests green.
2. Diff contains no scanner alert, risk, broker or execution changes.
3. One local/box `--dry-run` on a completed session returns nonzero events
   without provider/completeness errors.
4. Stored run uses only the dedicated non-Strat SQLite.
5. Re-running the same date is deterministic for event identities/counts.
6. Review at least a small sample of each populated family against source bars.
7. Only then consider an isolated after-close collector/timer. Still no alerts
   or execution.

**No proof, no run.**
