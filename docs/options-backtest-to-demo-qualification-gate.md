# Options Backtest -> DEMO Qualification Gate

Status: **read-only evidence gate**. It does not deploy, restart, activate a paper lane, submit an order, or authorize live options trading.

## Purpose

Move an options strategy from historical research to paper/DEMO only when the **underlying setup and the option implementation** both survive a frozen, realistic validation package.

The required sequence is:

```text
underlying strategy backtest
        |
        v
mechanical option-contract replay
        |
        v
untouched validation + walk-forward + stress
        |
        v
options Backtest -> DEMO qualification gate
        |
        +-- BLOCKED -> fix evidence gap / continue research
        |
        `-- DEMO_EVIDENCE_ELIGIBLE
                 |
                 v
independent review
                 |
                 v
current runtime/release reconciliation
                 |
                 v
paper/DEMO forward execution
```

A PASS is **not** automatic paper activation and never authorizes LIVE.

## Why options require a separate gate

A correct underlying call/put thesis can still be a bad option trade because of:

- hindsight contract selection;
- wrong expiration or strike;
- insufficient DTE;
- wide spreads;
- stale or missing quotes;
- low volume/open interest;
- IV/event exposure;
- theta decay;
- midpoint-only fill assumptions;
- missing premium-stop risk;
- contract concentration.

The existing options replay model explicitly does not simulate option premium or fetch an option chain. This gate therefore requires a separate frozen option-quote evidence package before a strategy may become DEMO evidence eligible.

## What the gate verifies mechanically vs what it requires as attestation

A PASS is **not proof that every historical claim was independently recomputed by this command**.

| Evidence | Gate treatment |
| --- | --- |
| `base_sha...HEAD` changed-file scope | **Mechanically verified** with git |
| `code_sha == HEAD` | **Mechanically verified** |
| Underlying manifest SHA-256 | **Mechanically verified against current bytes** |
| Option-quote manifest SHA-256 | **Mechanically verified against current bytes** |
| Contract-selector rule SHA-256 | **Mechanically verified against current bytes** |
| Options-policy SHA-256 | **Mechanically verified against current bytes** |
| Numeric cell sample floors, expectancy sign, risk caps, quote-age validity | **Mechanically checked from supplied evidence values** |
| No lookahead, formula parity, executable quote use, spread/fees/slippage inclusion, stale/missing-quote fail-closed behavior, parity fixture coverage, walk-forward/drawdown/concentration claims | **Required explicit attestations**; omission or false values block, but this command does not independently reconstruct those studies |

Therefore `DEMO_EVIDENCE_ELIGIBLE` means the evidence packet is structurally complete and passes the checks this tool can mechanically perform. Independent review remains mandatory before any paper/DEMO activation.

## Hard requirements

### 1. Change scope is mechanically narrow

The evidence packet must name:

- the exact pre-change `base_sha`;
- an explicit pre-registered list of strategy files allowed to change.

The gate computes `base_sha...HEAD` and permits only:

- the exact pre-registered strategy paths;
- `tests/`;
- `docs/`.

Any other code path blocks qualification.

The packet must also explicitly state that risk policy, contract-selection semantics, fill-model semantics, broker routing, and runtime activation are unchanged.

### 2. Underlying strategy identity is frozen

Required proof:

- entry formula frozen;
- underlying invalidation formula frozen;
- target formula frozen;
- timeframe formula frozen;
- replay/forward formulas identical;
- causal data only;
- no lookahead/future leak.

### 3. Contract selection is mechanical

The contract selector must be fixed before validation and prove:

- expiration rule frozen;
- strike rule frozen;
- DTE rule frozen;
- moneyness/delta rule frozen;
- liquidity rule frozen;
- quote timestamp aligned with the decision;
- no hindsight contract choice;
- the same selector is used in replay and forward operation.

The evidence packet records a selector ID, selector-rule file path, and SHA-256. The gate hashes that file and requires the bytes to match the claimed digest.

### 4. Underlying and option quote datasets are frozen

Both datasets require manifests whose current bytes match their claimed SHA-256.

The option evidence must prove:

- quote source identified;
- bid and ask available at the decision time;
- stale quotes fail closed;
- missing contract rows fail closed.

No synthetic option price is accepted as execution proof merely because the underlying move is known.

### 5. Fill realism

Baseline evidence must use executable-side quotes:

- entry: `ASK` or `ASK_PLUS_SLIPPAGE`;
- exit: `BID` or `BID_MINUS_SLIPPAGE`.

Also required:

- spread included;
- fees included;
- slippage included;
- no-fill behavior modeled;
- gap handling modeled;
- ambiguous same-bar stop/target resolution handled pessimistically;
- quote-age limit defined;
- adverse-slippage stress pre-registered and passed.

A midpoint-only result cannot qualify.

### 6. Risk policy

Required:

- underlying invalidation;
- numeric premium stop;
- planned risk computed from premium entry minus premium stop;
- aggregate open-risk enforcement;
- no averaging down.

The gate enforces the current default maximum of **$300 planned risk per trade**.

The aggregate open-risk budget must be **explicitly configured** in the evidence. The gate does not invent a default aggregate budget.

### 7. Validation cells

The sample requirement must be pre-registered at **at least 30 resolved fills per required cell**.

The gate inspects the actual cell list; an aggregate sample count is insufficient.

Recommended required dimensions for the current options system:

- setup family;
- direction: CALL / PUT;
- DTE bucket;
- liquidity quality;
- market regime.

Do not create a Cartesian explosion of cells that the campaign cannot realistically populate. Pre-register the cells that materially test the strategy.

Every required cell must have:

- resolved fill count meeting the floor;
- finite expectancy in R **after spread, slippage, and fees**;
- non-negative expectancy;
- drawdown inside its pre-registered limit;
- concentration check passing;
- valid average spread measurement.

At the aggregate level, expectancy and net P&L after costs must both be positive.

A positive aggregate result cannot hide a negative required cell.

### 8. Golden replay/forward parity

Frozen fixtures must prove parity for:

- underlying candidate;
- contract selection;
- risk decision;
- entry fill formula;
- exit fill formula.

The fixture set must include:

- no-fill;
- stale quote;
- missing quote;
- wide spread;
- premium stop;
- underlying invalidation;
- event risk.

### 9. Provenance

The evidence packet must pin:

- exact code SHA;
- exact options policy file;
- SHA-256 of that policy file.

The gate verifies both against the checkout on which it runs.

## Command

```bash
python3 scripts/options_demo_qualification_gate.py \
  --strategy <strategy-name> \
  --evidence-file <frozen-evidence.json>
