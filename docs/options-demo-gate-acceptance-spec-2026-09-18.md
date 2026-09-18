# Options Backtest → DEMO gate — shared-infrastructure acceptance spec (2026-09-18)

**Status: SPEC ONLY. Implementation is HELD until after 2026-09-30 under the standing directive
(no new strategy / gate / runtime / config behavior before 09-30 without an explicit operator waiver).**
Operator ruling 09-18: the four infrastructure items below are runtime/config behavior, not offline research
— do not build or deploy them yet. This document gives the builder an exact, fail-closed target for 09-30.

## 1. Baseline (preserved, do not modify)

| | |
|---|---|
| Baseline package | PR #653 → `data/options_demo_gate_212R_2026_09_18/evidence.json` |
| Gate | `scripts/options_demo_qualification_gate.py` (#651, `options_manager/validation/demo_qualification.py`) |
| Baseline `code_sha` | `5633a59` (main = #651 + #652) |
| Baseline verdict | `BLOCKED`, **60** blockers (exit 2) |
| Mechanically verified at baseline | `code_sha == HEAD`; underlying manifest SHA-256 (11 files); policy (`options_manager/config.py`) SHA-256 |
| Strategy under test | `strat_212_reversal_30m_options` — classification **`WAIT`** (retro +11.9pp first-sight 1R, n=77, did **not** persist prospectively: −4.3pp, n=21, INSUFFICIENT) |

The 60 baseline blockers partition exactly into **37 retirable by shared infrastructure** (items 1, 2, 2B, 3)
and **23 that must remain** after infrastructure lands, because they are 212R's own evidence burden.
The machine-readable partition is `docs/options-demo-gate-acceptance/expected_remaining_blockers_212R.json`.

## 2. Common rules for every item

- **Fail closed.** Every new input is required; absent, `null`, non-finite, or boolean-in-numeric-field → reject with a stable reason code. (`demo_qualification._number/_integer` already reject `bool`; the new code must match.)
- **Frozen inputs, hashed.** Every rule the gate is asked to attest as `*_frozen` must live in a file whose SHA-256 is recorded in the evidence packet and verified against bytes by the gate (`_hash_check`), never a free-text claim.
- **Determinism.** Same frozen input → byte-identical output. Proven by a test that runs the function twice on the fixture and once under replay, then compares serialized output.
- **No hindsight.** Any datum used by a decision must carry a timestamp ≤ the decision timestamp. Proven by a fixture where the only better contract/quote is timestamped *after* the decision and must not be chosen.
- **Replay/forward parity.** The same module and the same serialized inputs are used by replay and by the forward/paper path; a golden fixture pins the output.
- **Nothing here authorizes activation.** A retired blocker retires a *blocker*; `paper_demo_activation_authorized` and `live_trading_authorized` stay hard-coded `false`.

## 3. Item 1 — Mechanical contract selector

**Requirement.** A pure function that, given a frozen selector rule file and a decision-time chain snapshot,
chooses **exactly one** contract (or `NO_CONTRACT` with a reason code) from: deterministic expiration rule,
strike / delta / moneyness rule, DTE rule, liquidity rule. `contracts/contract_validator.evaluate_contract_constraints`
stays as the *validator* applied after selection; it is not a selector and must not be relabelled as one.

**Inputs (all required).** `selector_rule` (frozen file, hashed), `decision_ts`, `underlying_price@decision_ts`,
`direction`, chain snapshot rows each carrying `contract_id, expiration, strike, right, bid, ask, quote_ts, volume, open_interest, delta`
with `quote_ts ≤ decision_ts` (rows violating this are excluded before ranking, and counted).

**Outputs.** `{contract_id, expiration, strike, right, dte, delta, spread_percent, selection_reason, rule_id, rule_sha256, candidates_considered, candidates_excluded_by_reason}`
— serializable, byte-stable.

**Proof required (tests + fixtures, all fail-closed):**
1. Determinism: fixture chain → same `contract_id` twice and under replay.
2. Expiration rule: two expirations satisfying DTE → the rule picks one and the choice is explained by the rule, not by outcome.
3. Strike/delta/moneyness rule: fixture with a strike ladder → single pick; ladder permuted → same pick.
4. DTE floor (`risk_min_dte_days`, currently 14) → contract below floor never chosen even if best delta.
5. Liquidity rule (`quality_max_spread_percent` 20 / `quality_min_open_interest` 500) → illiquid best-delta contract never chosen.
6. No hindsight: a strictly better contract whose `quote_ts > decision_ts` is not chosen; count appears in `candidates_excluded_by_reason`.
7. Empty/partial chain → `NO_CONTRACT` with reason; never a default pick.
8. Replay/forward golden parity fixture: identical serialized output from both code paths.

**Evidence keys retired** (the packet sets `selection_rule_path` to the selector rule file; the gate hashes it):

| Baseline blocker line (#653 `report.txt`) |
|---|
| `contract_selection.mechanical_selection must be explicitly true` |
| `contract_selection.expiration_rule_frozen must be explicitly true` |
| `contract_selection.strike_rule_frozen must be explicitly true` |
| `contract_selection.dte_rule_frozen must be explicitly true` |
| `contract_selection.moneyness_or_delta_rule_frozen must be explicitly true` |
| `contract_selection.no_hindsight_contract_choice must be explicitly true` |
| `contract_selection.same_selector_replay_and_forward must be explicitly true` |
| `contract_selection.selection_rule_path is required` |
| `contract_selection.selection_rule_sha256 is required` |
| `golden_parity.contract_selection_parity must be explicitly true` |

## 4. Item 2 — Timestamped option-quote retention

**Requirement.** At every decision point that reaches contract stage (and, for backtest reconstruction, at every
candidate decision point the backtest is claimed over), persist a quote record sufficient to reconstruct an executable
fill later: `contract_id, underlying, expiration, strike, right, bid, ask, quote_ts, decision_ts, source, volume, open_interest, delta, iv`.
Missing `bid`/`ask`/`quote_ts` → the record is written with `status=MISSING` and **any consumer fails closed**.
Staleness rule: `decision_ts − quote_ts > max_quote_age_seconds` → `status=STALE`, consumer fails closed. `max_quote_age_seconds`
is a frozen, finite, positive number recorded in the evidence packet (`fill_realism.max_quote_age_seconds`).

**Dataset freeze.** A manifest builder emits `option_quotes_manifest.json` (files + SHA-256 + row counts + `source` + `max_quote_age_seconds`);
the packet references it via `data_integrity.option_quotes_manifest_path/_sha256` and the gate verifies bytes.

**Known baseline gap this closes.** 09-16 audit: 83/83 journal contracts carry bid/ask/OI/vol/IV and **0** carry a quote timestamp;
contract feasibility UNKNOWN for 91% of opportunities because no chain is retained unless a candidate reaches contract stage.

**Proof required:**
1. Schema test: every field required; absent `quote_ts` → `MISSING`, not a default.
2. Staleness test: `quote_ts` older than `max_quote_age_seconds` → `STALE`; consumer (selector, fill model, gate) rejects.
3. Missing-row test: decision with no chain row for the selected contract → fail closed with reason.
4. Wide-spread test: spread above `quality_max_spread_percent` → recorded, and the fill model refuses (parity fixture `wide_spread_case_covered`).
5. Manifest test: builder output hashes reproducibly; gate `_hash_check` passes on it.
6. Source identification: `source` is a required enum (provider name + endpoint), not free text.

**Evidence keys retired:**

| Baseline blocker line (#653 `report.txt`) |
|---|
| `contract_selection.quote_timestamp_aligned_to_decision must be explicitly true` |
| `data_integrity.option_quotes_dataset_frozen must be explicitly true` |
| `data_integrity.quote_source_identified must be explicitly true` |
| `data_integrity.bid_ask_available_at_decision must be explicitly true` |
| `data_integrity.stale_quotes_fail_closed must be explicitly true` |
| `data_integrity.missing_contract_rows_fail_closed must be explicitly true` |
| `data_integrity.option_quotes_manifest_path is required` |
| `data_integrity.option_quotes_manifest_sha256 is required` |
| `fill_realism.max_quote_age_seconds must be finite and > 0` |
| `golden_parity.stale_quote_case_covered must be explicitly true` |
| `golden_parity.missing_quote_case_covered must be explicitly true` |
| `golden_parity.wide_spread_case_covered must be explicitly true` |

## 5. Item 2B — Executable-fill reconstruction (dependent on item 2) — *needs operator confirmation of scope*

Retained quotes alone do not retire the fill-model blockers; a backtest fill engine over the retained quotes does.
`options_manager/paper_sim.py` already implements ASK-entry / BID-exit and fails closed on a missing ask/bid; it lacks
fees, slippage stress, no-fill, gap handling, and same-bar ambiguity. This sub-item is shared across families, which is
why it is listed here rather than under 212R — but it was not one of the three items in the 09-18 ruling.

**Requirement.** Fill = `ask × (1 + slippage%) + fees` on entry, `bid × (1 − slippage%) − fees` on exit, from a quote whose
`status=OK`; `NO_FILL` when the quote is `MISSING`/`STALE` or the spread exceeds the frozen limit; same-bar target-and-stop →
stop first (pessimistic); gap through stop → fill at the first available executable quote, not the stop price; a pre-registered
slippage stress (e.g. 2× the base slippage%) must still leave the aggregate result ≥ the pass threshold.

**Proof required:** unit fixtures for each of the seven cases above + golden entry/exit formula parity fixtures (replay vs forward).

**Evidence keys retired:**

| Baseline blocker line (#653 `report.txt`) |
|---|
| `fill_realism.fees_included must be explicitly true` |
| `fill_realism.slippage_included must be explicitly true` |
| `fill_realism.no_fill_modeled must be explicitly true` |
| `fill_realism.gap_handling_modeled must be explicitly true` |
| `fill_realism.same_bar_ambiguity_pessimistic must be explicitly true` |
| `fill_realism.slippage_stress_pre_registered must be explicitly true` |
| `fill_realism.slippage_stress_pass must be explicitly true` |
| `golden_parity.entry_fill_formula_parity must be explicitly true` |
| `golden_parity.exit_fill_formula_parity must be explicitly true` |
| `golden_parity.no_fill_case_covered must be explicitly true` |

## 6. Item 3 — Cross-cutting risk-policy cleanup

**Requirement.**
- **Planned risk from premium stop.** `planned_risk_dollars = (entry_fill − premium_stop) × multiplier × contracts`; must be finite, > 0, ≤ `risk_max_total_premium_dollars` (300). A plan whose planned risk is derived from anything else (e.g. full premium, underlying stop) is rejected with a reason code. `plans/base.py` already requires `premium_stop: float`; the calculation must be a single named function with a test.
- **Explicit aggregate open-risk budget.** `validation/portfolio_risk_gate.evaluate_portfolio_risk` already blocks on `None` (`aggregate_risk_budget_missing`) — keep. Add: the evidence packet must carry the *same* finite value the runtime config carries, and the gate compares them (new key, e.g. `risk_policy.aggregate_budget_source_path` + hash), so a packet cannot claim a budget the box does not enforce. *Setting the box value is a `.env` change — held until after 09-30.*
- **No averaging down.** A new entry on an underlying+direction that already has an open position (or an open order) is rejected with `averaging_down_rejected`; no code path may bypass it. Currently no such guard exists in `options_manager/` (baseline `_averaging_basis`).

**Proof required:**
1. Planned-risk test: premium stop above entry, zero, non-finite → reject; valid → exact dollars.
2. Budget test: `None` → block (existing); packet value ≠ config value → block (new).
3. Averaging-down test: open position exists → second entry rejected; closed position → allowed.
4. Golden `risk_decision_parity` + `premium_stop_case_covered` fixtures: replay and forward produce the same accept/reject with the same reason code.

**Evidence keys retired:**

| Baseline blocker line (#653 `report.txt`) |
|---|
| `risk_policy.planned_risk_uses_premium_stop must be explicitly true` |
| `risk_policy.no_averaging_down must be explicitly true` |
| `risk_policy.max_aggregate_open_risk_dollars must be explicitly configured` |
| `golden_parity.risk_decision_parity must be explicitly true` |
| `golden_parity.premium_stop_case_covered must be explicitly true` |

## 7. Regression target — what "done" means for the infrastructure

Re-run the **unmodified** #653 package with **only** the item-1/2/2B/3 evidence sections updated to point at the new
hashed artifacts:

```bash
python3 scripts/options_demo_qualification_gate.py --strategy strat_212_reversal_30m_options \
  --evidence-file data/options_demo_gate_212R_2026_09_18/evidence.json --json > /tmp/rerun.json
python3 -c "import json;a=set(json.load(open('/tmp/rerun.json'))['blockers']);e=set(json.load(open('docs/options-demo-gate-acceptance/expected_remaining_blockers_212R.json'))['expected_remaining_after_infra']);print('PASS' if a==e else ('EXTRA',sorted(a-e),'MISSING',sorted(e-a)))"
```

Acceptance is **all** of:
1. Verdict is still **`BLOCKED`** and `classification` is still **`WAIT`**. If the rerun returns `DEMO_EVIDENCE_ELIGIBLE`, the infrastructure work has silently converted a `WAIT` strategy into a passing one — that is a **spec violation**, not a success.
2. The remaining blocker set equals **exactly** the 23 lines below — no fewer (infra must not touch 212R's own burden) and no more (infra must retire everything it owns).
3. Every retired line is retired by a hashed artifact or a passing fixture, not by flipping a boolean in `evidence.json`. The PR that retires a line must cite the test/fixture that proves it.
4. `paper_demo_activation_authorized == false` and `live_trading_authorized == false` in the rerun output.

**Expected remaining after infrastructure (23 — 212R's own evidence burden):**

| Baseline blocker line (#653 `report.txt`) |
|---|
| `classification must be PROMISING BUT UNPROVEN or VALIDATED` |
| `change_scope.strategy_or_parameter_only must be explicitly true` |
| `change_scope.base_sha must be the pre-change commit` |
| `change_scope base_sha...HEAD diff is empty` |
| `change_scope has no changed pre-registered strategy path` |
| `strategy_identity.target_formula_frozen must be explicitly true` |
| `strategy_identity.replay_forward_formula_parity must be explicitly true` |
| `validation.untouched_validation_window must be explicitly true` |
| `validation.multiple_months_covered must be explicitly true` |
| `validation.chronological_walk_forward_pass must be explicitly true` |
| `validation.drawdown_limit_pre_registered must be explicitly true` |
| `validation.concentration_limit_pre_registered must be explicitly true` |
| `validation.aggregate_expectancy_after_costs_positive must be explicitly true` |
| `validation.aggregate_net_pnl_after_costs_positive must be explicitly true` |
| `validation cell 212R_30m_primary20_retro_2026-09-09..15_first_sight_ex_opening has insufficient resolved fills` |
| `validation cell 212R_30m_primary20_retro_2026-09-09..15_first_sight_ex_opening missing finite expectancy` |
| `validation cell 212R_30m_primary20_retro_2026-09-09..15_first_sight_ex_opening fails drawdown limit` |
| `validation cell 212R_30m_primary20_retro_2026-09-09..15_first_sight_ex_opening fails concentration check` |
| `validation cell 212R_30m_primary20_retro_2026-09-09..15_first_sight_ex_opening missing valid average spread` |
| `golden_parity.fixture_set_frozen must be explicitly true` |
| `golden_parity.underlying_candidate_parity must be explicitly true` |
| `golden_parity.underlying_invalidation_case_covered must be explicitly true` |
| `golden_parity.event_risk_case_covered must be explicitly true` |

Of these, two are pre-registration items that *may* be done offline before 09-30 without code
(`validation.drawdown_limit_pre_registered`, `validation.concentration_limit_pre_registered`) — as a docs-only
pre-registration for 212R, dated and hashed, if the operator wants it. Everything else needs a 212R implementation,
an option-side backtest with ≥30 resolved fills per required cell, positive after-cost expectancy, and a prospective sample
that actually persists — none of which this spec authorizes.

## 8. Freeze boundary (until 2026-09-30, absent an explicit waiver)

| Allowed now (offline) | Held |
|---|---|
| This spec; refinements to it | Selector code |
| Fixture *design* (expected inputs/outputs written down, no code) | Quote-retention change in scanner/runtime |
| 212R drawdown/concentration pre-registration (docs) | Averaging-down guard, planned-risk function |
| Test-plan review | Any `.env` / box config change (aggregate budget) |
| | Any rerun that requires new code |

Related: `docs/options-backtest-to-demo-qualification-gate.md` (gate contract, verified-vs-attested table).
