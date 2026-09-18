# Options Shared Infrastructure Acceptance Spec

Status: **offline acceptance specification only**.  
No runtime, scanner, risk, config, broker, deploy, restart, or paper/DEMO activation changes are authorized by this document.

Baseline evidence: PR #653, `claude/options-212r-gate-evidence@e13a950`, generated against `main@5633a59`.  
Baseline gate result: `BLOCKED`, exit 2, 60 blockers for `strat_212_reversal_30m_options`.

## Purpose

Define exactly what future infrastructure work must prove before the 212R evidence package can retire the shared option-side blocker classes.

This specification is intentionally **strategy-agnostic**. It must not turn 212R from `WAIT` into `PROMISING BUT UNPROVEN`; it only defines the common infrastructure needed to make a later strategy-specific gate run meaningful.

The 212R package remains the negative-proof baseline. After each future implementation, rerun:

```bash
python3 scripts/options_demo_qualification_gate.py \
  --strategy strat_212_reversal_30m_options \
  --evidence-file data/options_demo_gate_212R_2026_09_18/evidence.json
```

Expected outcome after shared infrastructure alone: still `BLOCKED` unless the strategy-specific evidence independently improves.

---

## A. Mechanical contract selector

### Objective

Create one deterministic contract-selection path that selects a contract from a caller-supplied chain without hindsight and is reusable by replay and forward observation.

### Required inputs

The selector must receive, at minimum:

- underlying ticker;
- direction: CALL / PUT;
- decision timestamp;
- underlying price at decision time;
- full candidate chain snapshot available at that timestamp;
- expiration dates;
- strikes;
- bid;
- ask;
- quote timestamp;
- volume;
- open interest;
- DTE;
- delta or a documented moneyness fallback if delta is unavailable;
- frozen selector policy.

No selector input may contain future price, future option quote, final trade outcome, or future liquidity.

### Required deterministic policy

The selector policy must freeze:

- expiration rule;
- strike rule;
- DTE rule;
- delta/moneyness rule;
- liquidity rule;
- spread rule;
- tie-break order;
- behavior when no contract qualifies.

The same chain and same policy must return the same selected contract byte-for-byte or the same explicit `NO_CONTRACT` result.

### Fail-closed requirements

The selector must reject rather than guess when:

- required quote fields are missing;
- quote timestamp is missing;
- quote is stale;
- bid/ask is invalid;
- spread exceeds policy;
- DTE is outside policy;
- volume/OI are below policy;
- no candidate meets all hard requirements.

It must never choose a contract by "nearest known winner," final realized move, highest later volume, or any other outcome-aware field.

### Required proof

Before the selector blocker class can be retired:

1. selector code exists in one canonical path;
2. selector policy exists in a separate frozen file or equivalent hashable artifact;
3. selector rule file is SHA-256 pinned;
4. replay and forward paths call the same selector function;
5. golden fixtures prove:
   - deterministic selection;
   - CALL and PUT;
   - multiple expirations;
   - tie-break behavior;
   - no qualifying contract;
   - stale quote;
   - missing quote;
   - wide spread;
   - low volume;
   - low OI;
   - DTE rejection;
6. no test fixture relies on future outcome data.

### Baseline blockers this item is allowed to retire

Only when proven:

- `contract_selection.mechanical_selection must be explicitly true`
- `contract_selection.expiration_rule_frozen must be explicitly true`
- `contract_selection.strike_rule_frozen must be explicitly true`
- `contract_selection.dte_rule_frozen must be explicitly true`
- `contract_selection.moneyness_or_delta_rule_frozen must be explicitly true`
- `contract_selection.no_hindsight_contract_choice must be explicitly true`
- `contract_selection.same_selector_replay_and_forward must be explicitly true`
- `contract_selection.selection_rule_path is required`
- `contract_selection.selection_rule_sha256 is required`

This work alone must **not** retire quote-retention, fill-realism, validation-sample, expectancy, strategy-identity, or classification blockers.

---

## B. Timestamped option quote retention

### Objective

