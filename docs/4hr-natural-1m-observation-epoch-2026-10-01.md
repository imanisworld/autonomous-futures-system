# 4HR natural-1m observation epoch

Status: **CANONICAL EPOCH STARTED — 2026-10-04T21:30:05Z on release `c44d32bc4961e56fae5c5f88a976eb6783341638`**

This note records why the first natural-1m deployment produced no 4HR evidence,
when a later sample is allowed to count, and the verified start of that sample.
It does not change the evidence verdict. `docs/strategy-rules/Strategy_Inventory.md`
remains the strategy-status authority. 4HR MNQ stays **PROMISING BUT UNPROVEN**
until the forward sample says otherwise.

## Canonical epoch start — 2026-10-04T21:30:05Z

Exact release `c44d32bc4961e56fae5c5f88a976eb6783341638` (= `489b55b` + #1095
+ #1103, eight files) was built, verified, and promoted with
`scripts/atomic_release.sh` on 2026-10-04 (promote 21:28:57–21:29:13Z, operator
GO). Conditions 1–5 below were then proven read-only on the running process:

1. release carries #1092/#1094 observation wiring **and** #1103 `contract_check`;
   symlink and futures-bot cwd both `/root/afs-releases/c44d32bc…1638`;
   `release integrity: OK — 1633 files checked`; 0 release-tree `__pycache__`;
2. `strat_4hr_retrigger` absent from `enabled_concepts` (only `orb_breakout` active);
3. `ONE_MIN_TRIGGER_ENABLED=true` = `EXPECTED_PROOF_ONE_MIN_TRIGGER_ENABLED`;
4. `ONE_MIN_4HR_OBSERVER_ENABLED=true` = `EXPECTED_PROOF_ONE_MIN_4HR_OBSERVER_ENABLED`;
5. `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `MAX_CONTRACTS_HARD_CAP=1`
   (pinned); broker position `null`; the bot's own in-process preflight at
   21:30:05Z read `0 position row(s)` / `0 order row(s)` / `0 working order(s)`;
   live stayed disarmed (`preflight_passed_not_armed`).

The epoch start is the timestamp of that last proof, **2026-10-04T21:30:05Z**,
before the Sunday 22:00Z CME reopen, so no natural-1m touch can predate it.
Evidence from this timestamp forward is the canonical sample. Nothing earlier
counts or is backfilled. No parameter, strategy, or alert change is allowed
while the sample runs. Rollback target: `489b55b` (`current.previous`).

## Disabled period — do not count

From the 2026-09-18 1-minute lane deployment through the runtime verification
of the observation-state fix, natural-1m 4HR forward evidence was **not
functioning**.

`ONE_MIN_TRIGGER_ENABLED=true` stored MNQ 1-minute bars, and the 3-2-2 observer
wrote its own evidence. `evaluate_armed_4hr_touch()` ran only when
`strat_4hr_retrigger` was in `enabled_concepts`. That concept stayed commented
out on purpose, so the executable journal state never became `ARMED`. The
isolated wide-stop collector could see 4HR candidates, but the 1-minute reader
did not consume that state.

Verified read-only result on 2026-10-02T02:15:49Z, release
`75f10e4540aa1f25b51b77c1ec2a2da40381a188`:

- 1,723 executable `strat_4hr_retrigger` snapshots from 2026-09-18 through
  2026-10-02, all empty
- 0 `ARMED` states
- 0 `tf1m/4hr_trigger_evidence_*.jsonl` files
- 0 claim files

That zero is the defect result. It is not a no-event sample, not a negative
expectancy sample, and not something to backfill. Do not convert historical
`strat_4hr_retrigger_observed` proxy rows into this lane.

## New epoch — conditions (met 2026-10-04, see above)

The canonical forward sample starts only after all of the following are true:

1. The pinned futures release contains both the observation-only wiring **and the current contract-month guard**:
   `context/four_hr_observation.py` published by the wide-stop forward collector,
   `evaluate_armed_4hr_touch()` reading it, and #1103's `contract_check` /
   mismatch-blocking behavior in `context/one_min_trigger.py`.
   Release `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` contained #1092/#1094 but
   predated #1103; release `c44d32b` (deployed 2026-10-04) carries both.
2. `strat_4hr_retrigger` is still absent from `enabled_concepts`.
3. `ONE_MIN_TRIGGER_ENABLED=true` and
   `EXPECTED_PROOF_ONE_MIN_TRIGGER_ENABLED=true`. The 4HR observer only runs
   when the generic 1-minute lane is also on; turning that lane off silently
   stops 4HR evidence too.
4. `ONE_MIN_4HR_OBSERVER_ENABLED=true` and
   `EXPECTED_PROOF_ONE_MIN_4HR_OBSERVER_ENABLED=true`.
5. An operator read-only check records that the running process has both
   pins, the observation file is the only arm source, and no broker order was
   created by the check.

The epoch timestamp is that verification time. This repository change does not
deploy, does not set the flag, and does not start the epoch. Evidence written
before that verification does not count.

The observer flag defaults off. Until it is explicitly enabled and pinned,
the corrected code still emits no natural-1m 4HR evidence.


### 2026-10-02 observer-on interval before #1103

Grok's read-only runtime check reported the 1-minute and 4HR observer pins ON at
`2026-10-02T15:17:19Z`, with DEMO flat, live trading off, no restart-created
orders, and the strategy still absent from the executable strategy list.

That timestamp is **not** the canonical evidence-epoch start because the running
release is still `489b55b`, which cannot emit #1103's `contract_check` fields.
Any natural-1m 4HR touch written before a release carrying #1103 is provisional
and must not be counted or backfilled into the canonical sample.

The canonical epoch timestamp is the first read-only verification after a
release carrying #1103 is deployed and conditions 2–5 are also proven. That
verification happened on 2026-10-04 (see "Canonical epoch start" above); any
4HR natural-1m record from 2026-10-02 15:17Z to 2026-10-04 21:30:05Z on
`489b55b` stays provisional and is not part of the sample.

## What the sample can and cannot see

Read the new sample with these boundaries. They are properties of the design,
not missing data, and they must not be backfilled.

- **Arm window.** The collector publishes the arm when the 5m bar that armed
  it completes, recorded once as `armed_available_at`. When that arm resolves,
  `terminal_available_at` records the first publish that showed it. A 1m bar
  can use the arm only if it opened at or after `armed_available_at` and
  before `terminal_available_at`. A 1m bar in the last minute of a 5m bar
  still counts when its webhook is processed after the same-boundary 5m
  webhook.
- **09:30–09:34 ET is not observable.** The machine arms while evaluating the
  09:30 5m bar, so the arm is first readable at 09:35. A touch from 09:30 to
  09:34 produces no 1-minute evidence. If the 09:30 bar itself touches the
  trigger, the arm is never published as ARMED and that day has no
  natural-1m record. Report such days from the 5m collector, not as 1-minute
  misses or no-touch days.
- **Contract month is checked only when both alerts prove it.** The arm keeps
  the dated contract the 5m alert proved when it was first published
  (`contract`, for example `MNQZ2026`). Every record written after a 1-minute
  touch carries `contract_check`:
  - `MATCH`: both alerts proved the same month.
  - `MISMATCH`: the months differ. The touch is recorded as `TRIGGER_BLOCKED`
    / `CONTRACT_MONTH_MISMATCH` and is never claimed as evidence.
  - `UNKNOWN`: either alert did not prove its contract. The record is written
    with `needs_manual_review: true` and must not be counted until reviewed.
    The alerts are least likely to prove their contract around a roll (next:
    2026-12-11 to 2026-12-14), so expect most roll-window records to be
    `UNKNOWN`.

  The check runs only on bars that touch the trigger. A day where the 5m and
  1-minute feeds are on different months but price never touches looks like a
  normal no-touch day.

  An arm published by a release without this field has no contract until the
  current release republishes it; after that, it takes the 5m alert's
  contract.
