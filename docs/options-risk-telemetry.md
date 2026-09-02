# Options risk telemetry core

Phase 1 is measurement first. This module records risk facts already attached to
an advisory thesis; it does not choose, change, or re-run risk policy.

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

## Authority boundary

`options_manager.validation.portfolio_risk_gate` remains the only portfolio-risk
authority. Telemetry does **not** recompute premium-stop risk from contract
premium, re-run current+candidate aggregate formulas, or re-apply trade/aggregate
caps. It projects the `RiskPlanSnapshot` that the canonical proof adapter already
persisted. Per-contract telemetry is only a decomposition of the canonical
persisted totals by the recorded contract count.

The function is pure and fail-closed on telemetry shape:

- missing contract/risk snapshots -> `INCOMPLETE`;
- non-finite, non-numeric, negative, or malformed persisted telemetry -> `INVALID`;
- complete usable measurement facts -> `COMPLETE`.

`COMPLETE` means the telemetry record is measurable, **not** that a trade is
approved and not that the recorded risk facts have been independently re-
validated. The canonical proof, contract, and portfolio gates remain the
authorities for actionability.

No position-count cap exists here. Position count is recorded only as a metric.
No default aggregate-risk budget is introduced.

This increment intentionally adds no storage table. The existing thesis snapshot
already persists the contract and risk facts used here. Any future calibration
storage should reuse the existing forward-outcome/event architecture rather than
create a competing event store.