Persist the exact option-market evidence available when a strategy candidate becomes decision-eligible, so replay can reconstruct executable contract selection and fills without hindsight.

### Required retained fields

For every decision-time chain snapshot used by the selector, retain at minimum:

- underlying ticker;
- decision timestamp;
- underlying price;
- option symbol / contract identity;
- CALL / PUT;
- expiration;
- strike;
- DTE;
- bid;
- ask;
- quote timestamp;
- quote age at decision;
- volume;
- open interest;
- delta if available;
- IV if available;
- provider/source identity;
- selector-policy ID/hash;
- selected/not-selected disposition;
- explicit missing/stale reason if unusable.

The retention format must be append-only or otherwise immutable for evidence purposes.

### Timestamp rules

- quote timestamp must be at or before the strategy decision timestamp;
- quote age must be mechanically calculable;
- no later quote may be substituted for a missing decision-time quote;
- stale threshold must be frozen in policy before validation;
- timezone handling must be explicit and tested.

### Data-integrity requirements

A frozen option-quote evidence package must include:

- manifest path;
- SHA-256 of manifest bytes;
- row/file counts;
- source/provider identity;
- date range;
- symbol/contract coverage summary;
- missing-row count;
- stale-row count;
- duplicate identity count;
- proof that missing/stale rows fail closed.

### Required proof

Golden fixtures must cover:

- valid fresh quote;
- quote timestamp after decision -> reject;
- stale quote -> reject;
- missing bid -> reject;
- missing ask -> reject;
- invalid crossed/zero quote -> reject;
- duplicate contract identity -> deterministic handling;
- missing selected contract row -> fail closed.

A forward collection audit must prove at least one full session of decision-time snapshots is retained without future-field contamination before the data-integrity attestations may become true.

### Baseline blockers this item is allowed to retire

Only when proven:

- `contract_selection.quote_timestamp_aligned_to_decision must be explicitly true`
- `data_integrity.option_quotes_dataset_frozen must be explicitly true`
- `data_integrity.quote_source_identified must be explicitly true`
- `data_integrity.bid_ask_available_at_decision must be explicitly true`
- `data_integrity.stale_quotes_fail_closed must be explicitly true`
- `data_integrity.missing_contract_rows_fail_closed must be explicitly true`
- `data_integrity.option_quotes_manifest_path is required`
- `data_integrity.option_quotes_manifest_sha256 is required`
- `fill_realism.max_quote_age_seconds must be finite and > 0`

This work alone must **not** create resolved fills or option expectancy.

---

## C. Fill-realism evidence

### Objective

Make replay use the same executable-side assumptions required by the qualification gate.

### Frozen baseline rules

Unless a later operator-approved policy explicitly changes them:

- long option entry uses ASK or ASK plus adverse slippage;
- long option exit uses BID or BID minus adverse slippage;
- spread is therefore naturally included;
- fees must be non-zero when applicable or explicitly documented as zero by the broker schedule;
- slippage must be explicit and stress-tested;
- no-fill behavior must exist;
- gap-through behavior must exist;
- same-snapshot stop/target ambiguity must resolve pessimistically.

### Required proof

Fixtures must demonstrate:

- ASK entry;
- BID exit;
- adverse entry slippage;
- adverse exit slippage;
- fee application;
- no-fill;
- gap-through stop;
- same-bar stop/target ambiguity;
- premium stop;
- underlying invalidation;
- stale quote;
- missing quote.

Stress levels must be pre-registered before inspecting the validation result.

### Baseline blockers this item is allowed to retire

Only when proven:

- `fill_realism.fees_included must be explicitly true`
- `fill_realism.slippage_included must be explicitly true`
- `fill_realism.no_fill_modeled must be explicitly true`
- `fill_realism.gap_handling_modeled must be explicitly true`
- `fill_realism.same_bar_ambiguity_pessimistic must be explicitly true`
- `fill_realism.slippage_stress_pre_registered must be explicitly true`
- `fill_realism.slippage_stress_pass must be explicitly true`

This work must not declare `slippage_stress_pass=true` until a real validation population is rerun under the pre-registered stress.

