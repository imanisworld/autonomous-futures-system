# Options Phase 2 paper automation — readiness checklist (2026-10-06)

**Audit only.** It adds no auto-submit, enables no paper or live order path,
and changes no execution code. Paper auto-submit requires a separate operator
authorization after Phase 1 proves out.

Status key: ✅ exists and verified in source · 🟡 partial · ❌ missing.
Runtime is unverified unless stated.

| # | Requirement | Status | Where / gap |
|---|---|---|---|
| 1 | Strategy fitness not failing, judged against a preregistered OOS reference | 🟡 | `options_evidence/fitness.py` (#1152) is revoke-only by design and has **no HEALTHY state that grants anything**. Missing: a registered tradable epoch with OOS R outcomes, a preregistered `FitnessPolicy`, and enough valid prospective observations past the first checkpoint. |
| 2 | Human-approved authority | 🟡 | `fitness.human_grant` needs a named approver plus an approval reference. Missing: a persisted `AuthorityState` store and an operator approval record format. |
| 3 | One canonical risk pipeline | ✅/🟡 | `options_manager/validation/portfolio_risk_gate.py` is canonical, using `(entry − premium_stop) × 100 × contracts`, $300 per trade, and an aggregate budget with no default. #1149 adds V1 NaN hardening and pins two remaining legacy full-debit authorities (`options_manager/risk_gate.py`, `risk/options_risk_engine.py`). **Prerequisite:** re-point the legacy packet chain (`paper_sim`/`dry_run_review`/…/`broker_boundary`) at the canonical gate before it is wired. |
| 4 | Contract-quality gate | ✅ | `contract_quality_gate.py` plus #1148, which fails closed on NaN, crossed quotes, and understated spreads. |
| 5 | Canonical signal | 🟡 | Schema, lifecycle and the read-only #1145 journal adapter are in #1151. Missing: a deployed #1145 collector with its timer installed, and a runtime consumer that folds the journal. |
| 6 | Paper order lifecycle (intent → ack → fill/partial/reject/cancel) | ❌ | `options_manager/order_ticket.py` / `broker_boundary.py` produce inert previews. No paper order state machine is keyed to `signal_id`. |
| 7 | Exit lifecycle (premium stop, invalidation, T1 trim, runner, time stop) | 🟡 | V1 resolution (`alert_ranker/lifecycle.py`, `contract_marks.py`) handles underlying and premium-stop exits for paper rows. Trim/runner policy is not defined per epoch (`122-IEX-E1` is UNRESOLVED). |
| 8 | Journal reconciliation (signal ↔ plan ↔ order ↔ fill ↔ outcome) | ❌ | Links exist in the schema (`SignalLinks`, `OutcomeEvidence`). No reconciler checks that every order and fill maps to exactly one signal and outcome. |
| 9 | Replay / prospective proof | ❌ | No options epoch is proof-ready (`docs/options-forward-proof-readiness-2026-10-06.md`). |
| 10 | Account safety independent of fitness | ✅ | Fitness imports nothing from the risk or execution modules (test). Caps live only in the risk gates. |
| 11 | Live options lock untouched | ✅ | `options_manager/live_lock.py` is not modified by any PR in this pass. |
| 12 | Security of operator surfaces | 🟡 | #1146 is merged (`445393f`) and provides the scanner in-app gate. Deployment is unverified. The operator must set the token and restore nginx auth. |
| 13 | Observer operability / runtime integrity | 🟡 | #1147 adds the read-only status tool, covering the #1145 heartbeat, WATCHING count, SPX health and journal tail. Not installed. The #1145 unit currently runs from the live tree, which is flagged; a release-pin drop-in template is in #1147. |

## Minimum sequence before any Phase 2 discussion

1. Deploy the #1145 collector release-pinned with its timer installed. Prove capture and dedupe live.
2. Run the #1151 adapter as a runtime consumer of the #1145 journal (follow-up).
3. Register a tradable epoch with OOS R and a `FitnessPolicy`, then collect
   one clean forward-proof window.
4. Build the paper order and exit lifecycle plus journal reconciliation,
   keyed on `signal_id`. Each is a separate PR, and anything touching the
   broker boundary gets independent review.
5. Only then: operator decision on paper auto-submit.
