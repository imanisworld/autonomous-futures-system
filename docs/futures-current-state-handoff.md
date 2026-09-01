# Futures — Current State Handoff

_As of 2026-09-01. This is the current audit handoff, not a proof artifact. Runtime/VPS facts remain unverified until the read-only box audit is run._

## Audit baseline

- Repository: `imanisworld/autonomous-futures-system`
- Audited `main` SHA: `14880a0b49a78f77a8ab37bdc701760a8fe9e90a`
- Main was rechecked on 2026-09-01 and had not moved before this PR.
- This document supersedes the stale 2026-07-08 runtime summary.
- Do not infer deployed-box parity from this repository baseline.

## Current posture

**HOLD / AUDIT ONLY pending box verification.**

The repository-side audit is substantially complete. Remaining unknowns are actual-system facts: deployed SHA, service env, running process/timer/cron state, current evidence freshness, journal state, and Tradovate demo account routing.

## Proven safe / do not touch

### MNQ ORB Breakout inverse paper lane

The current inverse implementation is intentionally isolated and should not be rewritten:

- Eligible instrument/concept: MNQ / `orb_breakout`
- `paper_sim` mode uses an internal `PaperBroker`
- fixed one-contract submission
- IOC-limit entry with 8-tick MNQ tolerance
- pessimistic same-bar handling
- breakeven disabled
- source signal/risk validation occurs before inversion
- no strategy-parameter change is authorized

### Forward outcome resolver

Do not change fill-resolution math without new evidence:

- same-bar stop + target => LOSS
- target-only movement on fill bar is not credited when entry-before-target is unknowable
- favorable fill-bar excursion is excluded
- unknown price path resolves stop-first
- slippage/commission assumptions remain required

### Daily loss scaling

Resolved as intentional: configured base daily loss scales with submitted contracts. The inverse lane submits one contract, so this is not an open defect.

### Active ORB field lineage

Repo audit found no active inverse-lane field-lineage defect:

- Pine template emits `timeframe`, `orb_high`, `orb_low`, `orb_status`
- state builder routes these fields into `MarketState.orb`
- LONG ORB Breakout geometry uses ORB high
- SHORT ORB Breakout fails closed when ORB low is absent

## Proven repository defects / fix status

### 1. Normal PaperBroker / ReplayEngine config parity

**FIXED ON PR #397 — not merged to `main` yet.**

Baseline defect: `webhook/runner.py::_paper_broker()` omitted:

- `breakeven_at_1r`
- `entry_fill_model`
- `entry_tolerance_ticks_by_root`

Replay passed all three. The isolated MNQ inverse lane was unaffected because it constructs its own broker explicitly.

PR #397 now passes the existing config fields through normal `_paper_broker()` and adds regression coverage. Targeted tests passed in the branch apply workflow. Full PR CI remains the merge gate.

### 2. Promotion command can report success with promotion blockers

`ops/project_check/promotion.py` currently uses `ok` primarily as “evidence loaded.” CLI exit behavior does not reliably fail when hard promotion blockers exist.

Additional defects:
- tolerance claim can match any instrument rather than the relevant instrument
- claimed contract quantity is accepted without cap validation

**Required fix:** preserve `ok` for report-generation compatibility; add fail-closed `gate_pass` / `promotion_eligible`, instrument-scoped tolerance/quantity validation, and CLI regression tests.

### 3. Forward campaign report/census can hide configured arms

Campaign config defines exactly five populations:

1. `vwap_hold/control`
2. `vwap_hold/modified`
3. `orb_reclaim/control`
4. `orb_reclaim/modified`
5. `vwap_rejection/observer`

Current report/census derive populations from observed rows and can make a zero-row arm disappear. `collector_census` also groups campaign state too broadly by variant.

**Required fix:** initialize the exact five configured populations, overlay observed data, report unexpected populations separately.

### 4. Daily project check can return green despite non-trade-chain blockers

`ops/project_check/daily.py` / CLI top-level success is driven primarily by trade-chain PASS. Runtime drift and source-of-truth contradictions can therefore coexist with a successful command.

The strategy inventory check also conflates evidence verdict with deployment state: PROMISING does not mean “must be enabled”; WAIT does not automatically mean “must be disabled.”

**Required fix:** explicit overall blockers/status and separate evidence classification from config state.

### 5. Why-no-trade final suppression is not durable in the primary journal

The system already has `.claude/commands/futures-why-no-trade.md` and should keep it.

Proven visibility gaps:
- `BLOCKED_MAX_TRADES` and `BLOCKED_LOSS_LOCKOUT` early-return before normal decision journaling
- schedule and working-order suppressions occur after a durable `TRADE_INTENT`
- returned result changes to `SHADOW_NO_ORDER` / `ORDER_SUPPRESSED`, but the final reason is not persisted to the primary journal
- Discord non-TRADE formatting does not consistently show `reason`, `gate_reason`, or `failed_gates`

