# Post-Freeze Futures Runtime Release Plan — 2026-09-17

**Status:** AUDIT / RELEASE PLAN ONLY. **NO DEPLOYMENT AUTHORIZED.** The standing no-release/no-restart restriction remains in force until its operator-approved end or explicit waiver.

## Purpose

Bound the next futures runtime release to the smallest proven delta. The deployed box was last verified at `8fd8b215063c83428fa15028ae76f0e7f6d25a8e`. At this audit, `main` is `4d75226f1ca40591ebd0756950dffed941c68ed6` and is 22 commits ahead.

A direct compare shows that almost all of that delta is research/docs/reporting. The futures runtime changes relevant to the next sanctioned release are narrow.

## Runtime allowlist

### Required runtime repair already merged: #612

Files:

- `webhook/runner.py`
- `strategy/signal_engine.py`

Purpose: a spent shared-journal daily execution budget must block **execution**, not erase later setup observation. The limits themselves remain unchanged. The blocked path must return before RiskEngine/broker/proof-paper opening logic.

### Offline-only change — do not treat as a VPS activation requirement

- `replay/replay_engine.py` (#621)

Purpose: preserve shadow-history continuity across replay day files. This is an offline replay/parity repair; it does not need runtime activation merely because it is merged.

### Conditional addition

If C8 is merged before the sanctioned release, include only the exact C8 journal-field file(s) after separate review. Do not broaden the release merely because `main` contains more research tooling.

## Sanctioned release-path constraint

`scripts/atomic_release.sh` builds and promotes an **exact commit SHA** into an immutable release directory. Its `verify` action starts an isolated candidate service with `BROKER=paper`, checks health/status and release integrity, and does not make that candidate the live service.

Promotion has two postures:

1. **full reset baseline** (`always_on_shadow` / static exit posture), where behavior-changing releases may promote after the normal proof gates;
2. **current operational posture fast-path**, allowed only when `ops/behavior_neutral_gate.py` proves every changed file is operational/observability-only.

#612 is **not behavior-neutral** under that gate. Both `webhook/runner.py` and `strategy/signal_engine.py` are outside every safe-file/safe-directory allowlist and therefore default-deny. Consequently:

> **The #612 runtime repair must use the full baseline promotion path. It may not use the behavior-neutral operational fast-path.**

Do not weaken the behavior-neutral allowlist just to ship #612.

### Last verified posture already matches the baseline

The authoritative handoff records the box, at its last 2026-09-16 verification, as:

- `LIVE_TRADING_ENABLED=false`
- `SCHEDULE_MODE=always_on_shadow`
- `EXIT_MODE=static`
- zero external-broker orders

Therefore the repository does **not** currently prove that an additional posture reset will be needed. If the actual box still matches those values after the freeze, it is already on the full baseline posture required for #612 promotion.

Repository state is not box proof. Re-check `/proc/<pid>/cwd`, the running environment, campaign state, and broker posture immediately before release. If the box has drifted to the operational posture, #612 must wait for an explicit return to the baseline posture; the fast-path remains forbidden.

## Pre-release proof gates

All must be recorded from the actual box immediately before any restart/deploy:

1. exact deployed release SHA and `/proc/<pid>/cwd`;
2. `LIVE_TRADING_ENABLED=false`;
3. `SCHEDULE_MODE`, `EXIT_MODE`, and their proof pins match the baseline required by the full promotion path;
4. effective broker mode and every additive demo/paper route explicitly identified;
5. current open-position state captured before restart;
6. active campaign/epoch files captured and not reset;
7. current `.env` hash or equivalent byte-level baseline captured without exposing secrets;
8. target release SHA pinned before deployment;
9. target diff reviewed against this allowlist;
10. exact-head CI green for the target release;
11. no unresolved merge conflict with #623/tranche-2 work or another active runtime PR;
12. any actual posture/epoch change required to reach baseline explicitly accepted before promotion.

Any mismatch is **HOLD**.

## Candidate verification before promotion

Use the sanctioned immutable-release verifier against the exact target SHA before any live-service promotion. Candidate verification must demonstrate at minimum:

- candidate starts successfully under forced `BROKER=paper`;
- `/health` passes;
- Tradovate-reliability/status endpoint is readable without authorizing a live route;
- release integrity/fingerprint passes;
- focused #612/C8 tests and exact-head CI correspond to that same SHA.

A successful candidate verification is necessary but **does not itself authorize promotion**.

## Post-release proof gates

Before calling the runtime release complete:

1. process points at the intended immutable release SHA;
2. `/proc/<pid>/cwd` resolves to the same release directory selected by the current symlink;
3. `LIVE_TRADING_ENABLED=false` reverified from the running process environment;
4. `SCHEDULE_MODE` / `EXIT_MODE` remain at the approved baseline values unless a separately authorized later activation changes them;
5. broker/auth state matches the pre-authorized paper/demo posture;
6. pre-existing paper position/campaign state is preserved or explicitly reconciled;
7. status/health endpoints respond;
8. one ordinary decision bar still journals normally;
9. top-level execution limits are unchanged;
10. #612 behavior is proven with an offline/focused test artifact and, when naturally encountered, a real blocked-capacity row shows:
   - final decision `BLOCKED_MAX_TRADES` or `BLOCKED_LOSS_LOCKOUT`,
   - `observed_decision` preserved,
   - candidate/setup evidence preserved when present,
   - no risk/broker/fill path reached;
11. observation-only M2K/MGC/MCL/MBT routing remains outside DecisionEngine/RiskEngine/broker;
12. watcher state is re-armed/reconciled for the sanctioned restart as required by the release procedure;
13. Discord/monitoring output remains clear and no new runtime errors appear.

## Stop conditions

Stop and roll back/hold if any of the following occurs:

- live flag or broker route differs from the approved pre-state;
- actual box posture does not satisfy the full baseline gate and no explicit reset is authorized;
- active campaign epoch/state changes unexpectedly;
- a pre-existing position cannot be reconciled byte-for-byte or semantically;
- #612 blocked-capacity path can reach risk or broker;
- the release contains unrelated runtime/config/risk/strategy changes not explicitly reviewed;
- target SHA changes after approval;
- promotion attempts to bypass the behavior-neutral gate by expanding its allowlist for this release.

## Release ruling

**No broad `main` deployment by assumption.** The next sanctioned futures runtime release should be reviewed as an exact-SHA release with #612 as the known required runtime repair and C8 included only if separately merged and proven. #612 requires the full baseline promotion path; based on the last verified handoff, the box may already be in that posture, but that must be re-proven from the box after the freeze.