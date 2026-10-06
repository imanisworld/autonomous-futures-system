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
| `alert_ranker/rh_options.py` `_risk_check` (manual RH evaluator, `/rh-options/evaluate`, gated by #1146) | per-contract + **full-debit** caps and stop-based R:R; **no planned-risk cap; NaN premium approved** | **fixed here (integration pass)**: binding `_planned_risk_guard`, so premium-stop planned risk × contracts ≤ $300 and non-finite or non-positive inputs refused, forcing `NO_TRADE` (no ticket, no shadow record); R:R and debit caps stay advisory | advisory/manual only; RH `submit_order` is a stub |
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

4. **Integration pass (post-#1145/#1146):** the manual RH evaluator approved on
   debit caps and R:R alone, with no planned-risk cap, and a NaN premium
   passed every comparison and was approved. It now applies the same
   `(entry − premium_stop) × 100 × contracts ≤ $300` rule (stop from its own
   DTE-tier multiplier) and refuses non-finite risk. Both tests fail on the
   prior code.

   Independent review then found the risk result was informational only:
   `evaluate_rh_options` set its decision from the setup gates and ignored
   `risk_result`. A NaN premium or a huge quantity still returned TRADE with
   an order ticket and a shadow record, and quantity 0 or negative was
   approved.

   Now `_planned_risk_guard` is a **binding** gate (`risk:<rule>` →
   `NO_TRADE`, with no ticket and no shadow record). It refuses a ticket when:
   - `premium`, `max_premium_per_contract`, `quantity` or `max_contracts` is
     not finite or not positive;
   - `quantity` is not a whole number ≥ 1;
   - premium risk or planned risk is ≤ 0;
   - planned premium-stop risk is greater than $300.

   The guard runs before, and independently of, the advisory checks, so a
   debit-cap refusal cannot hide an over-cap quantity.

   R:R (`rr_too_low`) and the debit caps stay **advisory** in `risk_result`,
   as before. The 0–7 DTE tier cannot meet its R:R floor by construction, so
   making R:R binding would have closed that lane; a test pins that it stays
   open. `per_contract_premium` was already a hard gate (`premium_over_cap`).

   Behaviour change, measured against `main` over 3,960 premium × DTE ×
   quantity inputs: every changed decision is `planned_risk_cap` on a ticket
   of two or more contracts whose premium-stop risk exceeds $300 (115 at 2
   contracts, 410 at 3, 645 at 5). No single-contract decision changes and
   nothing changes because of R:R. The $300 boundary passes, as in `paper_v1`.

   Intake (`_parse_rh_inputs`, used by `/rh-options/evaluate` and the
   messy-text path) refuses with a 422:
   - a boolean in any numeric field (`True` used to become 1);
   - a fractional `quantity` or `max_contracts` (1.5 used to be truncated to 1);
   - an overflowing or infinite count.

## Changed

- `alert_ranker/contract_marks.py`: non-finite stored risk ⇒ ∞.
- `alert_ranker/paper_v1.py`: refuse non-finite / negative / missing open risk.
- `alert_ranker/rh_options.py`: binding `_planned_risk_guard` (finite/positive inputs, whole quantity ≥ 1, planned premium-stop risk in (0, $300]) forces `NO_TRADE` in `evaluate_rh_options`; R:R and debit caps unchanged and advisory.
- `tests/test_options_risk_single_authority.py`: NaN regressions (8 fail on
  base), V1 ↔ canonical parity, cap agreement, canonical service does not
  import the legacy gate, legacy importer allowlist.

Evidence-epoch note: the V1 change only refuses a state that previously
produced an unbounded aggregate. It changes no setup, DTE, stop, target,
filter, risk cap, or scoring rule, so under the `cohort_change_rule` in
`docs/options_v1_evidence_epoch.json` it is an infra/reliability fix that does
not by itself start a new epoch. The operator decides when it ships.
