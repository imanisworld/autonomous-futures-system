# Futures — Current State Handoff

_As of 2026-09-01. This is the single current handoff. Repository facts below are verified; VPS/runtime facts remain UNKNOWN until the read-only box audit is run._

## Verdict

**HOLD / AUDIT ONLY pending VPS verification.**

The approved repository fixes are now merged. No strategy parameters, inverse mechanics, risk limits, broker routing policy, or fill-resolution mathematics were changed in this cleanup.

## Repository state

- Futures code-fix merge baseline after the approved sequence: `cb8c58786c89d85826ab1ec3f046ba3abc47fa16` on `main`.
- The concurrent options work that advanced `main` before this sequence was preserved; no options commits were rewritten or recreated.
- Current MNQ ORB Breakout inverse implementation remains a **do-not-touch** area: isolated PaperBroker, fixed 1 contract, pessimistic same-bar handling, static mirrored bracket, IOC-limit entry.
- Forward evidence resolver remains conservative; no fill-math changes were made.

## Approved futures fixes — merged

The original review PRs were drafts. GitHub's ready-for-review connector action failed, so each merge used a non-draft replacement branch pinned to the exact already-tested head SHA. No code was regenerated.

| Original | Merged via | Fix | Result |
|---|---|---|---|
| #397 | #410 | Normal PaperBroker/replay config parity | MERGED — exact tested head |
| #399 | #411 | Promotion gate fails closed on blockers / instrument-scoped execution claims | MERGED — exact tested head |
| #400 | #412 | Exact five campaign populations, conflicting-duplicate integrity, collector-census ownership correction | MERGED — exact tested head |
| #401 | #413 | `project_check daily` overall blockers / false-green correction | MERGED — exact tested head |
| #406 | #414 | Durable final no-trade suppression evidence + Discord reason visibility + existing diagnostic update | MERGED — exact tested head |
| #408 | #415 | Persist existing deterministic `AFS-...` client order identity across intent/trade/outcome/order-id evidence | MERGED — exact tested head |
| #407 | #416 | Current `TRADE_INTENT -> CANCELLED` semantics and exact-identity trade-chain joins | MERGED — exact tested head, after #408 |

Merge order intentionally placed client-order-ID persistence (#408/#415) before trade-chain exact-ID consumption (#407/#416).

## Strategy evidence classifications

`docs/strategy-rules/Strategy_Inventory.md` is the strategy evidence source of truth. The September reconciliation supersedes stale optimistic rows with already-proven closure results:

- ORB Reclaim current/first_cross — **BROKEN — negative evidence** (#368).
- ORB Reclaim V4-R — **WAIT** (#368 preregistered study).
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT EXECUTABLE FORM** (#372).
- 4HR Re-Trigger MES — **BROKEN / WAIT** (#372).
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#366).
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#367).
- ORB Breakout inverted evidence lane — **PROMISING BUT UNPROVEN** (#364 historical study; current box posture still requires verification).
- MES `strat_122` — **WAIT** (#373 executable-population audit).

Do not merge PR #369 as-is. Only its already-proven classification evidence was reconciled; stale runtime claims and its large evidence payload were not imported.

## Monitoring architecture already verified in repo

- Most futures evidence/shadow collectors execute inside the single `futures-bot` webhook process; they are not separate daemons.
- `scripts/evidence_lane_health.py` / `ops/evidence_lane_health.py` owns MNQ/MES event-driven lane health. Fresh feed + zero candidates can correctly be `QUIET`; candidate-file age alone must not label the lane DEAD.
- The forward campaign has exactly five configured populations: `vwap_hold/control`, `vwap_hold/modified`, `orb_reclaim/control`, `orb_reclaim/modified`, `vwap_rejection/observer`.
- Campaign enablement alone does not prove all five can produce evidence; entry-refresh, 5-minute feed, VWAP-early mode, and actual 5-minute webhook delivery are box-side prerequisites.
- Generic `feed_watchdog` and per-instrument `feed_gap_alarm` serve different purposes. Actual timer/cron installation is still a box fact.

## Box/runtime facts still required

The next pass is **READ ONLY**. Do not infer these facts from repository configuration alone.

1. `futures-bot` service state, PID/process cwd, deployed SHA/manifest, and release-integrity enforcement.
2. Nonsecret futures env pins: broker/demo mode, schedule mode, inverse/proof modes, fill model/tolerances, campaign prerequisites, 5-minute feed, and evidence-lane modes.
3. Fresh MNQ/MES authoritative 15-minute bars; 5-minute bars if enabled; generic webhook receipt freshness separately.
4. `scripts/evidence_lane_health.py --log-dir /root/afs-shared/logs --json` output.
5. Raw exact-five campaign counts/outcomes/days/generating SHAs and any duplicate-ID conflicts.
6. Actual systemd timers / cron for feed watchdog, per-instrument gap alarm, day-only exit, and ops automation evidence.
7. Current journal `TRADE_INTENT`, `TRADE`, `OUTCOME`, `ORDER_IDS`, `BLOCK_VISIBILITY`, suppression evidence, cancellations, and unresolved state.
8. Tradovate demo account list, resolved account, positions/orders, and whether account pinning is actually needed.

## Older PR posture

- #371 — substantially superseded; preserve unique evidence before closure.
- #374 — account-routing guard remains conditional on box proof of multiple/ambiguous demo-account routing.
- #377 — superseded by current `main` behavior.
- #383 — contaminated with unrelated strategy/research changes; do not merge.
- #390 — optional dashboard cleanup; not a futures proof blocker.

## Safe next step

Run one read-only VPS evidence pass against the then-current `main` SHA. Update **this same handoff** with the resulting service/env/feed/journal/broker proof. Do not create another current-state handoff and do not reopen strategy parameters as part of the runtime verification.
