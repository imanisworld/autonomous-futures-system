# Cursor VPS Readiness Audit — 2026-10-08

**Label:** `CURSOR VPS AUDIT — PENDING GROK REVIEW`  
**Mode:** READ-ONLY. No build, promote, restart, env edit, collector pin, broker action, journal rewrite, or evidence-window reset.  
**Access used:** Injected Cloud Agent `VPS_*` → **Grok audit allowlist** force-command (uid=1000 allowlist account; name redacted). **Not** unrestricted root.  
**Allowlisted verbs:** `help`, `identity`, `status`, `release`, `runtime-pins`, `health`, `broker`, `evidence`, `journal-today`, `demo-evidence`, `deploy-state`, `campaign`, `list-logs`, `service-logs`.  
**Public surfaces:** `https://app.afsvp.com/health`, `/status/live-preflight`, `/status/diagnostics`, `/status/broker-account`.  
**Collect timestamp (UTC):** `2026-10-08T12:33:42Z` (allowlist bundle + public preflight).  
**This comment/doc is not a Grok review and does not claim Grok approval.**

---

## Identities

| Role | SHA / value | Evidence |
|---|---|---|
| GitHub `main` tip | `a602501cb0e665952da2101e9c4f73a3e68aea90` | `git fetch origin main` @ audit time |
| Deployed futures release | `c44d32bc4961e56fae5c5f88a976eb6783341638` | allowlist `status` / `release` / `deploy-state`; public drift guard |
| Deployed ≠ main | **YES** — 201 commits `c44d32b..main` | `git rev-list --count` |
| Next deploy candidate | **UNSET** | plan + this audit; no nomination |
| #1186 | **MERGED** `0d02a8bd019b6a164e14ec2965b7e7ce95eee25e` @ 2026-10-08T12:06:31Z | `gh pr view` |
| #1190 | **MERGED** `e3c84a78ad89a0a014b157915788f6cd0dd8bbed` @ 2026-10-08T11:55:43Z | `gh pr view` |
| #1194 | **MERGED** `168e74207f508ba8bba3ab391d43620d9666947e` @ 2026-10-08T11:56:23Z | `gh pr view` |
| #1196 | **MERGED** `a602501cb0e665952da2101e9c4f73a3e68aea90` @ 2026-10-08T12:22:10Z | `gh pr view` (main tip) |

**Do not assume deployed code equals current main.** Deployed remains `c44d32b` (ActiveEnter 2026-10-04T21:29:02Z, MainPID=1851835, NRestarts=0).

---

## Evidence matrix

