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
- A decision bar whose open, high, or low disagree refuses the structural gate. Matching fill fields do not excuse it. 2026-03-06 is that case.

The allowlist is built from `docs/research-evidence/T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01/BAR_EVIDENCE.json`. The current close-delta days are 2024-09-06 (close delta +0.75) and 2026-06-11 (close delta -0.25). Those two days have agreeing open, high, and low. 2026-03-06 is not a close-delta day.

## 2026-03-06

This check is the reason the post-hoc rule refuses an open, high, or low disagreement instead of excusing a day whose fills happened to match.

The committed bar evidence records 2026-03-06 LONG at `2026-03-06T15:00:00+00:00`. The Oct 4 close is 24751.5 and the Databento close is 24764.5, a difference of +13.0. Open, high, and low disagree. Honest IOC is `NO_FILL` / `ENTRY_NOT_FILLED` on the Oct 4 ledger because 24751.5 is 36 ticks past the 32-tick cap from trigger 24742.5. The Databento close is farther from that trigger, so it is also a no-fill. That is why the day produced no `fill_price` mismatch.

The Oct 4 ledger, `data/replay_polygon_5m/MNQ/MNQ_2026-03-06.jsonl`, and `data/replay_corpus_v1_5m_4hr_audit/MNQ/MNQ_2026-03-06.jsonl` agree on that decision bar: open 24692.0, high 24754.75, low 24687.75, close 24751.5, volume 7616. No Polygon 5-minute bar on that date closes at 24764.5. The nearest Polygon close is 24762.75 at 13:35 UTC. The next Polygon bar after 15:00 UTC is 15:55 UTC, close 24858.5. A one-bar shift onto another Polygon print does not explain the Databento close.

`front_contract("MNQ", 2026-03-06, roll_days=8)` is MNQH6. The March 2026 third Friday is 2026-03-20, so the 8-day roll window starts 2026-03-12. This session is before that window. It is not the 2026-06-11 case, where the roll had already moved to MNQU6.

The committed evidence does not record the Databento open, high, low, volume, or instrument id for this bar, and that parent file is not in this checkout. A shifted Databento timestamp, a different Databento contract, and a bad Databento bar all remain possible. The rule refuses the day until one of those is shown.

## Older slices

Older-slice IOC results depend on Databento closing prints. A documented decision-bar close difference moves honest-IOC fill_price and net by that close delta and does not by itself fail the structural gate. A decision bar whose open, high, or low disagree refuses the structural gate, including on the older slices. Matching fill fields do not excuse that bar.

## What this commit changes, and what it leaves

- Each new gate attempt is written to its own file. An existing target is refused. Prior attempt bytes are not overwritten.
- The scoring profile reads the slippage list and fill models `_run_window` uses, the contracts, starting balance, and pessimistic flag stated in frozen `resolve_bracket`, and the MNQ tick size and tick value from `config/futures_contracts.py`.
- That profile check runs before reproduction scoring and before slice admission.
- Good Friday 2026 is 2026-04-03. Easter Sunday 2026 is 2026-04-05. The adapter calendar now classifies 2026-04-03 as closed.
- Early-close labeling is not changed. Pre-open 15-minute bars on an early-close day, such as 2025-11-28 from 07:00 through 08:15, remain `DATA_GAP` until a later change labels them `expected_holiday_closure`.

Frozen modules are not edited.
