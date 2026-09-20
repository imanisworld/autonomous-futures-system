# Webull Paper Adapter Spec — 2026-09-20

## Verdict

**SPEC ONLY / PAPER MIRROR CANDIDATE / NO ORDER AUTHORITY.**

This document defines the minimum safety contract before any Webull integration is added to the futures/options system. It does not build an adapter, call Webull, submit paper orders, alter futures/options strategy logic, alter risk logic, or authorize live trading.

Core rule: **No proof, no run.**

## Goal

Use Webull only as an optional external **paper-trade visibility mirror** so the operator can see paper trades in a broker-style interface and later compare Webull paper records against the system's internal paper journal.

The internal AFS journal remains the source of truth.

Valid direction:

```text
AFS signal -> AFS internal paper journal -> optional Webull paper mirror -> reconciliation report
```

Invalid direction:

```text
Webull state -> strategy authority
Webull fill -> strategy validation
Webull paper P/L -> live-trading approval
```

## Non-goals

This lane must not:

- add live execution;
- connect any live Webull trading endpoint;
- make Webull a broker-of-record for validation;
- replace the internal paper journal;
- change stops, targets, risk limits, strategy selection, scheduler cadence, Discord routing, or Signa behavior;
- mirror trades before paper-only guards and fail-closed tests exist;
- treat Webull paper fills as realistic live-fill proof.

## Required environment contract

The adapter must fail closed unless all required paper-only flags are explicitly safe.

Required variables:

```env
WEBULL_APP_KEY=<present>
WEBULL_APP_SECRET=<present>
WEBULL_TRADING_MODE=paper
WEBULL_LIVE_TRADING_ENABLED=false
WEBULL_API_ENABLED=false
WEBULL_PAPER_TRADING_ENABLED=true
```

Interpretation:

- `WEBULL_APP_KEY` and `WEBULL_APP_SECRET` may exist locally, but must never be logged, printed, committed, or copied into docs.
- `WEBULL_TRADING_MODE` must equal `paper`.
- `WEBULL_LIVE_TRADING_ENABLED` must equal `false`.
- `WEBULL_API_ENABLED=false` is the default safe state. It allows configuration to exist without permitting calls.
- `WEBULL_PAPER_TRADING_ENABLED=true` indicates the operator intends Webull to be a paper-only candidate, not an active submitter.

Any missing or unsafe value must produce a blocked state, not a fallback.

## Phase gates

### Phase 0 — Config presence only

Allowed:

- check that required variable names exist;
- redact all secret values;
- verify `.env` is ignored and untracked;
- verify Webull SDK folders/logs are ignored or absent.

Forbidden:

- API calls;
- authentication;
- account lookup;
- paper order submission;
- live endpoint discovery by trial.

Exit criteria:

- local env shows Webull paper flags are safe;
- no secrets in git diff/status/logs;
- Public API disabled if not under active read-only audit.

### Phase 1 — Read-only paper/account probe

Allowed only after Phase 0:

- authenticate against the documented paper/sandbox context only;
- retrieve paper-account identity/status if the official API supports it;
- record sanitized metadata only: provider, mode, reachable, timestamp, paper account indicator.

Forbidden:

- any order endpoint;
- any cancel endpoint;
- any live account endpoint;
- any retry loop capable of hammering API auth.

Exit criteria:

- read-only probe proves `mode=paper` from API response or official endpoint semantics;
- no live account identifiers are logged;
- failed auth fails closed without retries beyond a tight cap;
- test proves live mode refuses before network call.

### Phase 2 — Paper mirror dry-run

Allowed:

- convert an internal paper trade into a Webull paper order request object;
- log the request object with account/order credentials omitted;
- do not submit it.

Required fields:

```text
mirror_id
journal_trade_id
instrument
broker_symbol
side
quantity
order_type
limit_price if applicable
stop_price if applicable
time_in_force
source_strategy
source_timeframe
created_at
submit_allowed=false
```

Exit criteria:

- dry-run output is deterministic;
- all source journal IDs are preserved;
- no order submission function is called;
- invalid/missing stop blocks the mirror object.

### Phase 3 — Paper mirror submit

Allowed only after separate approval:

