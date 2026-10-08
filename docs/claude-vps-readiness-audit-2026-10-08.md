# Claude VPS Readiness Audit — 2026-10-08 (READ-ONLY, PENDING GROK)

**Label:** `CLAUDE SOURCE/CI AUDIT — PENDING GROK REVIEW`. Not a Grok review. Not deployment approval.
**Collected (UTC):** 2026-10-08T13:40Z–13:50Z.
**Mode:** read-only. No build, promote, restart, env edit, collector pin, journal access, or broker action.
**VPS access used:** none. This cloud session's network policy blocks outbound TCP/22 (connect to the VPS host timed out). No SSH session was opened and no command ran on the box. Every box gate below is either **Cursor-reported** (#1197, 12:33:42Z) or **UNVERIFIED**.

## Verdict

**HOLD.** Source and CI on `main` show **no actual safety failure**. What blocks is **missing root read-only box evidence** plus the unset candidate. One correction to the #1197 operator command block (B3 paths) is required before it is run.

## 1. Current state (verified from GitHub, this session)

| Item | Value | Evidence |
|---|---|---|
| `main` | `307fe56771031b44eeb8d0235224cf010616ce4a` (#1198) | `git fetch origin main; git rev-parse origin/main` |
| `main` CI | **success**, run 37784635423 (13:28:37Z–13:32:46Z) on exact `307fe56` | GitHub Actions `CI`, push to `main` |
| Prior merges, CI | #1192 `393ad72`, #1191 `41c6888`, #1196 `a602501c`, #1186 `0d02a8bd`, #1194 `168e7420`, #1190 `e3c84a78`, #1189 `064ee674`: all push-CI **success** | same |
| Deployed (Cursor-reported, not re-verified) | `c44d32bc4961e56fae5c5f88a976eb6783341638` | #1197 allowlist `status`/`release`/`deploy-state` |
| `c44d32b` ancestor of `main` | yes; 120 commits on `c44d32b..307fe56` | `git merge-base --is-ancestor`, `git log` |
| Candidate | **UNSET** | plan decision record |

### Changes since Grok AFS-0181

AFS-0181 PASS is scoped to `393ad72` (#1203 body, operator-supplied). `393ad72..307fe56` = **#1198 only** (single merge; parent `393ad72`):

- `options_evidence/options_122_adapter.py` (+518), `tests/test_options_122_canonical_adapter.py` (+354).
- Byte-identity: both blobs **and the whole tree** at `307fe56` equal AFS-0183's reviewed head `25dec22371fa686acb5a794c901cf2fc7c028aed` (`git diff 25dec22 307fe56` empty). AFS-0183 therefore covers the merged content exactly.
- Runtime reach: **none**. No module outside `tests/` and `options_evidence/` imports `options_evidence`.

## 2. Deployed → main delta still requiring review (`c44d32b..307fe56`)

Runtime-path files (34 files, +4161/−189). After `064ee67` (the last delta the plan reviewed), runtime-reaching changes are only:

| PR | Files | Reaches |
|---|---|---|
| #1186 | `alert_ranker/options_122_prospective.py`, `scripts/options_122_prospective_collect.py` | `options-122-prospective` collector (v0.1 → v0.2 producer stamp, duplicate-ARMED rejection). **Rides a futures promote if the collector is LIVE-TREE.** This is why B8 is decisive. |
| #1194, #1196 | `scripts/atomic_release.sh` | Release tooling: watcher pre-mutation guard, history-file refusal. |

No change after `064ee67` in `strategy/`, `risk/`, `risk_rules.yaml`, `execution/`, `webhook/`, `context/`, `journal/`, `ops/afs_watcher/`. The `c44d32b..064ee67` futures trading-path review (U7/U8 broker, #1137/#1143 webhook, watcher label) stands as recorded in the plan but must be re-attested against whichever SHA is nominated.

**Dependency lock** `c44d32b` → `307fe56`: 14 added, 0 removed, 0 version changes (`cachetools, cffi, charset-normalizer, cryptography, grpcio, jmespath, packaging, paho-mqtt, protobuf, pycparser, requests, six, urllib3, webull-openapi-python-sdk`). Matches #1197. Fresh Python 3.13.16 venv from the `307fe56` lock: `pip install --no-deps` + `pip check` clean. The live venv freeze remains unread (B5).

## 3. Gate matrix

| Gate | Status | Basis |
|---|---|---|
| B1 posture pins | Cursor-reported PASS (process env, 12:33Z); `.env` UNVERIFIED | #1197 public live-preflight |
| B2 fingerprint / identity | **UNVERIFIED** (not FAIL): allowlist check ran without pin | #1197 |
| B3 rollback readiness | **UNVERIFIED**. Source: rollback preflight requires `watcher.py, watcher_memory_guard.py, run_ro.sh, supervisor.sh, bootstrap_tmp_state.sh` in the previous release; **all present** in `489b55b` and `c44d32b` trees, so the #1194 guard does not structurally block rollback to either | `git cat-file -e` |
| B4 Python 3.13 | **UNVERIFIED** on box | — |
| B5 venv drift | **UNVERIFIED** live; source delta above | — |
| B6 contract-identity guard | Cursor-reported absent at `c44d32b`; `.env` UNVERIFIED | #1197 |
| B7 demo / cap / flat | Cursor-reported PASS at 12:33Z; `last_preflight_at` 00:37Z. **Stale: fresh capture required at GO** | #1197 |
| B8 collector pin `db9bc7e2` | **UNVERIFIED** and decisive (see #1186 above) | — |
| B9 lock / session | Lock absent at 12:33Z (Cursor). Stale for GO | #1197 |
| B10 watcher dir + `release_history.txt` | **UNVERIFIED** on box. Source guards (#1194/#1196) merged, CI green, local tests pass | below |
| Options journal ARMED dup/OOO | **UNVERIFIED** (never scanned) | — |

**Local tests at `307fe56`** (Python 3.13.16, lock venv, 2026-10-08T13:46:42Z):
`pytest tests/test_atomic_release_script.py tests/test_options_122_canonical_adapter.py tests/test_options_122_prospective_collect.py` → **87 passed**.

## 4. Correction to the #1197 root command block (must fix before running)

The B3 section reads `/root/afs-releases/current` and `/root/afs-releases/current.previous`. `scripts/atomic_release.sh` uses neither:

- live link: `CURRENT=/root/autonomous-futures-system` (symlink);
- previous pointer: **`/root/afs-shared/current.previous`, a plain text file** read with `cat` (rollback line `previous=$(cat '$SHARED/current.previous')`).

As written, the block would print empty values and look like a missing rollback target: a **false B3 FAIL**. Replace with:

```bash
readlink -f /root/autonomous-futures-system
cat /root/afs-shared/current.previous
PREV=$(cat /root/afs-shared/current.previous 2>/dev/null || true); echo "previous=$PREV"
test -d "$PREV" && test -f "$PREV/release_manifest.json" && echo manifest=yes
test -d "$PREV/.venv" && echo venv=yes
for f in watcher.py watcher_memory_guard.py run_ro.sh supervisor.sh bootstrap_tmp_state.sh; do test -f "$PREV/ops/afs_watcher/$f" && echo "watcher_src_ok $f" || echo "watcher_src_MISSING $f"; done
```

The rest of the #1197 block is consistent with source (`deploy.lock` is `$SHARED/deploy.lock`; history is `$SHARED/release_history.txt`).

## 5. Minimal additional access needed

One root read-only run of the #1197 block with the §4 correction, by the operator from the proven root path (not the restricted Cloud Agent/audit key), with exact command, full redacted output, exit code and UTC timestamp per gate, and B7/B9 captured in the same session within ~120 s. Nothing else is required to close B2–B5, B8, B10 and the journal scan.

## 6. Smallest safe next step

1. Grok reviews this document and #1197, including the §4 path correction.
2. Operator runs the corrected root read-only block once and pastes redacted output.
3. Only if every gate closes: nominate an exact SHA → Grok exact-SHA review → separate GOs for build/verify and promote.

No proof, no run.
