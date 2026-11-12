# Recut run-sheet wording — v14 (DRAFT — NOT RUN)

Successor to the rollback sentences in the 2026-10-29 v13r5 sheet. This file does not authorize a host install or a Friday recut. v10_fri1030 is retired. The next runbook is `futures_release_recut_and_os_maintenance_RUNBOOK_DRAFT.v11.md`. QA review and Imani's GO are required before anything is sourced on the box.

## Rollback point

Primary rollback is the verified clean recut `/root/afs-releases/94a71bdbb975-recut-20261029-210507`. That directory is the live tree after AFS-0454. For the next cut it is OLD and ROLLBACK, and it must pass `rollback_ok` first.

The original `94a71bdbb975b53f21a81b7084482ea3c5270ceb` directory stays on disk as forensic evidence for the unknown pyc writer. Do not delete the stray `.pyc` in place. A restart onto that directory refuses integrity and is not a rollback target.

`c44d32bc` only after a read-only integrity check passes AND Imani's explicit word. It is not an automatic fallback. Never use `cddef4a4`.

A second clean copy of `94a71bd`, built by `step_cut` with no `step_switch`, is optional and only inside an approved window. It is not required while the 20261029-210507 tree still passes `rollback_ok`.

## On HOLD

Paste `ROLLBACK_VERIFIED` or `HOLD`. On HOLD with a crash-looping unit, do NOT restart again, and paste `systemctl show -p ActiveState,SubState,NRestarts,MainPID` plus `journalctl -q -u futures-bot -n 30`.

A proven false post-gate (empty journal read as an error) is `RESWITCH_FALSE_POSITIVE=1 step_reswitch_new`, then `lock_release_hold`, only with Imani's GO, and only while this run's deploy lock is still held.
