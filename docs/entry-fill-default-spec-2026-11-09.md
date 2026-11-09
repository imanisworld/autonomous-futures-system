# Entry fill default — HOLD specification

Status: **HOLD. Do not implement from this file.** Option (a) is the chosen direction. The WIP branch stays unmerged.

| Item | Value |
|---|---|
| Main | `d703232b7c9df3bf2b942f1437235dc5488a0890` |
| WIP branch | `claude/honest-default-entry-fill-model` |
| WIP commit | `6fb01c935b7591dde5e9340351a16658b3ce79ee` |
| Decision | Option (a): an unconfigured run should use `ioc_limit`, not legacy `market`. |
| This document | Classification and gates only. It does not authorize a code change. |

A later implementation is not allowed to turn the suite green only by rewriting expected `TRADE` / `WIN` assertions into `CANCELLED`. Historical `market` reproduction stays available through an explicit `ENTRY_FILL_MODEL=market`.

## What the WIP already changed

Three files, and nothing else:

- `.env.example` example value `market` → `ioc_limit`.
- `config/settings.py` field default and `load_config()` fallback `market` → `ioc_limit`.
- `ops/project_check/runtime.py` reported code default `market` → `ioc_limit`.

`risk_rules.yaml` `fill_model` sets slippage and pessimistic-both-hit. It does not set `entry_fill_model`. On `main`, `load_config()` therefore resolves:

1. `ENTRY_FILL_MODEL` if set,
2. else `fill_model.entry_fill_model` if present,
3. else the code default `market`.

The WIP does not change these other `market` fallbacks:

- `execution/paper_broker.py` `PaperBroker(..., entry_fill_model="market")`.
- `webhook/runner.py` `_paper_broker`: `getattr(cfg, "entry_fill_model", "market")`.
- `replay/replay_engine.py` `getattr(..., "market")` in the commission and broker wiring paths.

The box value is UNVERIFIED. This session did not read the server env. If the box sets `ENTRY_FILL_MODEL`, the code default does not change paper fills. If it does not, flipping the code default does.

## Measured WIP suite

Command, from a detached worktree of `6fb01c9`, no other edits:

```text
.venv/bin/python -B -m pytest -q --tb=line
```

Result: **38 failed, 8977 passed, 11 skipped, exit 1** (12m43s). Claude's "about 34" is the same pile. The count below is the one this session ran.

## Classification

### 1. Intentional legacy-default contracts — 2

These tests exist to lock the old default. They do not set `entry_fill_model`. Under option (a) they must call `market` by name and stop claiming that `market` is the default. They are not IOC bugs.

- `tests/test_replay_ioc_baseline.py::test_default_market_model_unchanged`
- `tests/test_replay_identity_propagation.py::TestReplayEngineIdentityPropagation::test_default_market_model_pnl_and_result_unchanged`

A new test, still unwritten, has to show that an unset `ENTRY_FILL_MODEL` and a missing `fill_model.entry_fill_model` load `ioc_limit`.

### 2. Subject is not the fill model — pin `market` — 1

The assertion needs one filled trade so the journal join can be checked. The fill model is not the subject. Set `market` explicitly. Do not retarget the expected result to `CANCELLED`.

- `tests/test_replay_identity_propagation.py::TestReplayEngineIdentityPropagation::test_replay_output_resolves_cleanly_through_journal_reader`

### 3. Test double rejects the IOC call — 3

The IOC no-fill path calls the stub with `market_price`. The stub does not accept that argument, so the test dies before it can assert cancellation taxonomy. Widen the stub. Do not catch the `TypeError` inside the runner.

- `tests/test_execution_safety.py::test_execution_failure_clears_phantom_open`
- `tests/test_execution_safety.py::test_cancelled_outcome_carries_no_fill_taxonomy_fields`
- `tests/test_five_min_feed.py::test_failed_5m_execution_retains_arm_for_retry`

### 4. Fixture assumes an unconditional fill — 31

Risk is `APPROVED`. The entry then comes back `CANCELLED` / `ENTRY_NOT_FILLED` / `BLOCKED_EXECUTION_FAILED`, or the trade count stays 0. The log line is `ENTRY_NOT_FILLED: ... limit not filled (CANCELLED)`. That is the IOC model refusing a bar that never trades through the limit. It is not a broken gate.

For each test, pick one and write it down in the test:

- The test is about a fill. Give it a bar that trades through the IOC cap, and assert the fill or the cancel from that bar.
- The test is about something else (proof isolation, max-trades, journal latch, banner, event id). Set `ENTRY_FILL_MODEL=market` or `entry_fill_model="market"` so the subject still fills.

Do not replace an expected `TRADE` or `WIN` with `BLOCKED_EXECUTION_FAILED` unless the test's job is the no-fill.

Webhook and runner:

