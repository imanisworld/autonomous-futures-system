# Options risk telemetry core

Phase 1 is measurement first. This module records risk facts already attached to
an advisory thesis; it does not choose or change risk policy.

`options_manager.risk.measure_risk_telemetry()` keeps these quantities separate:

- planned premium-stop risk per contract;
- planned total trade risk;
- full premium debit per contract;
- full premium debit for the candidate;
- current and projected aggregate planned risk;
- current and projected aggregate full debit; and
- concurrent position count as telemetry only.

It also carries the recorded per-trade and aggregate caps used by the canonical
portfolio gate. Those values are evidence about the advisory decision, not a
claim that either limit is calibrated or optimal.

The function is pure and fail-closed:

- missing contract/risk snapshots -> `INCOMPLETE`;
- non-finite or contradictory persisted facts -> `INVALID`;
- internally reconciled measurements -> `COMPLETE`.

A `COMPLETE` telemetry result is **not** a TAKE verdict. The canonical proof,
contract, and portfolio gates remain the authorities for trade actionability.

No position-count cap exists here. A thesis with 25 concurrent positions can
still produce complete telemetry if its existing risk facts reconcile; whether
that portfolio should be allowed is a separate, evidence-backed policy question.

This increment intentionally adds no storage table. The existing thesis snapshot
already persists the contract and risk inputs used here. Append-only outcome/risk
calibration storage should be wired after the forward-outcome schema lands so we
do not create a competing event store.
