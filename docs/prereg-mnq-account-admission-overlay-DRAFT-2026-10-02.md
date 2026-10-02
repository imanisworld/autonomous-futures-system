# DRAFT — MNQ account-admission overlay — NOT APPROVED — DO NOT RUN

**Status: DRAFT. No scoring run is authorized.** This revision replaces the
earlier snapshot design. It does not name a population, it does not authorize
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
| Population | Exact family set and window | **BLANK** |
| Input event stream | Path plus SHA-256 of the fillable-event artifact | **BLANK** |
| Process timezone | Confirm what `date.today()` is on the futures-bot process. The code stand-in is UTC because this repo does not set `TZ` | **BLANK** |
| Drawdown seed | Provenance-backed starting balance and starting peak, or an explicit choice to leave drawdown `DRAWDOWN_GATE_NOT_EVALUATED` | **BLANK** |
| Decision criteria | What would count as pass, fail, or insufficient | **BLANK** |
| Allowed outputs | Whether a look may read counts only, or also P&L | **BLANK** |
| Same-timestamp census | Count of accepted `exit_ts` values that exactly equal a later candidate's `eligible_fill_ts`. Do not infer order from the tie | **BLANK** |

Do not fill these blanks from prereg #929, from the replay's $5,000
normalization, or from the $1,500 ladder seed.

## Daily-loss day key

Live `webhook/app.py` calls `process_alert` without `for_date`. The runner
sets `today = date.today()` and the gate reads
`journal.get_daily_state(today).realized_pnl_dollars`.

A resolved position is logged with `for_date=open_position_date`, the
calendar day the position was opened. The next calendar day's journal file
does not contain that outcome.

This overlay therefore:

- labels each candidate with the UTC calendar date of its fill timestamp;
- adds an accepted terminal result to the UTC calendar date of its **entry**,
  and only once the exit is strictly earlier than the candidate being judged;
- skips with `SKIPPED_DAILY_LOSS` when that entry-day total is at or below
  `-$150`;
- does not use `observation_day` for this gate. The three-fill cap still uses
  `event.observation_day`, because that is the frozen capacity rule.

UTC is the stand-in for `date.today()` until the process timezone blank is
filled. Do not describe that stand-in as a completed live-parity proof.

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

Before any scored run, census the selected population. Count cases where an
accepted position's `exit_ts` is exactly equal to a later candidate's
`eligible_fill_ts`.

- If that count is 0, record the census against the named population. On
  that population the current strict-before rule is sufficient for this
  dimension.
- If that count is greater than 0, this draft is not ready. The exact live
  ordering for those collisions has to be frozen and tested before a scored
  run.

Until that census is recorded, do not call this overlay exact live parity.

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

Stop. An operator has to fill the blanks and approve a revision of this draft
before anyone runs a scored comparison.