```

Use `--json` for machine-readable output.

Exit codes:

- `0` = `DEMO_EVIDENCE_ELIGIBLE`;
- `2` = blocked.

## Evidence skeleton

```json
{
  "strategy": "strat_212_swing",
  "classification": "PROMISING BUT UNPROVEN",
  "change_scope": {
    "base_sha": "<pre-change-sha>",
    "allowed_strategy_paths": [
      "options_manager/strategies/strat_212.py"
    ],
    "strategy_or_parameter_only": true,
    "risk_policy_unchanged": true,
    "contract_selection_semantics_unchanged": true,
    "fill_model_semantics_unchanged": true,
    "broker_routing_unchanged": true,
    "runtime_activation_unchanged": true
  },
  "strategy_identity": {
    "underlying_entry_formula_frozen": true,
    "underlying_invalidation_formula_frozen": true,
    "target_formula_frozen": true,
    "timeframe_formula_frozen": true,
    "replay_forward_formula_parity": true,
    "causal_data_only": true,
    "lookahead_or_future_leak": false
  },
  "contract_selection": {
    "mechanical_selection": true,
    "expiration_rule_frozen": true,
    "strike_rule_frozen": true,
    "dte_rule_frozen": true,
    "moneyness_or_delta_rule_frozen": true,
    "liquidity_rule_frozen": true,
    "quote_timestamp_aligned_to_decision": true,
    "no_hindsight_contract_choice": true,
    "same_selector_replay_and_forward": true,
    "selection_rule_id": "selector-v1",
    "selection_rule_path": "evidence/selector_rule.json",
    "selection_rule_sha256": "<sha256>"
  },
  "data_integrity": {
    "underlying_dataset_frozen": true,
    "option_quotes_dataset_frozen": true,
    "quote_source_identified": true,
    "bid_ask_available_at_decision": true,
    "stale_quotes_fail_closed": true,
    "missing_contract_rows_fail_closed": true,
    "underlying_manifest_path": "evidence/underlying_manifest.json",
    "underlying_manifest_sha256": "<sha256>",
    "option_quotes_manifest_path": "evidence/options_quotes_manifest.json",
    "option_quotes_manifest_sha256": "<sha256>"
  },
  "fill_realism": {
    "entry_uses_executable_quote": true,
    "exit_uses_executable_quote": true,
    "entry_fill_basis": "ASK",
    "exit_fill_basis": "BID",
    "spread_included": true,
    "fees_included": true,
    "slippage_included": true,
    "no_fill_modeled": true,
    "gap_handling_modeled": true,
    "same_bar_ambiguity_pessimistic": true,
    "max_quote_age_seconds": 60,
    "slippage_stress_pre_registered": true,
    "slippage_stress_pass": true
  },
  "risk_policy": {
    "underlying_invalidation_required": true,
    "numeric_premium_stop_required": true,
    "planned_risk_uses_premium_stop": true,
    "aggregate_open_risk_enforced": true,
    "no_averaging_down": true,
    "max_trade_risk_dollars": 300,
    "max_aggregate_open_risk_dollars": "<explicit operator policy>"
  },
  "validation": {
    "untouched_validation_window": true,
    "multiple_months_covered": true,
    "chronological_walk_forward_pass": true,
    "sample_requirement_pre_registered": true,
    "drawdown_limit_pre_registered": true,
    "concentration_limit_pre_registered": true,
    "aggregate_expectancy_after_costs_positive": true,
    "aggregate_net_pnl_after_costs_positive": true,
    "required_resolved_fills_per_cell": 30,
    "required_cell_dimensions": [
      "setup_family",
      "direction",
      "dte_bucket",
      "liquidity_quality",
      "market_regime"
    ],
    "cells": [
      {
        "cell_id": "212-call-45plus-liquid-aligned",
        "required": true,
        "resolved_fills": 30,
        "expectancy_r_after_costs": 0.01,
        "drawdown_within_limit": true,
        "concentration_pass": true,
        "average_spread_percent": 5.0
      }
    ]
  },
  "golden_parity": {
    "fixture_set_frozen": true,
    "underlying_candidate_parity": true,
    "contract_selection_parity": true,
    "risk_decision_parity": true,
    "entry_fill_formula_parity": true,
    "exit_fill_formula_parity": true,
    "no_fill_case_covered": true,
    "stale_quote_case_covered": true,
    "missing_quote_case_covered": true,
    "wide_spread_case_covered": true,
    "premium_stop_case_covered": true,
    "underlying_invalidation_case_covered": true,
    "event_risk_case_covered": true
  },
  "provenance": {
    "code_sha": "<current-head>",
    "options_policy_path": "<frozen-options-policy-file>",
    "options_policy_sha256": "<sha256>"
  }
}
```

## Safety boundary

`DEMO_EVIDENCE_ELIGIBLE` means only that the frozen evidence package is strong enough to request an independent review and a separate paper/DEMO activation decision.

This gate deliberately returns:

- `paper_demo_activation_authorized = false`;
- `live_trading_authorized = false`.

No code in this gate calls a broker, changes `LIVE_OPTIONS_TRADING_ENABLED`, creates an order ticket, deploys a service, or restarts a process.
