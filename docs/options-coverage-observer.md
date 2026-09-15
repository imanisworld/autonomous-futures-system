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

## After-close collector (`col-v0.1`) — isolated, pinned oneshot + timer

`scripts/options_coverage_collect.py` runs the three read-only scripts above
for every **newly completed session** and verifies each step. It is the daily
routine that was run by hand after each close, made idempotent, append-only,
fail-closed and self-recovering. Identity `OPTIONS_COVERAGE_COLLECTOR / col-v0.1`.

```
python scripts/options_coverage_collect.py                    # every settled, uncollected session since --from, oldest first
python scripts/options_coverage_collect.py --date 2026-09-16  # one explicit session
python scripts/options_coverage_collect.py --plan             # resolve + report only; runs and writes nothing
```

**Which sessions.** Candidates are every session from the collection start
(`--from`, default 2026-09-09) whose close + 30 min has passed (static NYSE
calendar: holidays, weekends and 13:00 ET early closes) and whose evidence is
not yet complete. They run oldest → newest and stop at the first failure, so a
box that misses several closes catches up by itself on the next firing;
`--max-sessions` (default 10) caps one run and defers the rest with a
`DEFERRED` ledger line. An explicit `--date` that has not settled fails closed;
a weekend or holiday is `SKIPPED` (exit 0). With credentials the broker's
read-only calendar is cross-checked per session: no session there → `SKIPPED`
(unscheduled closure); a different close → `FAILED calendar_mismatch`.

**Steps per session.** (1) observer `--date D` → observer sqlite; (2) outcomes
`--from D --to D` → `<data>/daily/outcomes_D_D.{json,csv,md}` plus a
**binding** sidecar `outcomes_D_D.binding.json` naming the observer run id,
raw event count, reducer episode count, outcome episode count and source
commit. Then once per run: (3) episode reducer `--from F --to D` →
`<data>/aggregate/episodes_F_D.json` and a **local** roll-up of every stored
daily outcome file → `<data>/aggregate/outcomes_F_D.{json,md}` (no provider
call; sessions without a daily file are listed in `sessions_missing`).

**Complete means bound.** A daily file counts as complete only when its
binding matches the *current* observer state for that date (latest run id,
event count) and its episode count equals what the reducer produces from the
stored events now. A repaired or re-run observer dataset therefore invalidates
the older outcome file: it is moved aside as `*.tainted.<ts>` and re-measured.

**Provenance.** Every ledger line carries the exact source commit. The
reducer aggregate `episodes_F_D.json` is self-describing: the collector adds a
top-level `provenance` block (source sha, how it was established, release
manifest fingerprint, collector/reducer/observer versions, range, episode
count) to the ep-v0.1 payload. The cumulative roll-up records who assembled it
(`aggregated_by.source`) **and** which commit produced each constituent daily
file (`sessions_provenance`, keyed by session, copied from each binding
sidecar; `constituent_source_shas` lists the distinct commits). A daily file
whose sidecar is missing, malformed, for another session, or names no real
commit is refused from the roll-up (`daily_provenance_missing` /
`daily_provenance_invalid`), never silently included. Under `--require-pinned` (the systemd unit) the commit must come
from `release_manifest.json` inside the immutable tree being executed, and that
tree's directory must be named after the commit; a working tree is refused. Off
the box, a working tree is allowed and labelled `working_tree` with its dirty flag.

**Fail closed** (`FAILED`, exit 1, reason in the ledger): missing
credentials, non-zero exit, any `provider error:` line from the observer,
a universe symbol without a row, an unobservable symbol not on the allow-list
(`--allow-unobservable`, default `SQ,VIX`), SPY or QQQ unobservable, zero
events, provider errors or zero episodes in the daily file, a binding
mismatch that survives re-measurement, or any missing output. The observer
sqlite path is refused if it is the V1 scanner database by name or by schema.

**Idempotent, append-only.** A complete session is `ALREADY_COLLECTED` without
any fetch. The observer's own `INSERT OR REPLACE` keys make a retry safe.
Nothing under `<data>` is deleted. Every attempt appends lines to
`<data>/ledger.jsonl` (`STARTED`/`DONE`/`ALREADY_COLLECTED`/`SKIPPED`/`FAILED`/
`DEFERRED`/`AGGREGATED`) and each subprocess gets a fresh log under
`<data>/runs/<D>/`. A file lock prevents overlap.

**Not in this lane.** No `git pull`, no service restarts, no V1 database
access, no alerts, no promotion or policy logic — the collector's commands
are the three scripts and nothing else (asserted by tests).

**Pinned install (not performed by this change).** The collector never runs
from the production tree. `deploy/coverage/install_coverage_release.sh build <sha>`
stages an immutable copy of exactly that commit (own `.venv`, `release_manifest.json`,
`chmod a-w`) under `/root/afs-shared/coverage/releases/<sha>`; `activate <sha>`
points `/root/afs-shared/coverage/current` at it and installs
`deploy/systemd/afs-coverage-collector.{service,timer}` (`Type=oneshot`,
`Mon..Fri 16:35 America/New_York`, `Persistent=true`, `--require-pinned`,
evidence paths passed explicitly under `/root/afs-shared/coverage/`). It never
touches `/root/autonomous-futures-system`, `/root/afs-releases`, `futures-bot`,
the scanner or the watcher. Moving the collector to a newer commit is a new
`build` + `activate`, never a `git pull`.
