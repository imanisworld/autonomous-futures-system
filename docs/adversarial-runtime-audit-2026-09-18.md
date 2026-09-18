# Adversarial runtime audit — 2026-09-18

## Verdict

**HOLD / PAPER ONLY / DEMO EVIDENCE ONLY**

No accidental real-money execution route was found in the paths tested, but the system is not live-ready. This audit was performed read-only before any repairs; no strategy, risk, epoch, or evidence state was changed to obtain a passing result.

## Runtime identity after remediation

- Futures service release: `088012983c3d6da8bc1b433e324703726178d770`.
- Futures release integrity: PASS, 1,073 files.
- Options scanner release: `3b9770d8fed4ad1825cc325bab536ffea618a94e`.
- Options release integrity: PASS, 1,070 files.
- Coverage observer release: `58d6c5fa25c5dd3c8b8ed2cbeda36db74f6a5efd`.
- Coverage release integrity: PASS, 1,060 files.
- `LIVE_TRADING_ENABLED=false`.
- `TRADOVATE_ENV=demo`.
- `SCHEDULE_MODE=always_on_shadow`.
- `MAX_CONTRACTS_HARD_CAP=1`.
- Main journal flat; Tradovate DEMO flat; no wide-stop DEMO pending state at final audit snapshot.

## Proven safety boundaries

- Ordinary futures order placement is suppressed by `always_on_shadow`.
- RiskEngine rejects configured live mode in the Phase-1 path.
- Working-order state is re-read immediately before an external broker submission and fails closed if unreadable.
- Manual OPEN is removed. Manual/admin mutation routes require the webhook secret.
- Options scanner is advisory-only; provider profile reports `order_supported=false` and account endpoints forbidden.
- Wide-stop Tradovate DEMO execution is separate from the main shadow route and requires proof-pinned route/arm flags, Tradovate DEMO, live disabled, expected account pin, 5m feed, New York session, lane/global risk admission, durable pre-submit reservation, and a non-live broker object.
- Wide-stop DEMO daily capacity is three slots and combined open-risk ceiling is $450.
- PaperBroker uses pessimistic same-bar stop-first resolution and #670 gap-stop input is deployed.
- Canonical replay/runtime paths share DecisionEngine, RiskEngine and canonical strategy modules; targeted deployed-lineage safety/parity tests passed 330/330 during the audit.

## Finding 1 — public operational status surface

### Before

nginx proxied all `/status/*` paths publicly. Unauthenticated requests returned broker/account/runtime information from endpoints including `/status/today`, `/status/risk`, `/status/broker-account`, diagnostics and latest-webhook.

### Remediation

The active nginx config now exposes only `/status/public` without authentication. Every other `/status/*` endpoint requires Basic Auth through the existing operator credential file.

Proof after the change:

- external `/status/public` -> 200;
- external `/status/today`, `/status/risk`, `/status/broker-account`, `/status/diagnostics`, `/status/latest-webhook`, `/status/signa`, `/status/test-bracket` -> 401;
- direct localhost application calls to `/status/today`, `/status/risk`, and `/status/broker-account` -> 200, so internal monitoring remains available;
- `nginx -t` passes.

A timestamped pre-change nginx backup is preserved under `/root/afs-shared/backups/`.

**Status: RESOLVED on the VPS boundary.**

## Finding 2 — wide-stop DEMO EOD independence

The pre-audit wide-stop DEMO EOD resolver depended on receiving a 5-minute bar. The generic day-only fallback targeted the main journal, not the isolated wide-stop DEMO state.

PR #691 added a dedicated exit-only fallback that reuses the isolated DEMO state and account-exclusive reconciliation rules. The curated release `088012983c3d...` is exactly the prior `c538e2bc...` runtime plus this six-file #691 delta.

Installed timer:

`afs-wide-stop-demo-eod-fallback.timer`

Schedule:

`Mon..Fri 16:02:00 America/New_York`

The timer is enabled/active and triggers the exit-only service. The service runs from `/root/autonomous-futures-system`, uses the shared environment, has `NoNewPrivileges=true` and `PrivateTmp=true`, and cannot authorize entries.

PR #691 proof recorded 164 targeted tests and 5,935 passed / 7 skipped full-suite.

**Status: RESOLVED for DEMO forward collection; still not a live-trading approval.**

## Finding 3 — revenge/loss-streak policy conflict

Current risk configuration intentionally disables several historical throttle mechanisms:

- `max_consecutive_losses=9999`;
- `circuit_breaker_losses=0`;
- `early_session_loss_floor=0`.

The project operating rule still says no revenge trades.

This is a policy/evidence conflict, not a current accidental-execution path. Changing it during active evidence epochs would alter risk admission and contaminate comparisons.

**Status: HOLD / unresolved for live readiness. Do not retune an active epoch to clear this finding.**

## Finding 4 — drawdown survival floor is currently breached

Configured max drawdown: 20%.

Observed runtime drawdown during the audit: approximately 24.6% from a peak of $1,910.75 to $1,440.25.

Direct invocation of the deployed `RiskEngine._check_max_drawdown()` returned:

`REJECTED / max_drawdown`

The gate is real and should not be reset, weakened, or bypassed to create a green state.

**Status: BLOCKED for ordinary execution/live readiness; enforcement is working as designed.**

## Remaining forward-proof gaps

- No naturally occurring post-#670 `STOP_GAP` event has yet proven the new path in forward data.
- Continuous-symbol source provenance does not prove the exact dated contract; several evidence populations remain quality-blocked on roll provenance.
- Strategy edge remains unproven. Several shadow populations have sufficient rows for review but materially negative outcomes.
- Current strategy classifications remain unchanged: transition WAIT; structural P8 PROMISING BUT UNPROVEN / partial replication; 4HR/3-2-2/MES lanes remain evidence-only.

## Monitoring after concurrent #691 promotion

The #691 release promotion restarted futures-bot at 13:56 UTC. The watcher correctly detected:
- stale watcher release;
- service wrong release;
- unexpected restart.

The watcher was then restarted/rebaselined at 13:57 UTC and adopted `088012983c3d...`; all three blocker conditions cleared. Futures release integrity, broker flatness and Tradovate reliability remained healthy.

## Do not do

- Do not enable live trading.
- Do not reset Daily or wide-stop accounting epochs.
- Do not weaken the 20% drawdown rule.
- Do not change loss-streak/revenge policy mid-epoch merely to satisfy this audit.
- Do not mix PaperBroker and Tradovate DEMO evidence.
- Do not treat a large shadow sample as proof of positive edge.