- submit paper orders to Webull paper only;
- capture Webull paper order ID/status;
- write mirror metadata to a separate Webull mirror journal.

Required guards:

- `WEBULL_API_ENABLED=true` must be explicit;
- `WEBULL_TRADING_MODE=paper`;
- `WEBULL_LIVE_TRADING_ENABLED=false`;
- `WEBULL_PAPER_TRADING_ENABLED=true`;
- internal AFS paper journal row already exists;
- trade has stop, target, instrument, quantity, and risk metadata;
- daily mirror order cap enforced independently of strategy cap;
- duplicate mirror suppression by `journal_trade_id`.

Hard blocks:

- live mode string;
- unknown mode string;
- missing stop;
- missing internal journal trade ID;
- instrument mapping mismatch;
- account identifier not proven paper;
- API response ambiguous about paper/live context;
- Webull SDK/API error that leaves order state unknown.

### Phase 4 — Reconciliation

Compare AFS paper against Webull paper. This is the actual value of the integration.

Required reconciliation fields:

```text
journal_trade_id
webull_order_id
instrument
broker_symbol
side
quantity
AFS entry time
Webull submit time
Webull accepted/rejected/cancelled state
AFS entry price
Webull fill price
AFS exit time
Webull exit/final state
AFS P/L
Webull paper P/L
delta_entry_price
delta_exit_price
delta_pnl
reject_reason
reconciliation_status
```

Reconciliation statuses:

```text
MATCHED
PRICE_DRIFT
TIMING_DRIFT
QUANTITY_MISMATCH
REJECTED_BY_WEBULL
WEBULL_UNKNOWN_STATE
AFS_MISSING_EXIT
WEBULL_MISSING_EXIT
DUPLICATE_MIRROR_BLOCKED
```

## Required tests before any order-submit code

Minimum test set:

1. `WEBULL_TRADING_MODE=live` blocks before network call.
2. `WEBULL_LIVE_TRADING_ENABLED=true` blocks before network call.
3. missing app key blocks.
4. missing app secret blocks.
5. `WEBULL_API_ENABLED=false` blocks all network calls.
6. `WEBULL_PAPER_TRADING_ENABLED=false` blocks mirror submit.
7. missing stop blocks mirror object creation.
8. duplicate `journal_trade_id` blocks duplicate mirror submission.
9. broker-symbol mapping mismatch blocks.
10. API ambiguous mode response blocks.
11. Webull exception does not retry indefinitely.
12. no secret values appear in logs, exceptions, Discord, or journals.
13. internal AFS paper journal remains source of truth even when Webull mirror fails.

## Logging rules

Allowed logs:

```text
provider=webull
mode=paper
operation=read_only_probe|dry_run|paper_mirror_submit|reconcile
journal_trade_id=<id>
mirror_status=<status>
redacted_order_id=<last4-or-hash-only>
```

Forbidden logs:

```text
WEBULL_APP_KEY
WEBULL_APP_SECRET
access tokens
refresh tokens
full account numbers
raw auth headers
raw request bodies containing credentials
```

## Deployment rules

No VPS deployment is required for Phase 0 or this spec.

Before any VPS deployment involving Webull:

- secrets must live only in the appropriate private environment file;
- release manifest must contain no secrets;
- release integrity must pass;
- live trading must remain disabled unless a separate live-execution authorization exists;
- Tradovate demo/futures posture must be verified unchanged;
- Webull adapter must remain paper-only;
- rollback target must be recorded.

## Current status

As of this spec:

- local Webull credentials may be present;
- desired local mode is paper;
- Webull live trading must remain disabled;
- Webull API calls are not approved;
- no adapter exists;
- no order submission is approved;
- Public is not the paper-trading path and should remain disabled unless under separate read-only audit.

## Safe next implementation after this spec

The smallest safe code change, if explicitly approved later, is **Phase 0 config validation only**:

- a pure config parser;
- no Webull SDK import required;
- no network;
- no order objects;
- no strategy/risk/execution path changes;
- tests for fail-closed flags and secret redaction.

Classification remains:

```text
PAPER MIRROR CANDIDATE
PROMISING OPERATOR VISIBILITY
UNPROVEN EXECUTION
NO LIVE AUTHORITY
```
