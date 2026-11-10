# Preregistration — MNQ 60M 3-2-2 — post-hoc structural gate — 2026-11-10

trial_id: T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01

This document was written after AFS-0586. It is post-hoc. AFS-0586 already ran `run_reproduction_gate` once at `000f43128764aedfc6a5702d9fb11847f8a58d1c` with `MAX_CONTRACTS_HARD_CAP=1` and recorded a FAIL. AFS-0588 and AFS-0590 already inspected the decision bars. Nothing in this registration is a prospective pre-commitment, and this commit does not score.

`RUN_AUTHORIZED` is false for this trial. `run_reproduction_gate` on `T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01` is closed because that trial is `SUPERSEDED`. This trial supersedes it.

## Structural rule

`compare_replay_structural` is the comparison. There is no numeric tolerance.

- Date, direction, trigger, stop, and target match exactly on every model.
- Original and pre-armed match exactly on every compared field.
- Honest IOC may differ only on `fill_price` and `net`, and only on a day where the committed bar evidence records a vendor close difference while open, high, and low agree.
- That difference must equal the recorded close delta, and the same delta applies at every slippage. The net difference must equal that close delta times the MNQ dollars per point from `config/futures_contracts.py`.
- `exit_price`, `status`, `result`, and `exit_reason` still match.
- A day whose open, high, or low differ, including 2026-03-06, is not exempt. Every compared field must still match. If they match, the day passes. If any field differs, the gate fails.

The allowlist is built from `docs/research-evidence/T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01/BAR_EVIDENCE.json`. The current exempt days are 2024-09-06 (close delta +0.75) and 2026-06-11 (close delta -0.25).

## Older slices

Older-slice IOC results depend on Databento closing prints. A documented decision-bar close difference moves honest-IOC fill_price and net by that close delta and does not by itself fail the structural gate.

## What this commit changes, and what it leaves

- Each new gate attempt is written to its own file. An existing target is refused. Prior attempt bytes are not overwritten.
- The scoring profile reads the slippage list and fill models `_run_window` uses, the contracts, starting balance, and pessimistic flag stated in frozen `resolve_bracket`, and the MNQ tick size and tick value from `config/futures_contracts.py`.
- That profile check runs before reproduction scoring and before slice admission.
- Good Friday 2026 is 2026-04-03. Easter Sunday 2026 is 2026-04-05. The adapter calendar now classifies 2026-04-03 as closed.
- Early-close labeling is not changed. Pre-open 15-minute bars on an early-close day, such as 2025-11-28 from 07:00 through 08:15, remain `DATA_GAP` until a later change labels them `expected_holiday_closure`.

Frozen modules are not edited.
