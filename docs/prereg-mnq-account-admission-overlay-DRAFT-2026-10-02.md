# DRAFT — MNQ account-admission overlay — NOT APPROVED — DO NOT SCORE

**Status: READY FOR INDEPENDENT PREREG REVIEW.** No scoring run is authorized
until that review approves this prereg. This draft does not authorize a
fetch, and it does not amend prereg #929.

**Trial ID:** `T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01`

The single registered variant is `apply_account_admission`. The one
sensitivity below is a robustness check on two unresolved pairs. It is not a
second variant. The trial id lowercases `DRAFT` from the filename so it
matches the ledger's lowercase trial-id rule.

Nothing here enables execution, changes `risk_rules.yaml`, or edits the pinned
#915 or #929 files.

`strategy_status_change_authorized = false`

## Mechanism

`research/mnq_account_admission_overlay.py` `apply_account_admission`.

It consumes an already-built `PortfolioEvent` stream. It does not rebuild
family detectors, fill models, stops, targets, slippage, commission, or
holding/EOD behavior.

Equal-time handling is opt-in. With no order map, an exit timestamp must be
strictly earlier than the candidate before capacity is freed or realized P&L
is booked. The frozen map lives in
`research/mnq_equal_time_admission_order.py` and is loaded from
`research/artifacts/pr915-reachable-order-audit-5a9f14b.json`. Runtime code
does not import that module.

## Blanks

| Item | Required before any run | This draft |
|---|---|---|
| Population | Exact family set and window | **FROZEN** #915 six-family window `2025-07-24`..`2026-06-26` |
| Input event stream | Path plus SHA-256 of the fillable-event artifact | **FROZEN** `research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl` `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942` (2257 rows) |
| Process timezone | Confirm what `date.today()` is on the futures-bot process | **FROZEN** UTC. Proven `Etc/UTC` at 2026-10-02T16:38:01Z |
| Drawdown seed | Provenance-backed starting balance and starting peak, or an explicit choice to leave drawdown unevaluated | **FROZEN** `DRAWDOWN_GATE_NOT_EVALUATED` |
| Decision criteria | What would count as valid, invalid, or insufficient | **FROZEN** below |
| Allowed outputs | What the single scored look may report | **FROZEN** below |
| Same-timestamp handling | Exact pairs and their treatments | **FROZEN** 45 `EXIT_BEFORE_CANDIDATE_PROVEN`; 2 primary `UNKNOWN_ORDER_BUSY_FIRST`; same 2 sensitivity `UNKNOWN_ORDER_EXIT_FIRST` |

Do not fill a drawdown seed from prereg #929, from the replay's $5,000
normalization, or from the $1,500 ladder seed.

## Population

The population is the archived #915 six-family fillable stream, not the #929
forward window.

- Families: `4HR_RETRIGGER`, `60M_322_FIRST_LIVE`, `DAILY_22_COMPLETED_CLOSE`,
  `12HR_MIYAGI`, `ASIA_D_EMA`, `SUSTAINED_TREND_V1`. Asia stays in.
- Window: `2025-07-24` through `2026-06-26`. Historical. #915 already
  published aggregate results for this window.
- Excluded: prereg #929 from `2026-09-23T22:00:00Z` onward, and
  `2026-06-27` through `2026-07-23`.
- Artifact: `research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl`,
  SHA-256 `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942`,
  2257 rows. Produced from archive `5a9f14b` plus the local corpora named in
  `docs/pr915-fillable-event-reproduction-2026-10-02.md`. The artifact bytes
  stay unchanged.
- Frozen capacity replay accepts 488 fills. That count is the capacity
  control. It is not the account-admission result.

The earlier current-`main` rebuild failed because `resolve_bracket` and
`PaperBroker` have changed since that archive. The archived code reproduces
the frozen family controls. Details are in that reproduction note.

## Daily-loss day key

Live `webhook/app.py` calls `process_alert` without `for_date`. The runner
sets `today = date.today()` and the gate reads
`journal.get_daily_state(today).realized_pnl_dollars`.

A resolved position is logged with `for_date=open_position_date`, the
calendar day the position was opened. The next calendar day's journal file
does not contain that outcome.

