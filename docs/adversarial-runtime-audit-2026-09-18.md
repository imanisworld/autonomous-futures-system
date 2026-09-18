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

## Finding 3 — "no revenge" policy defined from evidence

The loss-sequence audit does **not** support adding a fixed post-loss cooldown or a one/two-loss shutdown. Current active-family sealed rows contain only one same-day post-loss follow-up trade, and it won; 4HR and 3-2-2 have no same-day follow-up sequences. In the larger inactive 2-1-2 sensitivity population, stopping after two same-day losses removed profitable recovery trades and reduced net P&L in both temporal halves of both instruments.

Historical full-engine evidence points the same direction: PR #57 recorded a 556-day honest-fill comparison where removing the prior psychology/throttle bundle increased P&L and expectancy per trade on both MES and MNQ.

Therefore "no revenge trades" is defined mechanically as:
- no manual or unsignaled re-entry;
- no averaging down;
- every new entry must be a fresh reproducible strategy signal;
- every new entry must pass normal risk/execution gates.

Current settings remain `max_consecutive_losses=9999`, `circuit_breaker_losses=0`, and `early_session_loss_floor=0`. Add a post-loss throttle only if a compatible combined-account test later proves a benefit.

The three-trades/day limit remains a **provisional account-safety/isolation cap**, not an optimized revenge rule. It was restored by PR #376 for isolated forward-paper operation; current sealed studies do not provide a valid combined-account counterfactual for changing it.

**Status: POLICY RESOLVED FROM AVAILABLE EVIDENCE; re-open only with compatible combined-account post-loss evidence. See `docs/loss-sequence-risk-policy-audit-2026-09-18.md`.**

## Finding 4 — global drawdown policy corrected to 30%

At audit time the deployed global max drawdown was 20%, and the observed runtime drawdown was approximately 24.6% from a peak of $1,910.75 to $1,440.25. Direct invocation of the then-deployed `RiskEngine._check_max_drawdown()` returned `REJECTED / max_drawdown`.

Operator policy was subsequently clarified: the **global account hard survival floor is 30%**, not 20%. `risk_rules.yaml` is therefore updated to 0.30 as a prospective global risk-policy change. This does **not** rewrite historical evidence and does **not** alter frozen lane-specific drawdown contracts: wide-stop 4HR/3-2-2 remains at its preregistered 20% for the active epoch; Daily 2-2 and MES 1-2-2 already use their own 30% hard halts.

At 24.6%, the account is below the intended 30% global floor. The historical 20% rejection remains valid evidence of what the old policy did. The prospective policy was deployed in curated release `3715eb89b1f5...`, which differs from prior `088012983c3d...` by exactly one runtime file: `risk_rules.yaml`. Direct post-deploy invocation of the deployed `RiskEngine._check_max_drawdown()` at the same 24.62% state returned PASS.

**Status: RESOLVED FOR CURRENT GLOBAL POLICY at the `3715eb89b1f5` release boundary; frozen lane-specific thresholds remain unchanged.**

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

The watcher was then restarted/rebaselined at 13:57 UTC and adopted `088012983c3d...`; all three blocker conditions cleared. After the later #703 risk-policy promotion, futures-bot moved to `3715eb89b1f5...` at 14:45 UTC. The watcher correctly blocked on the new release until it was explicitly restarted/rebaselined at 15:05 UTC, then adopted `3715eb89b1f5...`. A transient MES feed-stale alert around the release boundary self-cleared by 15:06 UTC; the first post-rebaseline tick showed both feeds healthy and no blocker flags. Futures release integrity, broker flatness and Tradovate reliability remained healthy.

## Do not do

- Do not enable live trading.
- Do not reset Daily or wide-stop accounting epochs.
- Do not alter frozen lane-specific drawdown thresholds mid-epoch.
- Do not change loss-streak/revenge policy mid-epoch merely to satisfy this audit.
- Do not mix PaperBroker and Tradovate DEMO evidence.
- Do not treat a large shadow sample as proof of positive edge.
