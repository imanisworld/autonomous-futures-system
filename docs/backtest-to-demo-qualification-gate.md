# Backtest -> DEMO Qualification Gate

Status: **read-only evidence gate**. No deploy/restart/broker action.

## Purpose

Shorten the future validation cycle without lowering the safety bar.

For a **strategy/parameter-only** change, a complete canonical replay package may qualify the change to skip the long duplicate internal-paper evidence phase and move to **Tradovate DEMO**, provided a separate current runtime/release safety reconciliation also passes.

A gate PASS is **not** permission for live trading and does not itself deploy or activate anything.

## Why this exists

Older research results were sometimes optimistic because they used one or more of:

- non-canonical strategy implementations;
- different replay/live formulas;
- always-fill or late-fill assumptions;
- target-priority same-bar handling;
- missing commission/slippage;
- ambiguous continuous-contract roll identity;
- unproven futures session-day boundaries;
- tuned or outcome-selected validation populations.

The gate fails closed on those uncertainties.

## Direct-to-DEMO eligibility

All sections must pass.

### 1. Base promotion proof

The existing `ops.project_check.promotion` gate must pass, including:

- internally consistent attempt/fill/cancel/reject accounting;
- candidate/direction/entry-stop-target/timeframe parity;
- no lookahead/partial-bar dependency;
- current runtime entry-fill model, effective tolerance and quantity-cap parity.

Effective evidence classification must be either:

- `PROMISING BUT UNPROVEN`, or
- `VALIDATED`.

`WAIT`, `BROKEN`, `OVERFIT`, `UNSAFE`, unknown, or unclassified evidence cannot use the direct-to-DEMO path.

### 2. Change scope

Direct-to-DEMO is only for a strategy/parameter-only change. Evidence must explicitly prove:

- risk policy unchanged;
- execution path unchanged;
- broker routing unchanged;
- session/contract semantics unchanged.

If any of those changed, the long paper phase is **not automatically waived** by this gate. The changed infrastructure needs its own parity/smoke proof first.

### 3. Canonical replay path

Replay must use the real:

`ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker`

and the same strategy formula as runtime.

### 4. Data identity

Evidence must prove:

- frozen dataset/manifest;
- manifest SHA-256 matches the current bytes;
- exact contract/roll identity;
- futures session-day identity;
- feed integrity.

`session_day_identity_proven` is where the C14/TradingView fixture work belongs. Until that is proven for studies sensitive to day/session resets, this gate fails closed.

### 5. Execution realism

Required:

- IOC/no-fill behavior modeled;
- pessimistic stop-first treatment when stop and target are both inside one unresolved bar;
- gap-through handling;
- explicit adverse slippage in the baseline;
- explicit commission in the baseline;
- 2-tick and 3-tick adverse slippage stress;
- the stressed result still passes the strategy's evidence requirement.

### 6. Validation quality

Required:

- untouched validation window;
- multiple months covered;
- chronological walk-forward passes;
- sample requirement pre-registered before the validation result is inspected;
- minimum pre-registered requirement is 30 resolved fills;
- actual resolved fills meet that requirement;
- drawdown stays within the pre-registered limit;
- concentration check passes;
- session filters and direction-coverage requirements are respected.

The gate intentionally does not invent one universal profit-factor or win-rate threshold. Strategy evidence must already justify its `PROMISING BUT UNPROVEN` or `VALIDATED` classification.

### 7. Golden replay/runtime parity fixtures

The frozen fixture set must show replay/runtime agreement for:

- candidate identity;
- risk decision;
- order intent;
- no-fill case;
- same-bar stop/target ambiguity;
- gap case;
- session boundary;
- contract-roll boundary.

### 8. Replay provenance

The replay evidence must name:

- exact code SHA;
- exact `risk_rules.yaml` SHA-256.

The gate compares those claims to the checkout on which the qualification command is run.

## Workflow

