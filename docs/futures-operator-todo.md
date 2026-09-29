# Futures Operator TODO

> **Purpose:** durable operator action tracker for futures infrastructure/safety work. This file is **not** strategy-status authority and must not override `docs/strategy-rules/Strategy_Inventory.md`, the research trial ledger, or verified VPS/runtime evidence.
>
> **Last repo reconciliation:** `main` `715e03956f402fd553107a84125537768841d97c` after merged PR #1068 on 2026-09-29.
>
> Core rule: **No proof, no run.**

## Completed — do not reopen by default

- [x] Promotion hard-blocker / CLI exit semantics remain fail-closed (#893).
- [x] Promotion quantity proof requires per-entry-attempt observed quantities and exact count/claim reconciliation (#1070).
- [x] Promotion cap parser rejects fractional/invalid quantities instead of truncating them (#1072).
- [x] Six-market daily evidence reporting is on `main` for MNQ, MES, M2K, MBT, MCL, and MGC (#1068).
- [x] Reconciled repo-side futures defect list has no remaining open defect from the audited set.
- [x] No live-trading authority was created by these repo changes.

## Next operator phase — VPS / access / runtime truth

These are ordered. Do not skip ahead.

- [ ] **Read-only VPS reconciliation.** Verify exact deployed futures SHA, release manifest/fingerprint/risk hash, service PID/cwd/restart count, release integrity, deploy-lock state, current non-secret runtime posture, account-pin status, broker positions/orders, reporter pins, and current human/agent access paths.
- [ ] **Map current access before changing it.** Record operator login path, agent accounts/restrictions, sudo/forced-command boundaries, private-network/Tailscale state if any, deploy wrappers, and provider-console break-glass path.
- [ ] **Build replacement operator access.** Target one simple MacBook path and a phone-usable path, key-only auth, provider console retained. Do not remove old access yet.
- [ ] **Keep agents restricted.** Prefer allowlisted/read-only audit commands; no unrestricted root/sudo simply for convenience.
- [ ] **Wrap existing sanctioned release tooling instead of creating a competing deploy system.** Any operator wrapper must preserve exact-SHA, deploy-lock, release-integrity, posture, account-pin, rollback, and no-proof-no-run gates.
- [ ] **Prove replacement access before lockdown.** MacBook login, phone login, provider-console recovery, agent restriction, read-only status/audit, candidate build/verify, and rollback proof must all succeed.
- [ ] **Repin the six-market reporting surfaces safely.** The #1068 repo merge does not update the VPS pinned reporter by itself. Follow the documented immutable pin/curated-overlay procedure, run a pre-flip read-only smoke, then verify the reporting change without restarting the trading service.
- [ ] **Reconcile source vs runtime.** If a futures release update is actually required after the read-only audit, freeze an exact reviewed SHA and use a separate operator-approved controlled release window. No strategy/risk/broker-rule changes ride along implicitly.
- [ ] **Lock down old access only after replacement proof.** Then remove or restrict obsolete broad access paths, keeping documented break-glass recovery.

## Separate / not blocking the VPS phase

- [ ] **PR #1037 public-repo/governance audit:** keep separate from futures execution safety. Secret-discovery was clean; remaining questions concern public-history operational exposure/governance.
- [ ] **PR #994 MNQ ORB Stage A research:** WAIT / research only. Stage B was not earned. Do not resume without an explicit research decision.
- [ ] Continue normal paper/shadow/guarded-DEMO evidence collection under existing frozen contracts. Do not interpret more data as automatic promotion.

## Definition of done for this phase

This phase is complete only when:

- current VPS identity and runtime posture are independently verified;
- replacement human access works from MacBook and phone;
- agent access is proven constrained;
- reporter pin/state is reconciled with #1068 where authorized;
- rollback and break-glass recovery are proven;
- obsolete access is locked down **after** the replacement works;
- docs/handoff are updated with exact verified runtime facts;
- no accidental live execution path or unreviewed broker/risk change was introduced.

Until then: **repo source is ahead of runtime proof.**