| Gate | Verdict | Evidence | Remaining blocker |
|---|---|---|---|
| **B1 Runtime posture** | **PASS** (Path-1 in process env) | Public `/status/live-preflight` @ 12:33:42Z: `SCHEDULE_MODE=always_on_shadow` + pin; `HTF_DIRECTION_MODE=off` + pin; `EXIT_MODE=static` + pin; `unpinned_runtime_overrides=[]`. Allowlist `runtime-pins`: `SCHEDULE_MODE=always_on_shadow`. | Optional: root `.env` six-line confirm if process-env authority disputed. **Posture reset not indicated.** |
| **B2 Release identity** | **INCONCLUSIVE → UNVERIFIED pin** | `release`: path `…/c44d32b…`, manifest sha256 `3f9872e78c614ed211405b7b3e71963d7abff283d5b7a31be06512241a663e7f`, **1633/1633 tree OK**, then **UNPINNED** (`EXPECTED_RELEASE_FINGERPRINT` not set **in that check’s process env**). Drift guard: commit matches `c44d32b`, `missing_pins=[]`. | Root read of durable `.env` fingerprint + bare vs injected `ops.release_integrity`. **Do not rewrite pin until proven.** |
| **B3 Rollback** | **UNVERIFIED** | `deploy-state` lists prior dir `489b55b91b6303c195c8e84bfcbf05ef32d1ab04` under `recent_releases`. Historical docs claim it as `current.previous`. | Root: `current.previous`, prior manifest/venv/`release-complete`, integrity. Do **not** assume `489b55b` is rollback-ready. No rehearsal. |
| **B4 Python** | **UNVERIFIED** | Source requires Python 3.13 (`requirements.lock`, `atomic_release.sh`). Allowlist exposes no interpreter path/version. | Root: `python3.13 --version` + deployed venv `python --version`. |
| **B5 Dependencies** | **UNVERIFIED** (live) / **informational** (source) | Candidate UNSET. Provisional lock diff `c44d32b` (34) → `main` (48): **14 added**, 0 removed, 0 version-changed (webull SDK + crypto/grpc stack). Live `pip freeze` unread. | After nomination: freeze vs **that** SHA’s lock. Label any main-provisional compare **non-authoritative**. |
| **B6 Contract identity** | **PASS** (SHA-scoped off/absent) | `CONTRACT_IDENTITY_GUARD_ENFORCED` absent from deployed `c44d32b` proof-critical list and live-preflight blob. Policy: keep OFF until Pine/rollover proof. | Re-prove on any post-U8 candidate; root confirm effective + expected-proof pin before enablement. |
| **B7 DEMO safety** | **PASS** (fresh broker/flat; preflight age note) | `TRADOVATE_ENV=demo`, `LIVE_TRADING_ENABLED=false`, `PAPER_MODE=false` (ok with demo+live false), `MAX_CONTRACTS_HARD_CAP=1`, wide-stop route `tradovate_demo`, demo exec enabled, broker HEALTHY, `broker_flat=true`, `preflight_no_open_positions=True`, `preflight_no_working_orders=True`, `preflight_armed=False`. | `last_preflight_at=2026-10-08T00:37:01Z` (~12h stale). **Fresh preflight query required at any GO.** `WIDE_STOP_DEMO_SESSIONS` proof pin unset (ok=true / inactive). |
| **B8 Options collector isolation** | **BLOCKED / UNVERIFIED pin** | Source unit defaults to live symlink + `.venv`. Historical handoff: `10-release.conf` pin to `db9bc7e2…` (2026-09-22). Allowlist sees journal **name** `options_122_prospective.jsonl` only; cannot `systemctl cat/show` collector. #1186 merged (v0.2 producer identity) — journal boundary risk if futures promote rides collector. | Root classify PINNED / LIVE-TREE / MIXED; preserve pin if PINNED. Separate GO for any migration. **No repin performed.** |
| **Watcher** | **UNVERIFIED** | Required root cmds (`readlink -f /root/afs-shared`, `systemctl show/cat afs-watcher`, `ls afs_watcher_src`) **not on allowlist**. | Operator/root read-only B10 preflight from plan. |
| **Release history** | **UNVERIFIED** | `test -f /root/afs-shared/release_history.txt` not executable via allowlist. #1196 on main adds source pre-activation history refuse — **not deployed**. | Root regular-file check before any promote. |
| **Journal (options ARMED)** | **UNVERIFIED → HOLD** | Cannot hash/scan journal content via allowlist. Duplicate/out-of-order ARMED scan not run. | Root read-only hash + ARMED ordering scan. Any duplicate/OOO = HOLD. No rewrite. |
| **Journal (futures write)** | **PASS** (continuity only) | `journal_2026-10-08.jsonl` bytes=1158330 mtime=12:30:08Z lines=321; bot active since 2026-10-04. | Not a promote authorization. |
| **Rollback readiness** | **UNVERIFIED** | See B3; watcher recovery paths unread. | Root compatibility + separate rehearsal GO. |
| **Evidence continuity** | **PARTIAL** | Futures journals writing; epochs present in docs/runtime pins (wide-stop epoch `2026-09-09T04:21:04Z`; options `122-IEX-E1` / V1 cohorts in handoff). Collector effective version unread. | Cutover boundary below. Do not reset windows. |
| **Deploy lock** | **PASS** | `deploy_lock=absent` | — |
| **Disk capacity** | **UNVERIFIED** | Not in allowlist | Root `df` on release/shared volumes. |

### B5 provisional lock delta (`c44d32b` → `main` `a602501c`) — non-authoritative