Existing evidence surfaces should be reused:
- strategy-context observations
- opportunity lifecycle
- latest-webhook
- evidence-lane health

Do not build a replacement diagnostic system.

### 6. Trade-chain checker models old cancellation semantics

Current runner behavior for a broker attempt that never opens is:

`TRADE_INTENT -> CANCELLED OUTCOME`

A confirmed `TRADE` row is written only after OPEN is actually confirmed.

`ops/project_check/trade_chain.py` currently anchors pairing on confirmed `decision=="TRADE"` rows while treating OUTCOME rows broadly. Its tests still model cancellation as `TRADE -> CANCELLED`.

This can create false unmatched outcomes or FIFO mispairing.

**Required fix:** update the checker/tests for current confirmed-execution semantics.

### 7. Existing client order identity is not carried through the journal

The runner already computes deterministic `client_order_id` for idempotent broker submission. Do not invent another identity scheme.

**Required fix:** persist/use this existing ID to reconcile intent/order/outcome evidence where applicable, rather than relying only on same-instrument FIFO.

### 8. Campaign duplicate IDs can hide conflicting rows

`ops/forward_campaign_report.py` collapses candidate/outcome rows into dictionaries keyed by `candidate_id`. It reports duplicate counts but does not prove duplicate rows are identical.

**Required fix:** distinguish identical duplicate from conflicting duplicate; conflicting duplicate is a blocker.

### 9. Collector census is not authoritative for event-driven futures lanes

Many futures shadow/evidence lanes execute inside the main webhook service and write only when patterns occur. Silence can therefore mean healthy/quiet, not dead.

`ops/evidence_lane_health.py` already understands this and should remain authoritative for those lanes. Do not create another monitor.

## Forward campaign runtime prerequisites

`FORWARD_EVIDENCE_CAMPAIGN` alone does not prove every arm can generate rows.

Box audit must verify:

- `FORWARD_EVIDENCE_CAMPAIGN`
- `ENTRY_REFRESH_MODE`
- `FIVE_MIN_FEED_ENABLED`
- `VWAP_HOLD_EARLY_MODE`

ORB modified depends on entry-refresh. VWAP modified depends on the 5m feed and early-shadow mode.

## Runtime architecture map

Most futures/shadow collection is inside the single `futures-bot` webhook process, not separate daemons.

Expected in-process evidence includes:
- bar history/journal
- strategy-context observations
- generic shadow candidates/outcomes
- MNQ Strat evidence
- MES trend-consolidation evidence
- forward A/B evidence

Separate operational jobs include feed monitoring and day-only-exit fallback.

Do not ask the box auditor to hunt for one service per shadow lane.

## Box audit still required

The read-only VPS/Codex audit must establish:

- deployed SHA and repo/box parity
- running `futures-bot` PID/cwd
- release manifest/fingerprint and release-integrity enforcement
- nonsecret futures runtime pins
- MNQ/MES 15m feed freshness
- 5m freshness when enabled
- actual evidence-lane health
- feed-gap cron/timer state
- whether `collector_census` is scheduled anywhere
- raw five-population campaign counts and generating SHAs
- journal TRADE_INTENT/TRADE/cancel/reject/suppression state
- current unresolved/open position state
- Tradovate demo account list, selected account, positions, working orders
- broker/journal parity
- hard contract cap

Until that evidence exists, runtime conclusions remain UNKNOWN rather than PASS.

## Open PR posture

Do not merge stale PRs blindly.

- #371 — substantially superseded; preserve any unique evidence before closure
- #374 — account-routing guard contains reusable exact-account pin logic; port only after box account proof
- #377 — superseded by current main behavior
- #383 — contaminated with unrelated strategy/research changes; do not merge
- #390 — optional dashboard cleanup; low priority
- #369 — closed; salvage evidence/classifications only, not stale runtime posture
- #397 — current futures audit-fix PR; PaperBroker/replay parity fix plus this handoff update; do not merge until CI/review pass

## Strategy inventory reconciliation still required

The current inventory is stale relative to later evidence.

Evidence already established for reconciliation includes:
- ORB Reclaim current/first-cross: BROKEN — negative evidence
- ORB Reclaim V4-R: WAIT
- 4HR Re-Trigger MNQ: BROKEN FOR CURRENT EXECUTABLE FORM
- 4HR MES: BROKEN / WAIT
- 12HR Miyagi: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS
- 60M 3-2-2: BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS
- ORB Breakout inverted: PROMISING BUT UNPROVEN
- MES strat_122: WAIT

Do not merge PR #369 as-is; port only the evidence-backed documentation conclusions.

## Smallest safe sequence

1. Complete isolated repo fixes in reviewed PRs.
2. Run the already-prepared read-only VPS audit when box access is available.
3. Update this same handoff with verified box facts.
4. Reconcile Strategy Inventory from preserved evidence.
5. Re-run relevant tests and project checks.
6. Do not alter strategy parameters or inverse mechanics as part of cleanup.
