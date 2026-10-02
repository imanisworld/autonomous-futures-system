# DRAFT — MNQ account-admission overlay — NOT APPROVED — DO NOT RUN

**Status: SAME_TIMESTAMP_ORDER_BLOCKED. No scoring run is authorized.** The
#915 fillable stream has been reproduced from the archive. The same-timestamp
census is greater than zero, so this draft is not ready. It does not authorize
a fetch, and it does not amend prereg #929.

**Trial ID:** `T-2026-10-02-prereg-mnq-account-admission-overlay-draft-2026-10-02-01`

The single registered variant is `apply_account_admission`. The trial id
lowercases `DRAFT` from the filename so it matches the ledger's lowercase
trial-id rule.

Nothing here enables execution, changes `risk_rules.yaml`, or edits the pinned
#915 files.

## Mechanism

`research/mnq_account_admission_overlay.py` `apply_account_admission`.

It consumes an already-built `PortfolioEvent` stream. It does not rebuild
family detectors, fill models, stops, targets, slippage, commission, or
holding/EOD behavior.

## Blanks that block approval

| Item | Required before any run | This draft |
|---|---|---|
| Population | Exact family set and window | **REPRODUCED** #915 six-family window `2025-07-24`..`2026-06-26` |
| Input event stream | Path plus SHA-256 of the fillable-event artifact | **BOUND** `research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl` `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942` (2257 rows) |
| Process timezone | Confirm what `date.today()` is on the futures-bot process | **PROVEN** `Etc/UTC` at 2026-10-02T16:38:01Z |
| Drawdown seed | Provenance-backed starting balance and starting peak, or an explicit choice to leave drawdown `DRAWDOWN_GATE_NOT_EVALUATED` | **NOT CHOSEN** — census blocks a scored run |
| Decision criteria | What would count as pass, fail, or insufficient | **NOT FROZEN** — census blocks a scored run |
| Allowed outputs | Whether a look may read counts only, or also P&L | **NOT FROZEN** — census blocks a scored run |
| Same-timestamp census | Count of accepted `exit_ts` values that exactly equal a later candidate's `eligible_fill_ts`. Do not infer order from the tie | **280** — `SAME_TIMESTAMP_ORDER_BLOCKED` |

Do not fill these blanks from prereg #929, from the replay's $5,000
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
  `docs/pr915-fillable-event-reproduction-2026-10-02.md`.

The earlier current-`main` rebuild failed because `resolve_bracket` and
`PaperBroker` have changed since that archive. The archived code reproduces
the frozen family controls. Details are in that reproduction note.

Drawdown seed, decision criteria, and allowed outputs stay unfrozen. The
census below blocks a scored run before those blanks matter.

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
- adds an accepted terminal result to the UTC calendar date of its **entry**,
  and only once the exit is strictly earlier than the candidate being judged;
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

`date.today()` on that process is therefore the UTC calendar date. This is a
point-in-time fact about the running process and the host zone. It is not a
claim that a later `TZ` change is impossible, and it does not by itself make
the overlay exact live parity. The same-timestamp census is still undone.

## Same-timestamp realization is not live parity

Live `process_alert` can, inside one request:

1. resolve the open position and log it with `for_date=open_position_date`;
2. add that realized P&L to the in-memory `daily_state`
   (`realized_pnl_dollars += pnl`);
3. continue processing that same alert;
4. call `RiskEngine.validate` later in the request.

A new candidate handled by that same request can therefore see the
just-realized loss immediately.

This overlay does not do that. It books a resolved fill only when `exit_ts`
is strictly earlier than the next candidate's `eligible_fill_ts`. An equal
timestamp is not treated as already realized. Equal timestamps are not an
event order, and this draft does not infer one.

The census on the reproduced artifact counted 280 fillable pairs whose
`exit_ts` equals another event's `eligible_fill_ts`. The pairs are listed in
`research/artifacts/pr915-six-family-same-timestamp-collisions-5a9f14b.json`.
Equal timestamps were not treated as an order.

That count is greater than 0. This draft is not ready. The exact live
ordering for those collisions has to be frozen and tested before a scored
run. Do not call this overlay exact live parity.

## Drawdown

Default: `DRAWDOWN_GATE_NOT_EVALUATED`. That is not a pass. Daily loss still
runs, and it runs first.

After the frozen capacity checks, admission follows `RiskEngine.validate`:
the journal-day loss rule, then the drawdown floor. A candidate already
blocked by daily loss is `SKIPPED_DAILY_LOSS`. Its drawdown gate is
`DRAWDOWN_NOT_APPLIED` when a seed is present, and
`DRAWDOWN_GATE_NOT_EVALUATED` when it is not. Only a candidate that survives
daily loss is compared with the floor. A skipped candidate takes no fill
slot and does not move balance or peak.

If a scored run is to evaluate the 30% floor, the approved revision of this
draft must name a starting balance and a starting peak, each with provenance.
The overlay then updates that counterfactual path only when an **accepted**
fill resolves:

- balance starts at the seeded balance and adds resolved net P&L;
- peak starts at the seeded peak and rises when balance makes a new high;
- before the next otherwise-fillable event, `(peak - balance) / peak >= 0.30`
  is `SKIPPED_DRAWDOWN_FLOOR`;
- a skipped event does not move balance or peak.

Do not feed an external balance series. After the first skip or extra
admission, a historical account snapshot is no longer this path.

## Still off, and not part of this overlay

News blackout, session caps, session cutoffs, consecutive-loss lock, circuit
breaker, early-session loss floor, and profit-protect.

## MES and the other micros

MES is out of this draft. M2K, MGC, MCL, and MBT stay collection-only.
Options candidate admission stays **INSUFFICIENT EVIDENCE** and is not part
of this draft.

## Next

Stop. Status is `SAME_TIMESTAMP_ORDER_BLOCKED`. Do not score. The next
revision has to freeze and test the live same-request order for the 280
collisions before any scored look.