Added only: `webull-openapi-python-sdk==3.0.1`, `cryptography==42.0.8`, `grpcio==1.69.0`, `protobuf==5.29.6`, `requests==2.34.2`, `urllib3==2.8.0`, `paho-mqtt==1.6.1`, `cachetools==5.5.2`, `jmespath==0.10.0`, `packaging==26.3`, `cffi==2.1.1`, `pycparser==3.0`, `charset-normalizer==3.5.2`, `six==1.17.0`.

---

## Promote-gate implication (source on `main`)

Path-1 baseline (`always_on_shadow` / HTF `off` / EXIT `static` + matching expected-proof pins) appears **satisfied in the running process environment** → B1 alone would not refuse a behavior-changing promote.  

Still **NOT READY**: candidate UNSET; B2 pin confirm; B3/B4/B5/B8; watcher/history root proofs; options ARMED scan; #1194/#1196 are **merged in source only** (not deployed); behavior-neutral Path-2 would refuse `c44d32b→main` delta; explicit operator GO required for every layer.

**A source merge or green CI is not deployment authorization.**

---

## Evidence continuity & cutover boundary

**What a futures restart/promote would interrupt (if authorized later):**
- `futures-bot` process (PID 1851835 since 2026-10-04) — in-memory state, open websocket/session continuity.
- Watcher re-arm copy from new release into shared watcher src (when unit present).
- Any options unit **not** independently pinned to an immutable release would silently pick up new live-symlink code/venv/deps (#1186 schema risk).

**Required cutover boundary for any future release:**
1. Exact nominated SHA (≠ historical `7c93027` unless re-justified) with independent Grok exact-SHA review + green CI.
2. Root B10 watcher + `release_history.txt` regular-file proof; deploy lock clear; disk capacity.
3. B8 classification: if PINNED to `db9bc7e2` (or later approved pin), **preserve**; if LIVE-TREE/MIXED, HOLD for separate collector GO.
4. Options journal hash + duplicate/OOO ARMED scan **before** any collector schema migration; fresh partition only with explicit GO.
5. Fresh B7 preflight at GO (positions/orders/armed).
6. Separate operator GO for build → verify → promote; never bundled.

**Do not reset evidence windows.**

---

## Overall verdicts

| Lane | Verdict |
|---|---|
| **FUTURES** | **HOLD** — not READY FOR OPERATOR REVIEW for promote (candidate UNSET; multiple UNVERIFIED/BLOCKED gates) |
| **OPTIONS** | **HOLD** — collector pin and journal ARMED integrity UNVERIFIED; #1186 merged in source only |
| System classification | **DEMO BROKER ONLY** (live off, flat, disarmed) — not LIVE CAPABLE |
| Confidence | **Medium** on allowlist/public surfaces; **Low** on root-only gates (watcher, history, fingerprint durable pin, Python, freeze, collector unit, options journal content) |

---

## Operator decisions required (no auto-action)

1. Authorize **root read-only** B10 + fingerprint + Python + freeze + collector unit + options journal ARMED scan (expand allowlist or use approved root identity).
2. Decide B2: explain/repair `EXPECTED_RELEASE_FINGERPRINT` only after bare-vs-injected proof.
3. Leave B1 posture unchanged unless root `.env` contradicts process env.
4. Keep `CONTRACT_IDENTITY_GUARD_ENFORCED` off until Pine/rollover proof + separate GO.
5. B8: preserve existing pin if PINNED; else separate migration GO (account for #1186).
6. Nominate exact candidate SHA only after root proofs and independent Grok review of that SHA.
7. Fresh B7 query at any GO.

---

## Ask for Grok

1. Challenge every PASS (especially B1 from process-env, B6 SHA-scoped, B7 with stale preflight timestamp).
2. Treat UNVERIFIED/BLOCKED as missing proof, not soft pass.
3. Verify remaining risk from allowlist false-negative on B2 UNPINNED.
4. Return independent readiness: HOLD / NOT READY / (only if proven) READY FOR OPERATOR REVIEW — **not** deploy authority.

**Cursor does not claim Grok approval because this handoff was posted.**

---

## Artifact paths (session)

- Allowlist bundle: `/opt/cursor/artifacts/vps-audit-2026-10-08T12:33:42Z/`
- Public JSON: `/opt/cursor/artifacts/public-status/live-preflight.json` (+ health/diagnostics/broker-account)
