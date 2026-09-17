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

## Pre-release proof gates

All must be recorded from the actual box immediately before any restart/deploy:

1. exact deployed release SHA and `/proc/<pid>/cwd`;
2. `LIVE_TRADING_ENABLED=false`;
3. effective broker mode and every additive demo/paper route explicitly identified;
4. current open-position state captured before restart;
5. active campaign/epoch files captured and not reset;
6. current `.env` hash or equivalent byte-level baseline captured without exposing secrets;
7. target release SHA pinned before deployment;
8. target diff reviewed against this allowlist;
9. exact-head CI green for the target release;
10. no unresolved merge conflict with #623/tranche-2 work or another active runtime PR.

Any mismatch is **HOLD**.

## Post-release proof gates

Before calling the runtime release complete:

1. process points at the intended release SHA;
2. `LIVE_TRADING_ENABLED=false` reverified from the running process environment;
3. broker/auth state matches the pre-authorized paper/demo posture;
4. pre-existing paper position/campaign state preserved or explicitly reconciled;
5. status/health endpoints respond;
6. one ordinary decision bar still journals normally;
7. top-level execution limits are unchanged;
8. #612 behavior is proven with an offline/focused test artifact and, when naturally encountered, a real blocked-capacity row shows:
   - final decision `BLOCKED_MAX_TRADES` or `BLOCKED_LOSS_LOCKOUT`,
   - `observed_decision` preserved,
   - candidate/setup evidence preserved when present,
   - no risk/broker/fill path reached;
9. observation-only M2K/MGC/MCL/MBT routing remains outside DecisionEngine/RiskEngine/broker;
10. Discord/monitoring output remains clear and no new runtime errors appear.

## Stop conditions

Stop and roll back/hold if any of the following occurs:

- live flag or broker route differs from the approved pre-state;
- active campaign epoch/state changes unexpectedly;
- a pre-existing position cannot be reconciled byte-for-byte or semantically;
- #612 blocked-capacity path can reach risk or broker;
- the release contains unrelated runtime/config/risk/strategy changes not explicitly reviewed;
- target SHA changes after approval.

## Release ruling

**No broad `main` deployment by assumption.** The next sanctioned futures runtime release should be reviewed as an explicit allowlisted delta, with #612 as the known required runtime repair and C8 included only if separately merged and proven.