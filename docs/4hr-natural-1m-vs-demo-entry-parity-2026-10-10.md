# MNQ 4HR — natural 1-minute touch vs guarded DEMO entry

**Status: VERIFIED CODE-PATH MISMATCH / QA ONLY / NOT DEMO READY.**
**As of:** 2026-10-10. **Source:** GitHub `main` inspected in this session. **No VPS or broker proof.**

## Decision and scope

This is a narrow *source-route* inspection, not a new strategy experiment or a replay-scoring result. The profitable-looking pre-armed historical 4HR cohort must **not** be treated as evidence that the existing guarded Tradovate DEMO implementation exercises the same strategy. The trade-time semantics differ. No new execution route is authorized by this document.

## Two existing paths — do not treat as equivalent

| Contract | Natural 1m 4HR observer | Guarded Tradovate DEMO |
|---|---|---|
| Ingress | `webhook/runner.py::process_alert` 1m early-return branch | `context/five_min_feed.py::record_five_min` → `context/wide_stop_demo_runtime.py` → `context/wide_stop_demo_runtime_core.py::process_demo_five_min_bar` |
| Signal arm | Previously published read-only `context/four_hr_observation.py` ARMED state; separate canonical 5m arm publisher | Reconstructs candidate through 5m `collector._evaluate_canonical_candidate`, DecisionEngine and lane RiskEngine |
| Trigger/decision | Natural 1m bar crosses pre-armed level; reference is trigger or adverse 1m gap-through open | Completed 5m DecisionEngine signal, subsequent broker `ioc_limit` with **8-tick** entry tolerance |
| Stop source | Last genuinely complete prior 1H candle **before the touching 1m bar opened**; fail-closed when missing | Canonical completed 5m strategy decision stop; entry and post-fill checks apply |
| Scope | Observer-only, emits `TRIGGER_TOUCH` and classification metadata | Can issue a Tradovate DEMO bracket when independently proof-pinned, approved and eligible |
| Broker | **None**, `execution_reachable=false`, `fill=None`, `risk=None`, `trade_authorized=false` | `BracketOrder` / `broker.execute_bracket`, position and journal guarded by account/route gates |
| Data/evidence | `tf1m/4hr_trigger_evidence_*.jsonl` plus claim/observation state | Isolated `tradovate_demo_evidence/` ledger; DO NOT mix with paper/observer profit records |
| Risk | Observation records an *illustrative* `paper_entry_1tick`; it is **not** a broker fill or net return | Lane-local 4HR caps **300 ticks**, **R:R >= 1.0**, one contract, post-fill stop/R:R/dollar checks; cannot be inferred from observer event |

**Implication:** Passing safety tests for the 1m observer and 5m IOC DEMO independently does not establish *entry timing, stop selection or fill parity*. The observer must remain broker-inert. Never attach its event directly to a broker as a shortcut.

## Existing proof / no-duplicate checklist

**DONE / DO NOT REDO**: Historical MNQ 4HR broad pre-armed (Sep 18), continuation treatment (Sep 20), original risk ablations, 300t/1R paper-ledger IOC cell, tests of the guarded 5m DEMO route, and Oct 4 observer-epoch activation. See `docs/agent-work-state.md`, `docs/4hr-natural-1m-observation-epoch-2026-10-01.md`, `docs/4hr-prearmed-touch-ab-2026-09-18.md`, `docs/4hr-prearmed-continuation-treatment-2026-09-20.md`, and `docs/wide-stop-hypothetical-ledger-lane-amendment-2026-09-08.md`.

**New QA-only integration regression:** `tests/test_combined_execution_risk_qa.py::test_natural_1m_4hr_touch_cannot_place_demo_order_even_when_route_armed` exercises a real observer arm/touch under an explicitly armed DEMO selector and asserts **no 5m DEMO call, no risk, no fill, no broker authorization**. See draft PR #1210. This intentionally proves isolation, **not parity**.

## Evidence needed for a true 1m-vs-5m parity decision

1. Use only *eligible, distinct, naturally received* `arm_key` touches from the valid post-Oct-4 observer epoch. Exclude `UNKNOWN` contract matches until separately reviewed and all `MISMATCH` records; preserve blocks, duplicates and no-arm cases as separate counts.
2. Match to the corresponding same-day canonical 5m setup using **full setup identity** (date, direction, structural trigger, setup/4AM bar stamps, dated contract when proven); never join just by timestamp, ticker root or winning outcome. Report unmatched 1m and unmatched 5m candidates.
3. For each matched setup, report 1m earliest valid availability, gap-through fill reference, prior completed-1H stop, 5m signal decision time, completed-5m reference and IOC8 eligibility, actual post-fill geometry, invalid/no-fill reasons and source contract identity. No counterfactual P&L promotion from this mechanic-only proof.
4. Pin time zone/clock convention per lane, exact test commit SHA, session boundaries, source fingerprint, slippage assumption, pessimistic same-bar handling and risk config in a *new preregistered analysis contract* before scoring any new outcome or parameter cell.
5. Obtain **independent breaker review** on opening any 1m DEMO path. A broker-capable change requires explicit operator GO only *after* evidence and code-safety review. No existing live/demo flags, global stop/R:R floors, guards, collector epochs or settled trial results may be modified as a side effect.

## Progression block

The current observer prereg, `docs/prereg-forward-one-min-trigger-evidence-review-2026-09-18.md`, requires **10** separate natural touches, **20** trading days and **2 calendar months**, plus zero mechanism faults, *before a discussion of paper-fill authority*. Its canonical 4HR sample starts 2026-10-04T21:30:05Z. Therefore the calendar gate is not met as of 2026-10-10. Even after a mechanism PASS, positive net expectancy and separate risk/order permissions still must be established.

**Explicit non-actions:** No broker I/O, orders, restart, build, merge, deploy, parameter tuning, status promotion, journal/epoch reset, or blinded-trial peek performed here.
