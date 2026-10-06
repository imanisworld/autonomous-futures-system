# Options risk pipeline audit — 2026-10-06

Question: does any options path still treat the full premium debit as planned
risk, and is there one authoritative risk pipeline?

Intended rule: planned risk = (premium entry − premium stop) × 100 × contracts,
underlying invalidation required; ≤ $300 per trade; aggregate open planned
risk ≤ $1,000; position count recorded only; capital deployed and correlation
exposure separate; missing stop / invalidation / premium / contract data fails
closed.

## Inventory

| Path | Risk math | Status | Authority |
|---|---|---|---|
| `alert_ranker/paper_v1.py` `build_v1_contract_fields` (deployed V1 scanner) | (ask − 0.75·ask) × 100, 1 contract; $300 / $1,000; requires invalidation + target | correct | **runtime authority for the deployed V1 paper lane** |
| `alert_ranker/contract_marks.py` `aggregate_open_planned_risk` | sum of ACTIVE V1 OPEN `planned_risk_dollars`; damaged row ⇒ ∞ | **fail-open on NaN (fixed here)** | feeds V1 |
| `options_manager/validation/portfolio_risk_gate.py` | `planned_risk_from_premium_stop`; aggregate budget has no default (None blocks); capital deployed, position count, correlation reported separately | correct | **canonical authority for the options_manager advisory / plan lane** (`options_manager/app.py` → `advisory_decision`) |
| `options_manager/validation/contract_quality_gate.py` | (premium − premium_stop) × 100 × contracts ≤ stated max ≤ $300 | correct; NaN gap fixed in PR #1148 | contract facts |
| `options_manager/risk_gate.py` `evaluate_packet` | **full debit** `max_premium × 100 × contracts` + contract-count cap | legacy | **not wired into any service.** Its `RiskGateResult` still gates the legacy library chain `paper_sim` / `fill_stress` / `dry_run_review` → `human_confirm` → `order_ticket` → `broker_boundary` |
| `risk/options_risk_engine.py` | per-contract and total **debit** caps; stop-based R:R only | legacy | futures options companion only; `OPTIONS_COMPANION_ENABLED=false` by default |

## Findings

1. **No running path uses full debit as planned risk.** The deployed V1 lane
   and the canonical advisory gate both use the premium-stop formula, and the
   two agree numerically (parity test).
2. **Fail-open fixed:** a stored `NaN` planned risk made the V1 aggregate
   `NaN`; the caller only checked `== inf`, and `NaN > 1000` is False, so the
   aggregate cap would not have fired. Now any non-finite or negative stored
   risk reads as ∞, and `build_v1_contract_fields` refuses an unknown open-risk
   state (`open_risk_state_invalid`). Reachability is low (stored risk is
   computed from a validated finite ask), so this is defense in depth. No
   threshold or rule changed.
3. **Two legacy full-debit authorities remain in source.** They are pinned by a
   test: any new production importer of `options_manager.risk_gate` or
   `risk.options_risk_engine` fails CI. The legacy packet chain must be
   re-pointed at the canonical portfolio gate before it is ever wired. That
   chain ends at `broker_boundary`, so removing it is an independent-review
   change and is deliberately **not** done here (no second authority is
   created and none is removed unsafely).

## Changed

- `alert_ranker/contract_marks.py`: non-finite stored risk ⇒ ∞.
- `alert_ranker/paper_v1.py`: refuse non-finite / negative / missing open risk.
- `tests/test_options_risk_single_authority.py`: NaN regressions (8 fail on
  base), V1 ↔ canonical parity, cap agreement, canonical service does not
  import the legacy gate, legacy importer allowlist.

Evidence-epoch note: the V1 change only refuses a state that previously
produced an unbounded aggregate. It changes no setup, DTE, stop, target,
filter, risk cap, or scoring rule, so under the `cohort_change_rule` in
`docs/options_v1_evidence_epoch.json` it is an infra/reliability fix that does
not by itself start a new epoch. The operator decides when it ships.
