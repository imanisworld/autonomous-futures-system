# Futures Evaluation-Integrity Audit — 2026-10-01

**Scope:** repository-only audit of strategy-evaluation correctness on current `main` at `22ab9d84fc77a9add7a5dd109d16cea37496584f`.

**Verdict:** **AUDIT ONLY — no code change justified.**

This note is evaluation-method provenance only. It does not change Strategy Inventory classifications, authorize deployment, alter runtime state, or grant broker/execution authority.

## What was checked

The audit re-checked two previously suspected evaluation defects against current `main`:

1. **PaperBroker ↔ ReplayEngine execution-parity wiring**
2. **Forward-campaign per-strategy / zero-population reporting**

Neither suspected defect reproduced in current source.

### PaperBroker ↔ ReplayEngine

Current normal paper/replay paths pass the same configured execution fields into `PaperBroker`:

- `entry_fill_model`
- per-root `entry_tolerance_ticks_by_root`
- `fill_slippage_ticks`
- `fill_pessimistic_both_hit`
- breakeven / runner settings

For IOC-limit evaluation, replay supplies the decision-bar close as `market_price`. Existing regression coverage also pins normal PaperBroker fill-model wiring and replay IOC cancellation behavior.

**Ruling:** no repair was made because the suspected wiring defect is not present on this `main`.

### Campaign population reporting

Current campaign reporting:

- keys configured arms by `strategy/variant`;
- pre-creates configured populations even when candidate count is zero;
- keeps unexpected populations separate;
- exposes configured zero-count populations in the forward campaign report.

Existing regression coverage separately checks shared variant names across strategies and zero-population visibility.

**Ruling:** no repair was made because the suspected pooling / disappearing-zero-arm defect is not present on this `main`.

## Evaluation contract for future strategy research

A strategy result is not parity-grade evidence unless the report states and proves all of:

- entry fill model;
- IOC tolerance, where IOC is used;
- adverse slippage assumption;
- same-bar stop/target policy;
- commission treatment.

Canonical futures evidence should preserve pessimistic stop-first handling when intrabar ordering is unknowable.

### Commission

Raw `PaperBroker` / `ReplayEngine` P&L does not itself include round-trip commission. Where a strategy's evidence contract requires commission, the analysis/reporting layer must deduct it explicitly. Raw replay P&L must not be presented as final net performance.

### Strat 2-1-2 / 1-2-2 fill-cost caveat

The causal 2-1-2 / 1-2-2 replay path can restore a position at the already-established causal entry, and same-bar `force_resolve()` exits use the structural exit price. Those mechanics do not themselves apply the missing entry / same-bar exit slippage.

The MES 1-2-2 forward-paper evidence lane already compensates through its realistic ledger:

- one adverse entry tick;
- one additional adverse same-bar exit tick when applicable;
- round-trip commission.

For this family, **use the realistic adjusted ledger for strategy evaluation; do not use raw PaperBroker totals as the final economic result.**

### IOC tolerance provenance

For canonical MNQ/MES IOC comparisons, the evidence must explicitly pin or prove the intended per-root tolerance. Existing canonical evidence uses:

- MES: 16 ticks
- MNQ: 32 ticks

An evaluation that leaves IOC tolerance provenance ambiguous is **UNPROVEN for execution parity**, even if its P&L is positive.

## Test status

No new tests were added because no current-main defect was reproduced.

This audit did not rerun the repository test suite in this session. The immediately preceding scratch audit of this same main reported **7,329 passed / 8 skipped / 0 failed**. Treat that as prior evidence, not as a fresh test run from this documentation change.

## Required action

None for the evaluation-integrity code path.

Do not open a repair PR for either suspected issue unless a future regression reproduces it on the then-current source.

The next research lane may proceed using the evaluation contract above. Strategy status remains governed by `docs/strategy-rules/Strategy_Inventory.md`.