- `tests/test_webhook.py::test_runner_trending_orb_breakout_mes_produces_trade`
- `tests/test_webhook.py::test_runner_logs_exec_trace_around_broker_call`
- `tests/test_webhook.py::test_paper_open_writes_intent_then_confirmed_trade`
- `tests/test_webhook.py::test_runner_blocks_when_max_trades_reached`
- `tests/test_runner_budget_observation.py::test_max_trades_reached_setup_still_observed_but_not_executed`
- `tests/test_shadow_feed_banner.py::test_main_15m_alert_behavior_is_unchanged`
- `tests/test_event_id_propagation.py::test_confirmed_trade_result_and_journal_carry_event_id`
- `tests/test_event_id_propagation.py::test_event_id_does_not_change_decision_or_routing`
- `tests/test_five_min_feed.py::test_process_alert_executes_only_the_armed_15m_setup`

Fault injection (the paper trade row is never written because the entry never fills):

- `tests/fault_injection/test_jw_journal_write_failure.py::test_jw4_paper_lost_trade_row_never_leads_to_second_entry`
- `tests/fault_injection/test_jw_journal_write_failure.py::test_jw_fix_c_latch_blocks_entries_but_not_resolution`
- `tests/fault_injection/test_jw_journal_write_failure.py::test_jw_fix_e_lost_main_book_outcome_latches_and_alerts`

Proof lanes (baseline fixture expected a `TRADE` and got `BLOCKED_EXECUTION_FAILED`):

- `tests/test_mnq_orb_breakout_inverse_paper.py::test_observe_only_is_a_noop`
- `tests/test_mnq_orb_breakout_proof.py::test_observe_only_does_not_change_existing_orb_breakout_behavior`
- `tests/test_mnq_orb_breakout_proof.py::test_observe_only_audit_recorded_but_trade_proceeds`
- `tests/test_mnq_orb_breakout_proof.py::test_paper_sim_does_not_affect_orb_reclaim_on_mnq`
- `tests/test_mnq_orb_reclaim_proof.py::test_observe_only_audit_recorded_but_trade_proceeds`
- `tests/test_mnq_vwap_hold_proof.py::test_paper_sim_does_not_affect_orb_reclaim_on_mnq`

Replay engine (sample day and carried positions come back `CANCELLED` or a zero trade count instead of `WIN`):

- `tests/test_replay_engine.py::test_replay_engine_runs_sample_day`
- `tests/test_replay_engine.py::test_replay_stops_after_max_trades`
- `tests/test_replay_engine.py::test_manifest_replay_is_idempotent`
- `tests/test_replay_engine.py::TestFullDayReplay2026_05_22::test_at_least_one_win`
- `tests/test_replay_engine.py::TestFullDayReplay2026_05_22::test_positive_realized_pnl`
- `tests/test_replay_engine.py::test_position_open_at_day_boundary_carries_forward_and_resolves`
- `tests/test_replay_engine.py::test_position_open_at_day_boundary_resolves_after_multiple_days`
- `tests/test_replay_engine.py::test_position_still_open_at_true_corpus_tail_is_still_flagged`
- `tests/test_replay_engine.py::test_position_open_at_day_boundary_carries_forward_across_bare_run_calls`
- `tests/test_replay_engine.py::test_carried_loss_updates_daily_risk_state_for_later_same_day_candidate`
- `tests/test_replay_engine.py::test_carried_resolution_mixed_instrument_candle_never_resolves_wrong_bracket`
- `tests/test_replay_engine.py::test_carried_position_absent_instrument_blocks_new_entry_and_later_resolves`
- `tests/test_replay_engine.py::test_same_day_mixed_instrument_candle_never_resolves_wrong_bracket`

### 5. Not caused by the fill flip — 1

`tests/test_options_paper_v1.py::test_frozen_212r_cross_trade_price_binds_to_production_selector_context_path` fails because replay uses the wall clock and the 2026-11-20 expiry is now inside the 14-DTE gate. The same failure reproduces without the fill-default diff. It is not part of option (a).

## Gates before any implementation

1. Read the box `ENTRY_FILL_MODEL` and the `load_config()` fallback, read-only. This session did not.
2. Keep `market` as an explicit setting for frozen reproduction. Do not delete it.
3. Classify each of the 37 fill failures as section 1, 2, 3, or 4 above before editing assertions. The list above is that classification.
4. Check replay and paper entry-price availability, IOC cancel behavior, and live/replay fill parity on one fixture that is supposed to fill and one that is supposed to cancel.
5. The relevant suite must pass. A green run that only rewrites `TRADE`/`WIN` into `CANCELLED` does not pass this gate.
6. No deploy follows a merge. The October HOLD still governs the box.

## Out of scope

New strategy research. Installing external bots. Merging `6fb01c9` as it stands. Changing `PaperBroker` live order routing. Editing `risk_rules.yaml` thresholds.
