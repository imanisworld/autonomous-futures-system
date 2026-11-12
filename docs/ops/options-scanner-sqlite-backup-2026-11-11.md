# Options scanner SQLite backup verification and recovery HOLD — 2026-11-11

**Scope:** Dated, read-only storage/recovery audit of the futures/options VPS and a separately authorized **off-server copy** onto the operator's FileVault-encrypted Mac. This is **not** a backup retention approval, application restore proof, Hetzner snapshot attestation, trading authorization, or deploy instruction.

## Verified evidence

| Item | Observed / checked | Interpretation |
|---|---|---|
| VPS root filesystem | `df`: **96% use**, about **1.55 GiB available** at audit | Storage exhaustion risk; `df` capacity percentages and raw-used bytes need not have identical denominators |
| VPS live scanner DB | `options_scanner.sqlite` about **1.93 GiB**, zero free-list pages in a read-only SQLite header check | **Preserve**; VACUUM is not a proven space-saving action |
| Same-disk snapshots | `options_scanner.2026-11-08.sqlite` 1,906,794,496 bytes; `2026-11-09` 1,952,137,216; `2026-11-10` 1,990,782,976; `2026-11-11` 2,069,614,592 | Total about **7.38 GiB**; three older copies together about **5.45 GiB**, conditional opportunity only |
| Oldest snapshot provenance | Filename says November 8; filesystem modification time is **November 6, 2026** | Do not assume filename dates establish creation or retention ordering |
| Raw options-122 evidence | About **7.31 GiB** of `.jsonl` source-trade records, updated as recently as November 11 | **Preserve**; compressibility samples are not retention approval |
| Release directories | 21 directories, about **3.32 GiB** logical file content | Multiple releases are still referenced by service drop-ins or rollback paths; **do not remove** |
| Mac copy | FileVault enabled; `~/AFS-Backups/options_scanner.2026-11-11.sqlite`; exact size **2,069,614,592 bytes**; owner-only `0600` | One independent physical-machine copy; Mac may still have its own loss/failure risk |
| Copy identity | Source/destination SHA-256 compared and **matched**; source size and mtime rechecked unchanged before final rename | Byte-for-byte copy verified at transfer time; full digest not published in this repository |
| Database readability | SQLite on the Mac opened the copy **read-only/immutable**, found **9 tables**; `PRAGMA quick_check(1)` and full `PRAGMA integrity_check` each returned exactly `ok` | **SQLite file integrity verified**, not a complete application recovery |
| Time Machine | `tmutil isexcluded` reported the Mac file **Included** | **Backup completion is NOT verified** |
| Provider coverage | No configured Hetzner Cloud API client/credentials established in the audited access paths; VPS metadata did not establish backup entitlement | **Hetzner backups/snapshots UNKNOWN** |
| Snapshot verification on VPS | Read-only full/quick integrity checks stopped at bounded time limits; no corruption reported | Treat VPS run as **INCOMPLETE**, not PASS or CORRUPT |

The audit was read-only on the VPS. Copy creation and SQLite checks occurred on the encrypted Mac. No source DB, snapshot, raw trade evidence, service, timer, broker, webhook, release, or server configuration was changed.

## Recovery prerequisites (not yet satisfied)

1. **Provider backup proof:** Obtain a read-only Hetzner Console/API view identifying whether **this specific server** has Backups enabled and/or independently stored Snapshots, the latest successful restore point, retention, and an independently protected destination. An unavailable CLI or missing metadata **does not mean backups are disabled**.
2. **Independent copy lifecycle:** Confirm the Mac's Time Machine job actually created a recoverable backup of this file. Do not equate **Included** with **Backed Up**. Preserve the checksum-verified Mac copy as an independent file while testing.
3. **Application-level recovery rehearsal:** In an isolated non-production environment, open/recover the copied database using the expected scanner schema and consumers, with **no** production database writes, alert sends, scan jobs, or broker order paths. Record pass/fail and version compatibility. SQLite integrity alone does not prove the application can resume.
4. **Evidence and retention policy:** Establish the required number/age of daily restore points and the retention of options-122 SIP trade windows, journals, research evidence, production backups, and rollback releases. Do not silently assume the newest snapshot supersedes older points in time.
5. **Deletion-specific approval:** Only after steps 1–4, prepare an explicit path allowlist, source/destination identities/checksums, last-resort rollback plan, estimated reclaimed bytes and post-action disk check. Require a **new explicit operator GO** for that exact deletion; do not authorize broad `find -delete`, `rm`, or database `VACUUM` from this document.

## Current disposition

- **VERIFIED:** Encrypted Mac off-server copy; exact byte count and SHA-256 match; full SQLite integrity PASS; original VPS snapshot unchanged.
- **UNVERIFIED:** Hetzner-backed restore point, Time Machine completed backup, full application-level recovery, older-snapshot redundancy, retention signoff.
- **HOLD / DO NOT RUN:** Delete, move, or modify any VPS snapshot or live database; prune raw evidence or release trees; install agents; restart services; deploy code; modify trading/broker settings.
- **NEXT:** Provider coverage and retention decision, isolated restore test, then separately approved targeted reclaim if justified.

Related live operation queue: [futures operator TODO](../futures-operator-todo.md). Short agent checkpoint: [agent work state](../agent-work-state.md). This record is a dated point-in-time verification; obtain fresh `df`, file identity, and environment evidence before acting.
