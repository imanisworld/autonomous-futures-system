# Options coverage observer (`cov-v0.1`) — read-only evidence lane

**What it is.** A batch, after-the-close observer that records every mechanical
30m Strat structure in a liquid universe and asks, per event, *at which V1 gate
would this have died?* It exists because the 2026-09-15 signal-coverage audit
found the ACTIVE options lane evaluates one 30m family (2-1-2 continuation) on
six symbols, and the follow-up replay found 0/17 of those episodes could pass
the nearest-level target rule at the trigger.

**What it is not.** Not a trade lane. It never selects a contract, never
promotes to ACTIVE, never alerts, never consumes risk, never writes an episode
block, never opens `options_scanner.sqlite`, and never changes `V1-EPOCH-1`.
A row means "this structure existed" — nothing more.

## Identity

Every row and run carries `observer_id = OPTIONS_COVERAGE_OBSERVER` and
`observer_version = cov-v0.1`, and lives in its own file
(`OPTIONS_COVERAGE_SQLITE_PATH`, default `logs/options_coverage_observer.sqlite`).
Any change to a definition below is a new `cov-v*` version; rows from different
versions are never pooled.

## Definitions (all borrowed from what V1 already runs)

| element | source |
|---|---|
| bars | consolidated SIP `30Min`, regular session only, whole sessions required (a gap fails the symbol closed) |
| candle types / 2-1-2R / 3-1-2 | `strategy.strat_classifier` |
| 2-1-2C, 2-2-2 C/R, 3-2-2 C/R | `alert_ranker.daily_strat.evaluate_daily_setup` on the 4-bar 30m window (as the 1H/4H observers do) |
| trigger / invalidation | previous bar high/low |
| target pool | same reduction as `BarContextBuilder._causal_structure_levels` |
| geometry, V1 rule | `find_targets` nearest level; ok when `rr_1 >= 1.0` |
| geometry, floor rule | `find_targets(min_target_rr=1.0)` (the Daily-lane rule; needs two qualifying levels) |
| alignment | SPY trend, QQQ trend (close vs session VWAP and EMA20), session-hour candle, prior-session daily candle — all must match direction (`_promote_paper_evidence_setup`) |
| first sight | bar close + 960 s, rounded up to the scanner grid (hh:02:57 + 5k) |
| late | remaining R:R from the first-sight price < 1.0 (`paper_v1.remaining_reward_to_risk`); first-sight price = close of the last `5Min` bar closed by then (5Min is used for this price only, never for detection) |
| after close | first sight ≥ session close → V1 can never see the bar |

`STRAT_212_CONTINUATION` is the only `v1_supported` family. The requested
populations are `STRAT_212_REVERSAL`, `STRAT_222_CONTINUATION/REVERSAL`,
`STRAT_312`, `STRAT_322_CONTINUATION/REVERSAL`. Other directional shapes
(1-2-2, inside-bar break, outside-bar follow-through) are kept as
`OTHER:<label>` so "all structural events" is a real total.

## Universe

`research/coverage/options_watchlist_150.csv` — the pre-registered
equity-corpus watchlist (sha256 `2770c80b…`, 150 tickers). SPY and QQQ are
always fetched for the alignment test. Each run records per-symbol
observability and the reason when bars are missing.

## Funnel (per session, per rule set)

```
all structural events
→ V1-supported                    (family V1 evaluates)
→ unsupported setup family
→ unsupported timeframe           (always 0 in cov-v0.1: 30m only)
→ target geometry failure
→ market alignment failure
→ first sight after close
→ late at first sight
→ would otherwise qualify
```

`v1_rule` uses the nearest-level geometry; `floor_rule` uses the ≥1R floor.
`by_family` re-walks the same gates *as if* each family were supported, which
is the number that says whether a missing population is worth testing.

## Running

```
python scripts/options_coverage_observer.py --date 2026-09-15            # fetch + write + print
python scripts/options_coverage_observer.py --date 2026-09-15 --dry-run  # print only
python scripts/options_coverage_observer.py --report 2026-09-15          # from the sqlite
```

Needs Alpaca data credentials (`ALPACA_KEY`/`ALPACA_SECRET` or the `_API_`
spellings). Not installed on the box and not scheduled; it is run by hand
after the close during collection.

## Out of scope for cov-v0.1

15m/5m detection (provider/session semantics to be proven separately),
reclaim / break-retest / PDH-PDL / VWAP-reclaim families, any promotion
decision.

## Episode reducer (`ep-v0.1`) — read-only

`scripts/options_coverage_episodes.py --from D1 --to D2 [--json out]` reduces raw
bar-events into episodes with one structural rule: **contiguous 30m bars, same
symbol, session, family and direction = one episode.** Contiguity is the
identity — the later bar's "previous bar" *is* the earlier event's breakout
bar, so it is the same directional run, not a new mechanical trigger. A gap, a
family change or a direction change starts a new episode. Episode fields are
the FIRST event's (first mechanical opportunity, as V1 defines an episode);
`n_events` preserves the raw count. `direction_runs` (contiguous, same
direction, any family) is reported as a stricter denominator only.

R:R quality flags are reported, never clipped: `structural_risk_tiny`
(risk < 0.1% of price), `first_sight_denominator_small` (first-sight risk
< 10% of structural risk), `remaining_rr_implausible:*` (non-finite or
|R| > 20). Rejection reasons are kept per rule, in gate order, uncollapsed.

## Episode outcome study (`out-v0.1`) — read-only research

`scripts/options_coverage_outcomes.py --from D1 --to D2 [--out DIR]` measures
causal forward movement for every deduped episode, same regular session only
(`UNRESOLVED_AT_CLOSE` otherwise; nothing carries overnight). Two entry views
are always computed and never substituted: **mechanical** (entry at the
trigger, path from the first 5m bar inside the breakout 30m bar that crossed
it) and **first sight** (entry at the observer's stored first-sight price,
path strictly after that tick). `blind_window_extension_r` = directional move
from trigger to first-sight price ÷ structural risk. 5m bars are used only to
resolve the path — never to discover or redefine a setup. Intrabar ordering is
never assumed: target and stop in one 5m bar → `AMBIGUOUS` (bar preserved);
trigger and stop in the cross bar → `trigger_path_ambiguous`; thresholds are
not credited in the invalidation bar. Both stored geometries are evaluated;
R is normalised so favourable is positive with structural risk as the
denominator for both views. Cohorts: orthogonal flags plus a mutually
exclusive gate-stage bucket (UNSUPPORTED_FAMILY → TARGET_GEOMETRY_REJECTED →
MARKET_ALIGNMENT_REJECTED → LATE_AT_FIRST_SIGHT → WOULD_OTHERWISE_QUALIFY).
Outputs JSON + CSV + Markdown under `logs/coverage_outcomes/` (gitignored).
Identity `OPTIONS_COVERAGE_OUTCOMES / out-v0.1`. Not a promotion study.