This overlay therefore:

- labels each candidate with the UTC calendar date of its fill timestamp,
  which is the proven `date.today()` calendar on the futures-bot process;
- adds an accepted terminal result to the UTC calendar date of its **entry**;
- books that result when the exit is strictly earlier than the candidate, and
  also when a frozen exit-first pair says this exact candidate is judged
  after that exact exit;
- skips with `SKIPPED_DAILY_LOSS` when that entry-day total is at or below
  `-$150`;
- does not use `observation_day` for this gate. The three-fill cap still uses
  `event.observation_day`, because that is the frozen capacity rule.

Process timezone, read only at `2026-10-02T16:38:01Z`:

- futures-bot was already running (MainPID `1457117`, `NRestarts=0`). It was
  not restarted.
- The process environment has no `TZ`. The unit `Environment` has no `TZ`.
  The unit's environment files do not assign `TZ`.
- `timedatectl` reports `Etc/UTC`. `/etc/localtime` is
  `/usr/share/zoneinfo/Etc/UTC`.
- A host `python3` `date.today()` at that read returned `2026-10-02` with
  `time.tzname` `('UTC', 'UTC')`.

`date.today()` on that process is the UTC calendar date. The journal-day
mapping for this study stays UTC. This is a point-in-time fact about the
running process and the host zone. It is not a claim that a later `TZ`
change is impossible.

## Equal-time handling

Live ingestion uses one global `_alert_lock`, so separate payloads are
serialized in whichever order acquires the lock. Historical arrival order for
the two cross-timeframe pairs is not recorded. This prereg does not invent it.

The raw census counted 280 fillable pairs whose `exit_ts` equals another
event's `eligible_fill_ts`. Those pairs are listed in
`research/artifacts/pr915-six-family-same-timestamp-collisions-5a9f14b.json`.
Frozen capacity arbitration accepts 488 of the 2257 fillable events. Only 47
of the raw pairs are an accepted open position whose exit timestamp equals a
candidate that would otherwise be the next reachable fill. That list is
`research/artifacts/pr915-reachable-equal-time-collisions-5a9f14b.json`.
The ordering audit is
`research/artifacts/pr915-reachable-order-audit-5a9f14b.json`.

### Proven pairs

Primary and sensitivity both use `EXIT_BEFORE_CANDIDATE_PROVEN` for the exact
45 Asia-to-Asia pairs in that audit.

Meaning, for those pairs only:

- the prior accepted position is resolved before the candidate is judged;
- realized P&L from that resolution is available to the journal-day
  daily-loss path before candidate admission;
- capacity is freed before candidate admission.

This is the same 15-minute Asia bar. `process_bar` clears the cohort position
before it considers that bar's candidate. Do not generalize "same timestamp
means exit first." Any equal-time pair outside this set stays strict-before.

### Two unresolved pairs — primary

Primary treatment is fail-closed: `UNKNOWN_ORDER_BUSY_FIRST`.

The exact pairs are:

| Timestamp | Exiting source | Candidate source |
|---|---|---|
| `2025-12-01T01:15:00+00:00` | `daily22:2025-12-01:2025-11-30T23:00:00+00:00` | `asia:2025-12-01T01:15:00+00:00:strat_22_continuation_observed:SHORT` |
| `2026-02-05T12:45:00+00:00` | `asia:2026-02-05T02:30:00+00:00:strat_22_continuation_observed:SHORT` | `daily22:2026-02-05:2026-02-05T12:40:00+00:00` |

Meaning, for those two pairs only:

- the existing position remains active;
- the candidate is not admitted at that timestamp;
- the candidate disposition is busy/occupied capacity;
- the unresolved ordering does not receive optimistic execution.

The first pair is a Daily 5-minute payload against an Asia 15-minute payload.
The second pair is an Asia 15-minute payload against a Daily 5-minute payload.
They are separate requests.

Journal days follow the frozen UTC entry-day rule. These facts use only the
timestamps already in the pair ids. They do not use P&L.