---

## D. Risk-policy cleanup

### Objective

Make the options risk policy explicit, mechanically enforceable, and separately provable.

### D1. Planned risk from premium stop

Required formula:

```text
planned_risk_dollars =
    (entry_premium - premium_stop)
    * contract_multiplier
    * contracts
```

Requirements:

- numeric premium stop required;
- premium stop must be below entry for a long option;
- missing/invalid premium stop blocks;
- full premium debit is not substituted as planned risk when a valid premium stop exists;
- formula must be used by the canonical options decision path;
- regression fixture must prove the exact dollars.

Allowed blocker retirement:

- `risk_policy.planned_risk_uses_premium_stop must be explicitly true`

### D2. Aggregate open-risk budget

The budget must be an explicit operator policy value, not silently invented by code.

Requirements:

- unset -> block;
- invalid/non-finite/<=0 -> block;
- projected aggregate planned risk is computed before acceptance;
- proposed trade blocks when projected aggregate exceeds the configured budget;
- no position-count cap may be introduced as a substitute for planned-risk accounting.

Allowed blocker retirement:

- `risk_policy.max_aggregate_open_risk_dollars must be explicitly configured`

Changing the actual deployed `.env` value is a separate operator/runtime action and is not authorized by this specification.

### D3. No averaging down

The canonical options plan/position lifecycle must prohibit increasing long-option exposure after an active position has moved adversely or after the setup has invalidated.

Minimum acceptance behavior:

- a second order/plan that increases quantity in the same thesis/contract while an active position exists must be rejected unless it is an explicitly distinct, pre-registered scale-in plan created before first entry;
- no scale-in may occur after underlying invalidation;
- no scale-in may occur after premium stop;
- no hidden "replace losing contract with more contracts" path;
- fixture proves rejection.

Allowed blocker retirement:

- `risk_policy.no_averaging_down must be explicitly true`

---

## E. Strategy-specific work that shared infrastructure must NOT solve

These blockers belong to 212R itself and must remain until independent evidence exists:

- `classification must be PROMISING BUT UNPROVEN or VALIDATED`
- `change_scope.strategy_or_parameter_only must be explicitly true`
- `change_scope.base_sha must be the pre-change commit`
- `change_scope base_sha...HEAD diff is empty`
- `change_scope has no changed pre-registered strategy path`
- `strategy_identity.target_formula_frozen must be explicitly true`
- `strategy_identity.replay_forward_formula_parity must be explicitly true`
- all 212R-specific golden parity blockers until a real 212R strategy/replay exists;
- untouched validation window;
- multiple months;
- walk-forward pass;
- drawdown/concentration preregistration;
- positive option expectancy/P&L after costs;
- >=30 resolved option fills per required cell.

Shared infrastructure is successful even if 212R remains `WAIT`.

---

## F. Required rerun comparison

After each future infrastructure PR, produce a machine-readable blocker diff against the PR #653 baseline.

The report must classify every blocker as:

- `RETIRED_BY_INTENDED_CHANGE`
- `UNCHANGED_EXPECTED`
- `NEW_BLOCKER`
- `UNEXPECTEDLY_RETIRED`

Acceptance rule:

- no `UNEXPECTEDLY_RETIRED` blockers;
- no new strategy-classification promotion;
- no reduction in sample/validation requirements;
- no silent weakening of liquidity, quote freshness, fill realism, or risk policy.

The ideal first infrastructure rerun will still say `BLOCKED`, but with only the blocker class targeted by that PR removed.

---

## G. Build order after the standing hold is lifted

1. Mechanical selector.
2. Timestamped decision-time quote retention.
3. Fill-realism parity/stress fixtures.
4. Risk-policy cleanup.
5. Rerun the preserved 212R package and diff blockers.
6. Only then rerun/build the 1-2-2 evidence package.
7. Strategy-specific 212R implementation/replay only if its underlying evidence later earns reconsideration.

No item in this document authorizes deployment, restart, broker integration, automatic paper activation, or LIVE trading.
