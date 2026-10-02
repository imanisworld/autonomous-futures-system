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
   collector, and read by `evaluate_armed_4hr_touch()`.
2. `strat_4hr_retrigger` is still absent from `enabled_concepts`.
3. `ONE_MIN_4HR_OBSERVER_ENABLED=true`.
4. `EXPECTED_PROOF_ONE_MIN_4HR_OBSERVER_ENABLED=true`.
5. An operator read-only check records that the running process has that pin,
   the observation file is the only arm source, and no broker order was
   created by the check.

The epoch timestamp is that verification time. This repository change does not
deploy, does not set the flag, and does not start the epoch. Evidence written
before that verification does not count.

The observer flag defaults off. Until it is explicitly enabled and pinned,
the corrected code still emits no natural-1m 4HR evidence.