- `2025-12-01T01:15:00+00:00`: the exiting entry is
  `2025-11-30T23:00:00+00:00`, so its realized P&L books to journal day
  `2025-11-30`. The candidate fill is journal day `2025-12-01`. Exit-first
  frees capacity. That exit's P&L is not on the candidate's journal day.
- `2026-02-05T12:45:00+00:00`: the exiting entry is
  `2026-02-05T02:30:00+00:00` and the candidate fill is
  `2026-02-05T12:45:00+00:00`. Both are journal day `2026-02-05`. Exit-first
  can change the $150 gate for that candidate.

### Sensitivity — not the primary result

One alternate sensitivity is authorized: `UNKNOWN_ORDER_EXIT_FIRST`.

For the same two pairs only:

- treat the exit as processed before the candidate;
- free capacity;
- apply realized P&L before candidate admission;
- continue the normal account gates.

The 45 proven pairs stay `EXIT_BEFORE_CANDIDATE_PROVEN`. No other pair
changes. No other order permutation is authorized. There is no parameter
search.

The sensitivity exists to answer one question: would the unknown arrival
order of these two payload pairs materially change the account-level
conclusion?

### Fail closed

An override that names a source id missing from the input stream fails
closed. A duplicate pair, or two definitions that disagree, fails closed.
The frozen loader accepts only 45 proven pairs plus these 2 unknown pairs.

## Drawdown

`DRAWDOWN_GATE_NOT_EVALUATED`.

No separately proven starting balance and starting peak were found for the
`2025-07-24` boundary. This study does not use the replay's $5,000
normalization, the $1,500 sizing ladder, or a guessed broker balance.

This experiment evaluates:

- frozen capacity
- journal-day $150 daily-loss admission
- proven equal-time ordering
- conservative treatment of two unresolved arrivals

It does not claim full current-account parity on the 30% survival-floor
dimension. `SKIPPED_DRAWDOWN_FLOOR` is not an output of this study.

Daily loss still runs, and it runs first. A skipped candidate takes no fill
slot and does not move balance or peak. Because no seed is supplied, balance
and peak are not updated.

## Decision criteria

This study is a mechanical account-admission audit. It does not validate or
promote any strategy.

### VALID_RUN

A run is valid only if:

- the input artifact SHA-256 matches
  `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942`;
- the row count is 2257;
- the frozen capacity replay reproduces 488 accepted fills;
- the 45 proven equal-time overrides match the audit pairs exactly;
- the two unresolved pair ids match the table above exactly;
- the process timezone assumption remains UTC for the journal-day mapping;
- #929 remains untouched;
- no drawdown result is claimed while the seed remains unavailable.

### INVALID_EXPERIMENT

Classify `INVALID_EXPERIMENT` if:

- the artifact hash differs;
- source ids differ;
- the override count differs from 45 + 2;
- an unknown equal-time collision appears outside the frozen set;
- the frozen capacity count no longer equals 488;
- replay mechanics differ from this prereg;
- prohibited outputs are viewed before the one authorized look.

### INSUFFICIENT_EVIDENCE

Use `INSUFFICIENT_EVIDENCE` if:

- the primary result and the two-case exit-first sensitivity lead to
  materially different account-level conclusions, under the rule below;
- missing evidence prevents the required mechanical reconciliation;
- the experiment cannot determine whether the daily-loss/account-admission
  effect is stable under the only unresolved ordering ambiguity.

## Material difference

Compare only the primary pass with the one sensitivity pass. The difference
is material if any of these is true:

- the account-level label differs, using the labels in the reading rules;
- primary `SKIPPED_DAILY_LOSS` count is 0 and the sensitivity count is not,
  or the reverse;
- the set of `FILLED` source ids differs by any id other than the two
  unresolved candidate ids;
- any source id other than those two candidates has a different disposition;
- the sign of total realized account P&L differs, where the signs are
  positive, negative, and zero;
- the max consecutive losing fills count differs;
- the set of journal days that contain at least one `SKIPPED_DAILY_LOSS`
  differs.

If the only disposition changes are those two candidate ids, and none of the
conditions above are true, report the uncertainty and keep the study
interpretable. Do not add a statistical threshold after viewing results. Do
not use a profitability cutoff.

## Reading rules