```text
strategy/parameter change
        |
        v
canonical historical replay
        |
        v
untouched validation + walk-forward + stress
        |
        v
Backtest -> DEMO qualification gate
        |
        +-- BLOCKED -> fix proof gap / use internal paper when required
        |
        `-- PASS
             |
             v
current runtime/release safety reconciliation
             |
             v
Tradovate DEMO forward execution
```

The goal is to remove duplicate waiting, not remove forward validation. DEMO remains the forward execution check.

## Command

```bash
python3 scripts/demo_qualification_gate.py \
  --strategy <strategy_name> \
  --evidence-file <facts.json>
```

Use `--json` for a machine-readable report.

Exit code:

- `0` = DEMO evidence eligible;
- `2` = blocked / missing proof.

## Evidence facts skeleton

```json
{
  "strategy": "example",
  "identity_parity": {
    "raw_candidate_count": 0,
    "candidate_identity_parity": true,
    "direction_parity": true,
    "entry_stop_target_parity": true,
    "timeframe_parity": true,
    "causal_data_availability": true,
    "lookahead_or_partial_bar_dependency": false
  },
  "execution": {
    "entry_attempts": 0,
    "fills": 0,
    "cancellations": 0,
    "rejects_or_known_no_fills": 0,
    "resolved_outcomes": 0,
    "legitimately_open": 0
  },
  "execution_context_claimed": {
    "instrument": "MNQ",
    "entry_fill_model": "ioc_limit",
    "entry_tolerance_ticks": 32,
    "contract_qty": 1,
    "commission_slippage_assumptions": "explicit text"
  },
  "stated_classification": "PROMISING BUT UNPROVEN",
  "change_scope": {
    "strategy_or_parameter_only": true,
    "risk_policy_unchanged": true,
    "execution_path_unchanged": true,
    "broker_routing_unchanged": true,
    "session_contract_semantics_unchanged": true
  },
  "canonical_replay": {
    "real_replay_engine": true,
    "real_decision_engine": true,
    "real_risk_engine": true,
    "real_paper_broker": true,
    "replay_live_logic_confirmed": true,
    "same_strategy_formula_confirmed": true
  },
  "data_integrity": {
    "dataset_frozen": true,
    "dataset_manifest_path": "path/to/manifest.json",
    "dataset_manifest_sha256": "...",
    "contract_roll_identity_proven": true,
    "session_day_identity_proven": true,
    "feed_integrity_proven": true
  },
  "execution_realism": {
    "ioc_no_fill_modeled": true,
    "pessimistic_same_bar": true,
    "gap_through_modeled": true,
    "slippage_included": true,
    "commission_included": true,
    "baseline_adverse_slippage_ticks": 1,
    "commission_round_turn_dollars": 1.48,
    "slippage_stress_ticks": [1, 2, 3],
    "slippage_stress_pass": true
  },
  "validation": {
    "untouched_validation_window": true,
    "multiple_months_covered": true,
    "walk_forward_pass": true,
    "sample_requirement_pre_registered": true,
    "required_resolved_fills": 30,
    "resolved_fills": 0,
    "drawdown_within_pre_registered_limit": true,
    "concentration_check_pass": true,
    "session_filters_respected": true,
    "direction_coverage_requirement_met": true
  },
  "golden_parity": {
    "fixture_set_frozen": true,
    "runtime_vs_replay_candidate_parity": true,
    "runtime_vs_replay_risk_parity": true,
    "runtime_vs_replay_order_intent_parity": true,
    "no_fill_case_covered": true,
    "same_bar_ambiguity_case_covered": true,
    "gap_case_covered": true,
    "session_boundary_case_covered": true,
    "roll_boundary_case_covered": true
  },
  "replay_provenance": {
    "code_sha": "...",
    "risk_rules_sha256": "..."
  }
}
```

## Safety boundary

This gate never authorizes LIVE. A strategy that passes still requires current DEMO runtime/deployment checks, and DEMO evidence remains necessary before any later live consideration.
