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
- A decision bar whose open, high, or low disagree is classified from pinned 1-minute evidence. There is no caller override.
  - Databento-side cause (wrong contract, shifted timestamp, bad bar, or adapter bug): the trial FAILs. Fix the adapter and register a new trial.
  - Reference defect: Databento's own 1-minute bars for the selected contract rebuild its 5-minute bar exactly, and the reference bar is the odd one out. Count it, flag it, back it with the pinned evidence, and exclude it from the open/high/low comparison only when the replay's scored `trigger_bar_open/high/low/close` equals that pinned Databento bar. Otherwise the day is `DATABENTO_SIDE` and the trial fails. Outcome fields on that date are still compared. A close-delta day fails unless the replay's `trigger_bar_close` equals the pinned Databento close.
  - Unexplained, or no pinned 1-minute evidence: the trial FAILs.
- This root-cause rule was written after AFS-0594 classified 2026-03-06 as `REFERENCE_DEFECT`. The matching IOC no-fills on that day are not the reason it is excluded.

The allowlist is built from `docs/research-evidence/T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01/BAR_EVIDENCE.json`. The current close-delta days are 2024-09-06 (close delta +0.75) and 2026-06-11 (close delta -0.25). Those two days have agreeing open, high, and low. 2026-03-06 is not a close-delta day.

## 2026-03-06

This check is the reason the post-hoc rule refuses an open, high, or low disagreement instead of excusing a day whose fills happened to match.

The committed bar evidence records 2026-03-06 LONG at `2026-03-06T15:00:00+00:00`. The Oct 4 close is 24751.5 and the Databento close is 24764.5, a difference of +13.0. Open, high, and low disagree. Honest IOC is `NO_FILL` / `ENTRY_NOT_FILLED` on the Oct 4 ledger because 24751.5 is 36 ticks past the 32-tick cap from trigger 24742.5. The Databento close is farther from that trigger, so it is also a no-fill. That is why the day produced no `fill_price` mismatch.

The Oct 4 ledger, `data/replay_polygon_5m/MNQ/MNQ_2026-03-06.jsonl`, and `data/replay_corpus_v1_5m_4hr_audit/MNQ/MNQ_2026-03-06.jsonl` agree on that decision bar: open 24692.0, high 24754.75, low 24687.75, close 24751.5, volume 7616. No Polygon 5-minute bar on that date closes at 24764.5. The nearest Polygon close is 24762.75 at 13:35 UTC. The next Polygon bar after 15:00 UTC is 15:55 UTC, close 24858.5. A one-bar shift onto another Polygon print does not explain the Databento close.

`front_contract("MNQ", 2026-03-06, roll_days=8)` is MNQH6. The March 2026 third Friday is 2026-03-20, so the 8-day roll window starts 2026-03-12. This session is before that window. It is not the 2026-06-11 case, where the roll had already moved to MNQU6.

AFS-0594, written after that limit, pinned `MAR06_BAR_EVIDENCE.json` and `MAR06_BAR_EVIDENCE.md`. The five MNQH6 1-minute bars at 15:00–15:04 UTC rebuild the Databento 5-minute bar exactly (open 24691.5, high 24777.75, low 24687.75, close 24764.5, volume 51008). The Oct 4 / Polygon bar does not match that rebuild, and no other contract or shifted window does either. The classification is recomputed from those pinned rows. It is `REFERENCE_DEFECT`. The day is excluded from the open/high/low comparison and reported. Both vendors' honest-IOC results were `NO_FILL` only because both closes sat beyond the 32-tick cap. That match is not the reason the day is excluded.

## Older slices

The reproduction gate is the only check of Databento bar quality. Older-slice results depend on Databento's bars and closing prints.

## What this commit changes, and what it leaves

- Each new gate attempt is written to its own file. An existing target is refused. Prior attempt bytes are not overwritten.
- The scoring profile reads the slippage list and fill models `_run_window` uses, the contracts, starting balance, and pessimistic flag stated in frozen `resolve_bracket`, and the MNQ tick size and tick value from `config/futures_contracts.py`.
- That profile check runs before reproduction scoring and before slice admission.
- Good Friday 2026 is 2026-04-03. Easter Sunday 2026 is 2026-04-05. The corrected Easter calculation makes 2026-04-03 a closed session. AFS-0586 recorded three data gaps, including 2026-04-03. A later run with this calendar drops that day out of `DATA_GAP`, so the data-gap count goes from 3 to 2. The 34 scored dates stay the same. The other two AFS-0586 gaps, 2025-01-09 and 2025-11-28, stay `DATA_GAP`.
- Older-slice Good Friday labels move in the years the previous Easter calculation missed the lunar correction. Inside the NQ slice (2010-06-06..2019-04-30) the closed day changes in 2011 (2011-03-25 to 2011-04-22), 2012 (2012-04-13 to 2012-04-06), 2013 (2013-04-05 to 2013-03-29), 2015 (2015-04-10 to 2015-04-03), 2016 (2016-04-01 to 2016-03-25), 2017 (2017-04-21 to 2017-04-14), 2018 (2018-04-06 to 2018-03-30), and 2019 (2019-03-29 to 2019-04-19). Inside the MNQ slice (2019-05-01..2024-06-30) it changes in 2020 (2020-04-17 to 2020-04-10), 2022 (2022-03-25 to 2022-04-15), and 2023 (2023-04-14 to 2023-04-07). 2010's Good Friday is before the NQ slice starts. 2014 and 2021 do not move.
- Early-close labeling is not changed. Pre-open 15-minute bars on an early-close day, such as 2025-11-28 from 07:00 through 08:15, remain `DATA_GAP` until a later change labels them `expected_holiday_closure`.

Frozen modules are not edited.
