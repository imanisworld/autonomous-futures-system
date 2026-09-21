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

## First proof run — 2026-09-21 (off-box, working tree `4cd83b1`, dry run)

Gates 1–5 of the acceptance list below were exercised on the merge commit;
gate 6 (sample review) was done for EBAY only. Nothing was written to any
repo-tracked or box database.

| gate | result |
|---|---|
| tests | 6512 passed / 7 skipped (`pytest -q`) |
| isolation | diff = 4 files; not imported by the collector; scanner sqlite refused by name/schema |
| `--dry-run --date 2026-09-18` (150-symbol universe, lookback 4) | 2766 events / 2414 episodes, 146/150 observable, 0 provider errors |
| not observable | `SQ`, `VIX` (`missing_symbol`, same allow-list as cov-v0.1); `EQIX`, `TDY` (`target_session_incomplete` — a 5m interval with no SIP trades fails the symbol closed, as in cov-v0.1) |
| determinism | `--date 2026-05-01 --symbols EBAY` run twice into a scratch sqlite: 22 rows both times, 0 duplicates (UNIQUE keys hold) |
| logged-fixture check (EBAY, 04-30/05-01) | `COVERAGE_MATCH`: `PDL_REJECTION_LONG` 09:55 ET → `PDL_RECLAIM_SHORT` 10:00 → `PDL_BREAK_RETEST_SHORT` 10:05 → `PDL_REJECTION_LONG` 10:25 — the "~55-minute whipsaw" the fixture describes |

**Read-only observations from that single session (not evidence):** every
LONG family had a positive EOD mean and every SHORT family a negative one.
That is one up-day, not a property of the families. Nothing here is compared
to the null baseline (p95 PF 1.94) and nothing may be until ≥ 5 sessions and
an episode-level (not event-level) summary exist.

### Open doubts recorded at the first run

1. **PDL identity vs the hand-logged fixture.** The observer's PDL for EBAY
   on 04-30 is **100.09** (SIP regular-session low of 04-29). The fixture
   inventory records **$100.20**. The 11¢ gap is a definition or feed
   difference (extended-hours low, a different vendor, or a rounded chart
   read) — not a code defect, but it means "PDL" in this lane and "PDL" in
   the fixture language are not yet the same number. Resolve before any
   fixture is used as a pass/fail oracle.
2. **Event-level means double count contiguous episodes.** `summarize`
   averages EOD return over events; a 3-bar `VWAP_TEST_HOLD` episode counts
   three times. The CLI line now says so. An episode-level summary is an
   ns-v0.2 change (new version, rows not pooled).
3. **Thin names fail closed.** `EQIX`/`TDY` dropped for one empty 5m
   interval. Correct under the whole-session rule, but coverage of illiquid
   names is structurally lower; report it, do not relax it.
4. **Gap-through-PDH/PDL opens** never arm break-retest (see limits above).
   Frequency unknown until several sessions are stored.

## Five-session read — 2026-09-21 11:50Z (sessions 09-14 → 09-18, local store)

Real (non-dry) runs into `logs/options_non_strat_coverage.sqlite` (local
working tree `46e0834`; the box holds no copy). 09-18 was observed
prospectively on 09-21 morning; 09-14 → 09-17 were **backfilled the same
morning** under unchanged rules — retrospective rows, labelled here so they
are never mistaken for a prospective epoch. 14,349 events / 12,547 episodes;
145–147 of 150 symbols observable per session.

Episode-level (first event per episode), median close return in bps:

| family | episodes | 60m | EOD | MFE≥2×MAE | aligned n / 60m | unaligned n / 60m |
|---|---:|---:|---:|---:|---|---|
| ORB_BREAKOUT_LONG | 870 | −2.13 | −10.82 | 0.34 | 384 / −3.52 | 469 / 0.00 |
| ORB_BREAKOUT_SHORT | 1106 | +0.62 | +4.14 | 0.38 | 468 / +3.03 | 617 / −1.66 |
| ORB_BREAK_RETEST_LONG | 327 | −2.48 | −11.46 | 0.36 | 142 / −3.03 | 176 / −2.15 |
| ORB_BREAK_RETEST_SHORT | 436 | −0.89 | −0.18 | 0.35 | 178 / +0.41 | 246 / −1.30 |
| ORB_REJECTION_LONG | 673 | 0.00 | −5.75 | 0.37 | 185 / +2.33 | 473 / −1.52 |
| ORB_REJECTION_SHORT | 508 | −3.69 | +4.85 | 0.34 | 125 / −1.91 | 368 / −3.99 |
| PDH_BREAK_RETEST_LONG | 213 | −3.75 | −18.44 | 0.33 | 97 / −1.38 | 111 / −5.56 |
| PDH_RECLAIM_LONG | 492 | −3.73 | −22.76 | 0.31 | 199 / −1.69 | 286 / −6.07 |
| PDH_REJECTION_SHORT | 336 | +2.82 | +25.71 | 0.42 | 74 / +1.59 | 249 / +2.88 |
| PDL_BREAK_RETEST_SHORT | 287 | −3.76 | +9.30 | 0.36 | 92 / +2.57 | 192 / −4.89 |
| PDL_RECLAIM_SHORT | 699 | +1.38 | +13.16 | 0.37 | 245 / +4.86 | 442 / +0.09 |
| PDL_REJECTION_LONG | 424 | −4.72 | −4.23 | 0.34 | 96 / −4.87 | 316 / −4.32 |
| VWAP_FAILED_RECLAIM_SHORT | 671 | 0.00 | +13.60 | 0.34 | 177 / +2.65 | 484 / −2.32 |
| VWAP_RECLAIM_LONG | 2290 | −1.28 | −16.13 | 0.35 | 864 / +1.14 | 1392 / −2.74 |
| VWAP_TEST_HOLD_LONG | 1590 | −4.08 | −12.33 | 0.34 | 579 / −1.69 | 982 / −5.66 |
| VWAP_TEST_HOLD_SHORT | 1625 | +0.94 | +11.14 | 0.38 | 411 / +2.70 | 1187 / +0.50 |

**Read (coverage only, no candidate rule was pre-registered for options):**

- On 09-18 alone every LONG family was positive and every SHORT family
  adverse; across the five sessions the sign flips family-by-family. The
  populations follow the week's tape; nothing here is separable from drift
  without a per-session all-bar control (the futures record shows how) and
  a much longer window.
- SPY/QQQ-aligned episodes are better than unaligned ones for most families
  in the direction of the family — but "aligned" means the index was already
  moving that way at the trigger, so this is the same drift measured twice,
  not a filter result. It is what a filter test would have to beat.
- MFE ≥ 2×MAE share sits at 0.31–0.42 for every family: no family produces
  asymmetric excursions on this tape.
- `PDH_REJECTION_SHORT` (EOD +25.7, n=336) and `PDH_RECLAIM_LONG`
  (EOD −22.8, n=492) are the largest magnitudes; both are one week of a
  down-tape and are recorded, not promoted.

Nothing in this section authorises a geometry rule, a paper ticket or a
filter. Next read after 20 sessions, with a per-session all-bar control.

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
