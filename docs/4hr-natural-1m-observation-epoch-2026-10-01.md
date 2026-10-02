# 4HR natural-1m observation epoch

Status: **CODE ONLY — EPOCH NOT STARTED**

This note records why the first natural-1m deployment produced no 4HR evidence,
and when a later sample is allowed to count. It does not change the evidence
verdict. `docs/strategy-rules/Strategy_Inventory.md` remains the strategy-status
authority. 4HR MNQ stays **PROMISING BUT UNPROVEN**.

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

## New epoch — not started by this change

The canonical forward sample starts only after all of the following are true:

1. The pinned futures release contains the observation-only wiring:
   `context/four_hr_observation.py` published by the wide-stop forward
   collector, and read by `evaluate_armed_4hr_touch()`. **Met 2026-10-02
   03:43Z:** release `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` (#1092 with the
   arm-window race fix, plus #1094's publish fail-safe). Condition 3 is met
   on the running process. Conditions 4 and 5 are still open, so the epoch
   has not started.
2. `strat_4hr_retrigger` is still absent from `enabled_concepts`.
3. `ONE_MIN_TRIGGER_ENABLED=true` and
   `EXPECTED_PROOF_ONE_MIN_TRIGGER_ENABLED=true`. **Met 2026-10-02 15:06Z**
   on PID `1327346`. The 4HR observer only runs
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
the corrected code still emits no natural-1m 4HR evidence. There is no
`4hr_trigger_evidence` file.

Verified on PID `1327346` at 2026-10-02 15:06Z: `ONE_MIN_TRIGGER_ENABLED=true`
and `EXPECTED_PROOF_ONE_MIN_TRIGGER_ENABLED=true`.
`ONE_MIN_4HR_OBSERVER_ENABLED` and its proof pin are absent.

`/root/afs-shared/logs/tf1m/4hr_observation/state_2026-10-02.json` is being
rewritten anyway. At 15:00Z it was `status=INVALIDATED`, `executable=false`,
`trade_authorized=false`, `source=wide_stop_forward_v1`. That file is not
the epoch, and rows written before the condition-5 verification do not count.

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
- **Contract month is not checked.** The snapshot records the `MNQ` root only.
  Around a roll (next window 2026-12-11 to 2026-12-14), the 5m and 1-minute
  feeds could be on different months. Until the snapshot carries the contract
  and the reader matches it, flag any natural-1m record from a roll window
  for manual review before counting it.
