# /futures-deployment-safety-audit

Purpose:
Audit whether the deployed box is actually safe, and whether the deployed code is actually what was reviewed. `~/MAINVSCODE/afs-deploy.sh` / `scripts/atomic_release.sh` build from live `origin/main` at deploy time, not a pinned SHA reviewed earlier — this system has already had a deploy pick up an unreviewed-at-review-time commit that rode along between merge and deploy. This audit exists to catch that class of gap before or after any deploy.

Core rule: No proof, no run. "The box does X" is never asserted from a prior audit or from what main looked like at review time — it is asserted only after checking the box's actual deployed SHA directly.

Required checks:
- Reviewed SHA (the commit that was actually diff-reviewed, e.g. via `/futures-diff-review`) vs. deployed SHA (read from the box, not assumed) — `ops/live_box_guard.py`'s drift-report machinery (`_git`, `_sha256`, `_cmp`, `live_box_drift_report`) is the existing mechanism for this; use it rather than a fresh SSH one-off where possible
- If deployed SHA differs from reviewed SHA: read the diff between them and confirm it is safe to have ridden along (additive-only, no execution/risk/broker/strategy/journal touch) before treating the deploy as clean — do not assume a rideal ong commit is safe without reading it, same discipline as `/futures-diff-review`
- Service health: `systemctl is-active futures-bot` (or equivalent on the current box), `/health` endpoint
- Status endpoints: `/status/today`, `/status/broker-account`, `/status/live-preflight`, `/status/strategy` — confirm they respond and their content matches expected config (not stale/cached from a prior release)
- Env vars: `LIVE_TRADING_ENABLED` (must be `false` unless explicitly authorized), `PAPER_MODE` (must be `true` under current posture), `BROKER` (expected `tradovate`), `TRADOVATE_ENV`/account routing (expected `demo`, not `live`), `SCHEDULE_MODE`, `EXIT_MODE` (expected `static` under current posture)
- Live preflight armed state: `execution/live_preflight.py`'s state machine — confirm disarmed unless live trading has been explicitly authorized for this session
- Current position state: confirm `has_open_position` matches what's expected (flat, unless a position is deliberately open) — an unexpected open position after a deploy/restart is a blocker, not a warning
- `errors.log`: tail it, confirm no new errors since the last deploy that weren't already known
- Journal write health: confirm new journal entries are actually being written post-deploy (not silently broken by the restart)
- Broker auth state: confirm no active Tradovate auth-breaker trip (see the known 900s-breaker-on-401 behavior) that would silently block order placement
- Webhook validation: confirm a real (or synthetic, non-order-placing) payload still passes intake validation post-deploy — a schema/import drift here has caused a runtime crash on order placement once before (the lazy-import deploy trap)
- Deploy lock state: `scripts/deploy_lock.sh` / `$AFS_SHARED_DIR/deploy.lock` — confirm no stale/abandoned lock from a prior interrupted deploy

Forbidden actions:
- Do not run `scripts/atomic_release.sh promote`, `afs-deploy.sh --release`, or any action that restarts the live service, as part of this audit — deploying/restarting is always a separate, explicit operator-directed action, never bundled into an audit.
- Do not modify config, env vars, or code on the box.
- Do not arm live trading.
- Do not place a real test order — a synthetic payload for webhook-validation checks must not reach `execute_bracket`.
- Do not assert the box's state from a prior audit's memory — always re-check directly for this run.
- Do not treat "the code on main looks safe" as equivalent to "the deployed box is safe" — these are different claims requiring different evidence.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

System Classification:
PAPER ONLY / DEMO BROKER ONLY / EXECUTION CAPABLE / LIVE CAPABLE / UNSAFE / INCONCLUSIVE

Why:
2-5 decisive reasons.

What I Verified:
- reviewed SHA vs. deployed SHA compared directly
- any ride-along commits between them read and assessed
- service health and status endpoints checked live
- env vars (LIVE_TRADING_ENABLED, PAPER_MODE, BROKER, TRADOVATE_ENV, EXIT_MODE) checked live
- live preflight armed state checked
- position state checked
- errors.log tailed
- journal write health confirmed post-deploy
- broker auth breaker state checked
- deploy lock state checked

Problems Found:
Separate:
- blockers
- warnings
- minor cleanup

Required Fixes:
- must-fix before trusting this deploy
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- If deployed SHA differs from reviewed SHA and the diff between them has not been read, verdict is capped at HOLD — never APPROVE on an unexamined gap.
- `LIVE_TRADING_ENABLED=true` on the box, or reachable without explicit multi-layer authorization, is an automatic REJECT / UNSAFE regardless of any other finding.
- An unexpected open position after a deploy/restart is a blocker, capping the verdict at HOLD until explained.
- A broker auth-breaker trip or a webhook-validation failure caps the verdict at HOLD — the system may look "up" while silently unable to place or receive orders.
- A stale/abandoned deploy lock is a warning, not automatically a blocker, but must be surfaced and explained, not silently cleared as part of this audit.

Safe next step:
If APPROVE or PAPER ONLY / DEMO BROKER ONLY with no blockers, the safe next step is normal observation — this audit never itself authorizes a deploy or a config change. If HOLD or REJECT, name the exact discrepancy (SHA gap, env var, broken endpoint) and treat resolving it as the only safe next action before trusting anything else this box reports.
