# Fill-default flip to `ioc_limit` — change spec

Status: **SPEC ONLY. DO NOT MERGE. DO NOT IMPLEMENT FROM THIS BRANCH.**

Date: 2026-11-09

Operator decision (Imani), option (a): the honest default is eventually `ioc_limit`. Hold `claude/honest-default-entry-fill-model` until this spec exists. This document is that spec. It changes no code, no test assertion, no runtime, and no deploy state.

| Item | Value |
|---|---|
| Merge base / `main` | `d703232b7c9df3bf2b942f1437235dc5488a0890` (`d703232b`, PR #1257) |
| Held WIP | `claude/honest-default-entry-fill-model` @ `6fb01c935b7591dde5e9340351a16658b3ce79ee` (`6fb01c9`) |
| WIP parents | `6fb01c9` is one commit ahead of `d703232b` |
| This branch | `cursor/fill-default-ioc-spec` off `d703232b` |
| Evidence cited | `docs/ioc-faithful-baseline-622d-2026-07-06.md` |

`6fb01c9` flips the unset default from `market` to `ioc_limit` in three files only: `.env.example`, `config/settings.py`, and `ops/project_check/runtime.py`. It does not touch the broker, the risk engine, strategy logic, or the proof-lane hardcodes. It is not mergeable. The suite results below are the reason.

## What was run

Both runs used the repo virtualenv (Python 3.13.15, pytest 9.2.0), from a clean tree of that exact SHA. `ENTRY_FILL_MODEL` was unset in the shell. `tests/conftest.py` sets `PYTHON_DOTENV_DISABLED=1`, so a developer `.env` is not loaded. No `.env` file was read. No VPS was contacted.

```text
.venv/bin/python -B -m pytest -q --tb=line -p no:cacheprovider
```

| SHA | Result | Passed | Failed | Skipped | Wall time | Exit |
|---|---|---:|---:|---:|---:|---:|
| `d703232b` (main) | 1 failed | 9017 | 1 | 8 | 1341.60s | 1 |
| `6fb01c9` (WIP) | 38 failed | 8977 | 38 | 11 | 1342.57s | 1 |

Both collected 9026 tests. The WIP has 37 failures that pass on main, plus the one failure that fails on both. The WIP also skips 3 tests that pass on main. 37 failures + 3 skips = 40, which is 9017 − 8977.

`git rev-parse --is-shallow-repository` is `false`. `/usr/bin/ssh-keygen` is present. No failure in either run is an `ssh-keygen` or shallow-clone error.

### Shared failure, both SHAs (not part of the flip)

`tests/test_options_paper_v1.py::test_frozen_212r_cross_trade_price_binds_to_production_selector_context_path`

`tests/test_options_paper_v1.py:423` raises `TypeError: 'NoneType' object is not subscriptable` because `evidence["production_replay_result"]` is `None`. The same assertion fails on `d703232b` and on `6fb01c9`. The WIP diff does not touch this test or `alert_ranker/`. It is a pre-existing options failure in this environment. It is not class A, B, or C.

## Why the WIP fails

On `d703232b`:

- `config/settings.py:373` sets `SystemConfig.entry_fill_model` to `"market"`.
- `config/settings.py:930-933` resolves `ENTRY_FILL_MODEL`, then `risk_rules.yaml` `fill_model.entry_fill_model`, then `"market"`. A blank env value also becomes `"market"` via `or "market"`.
- `ops/project_check/runtime.py:193` reports the same code default, `"market"`.
- `risk_rules.yaml` `fill_model` (lines 380-382) sets slippage and pessimistic-both-hit only. It does not set `entry_fill_model`.

On `6fb01c9` those three fallbacks become `"ioc_limit"`. Nothing else in the fill path changed.

Two constructors still default to `"market"` on both SHAs, and the WIP did not change them:

- `execution/paper_broker.py:76` — `PaperBroker(entry_fill_model="market")`. Direct `PaperBroker()` callers keep the legacy plan-price fill. `tests/test_trend_and_fills.py::test_defaults_preserve_legacy_optimistic_behavior` passed on the WIP.
- `replay/replay_engine.py:294` and `webhook/runner.py:345` use `getattr(cfg, "entry_fill_model", "market")`. `SystemConfig` always has the attribute, so those fallbacks are idle when the dataclass default flips. A duck-typed config that omits the attribute still selects `market`.

The shared mechanics fixtures construct `SystemConfig()` and omit `entry_fill_model` and `entry_tolerance_ticks_by_root`:

- `tests/conftest.py:135` (`config` fixture). Its comment says mechanics tests use the legacy fill model. It sets slippage to 0 and pessimistic-both-hit to false, and it leaves the fill-model field to the dataclass default.
- `tests/test_e2e_scenarios.py:135` (`_base_config`), used by the proof-lane tests and other webhook scenarios.

`PaperBroker` with `ioc_limit` and an empty tolerance map uses 0 ticks (`execution/paper_broker.py:121-125` and `:238-249`). A decision-bar close even one tick through the plan entry books `CANCELLED` / `ENTRY_NOT_FILLED`. `load_config()` is different: `_entry_tolerance_map()` (`config/settings.py:733-765`) fills MES=16 / MNQ=32 when the tolerance env vars are unset. The WIP default on a bare `SystemConfig()` is a 0-tick IOC. The WIP default on `load_config()` is a 16/32 IOC. The failing mechanics tests are the 0-tick case.

When that no-fill happens inside the webhook runner, `webhook/runner.py:3274-3353` journals `exit_reason="execution_failed:CANCELLED"` and returns `decision="BLOCKED_EXECUTION_FAILED"`. Replay keeps `ENTRY_NOT_FILLED` (`replay/replay_engine.py:923-948`, `execution/paper_broker.py:364-394`). `tests/test_execution_safety.py:360-396` already locks the runner string `execution_failed:CANCELLED` for `ops/fill_realism.py` and `ops/proof_30_mnq.py`. The flip must leave that journal string in place.

## Classification

37 tests fail on `6fb01c9` and pass on `d703232b`.

| Class | Count | Meaning for the later implementation |
|---|---:|---|
| A | 34 | The test locks legacy `market` behavior (a fill, a WIN, a plan-price entry, a counted trade, a carried position). Pin `entry_fill_model="market"` or `ENTRY_FILL_MODEL=market` in that test's setup. Keep the current assertions. |
| B | 3 | The test is a cancel / no-fill path. The runner now passes `market_price` into `execute_bracket`, and the test double does not accept it. Accept the keyword. Keep the current assertions, including `execution_failed:CANCELLED`. |
| C | 0 | — |

### A — pin `market`, keep the assertions (34)

Shared root cause for this class: the test builds a `SystemConfig` without `entry_fill_model` (`tests/conftest.py:135` or `tests/test_e2e_scenarios.py:135`). On `6fb01c9` that config is `ioc_limit` with an empty tolerance map, so `execution/paper_broker.py:238-249` cancels the entry. Replay then has no WIN and no carried position. The webhook returns `BLOCKED_EXECUTION_FAILED` from `webhook/runner.py:3353`.

| Test | What broke |
|---|---|
| `tests/fault_injection/test_jw_journal_write_failure.py::test_jw4_paper_lost_trade_row_never_leads_to_second_entry` | Harness never saw a paper TRADE row (`FaultSetupError`). |
| `tests/fault_injection/test_jw_journal_write_failure.py::test_jw_fix_c_latch_blocks_entries_but_not_resolution` | Setup required an open paper trade; decision was `BLOCKED_EXECUTION_FAILED`. |
| `tests/fault_injection/test_jw_journal_write_failure.py::test_jw_fix_e_lost_main_book_outcome_latches_and_alerts` | Same setup failure. |
| `tests/test_event_id_propagation.py::test_confirmed_trade_result_and_journal_carry_event_id` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_event_id_propagation.py::test_event_id_does_not_change_decision_or_routing` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_five_min_feed.py::test_process_alert_executes_only_the_armed_15m_setup` | Risk approved, then `BLOCKED_EXECUTION_FAILED`. The test also asserts the fill entry equals the armed plan price (`tests/test_five_min_feed.py:269`). That plan-price assertion stays; pin `market`. |
| `tests/test_mnq_orb_breakout_inverse_paper.py::test_observe_only_is_a_noop` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_mnq_orb_breakout_proof.py::test_observe_only_audit_recorded_but_trade_proceeds` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. `apply_override` is false; this is the existing path. |
| `tests/test_mnq_orb_breakout_proof.py::test_observe_only_does_not_change_existing_orb_breakout_behavior` | Baseline fixture no longer produces a TRADE. |
| `tests/test_mnq_orb_breakout_proof.py::test_paper_sim_does_not_affect_orb_reclaim_on_mnq` | The non-proof `orb_reclaim` control expected `TRADE`. |
| `tests/test_mnq_orb_reclaim_proof.py::test_observe_only_audit_recorded_but_trade_proceeds` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_mnq_vwap_hold_proof.py::test_paper_sim_does_not_affect_orb_reclaim_on_mnq` | The non-proof control expected `TRADE`. |
| `tests/test_replay_engine.py::TestFullDayReplay2026_05_22::test_at_least_one_win` | `wins == 0`. |
| `tests/test_replay_engine.py::TestFullDayReplay2026_05_22::test_positive_realized_pnl` | Realized P&L `0.0`. |
| `tests/test_replay_engine.py::test_carried_loss_updates_daily_risk_state_for_later_same_day_candidate` | No carried LOSS, so `stopped_reason` stayed `None` instead of `max_consecutive_losses`. |
| `tests/test_replay_engine.py::test_carried_position_absent_instrument_blocks_new_entry_and_later_resolves` | Carried-position map was empty. |
| `tests/test_replay_engine.py::test_carried_resolution_mixed_instrument_candle_never_resolves_wrong_bracket` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_engine.py::test_manifest_replay_is_idempotent` | Locked market-model totals (`approved_trades == 3`, `wins == 2`, `losses == 1`, P&L `245.0` at `tests/test_replay_engine.py:665-669`) became 6 approvals. |
| `tests/test_replay_engine.py::test_position_open_at_day_boundary_carries_forward_across_bare_run_calls` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_engine.py::test_position_open_at_day_boundary_carries_forward_and_resolves` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_engine.py::test_position_open_at_day_boundary_resolves_after_multiple_days` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_engine.py::test_position_still_open_at_true_corpus_tail_is_still_flagged` | A `CANCELLED` OUTCOME row was written; the test requires no OUTCOME while the position is still open (`tests/test_replay_engine.py:1106`). |
| `tests/test_replay_engine.py::test_replay_engine_runs_sample_day` | `wins` was 0; the test locks 1 win and positive P&L (`tests/test_replay_engine.py:37-40`). |
| `tests/test_replay_engine.py::test_replay_stops_after_max_trades` | Approval count was 6; the test locks 3. Cancelled IOC attempts are not counted, so the cap does not stop the run. |
| `tests/test_replay_engine.py::test_same_day_mixed_instrument_candle_never_resolves_wrong_bracket` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_identity_propagation.py::TestReplayEngineIdentityPropagation::test_default_market_model_pnl_and_result_unchanged` | Name and body lock the default market model's win count. `wins` was 0. |
| `tests/test_replay_identity_propagation.py::TestReplayEngineIdentityPropagation::test_replay_output_resolves_cleanly_through_journal_reader` | Outcome `CANCELLED` where the test locks `WIN`. |
| `tests/test_replay_ioc_baseline.py::test_default_market_model_unchanged` | Docstring and body lock one WIN under the legacy default (`tests/test_replay_ioc_baseline.py:60-67`). The sibling IOC tests in the same file passed. |
| `tests/test_runner_budget_observation.py::test_max_trades_reached_setup_still_observed_but_not_executed` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`, so the cap setup never armed. |
| `tests/test_shadow_feed_banner.py::test_main_15m_alert_behavior_is_unchanged` | `BLOCKED_EXECUTION_FAILED` is outside `('TRADE', 'NO_TRADE', 'RISK_REJECTED')`. |
| `tests/test_webhook.py::test_paper_open_writes_intent_then_confirmed_trade` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_webhook.py::test_runner_blocks_when_max_trades_reached` | `max_trades_per_day` never tripped, because the attempts did not remain open trades. |
| `tests/test_webhook.py::test_runner_logs_exec_trace_around_broker_call` | Expected `TRADE`, got `BLOCKED_EXECUTION_FAILED`. |
| `tests/test_webhook.py::test_runner_trending_orb_breakout_mes_produces_trade` | Expected `TRADE` / `OPEN`. Docstring locks the MES bracket and `close=5900.0` (`tests/test_webhook.py:439-479`). With 0-tick IOC that close is through the plan entry. |

The proof-file rows above are the control path. They are class A. The proof PaperBroker itself is already pinned in code and its spy tests passed on the WIP. See "Proof lanes" below.

### B — IOC cancel path does not accept the entry-price argument (3)

These tests replace `PaperBroker.execute_bracket` with a double that returns `CANCELLED`. They assert the journal and the arm/phantom behavior of a no-fill. They passed on `d703232b` because the runner calls `execute_bracket(order)` when the model is `market`.

On `6fb01c9`, `load_config()` / the dataclass default is `ioc_limit`. The runner sees a `PaperBroker` whose `_entry_fill_model` is `ioc_limit` and a bar close, and it calls `execute_bracket(order, market_price=close)`.

| Test | Stub | Root cause |
|---|---|---|
| `tests/test_execution_safety.py::test_execution_failure_clears_phantom_open` | `_cancelled(self, order)` at `tests/test_execution_safety.py:335` | `TypeError: unexpected keyword argument 'market_price'` |
| `tests/test_execution_safety.py::test_cancelled_outcome_carries_no_fill_taxonomy_fields` | `_cancelled(self, order)` at `tests/test_execution_safety.py:374` | Same `TypeError`. This test also locks `exit_reason == "execution_failed:CANCELLED"` at line 396. That assertion stays. |
| `tests/test_five_min_feed.py::test_failed_5m_execution_retains_arm_for_retry` | `_cancel(self, order)` at `tests/test_five_min_feed.py:288` | Same `TypeError`. The arm-retention assertions stay. |

Call site: `webhook/runner.py:3207-3210`. The price is chosen at `webhook/runner.py:3186-3200` (`state.ohlc.close` for a paper IOC broker).

`load_permissive_config()` (`tests/conftest.py:109-127`) calls `load_config()`, so the two execution-safety tests follow the `load_config()` default even if the `config` fixture is later pinned to `market`. Pinning only the fixture would hide `test_failed_5m_execution_retains_arm_for_retry` and would leave the other two red. The doubles must accept `market_price` (and ignore it, since they force `CANCELLED`).

### C

None.

### Skips that pass on main (not failures; the flip still has to undo them)

These three skip on `6fb01c9` when bar 1 is not `TRADE` (`tests/test_webhook.py:890` and the matching guards in the other two). On main, bar 1 is `TRADE` and the body runs.

| Test | Skip message on `6fb01c9` |
|---|---|
| `tests/test_webhook.py::test_runner_resolves_open_position_on_next_bar` | `Signal engine produced NO_TRADE on bar 1 — skip resolution test` |
| `tests/test_webhook.py::test_runner_blocks_when_open_position_does_not_resolve` | `Signal engine produced NO_TRADE on bar 1 — skip open-position test` |
| `tests/test_webhook.py::test_process_alert_writes_runner_shadow_proof` | `Signal engine produced NO_TRADE on bar 1` |

They are the same class-A setup: the `config` fixture's new 0-tick IOC default prevents the opening fill. After the fixture pins `market`, these three must execute and pass. A green suite that still skips them has not restored the coverage.

## Change spec

Implement this on a new branch off then-current `main`. Leave `claude/honest-default-entry-fill-model` unmerged. Do not start from `6fb01c9` as the implementation commit; cherry-pick or rewrite the three-file default flip only as part of a branch that also does the items below.

### 1. Library default becomes `ioc_limit`

- `SystemConfig.entry_fill_model` default becomes `ioc_limit` (`config/settings.py:373` on `d703232b`).
- `load_config()` fallback becomes `ioc_limit` (`config/settings.py:930-933`).
- `.env.example` documents `ENTRY_FILL_MODEL=ioc_limit` and says frozen-result reproduction sets `market` explicitly.
- `ops/project_check/runtime.py:193` reports `ioc_limit` when both the env var and `risk_rules.yaml` `fill_model.entry_fill_model` are absent, matching `load_config()`.
- Precedence stays: environment, then yaml, then code default. Do not add an `entry_fill_model` key to `risk_rules.yaml` in this change. A yaml value would hide an unset server env.
- `PaperBroker.__init__`'s own default stays `"market"` (`execution/paper_broker.py:76`). Callers that have a `SystemConfig` already pass `config.entry_fill_model`.
- `replay/replay_engine.py:294` and `webhook/runner.py:345` currently fall back to `"market"` when the attribute is missing. Move those fallbacks to `"ioc_limit"` in the same change so a config object without the attribute cannot keep the plan-price artifact. `PaperBroker()` with no model argument is the remaining explicit legacy constructor, covered by `test_defaults_preserve_legacy_optimistic_behavior`.

### 2. Historical `market` reproduction stays explicit

- `market` remains a legal `entry_fill_model` (`config/settings.py:1250-1253`).
- The `config` fixture (`tests/conftest.py:135`) and `_base_config` (`tests/test_e2e_scenarios.py:135`) pass `entry_fill_model="market"`. Their job is deterministic legacy fills, and they do not populate IOC tolerances.
- Every class-A test keeps its current expected decision, fill status, win count, P&L, and plan-price entry. The pin lives in the setup, not in a rewritten assertion.
- `tests/test_replay_ioc_baseline.py::test_default_market_model_unchanged` and `test_default_market_model_pnl_and_result_unchanged` set `market` explicitly and keep the one-WIN assertion. Add a separate test that the `SystemConfig` / `load_config()` default, with `ENTRY_FILL_MODEL` unset and no yaml key, is `ioc_limit`. That new test is the lock for the flip. It is the one new expected value. It does not replace the WIN assertions.
- Sample-day replay with explicit `market` still reports `approved_trades == 1` and `wins == 1` (`tests/test_replay_engine.py:28-40`). The existing explicit `ioc_limit` cases in `tests/test_replay_ioc_baseline.py` stay as they are: zero tolerance cancels, generous tolerance wins.

### 3. Server env unset fails closed

`webhook/app.py:598` calls `load_config()` at import, which is how `uvicorn webhook.app:app` starts the futures process.

On 2026-09-23 the futures service was observed with `ENTRY_FILL_MODEL` unset, `BROKER=tradovate`, and the code default `market` (`docs/futures-current-status-2026-09-22.md`, runtime reconciliation, the environment bullet and the note that the box default is `market` with no env or yaml override). That box read is historical. This session did not re-read the VPS. Current server env is **UNVERIFIED**.

Required startup behavior:

- After env load, if `ENTRY_FILL_MODEL` is missing or blank, the futures webhook process raises `ConfigError` and does not serve. Blank is the same as unset. Today's `or "market"` (`config/settings.py:932`) treats blank as the default; the server path must not.
- The check reads the process environment. It runs even when `risk_rules.yaml` has no `entry_fill_model` and even when the library default is `ioc_limit`.
- Explicit `ioc_limit` and explicit `market` both pass the check and then go through the existing validator.
- `market_at_reference` stays off the production broker. The existing fail-closed wrapper remains the only user of that model.
- Replay CLI and unit tests that are not the server process may use the library default `ioc_limit`. Put the server refusal at the webhook startup path so importing `load_config()` from a replay test does not require the env var. Tests of the server entrypoint cover the refusal.
- `ops/project_check/runtime.py` may still report the library fallback for an offline snapshot. Reporting `code_default=ioc_limit` is a description of `load_config()`, not permission for the server to keep running with the var unset.

### 4. Replay and paper entry-price availability

- `ioc_limit` fills against the decision-bar close. Replay already passes `candle.close` (`replay/replay_engine.py:877` and `:904-906`). The runner already passes `state.ohlc.close` for a paper IOC broker (`webhook/runner.py:3186-3210`). `tests/test_webhook.py::test_normal_paper_ioc_uses_decision_close_like_replay` passed on `6fb01c9`. Keep that parity.
- `execution/paper_broker.py:239-243` raises `ValueError` when `ioc_limit` is asked to `execute_bracket` without `market_price`. That exception must become a fail-closed no-fill: `result="CANCELLED"`, `exit_reason="ENTRY_NOT_FILLED"` (or one dedicated missing-price reason if a caller already matches it), no position, no P&L. It must not escape `process_alert` as an uncaught exception. Replay must book that outcome and continue, the same way it already books an unreachable IOC (`replay/replay_engine.py:923-948`).
- A bare `SystemConfig()` has `entry_tolerance_ticks_by_root={}` (`config/settings.py:382`). Under `ioc_limit` that is 0 ticks, which is not the live MES=16 / MNQ=32 fallback inside `load_config()`. Mechanics fixtures pin `market` so they never depend on that 0-tick map. Production replay and the server, once the env is set to `ioc_limit`, go through `load_config()` and keep the existing 16/32 fallback when the tolerance env vars are unset. Do not change those fallback numbers in this flip. The Tradovate broker's own unset-tolerance fallback stays 0, as `config/settings.py:741-748` already documents; on a box that sets `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES` and `_MNQ` the two paths already agree.

### 5. IOC cancellation behavior

- An unreachable limit stays `CANCELLED` / `ENTRY_NOT_FILLED`, no position, no session-budget consumption. That is the live and replay contract in `.env.example` and in `replay/replay_engine.py:923-948`.
- The runner's journal `exit_reason` for that case stays `execution_failed:CANCELLED` (`webhook/runner.py:3319-3322`). `tests/test_execution_safety.py:396` locks it because `ops/fill_realism.py` and `ops/proof_30_mnq.py` match that string. Do not rename it as part of making the suite green.
- The runner's decision for a non-open paper entry stays `BLOCKED_EXECUTION_FAILED` (`webhook/runner.py:3353`). Class-A tests that expected `TRADE` get `market` pinned so they still fill. They do not get a rewritten expected decision.
- The three class-B doubles accept `market_price` and still return `CANCELLED`, so the taxonomy and arm-retention assertions run under `ioc_limit`.

### 6. Fill parity

- Paper IOC and replay IOC both use the decision-bar close as `market_price`. The passing test named above is the check. Keep it.
- An IOC fill price is the market, capped at entry ± tolerance, with adverse slippage (`execution/paper_broker.py:246-255`). A `market` fill price is the plan entry ± slippage (`execution/paper_broker.py:271-274`). Tests that assert the plan price, including `tests/test_five_min_feed.py:269`, pin `market` and keep the assertion.
- Proof lanes already construct their own PaperBroker with `entry_fill_model="market"` at `webhook/runner.py:1460-1468` (open-position resolution) and `webhook/runner.py:2621-2628` (entry). The WIP did not change those lines, and the spy tests that assert `entry_fill_model == "market"` passed. Leave those pins. This flip does not make proof evidence IOC-honest. A later decision is required before a proof lane leaves `market`.
- The inverse paper lane already hardcodes `entry_fill_model="ioc_limit"` at `webhook/runner.py:1451` and `:2601`. Leave it.
- Tradovate order type follows `TRADOVATE_ENTRY_EXECUTION_MODE` (`execution/tradovate_broker.py:1413`), not `ENTRY_FILL_MODEL`. This flip does not change demo or live order construction. The paper and replay paths do change, and the server fail-closed check in section 3 is what stops an unset box from changing paper fills on the next process start.

### 7. Server behavior if `ENTRY_FILL_MODEL` is unset

| Path | `d703232b` today | After this spec |
|---|---|---|
| `load_config()` with unset env and no yaml key | `market` | `ioc_limit` for library / replay / tests |
| Futures webhook process (`webhook/app.py:598`) with unset or blank env | Serves, paper paths use `market` | Refuses to start |
| `BROKER=tradovate` order construction | `TRADOVATE_ENTRY_EXECUTION_MODE` | Unchanged |
| Proof paper broker | Hardcoded `market` | Unchanged |
| Inverse paper broker | Hardcoded `ioc_limit` | Unchanged |
| `PaperBroker()` with no model argument | `market` | Unchanged |
| Bare `SystemConfig()` used by mechanics fixtures | `market`, empty tolerance map | Fixtures pin `market` |
| Project-check code default | `market` | `ioc_limit`, matching `load_config()` |

Deploying `6fb01c9` as-is onto a host whose futures env still omits `ENTRY_FILL_MODEL` would switch every paper path that reads `SystemConfig.entry_fill_model` from plan-price fills to IOC fills at the next process start. The 2026-09-23 observation says that variable was unset. Whether it is still unset is unverified. The implementation must not be deployed to find out.

### 8. Acceptance bar

The implementation is done only when all of the following are true:

- `python -B -m pytest -q` on the implementation head is green for every test this flip can affect. The class-A assertions are unchanged. The class-B taxonomy and arm assertions are unchanged. The three webhook tests listed under skips execute and pass.
- A new test locks the library default `ioc_limit` with the env var unset. A new test locks that the webhook startup path raises when `ENTRY_FILL_MODEL` is unset and when it is blank, and that explicit `market` and explicit `ioc_limit` are accepted.
- A test locks the missing-`market_price` IOC path as `CANCELLED` / no position, not as a raised `ValueError`.
- `tests/test_replay_ioc_baseline.py` explicit IOC cases still pass. Explicit `market` sample-day replay still has one win.
- `tests/test_webhook.py::test_normal_paper_ioc_uses_decision_close_like_replay` still passes.
- No edit to `risk_rules.yaml` thresholds, the live-trading lock, broker routing, strategy status, or the proof-lane `market` pins.
- The pre-existing `tests/test_options_paper_v1.py:423` failure, if it still fails on the merge base, is reported as pre-existing. It is not fixed by editing that assertion, and it is not a fill-model regression.
- No deploy, no service restart, no broker call, no merge of `6fb01c9`, and no merge of the implementation until operator GO after independent review.

Editing an expected WIN, P&L, plan-price entry, or `TRADE` decision so that it matches a 0-tick or 16/32 IOC outcome is not acceptance.

## What blocks the flip

1. `6fb01c9` itself: 37 new failures and 3 new skips. Held. Do not merge.
2. The mechanics fixtures inherit the dataclass default and carry an empty tolerance map. A one-line default change turns them into a 0-tick IOC, which is not the live 16/32 model. They have to pin `market` before the default moves.
3. The three class-B cancel doubles reject `market_price`. The no-fill journal path does not run until they accept it.
4. `execution/paper_broker.py:239-243` raises when an IOC entry has no price. The server and replay have to book a no-fill instead.
5. An unset `ENTRY_FILL_MODEL` on the futures process must refuse to start. A silent library default would change paper fills on any host that still matches the 2026-09-23 observation. Current host env is unverified, and this work does not check it.
6. `replay/replay_engine.py:294` and `webhook/runner.py:345` still fall back to `"market"` when the attribute is absent. Those fallbacks have to move with the dataclass default.
7. Proof lanes stay on hardcoded `market` (`webhook/runner.py:1467` and `:2628`). Shipping the library default does not make proof evidence honest, and this spec does not authorize retargeting them.
8. No implementation, deploy, or restart is authorized by this document.

## Next action

Builder implements sections 1-8 on a new branch off `main`, with the class-A pins and the class-B signature updates, then runs `python -B -m pytest -q`. Independent review before any merge discussion. Operator GO is required before any host change. `claude/honest-default-entry-fill-model` stays unmerged.
