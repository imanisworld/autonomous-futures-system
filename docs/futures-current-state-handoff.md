# Futures — Current State Handoff

_As of 2026-09-01. This is the single current handoff. It records verified repository state and explicitly separates it from box/runtime facts that have not yet been reverified._

## Verdict

**HOLD / AUDIT ONLY until the pending futures fixes are reviewed and the VPS evidence pass is complete.**

No strategy parameters, inverse mechanics, risk limits, or fill mathematics were changed in this cleanup.

## Repository state

- Docs reconciliation base: `1b07b6a482423b57d966bcd53c1940bdbe3dac78` (`main` at the time this branch was created).
- The concurrent options session advanced `main` during the futures audit; its options commits were preserved and not rewritten.
- Current MNQ ORB Breakout inverse implementation passed the repo audit and is a **do-not-touch** area: isolated PaperBroker, fixed 1 contract, pessimistic same-bar handling, static mirrored bracket, IOC-limit entry.
- Forward evidence resolver remains conservative; no fill-math changes are authorized.

## Futures fixes prepared for review

All items below are **unmerged** unless a later handoff update explicitly says otherwise.

| PR | Fix | Verification state |
|---|---|---|
| #397 | Normal PaperBroker/replay config parity | targeted tests + full CI passed on its reviewed head |
| #399 | Promotion gate fails closed on blockers / instrument-scoped execution claims | targeted tests + full CI passed |
| #400 | Exact five forward-campaign populations, conflicting-duplicate integrity, collector-census ownership correction | targeted tests + full CI passed |
| #401 | `project_check daily` overall blockers / false-green correction | targeted tests + full CI passed |
| #406 | Durable final no-trade suppression evidence + Discord reason visibility + existing why-no-trade diagnostic update | targeted tests + full CI passed |
| #407 | Current `TRADE_INTENT -> CANCELLED` trade-chain semantics; exact identity preferred when available | targeted tests + full CI passed on latest exact-client-id head |
| #408 | Persist the existing deterministic `AFS-...` client order id across intent/trade/outcome/order-id evidence | targeted tests + full CI passed |

Older PRs #371/#374/#377/#383/#390 are not substitutes for these fixes. In particular, #374's Tradovate account pin remains conditional on box evidence showing multiple/ambiguous demo-account routing.

## Strategy evidence classifications

`docs/strategy-rules/Strategy_Inventory.md` is the strategy evidence source of truth. The September reconciliation supersedes the stale July optimistic rows with the already-proven closure results:

- ORB Reclaim current/first_cross — **BROKEN — negative evidence** (#368).
- ORB Reclaim V4-R — **WAIT** (#368 preregistered study).
- 4HR Re-Trigger MNQ — **BROKEN FOR CURRENT EXECUTABLE FORM** (#372).
- 4HR Re-Trigger MES — **BROKEN / WAIT** (#372).
- 12HR Miyagi — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#366).
- 60M 3-2-2 First Live — **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (#367).
- ORB Breakout inverted evidence lane — **PROMISING BUT UNPROVEN** (#364 historical study; runtime status must be verified on box).
- MES `strat_122` — **WAIT** (#373 executable-population audit).

Do not merge PR #369 as-is. Only its already-proven classification evidence was reconciled here; its stale runtime claims and large evidence payload were not imported.

## Monitoring architecture already verified in repo

- Most futures evidence/shadow collectors are embedded in the single `futures-bot` webhook process; they are not separate daemons.
- `scripts/evidence_lane_health.py` / `ops/evidence_lane_health.py` owns MNQ/MES event-driven lane health. Fresh feed + zero candidates can correctly be `QUIET`; candidate-file age alone must not label the lane DEAD.
- The forward campaign has exactly five configured populations: `vwap_hold/control`, `vwap_hold/modified`, `orb_reclaim/control`, `orb_reclaim/modified`, `vwap_rejection/observer`.
- Campaign enablement alone does not prove all five can produce evidence; entry-refresh, 5-minute feed, VWAP-early mode, and actual 5-minute webhook delivery are box-side prerequisites.
- Generic `feed_watchdog` and per-instrument `feed_gap_alarm` serve different purposes. Actual timer/cron installation must be verified on the box.

## Box/runtime facts still required

No current VPS shell evidence was available in this chat. Do not infer these from repository configuration alone. The next runtime pass is **READ ONLY** and must establish:

1. `futures-bot` service state, process cwd, deployed SHA/manifest, and release-integrity enforcement.
2. Nonsecret futures env pins, especially broker/demo mode, schedule mode, inverse/proof modes, fill model/tolerances, campaign prerequisites, 5-minute feed, and evidence-lane modes.
3. Fresh MNQ/MES authoritative 15-minute bars; 5-minute bars if enabled; generic webhook receipt freshness separately.
4. `scripts/evidence_lane_health.py --log-dir /root/afs-shared/logs --json` output.
5. Raw exact-five campaign counts/outcomes/days/generating SHAs and any duplicate-ID conflicts.
6. Actual systemd timers / cron for feed watchdog, per-instrument gap alarm, day-only exit, and ops automation evidence.
7. Current journal `TRADE_INTENT`, `TRADE`, `OUTCOME`, `ORDER_IDS`, `BLOCK_VISIBILITY` / suppression evidence and unresolved state.
8. Tradovate demo account list, resolved account, positions/orders, and whether account pinning is actually needed.

## Safe next step

Review the isolated futures PRs. After approved fixes land, perform one read-only VPS evidence pass against the then-current `main` SHA and update **this same handoff** with the resulting proof. Do not create another current-state handoff.
