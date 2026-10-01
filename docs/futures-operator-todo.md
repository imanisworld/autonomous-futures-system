# Futures Operator TODO

> **Purpose:** durable operator action tracker for futures infrastructure/safety work. This file is **not** strategy-status authority and must not override `docs/strategy-rules/Strategy_Inventory.md`, the research trial ledger, or verified VPS/runtime evidence.
>
> **Last repo reconciliation:** `main` `3737d532919859960bdefe8b11a611da785327ae` after PR #1080. Runtime facts are in `docs/futures-runtime-reconciliation-2026-09-30.md`.
>
> Core rule: **No proof, no run.**


## Immediate state — no action required now

Latest read-only health pass reported **PASS — KEEP COLLECTING / NO CHANGES**:

- futures-bot remains on pinned release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, DEMO, live trading disabled;
- release integrity passed; service remained one PID with zero restarts;
- Tradovate demo account routing remained pinned and healthy;
- journal and broker state agreed flat with zero execution attempts/fills for the current journal day;
- forward evidence is still writing for `vwap_hold`; `orb_reclaim` is stale and `vwap_rejection` is quiet. Those stale/quiet arms are evidence-quality facts, not a reason to tune or restart the current collector;
- GitHub `main` being ahead of the pinned release is reportable source/runtime drift, **not** an instruction to deploy.

Do not deploy, restart, retune, harden SSH, or reopen research merely because paper results are losing. The next work remains operator-window work below.

## Completed — do not reopen by default

- [x] Promotion hard-blocker / CLI exit semantics remain fail-closed (#893).
- [x] Promotion quantity proof requires per-entry-attempt observed quantities and exact count/claim reconciliation (#1070).
- [x] Promotion cap parser rejects fractional/invalid quantities instead of truncating them (#1072).
- [x] Six-market daily evidence reporting is on `main` for MNQ, MES, M2K, MBT, MCL, and MGC (#1068).
- [x] Shadow daily P&L runtime copy now uses the exact #1068 file. The 2026-09-29 scratch comparison kept the same totals and added the four zero-evidence markets. One Discord smoke showed all six markets. Futures-bot was not restarted.
- [x] Paper-collection runtime uses derived pin `b60931a6a9f8-reporter-1068-sixmarket-backport`. It shows MNQ, MES, M2K, MBT, MCL, and MGC, including zeros, on the #899 reporter. The full #1068 reporter file was not installed. Frozen-input data matched, one Discord smoke showed the six markets, and futures-bot was not restarted.
- [x] Reconciled repo-side futures defect list has no remaining open defect from the audited set.
- [x] No live-trading authority was created by these repo changes.

## Next operator phase — VPS / access / runtime truth

These are ordered. Do not skip ahead.

- [x] **Read-only runtime identity, 2026-09-30.** Futures-bot PID `791194`, `NRestarts=0`, release `75f10e4540aa1f25b51b77c1ec2a2da40381a188`, DEMO, live trading disabled. Reporter pins and the access map are in `docs/futures-runtime-reconciliation-2026-09-30.md`. A later read-only health pass re-ran release integrity successfully and reconciled journal/broker state flat.
- [x] **Current access map, read only.** Re-read 2026-09-30: public SSH port 22, `PasswordAuthentication yes`, `PermitRootLogin prohibit-password`, Tailscale absent, non-root login accounts password-locked, Grok audit keys use `command=` and `restrict`, `claude-audit` has an interactive shell and no sudoers entry. MacBook root-key login stays the proven current path from the access audit. Current access stays in place.
- [ ] **Phone SSH login.** Requires the operator. Not proven.
- [ ] **Hetzner/provider console recovery.** Requires the operator. Not proven.
- [ ] **Ready to test, not done tonight:** candidate build/verify, and a rollback drill. The deploy-lock script is in the live release. The recorded rollback target directory `/root/afs-releases/41ae1881655c-20260924-124607` is present. Do not run those tests without an operator window.
- [ ] **Not authorized yet:** `PasswordAuthentication no`, root-key cleanup, converting `claude-audit` to a forced command, public SSH restriction, or old-access removal. Do not harden SSH until phone login and provider-console recovery are proven.
- [ ] **Gate-condition #1068 runtime is deferred until the next sanctioned futures release.** The cron module runs from the immutable live release. Adopting the #1068 file would edit that tree or change the scheduler. Paper-collection and shadow daily are already installed. This is not an urgent defect.
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

Until then: the access phase is not complete. Do not treat a mapped current path as permission to remove it.