These rules are how the one look is read. They do not add a gate.

The one look is exactly two calls to `apply_account_admission` on the pinned
artifact, plus the already-frozen capacity replay used only as the 488-fill
control:

- primary: `load_frozen_equal_time_order("primary")`, no account seed;
- sensitivity: `load_frozen_equal_time_order("unknown_order_exit_first")`,
  no account seed.

No third mode, no family slice, no seed, and no prereg #929 run.

An override is exercised when that candidate's decision carries the frozen
`equal_time_treatment`. The expected counts are 45 proven and 2 unresolved.
Any other exercise count is `INSUFFICIENT_EVIDENCE`.

`SKIPPED_DAILY_LOSS` is the set of events the $150 gate blocked. Those events
already passed the busy check and the three-fill cap. Do not attribute an
exit-first admission difference to that gate.

The account-level label is:

- `DAILY_LOSS_NEGLIGIBLE` when the primary `SKIPPED_DAILY_LOSS` count is 0;
- `DAILY_LOSS_CONSEQUENTIAL` when that primary count is greater than 0.

`INSUFFICIENT_EVIDENCE` replaces that label when a material difference is
true. `INVALID_EXPERIMENT` replaces it when a validity check fails. Neither
of those two outcomes is a strategy result.

Economics use accepted terminal fills only, ordered by `eligible_fill_ts`:

- realized account P&L is the sum of their `net_pnl`;
- ending P&L is that sum, with no starting balance added;
- the daily path sums `net_pnl` by the entry journal day;
- a losing, winning, or flat day is a journal day with at least one such
  fill whose sum is negative, positive, or zero;
- max consecutive losses counts fills with `net_pnl < 0`; a zero or positive
  fill resets the streak;
- maximum dollar drawdown is the largest peak-to-trough drop of the
  cumulative sum, starting at 0.

The research-trial ledger `PLANNED` row keeps its registration-time
population text. This prereg is the population authority. Do not append a
`COMPLETED` row in order to authorize the look.

## Allowed outputs

The single scored look may report only the following.

### Structural

- input event count
- frozen capacity accepted fills
- account-admission accepted fills
- counts of `SKIPPED_BUSY_PORTFOLIO`, `SKIPPED_MAX_TRADES_PORTFOLIO`, and
  `SKIPPED_DAILY_LOSS`
- count of the 45 proven equal-time overrides exercised
- count of the two unresolved cases exercised
- downstream differences caused by the primary treatment versus the
  sensitivity treatment

`SKIPPED_DRAWDOWN_FLOOR` stays out. Drawdown is not evaluated.

### Economics

#915 historical aggregate economics are already exposed. This
account-admission study may reveal:

- realized account P&L of accepted fills
- ending P&L of the accepted-fill path, with no account seed added to that sum
- the daily realized P&L path
- the count of losing, winning, and flat days
- max consecutive losses
- maximum realized drawdown in dollars from the simulated P&L path

Do not label a percentage drawdown as current-account drawdown. There is no
account seed.

### Comparison

Report:

- capacity-only replay versus the account-admission overlay
- primary busy-first treatment versus the two-case exit-first sensitivity
- which events became unreachable specifically because of the $150
  daily-loss gate
- whether any downstream capacity chain changed

Do not expose exploratory strategy-level rankings. Do not retune anything.

## No strategy-status promotion

`strategy_status_change_authorized = false`

This experiment may change confidence in account realism. It must not, by
itself, move 4HR, 60M 3-2-2, Daily 2-2, Miyagi, Asia D+EMA, or Sustained
Trend to a stronger validation state.

## Still off, and not part of this overlay

News blackout, session caps, session cutoffs, consecutive-loss lock, circuit
breaker, early-session loss floor, and profit-protect.

## MES and the other micros

MES is out of this draft. M2K, MGC, MCL, and MBT stay collection-only.
Options candidate admission stays **INSUFFICIENT EVIDENCE** and is not part
of this draft.

## Next

Status is `READY FOR INDEPENDENT PREREG REVIEW`. Do not score. Do not run
`apply_account_admission` on the historical stream until an independent
reviewer approves this prereg. Do not open or run prereg #929.
