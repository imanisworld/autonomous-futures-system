# Futures release re-cut + OS maintenance — RUNBOOK DRAFT v3 (PLAN ONLY)

**DRAFT — NOT RUN. v11 (2026-10-29). Spec only. Do not install this lib on the host and do not source it there until QA review and Imani's GO.**

Supersedes the executed v10_thu1029 (sha256 `13a6a459a022547948c8b5d9d7a33b68a6159d4626daf8b2dbba313b8db644cc`, the text installed as `/root/afs-maint-lib.sh` on 2026-10-29). Retires v10_fri1030: that file differs only in the header and the window constants and has both bugs below. Do not run it.

Fixes from the 2026-10-29 recut (AFS-0450..0455), not applied on the box by this file:
- Fix A: a clean `journalctl` (`-- No entries --`, or rc 0 with no real lines) is `JOURNAL_CLEAN`, not a rollback. A `journalctl` failure is a fail, not a pass.
- Fix B: rollback target is the verified clean recut `/root/afs-releases/94a71bdbb975-recut-20261029-210507`, not the polluted `94a71bd` directory. That old directory stays as forensic evidence. Do not delete its stray `.pyc` in place.
- AFS-0458: `rollback_ok` does not need a live MainPID. It uses the four integrity pins saved at precheck, or the systemd unit Environment if that file is missing. It does not read the shared secret file. `_switch_back` and `step_reswitch_new` can run while the unit is crash-looping.
- `c44d32bc` is not an automatic fallback. Use it only after a read-only integrity check passes and Imani says so. Never `cddef4a4`.
- Next window constants are Fri 2026-10-30 17:05–17:30 ET switch stop, rollback warning at 17:45 ET (`RB_STOP`), so the window does not end on the 17:50 apt-daily slot. Friday still needs Imani's fresh GO. `step_postboot`'s `journal_postboot_warn.txt` is a report only; nothing tests it with `-s`.


Status: DRAFT v9 (QA AFS-0304 C1–C3 on v8; v8 kept as `.v8.md`). **Scope: RELEASE RECUT ONLY.** Operator decision, PM-reported Oct 19 21:51 ET: CONSOLE_OK = NO for this window, so all OS/kernel/apt/GRUB/reboot steps are SKIPPED THIS WINDOW. v7 kept as `.v7.md`. Built on v5 FINAL, which Folds in Imani's partial dry runs (`heartbeats/predryrun_2026-10-19/NOTES.md`, 21:37 ET) and the D2 key names (21:40 ET). v4 kept as `.v4.md`; v3 sha256 731ddff8…d1a489. It answers QA AFS-0288 (`QA_RECUT_RUNBOOK_V2_ATTACK_2026-10-19.md`, blockers B2–B8 and notes N1–N9). Nothing in it has been run on any host.
Earlier versions, kept alongside: `.v1.md` (sha256 92027041…72c165) and `.v2.md` (sha256 b880ec79…46525).
Author: Grok Bot (executor). Mon Oct 19 2026 ~21:45 ET.

Scope, in window order:
1. Writer decision (§5): the drop-in **only if** D1 shows the env var is absent; otherwise investigate.
2. ~~apt upgrade + GRUB + reboot~~: **SKIPPED: CONSOLE_OK=NO (operator 2026-10-19)**. Deferred to a future console-tested window (§6 kept for reference only). The pre-checks go straight to the cut.
3. Clean immutable re-cut of the **same SHA** `94a71bd`.
4. Atomic switch, restarting only `futures-bot`.
5. A 10-minute post-gate under the deploy lock.

All times are **America/New_York (ET)**. The box clock is UTC (AFS-0281). Every gate compares the full ET date and time (`date +%Y%m%d%H%M`) against absolute thresholds (B2).

---

## 0. GO line + window

Friday still needs Imani's fresh GO. This file is not that GO. A PM GO relayed through someone else is not Imani's. The earlier multi-day window is retired.

| Gate (ET) | Threshold `%Y%m%d%H%M` | Meaning |
|---|---|---|
| Window start | `202610301705` | no step before Fri 2026-10-30 17:05 |
| Late-start abort | `202610301715` | the HARD ABORT gate must be passed before 17:15 |
| Cut stop | `202610301725` | no new cut started at or after 17:25 |
| Switch stop (HARD STOP 1) | `202610301730` | no symlink switch at or after 17:30 |
| Rollback warn (HARD STOP 2) | `202610301745` | rollback is never blocked by the clock; warn at or after 17:45, before the 17:50 apt-daily slot |

The nominal cut is about 25 minutes, so a start at 17:05 can miss the 17:30 switch stop. The gates fail safe. Do not stretch them.

Track B references at `94a71bd`. They are used by the parity checks and are not touched by this change:
- **Entry windows:** 09:30 ET for 4hr, or 10:00 ET, until 11:00 ET: `context/wide_stop_forward_collector.py:208,213`.
- **Demo sessions:** `("new_york",)`: `context/wide_stop_execution.py:53`.
- **Demo stop cap:** `DEMO_MAX_STOP_TICKS = 300.0` at `context/wide_stop_execution.py:35`, with $0.50/tick at :43-45 (= $150). The order uses min(ledger, 300), :38-40.
- **`wide_stop_4k` stop cap:** `max_stop_ticks=400.0` at `context/wide_stop_ledger_paper.py:86` (`LEDGERS` block :82-92). Also `daily_loss_limit=400.0` at :88 and `fill_eligible=("strat_4hr_retrigger",)` at :90. Evidence: **CODE**, GitHub at ref 94a71bd, QA-verified. The prereg `docs/prereg-4hr-mnq-400-forward-2026-10-09.md` is supporting only.
- **Proof-critical pins:** `ops/live_box_guard.py:24-80`. Approved values: `heartbeats/monday_box_proof_2026-10-12/runtime-pins.txt`.

---

## 1. SHA decision: re-cut 94a71bd (not bda553b)

Recommendation: re-cut `94a71bdbb975b53f21a81b7084482ea3c5270ceb`, with the same content.
- **Track B approval:** it binds deployed SHA == reviewed SHA == `94a71bd` (`docs/futures-track-b-forward-readiness-2026-10-10.md`; ledger AFS-0265/0267). That is a ledger claim, rated LIKELY until D1 shows the box manifest commit (N9).
- **What bda553b changes:** `94a71bd...bda553b` is 38 commits and 38 files. None touch strategy/, execution/, context/, risk_rules.yaml, the webhook, or the scheduler. It does touch runtime-scanned dirs: `sources/polygon_client.py` (+27, `fetch_contract_listings`), new `ops/*`, `scripts/`, and systemd templates.
- **Why that matters:** those changes produce a new `fingerprint_sha256`, which means new `.env` pins, a monitor repin, and a fresh Track B review. That is a deploy, not a repair.
- **What the re-cut keeps:** the commit, the git tree `39903f485defa04341c4378f0ce20099ae5910ac`, the fingerprint, and every pin.

## 2. Root cause: Branch B confirmed; the writer is UNKNOWN

Dry-run facts (NOTES.md, Imani, Oct 19 21:37 ET):
- The only stray file is `94a71bd/scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc`.
  - Its mtime is **2026-10-11 14:58:13Z (10:58:13 ET, Sat)** and it is owned by root.
  - It is a **cpython-313** file. **The OLD venv runs Python 3.13.16** (D1 top, 21:49 ET). The v5 "3.14" claim was wrong.
  - Nothing else is under 94a71bd.
- `options-122-prospective` runs **pinned to `db9bc7e`** (10-release.conf), not 94a71bd. It sets `PYTHONDONTWRITEBYTECODE=1` (+ a no-bytecode.conf) and has `ProtectHome=read-only`. That matches the repo `ops/systemd/options-122-prospective.service:10,16`.
- So the options-122 unit is **not** the writer. futures-bot imports from the release, but the bot is not excluded by interpreter tag alone. Its venv is 3.13 too, though its unit has `PYTHONDONTWRITEBYTECODE` per the drop-in (to be confirmed in D1: `systemctl show futures-bot -p Environment`).

The writer was **some Python 3.13 interpreter**: either system `/usr/bin/python3.13` or **the OLD venv's own `$OLD/.venv/bin/python` (3.13.16)**. It ran as root with cwd or `sys.path` in `/root/afs-releases/94a71bd…`, with bytecode writing on. The ledger has no entry at 10:58 ET Oct 11. The nearest are AFS-0255/0256 (10:20/10:30 ET, PR #1218 reviews, workspace-side), which is UNRELATED-LIKELY.

Hypotheses. These are the repo importers of `scripts.options_122_prospective_collect` at 94a71bd; D1b decides between them:
- **H1, a manual module run** with either interpreter, e.g. `$OLD/.venv/bin/python -m scripts.options_122_prospective_collect …` or `python3 -m …`. That is the same module form as the unit's `ExecStart` (`ops/systemd/options-122-prospective.service:11`), as in a hand smoke test or a replay pointed at the 94a71bd venv.
- **H2, a test run** from the release dir with the venv or system pytest. `tests/test_options_122_prospective_collect.py` imports it at :5, :143, :168, :522, :567. Other search hits that reference the module (lines not read): `tests/test_options_122_canonical_adapter.py`, `tests/test_options_strategy_epochs.py`, `tests/test_options_signal_provenance_hardening.py`.
- **H3, an indirect import** through `options_evidence/options_122_adapter.py:21`, by any process importing `options_evidence.options_122_adapter` from 94a71bd. That includes a unit or ad-hoc script using `$OLD/.venv/bin/python` without `PYTHONDONTWRITEBYTECODE`: futures-bot (if its env lacks the var) or `afs-wide-stop-demo-eod-fallback`, both on the LINK (NOTES.md).
- **H4, cron/at** with either interpreter. No repo cron is known; check the box crontabs (D1b).
- Excluded: the options-122 unit (pinned to db9bc7e, bytecode off), and the scanner and six-micro (pinned to other dirs).

Consequences:
- **§5A drop-in: SKIPPED (Branch B).** It would be a no-op.
- **AFS-0266 whitelist: KEPT.**
- The polluted `94a71bd` directory stays on disk as forensic evidence. Do not delete the stray `.pyc` in place. It is not a rollback target.
- Recurrence is possible until the writer is found. It is caught by the all-`$RELEASES` scans in §9.

---

## PRE-WINDOW DRY RUNS (read-only; completed Oct 19; not a gate for Fri 2026-10-30)

Run these as root on the box. They change nothing: no restart, lock, write, or reboot. Send every output marked "send back" to Ops.

**D1 status (NOTES.md):** these parts are done:
- the pyc scan and stray stat
- unit envs/pins
- `git ls-remote` (1747 lines, so G6 closed)
- PyPI `HTTP/2 200` (G7 closed)

**Top of D1: DONE (NOTES.md 21:49 ET).**
- Oct 19 fact only: OLD was `/root/afs-releases/94a71bdbb975b53f21a81b7084482ea3c5270ceb` and PID 2618547 cwd matched it. That is not the v11 live target. After AFS-0454, P2 pins `LIVE_EXPECT` to `/root/afs-releases/94a71bdbb975-recut-20261029-210507`. The full-SHA directory is forensic evidence and fails `rollback_ok`.
- venv **Python 3.13.16**.
- Manifest: fingerprint `677d7884…2f13c`, branch `main`, dirty False. `commit=None` came from the wrong key; the correct key is `repo.commit` (`ops/release_manifest.py:107-115`, `"commit"` at :112).
- Integrity: FAIL, 1799 files, exactly one problem (the stray .pyc), rc=1.
- **All 74 overrides are unset** in observed and expected, so **no pass-through is needed (CLOSED)**.

Still to send (small): `readlink -f "$OLD/.venv/bin/python"; dpkg -S "$(readlink -f "$OLD/.venv/bin/python")"` (is the venv base apt-managed `/usr/bin/python3.13`?) and `systemctl show futures-bot -p Environment | grep -o PYTHONDONTWRITEBYTECODE=1`. Originally requested, now done:
- `OLD=`, `PID=… cwd=…`
- the manifest commit/fingerprint
- `integrity_rc` and the problem lines
- the `manifest repo.branch` and `override …` lines
- `SYSTEM_YAML_*`
- the integrity/manifest `--help`

Also send `systemctl show futures-bot -p MainPID -p ActiveState -p SubState` and `"$OLD/.venv/bin/python" -V`.

About futures-bot `ExecStart … pid=0`: that is normal. `systemctl show -p ExecStart` reports the pid of the *last exec record*, which is often 0 or stale for a long-running service. **MainPID is authoritative**, so P2 now checks MainPID > 0, `/proc/<MainPID>` exists, and its cwd == OLD. Port 8000 answering D2 already suggests it is running.

**D1. Integrity CLI, unit envs, and pyc forensics.** Everything runs in a subshell, so Imani's shell keeps its cwd and options.
```bash
(
  export PYTHONDONTWRITEBYTECODE=1
  LINK=/root/autonomous-futures-system; RELEASES=/root/afs-releases
  OLD=$(readlink -f "$LINK"); echo "OLD=$OLD"; "$OLD/.venv/bin/python" -V; readlink -f "$OLD/.venv/bin/python"; dpkg -S "$(readlink -f "$OLD/.venv/bin/python")" 2>&1
  PID=$(systemctl show -p MainPID --value futures-bot); echo "PID=$PID cwd=$(readlink /proc/$PID/cwd 2>/dev/null)"
  python3 -B -c 'import json,sys;d=json.load(open(sys.argv[1]));print("manifest",d.get("repo",{}).get("commit"),d.get("fingerprint_sha256"))' "$OLD/release_manifest.json"   # repo.commit: release_manifest.py:112
  cd "$OLD" || { echo "NO_OLD_DIR"; exit 1; }; "$OLD/.venv/bin/python" -S -B -m ops.release_integrity --help 2>&1 | head -n 15
  "$OLD/.venv/bin/python" -B -m ops.release_manifest --help 2>&1 | head -n 15; python3 -B -c "import yaml" 2>/dev/null && echo SYSTEM_YAML_OK || echo SYSTEM_YAML_ABSENT
  env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$OLD" \
    $(tr '\0' '\n' </proc/$PID/environ | grep -E '^(EXPECTED_LIVE_COMMIT|EXPECTED_RELEASE_FINGERPRINT|EXPECTED_RISK_RULES_SHA256|RELEASE_INTEGRITY_ENFORCED)=') \
    "$OLD/.venv/bin/python" -S -B -m ops.release_integrity --repo-root "$OLD"; echo "integrity_rc=$?"
  python3 -B -c 'import json,sys;d=json.load(open(sys.argv[1]));r=d.get("repo",{});print("manifest repo.branch=",r.get("branch"),"dirty=",r.get("dirty"));[print("override",k,"observed=","SET<redacted>" if v.get("observed") is not None else "unset","expected=","SET<redacted>" if v.get("expected") is not None else "unset","pinned=",v.get("pinned"),"matches=",v.get("matches")) for k,v in sorted(d.get("proof_critical_runtime_overrides",{}).items())]' "$OLD/release_manifest.json"
  echo "--- all pyc/__pycache__ under releases (excluding .venv), with mtimes UTC"
  find "$RELEASES" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) -printf '%TY-%Tm-%TdT%TH:%TM:%TSZ %u %p\n' 2>/dev/null | sort
  stat -c 'stray mtime=%y uid=%U' "$OLD/scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc"
  echo "--- python units"
  for u in options-122-prospective options-setup-capture options-scanner afs-paper-collection-eod afs-paper-collection-eow six-micro-daily-tsmom afs-forward-lanes-daily-proof afs-coverage-collector afs-day-only-exit afs-external-heartbeat afs-wide-stop-demo-eod-fallback afs-watcher futures-bot; do
    echo "== $u"; systemctl show "$u.service" -p LoadState -p WorkingDirectory -p ExecStart -p Environment -p ProtectHome -p ProtectSystem -p ReadWritePaths -p DropInPaths --no-pager 2>&1 | grep -v '^Environment=$' | sed -E 's/((TOKEN|SECRET|PASSWORD|KEY|PASS)[A-Z_]*=)[^ ]*/\1<redacted>/g' | sed -E 's#([a-z][a-z0-9+.-]*://)[^/ "@]*@#\1<cred>@#g; s#([a-z][a-z0-9+.-]*://[^/ ?"]*)[^ "]*#\1#g'
  done
  echo "--- options-122 full unit"; systemctl cat options-122-prospective.service 2>&1 | sed -E 's/((TOKEN|SECRET|PASSWORD|KEY|PASS)[A-Z_]*=)[^ ]*/\1<redacted>/g' | sed -E 's#([a-z][a-z0-9+.-]*://)[^/ "@]*@#\1<cred>@#g; s#([a-z][a-z0-9+.-]*://[^/ ?"]*)[^ "]*#\1#g'
  systemctl list-timers --all --no-pager | grep -E 'options|afs-|six-micro'
  git ls-remote "$(git -C "$OLD" remote get-url origin 2>/dev/null || echo https://github.com/imanisworld/autonomous-futures-system.git)" 2>&1 | grep -c . ; curl -sI https://pypi.org/simple/ | head -n 1
)
```
Notes:
- The pins go only into the `env -i` child. They are commit, fingerprint and sha values, not secrets.
- `Environment=` lines are redacted by name pattern. Units normally use `EnvironmentFile`, which `show` does not expand.

Expected:
- OLD is `/root/afs-releases/94a71bd…`, cwd is the same, and the manifest commit is `94a71bd…` plus a fingerprint.
- Both `--help` texts list `--repo-root`. The manifest one also lists `--output` (QA: `release_integrity.py:366-369`, `release_manifest.py:133-135`).
- Integrity is nonzero (FAIL) with **exactly one** problem: the stray `.pyc`.
- The find lists only that file and its `__pycache__` dir, in OLD only.
- options-122 shows `Environment=PYTHONDONTWRITEBYTECODE=1` and `ProtectHome=read-only`.
- `git ls-remote` returns a count of 1 or more, and PyPI answers `HTTP/2 200`.

Send back: the whole output. Ops then sets §5's branch, the exact integrity OK/FAIL strings used by `integrity_ok` in the library, and the options-122 timer times.

**D1b. Writer forensics for 2026-10-11 10:50–11:05 ET (read-only; subshell; prints only).** `TZ` makes journalctl, last and atop read the window in ET:
```bash
RED=$(cat <<'SED'
s/(Bearer|Basic|token) +[A-Za-z0-9._~+\/=-]+/\1 <redacted>/Ig
s#(://)[^/ @"]*@#\1<cred>@#g
s/((secret|passw(or)?d|pass|apikey|api_key|token|auth(orization)?|cookie)[A-Za-z_]*["]?[=:] *)[^ "']+/\1<redacted>/Ig
s/(^| )(-p|--password[= ]?)[^ ]+/\1\2<redacted>/g
s/\b([0-9]{1,3}\.){3}[0-9]{1,3}\b/<ip>/g
SED
)   # = /workspace/qa_recut_v5/redact.sed (QA AFS-0299), inlined: that file is on the shared box, not the VPS
(
  export TZ=America/New_York; S='2026-10-11 10:50'; U='2026-10-11 11:05'; EPOCH=1791730693   # 14:58:13Z
  R=$(ls -d /root/afs-releases/94a71bd* | head -n1); stat -c 'pyc birth=%w mtime=%y' "$R/scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc"
  echo "== all units"; journalctl --since "$S" --until "$U" --no-pager -o short-iso | head -n 400
  VPY=$(readlink -f "$R/.venv/bin/python"); echo "venv python resolves to $VPY"
  echo "== python execs"; journalctl --since "$S" --until "$U" --no-pager -o short-iso _EXE=/usr/bin/python3.13 + _EXE="$VPY" + _EXE="$R/.venv/bin/python" | head -n 50
  echo "== timers/units started or finished"; journalctl --since "$S" --until "$U" --no-pager -o short-iso | grep -E 'Started|Finished|Deactivated|Starting' | head -n 100
  systemctl list-timers --all --no-pager
  echo "== logins"; last -F -w -s "$S" -t "$U" 2>/dev/null; last -F -w -f /var/log/wtmp.1 -s "$S" -t "$U" 2>/dev/null; lastlog -u root 2>/dev/null
  echo "== auth/sudo"; journalctl --since "$S" --until "$U" --no-pager -o short-iso _COMM=sshd + _COMM=sudo + _COMM=su | head -n 100
  zgrep -hE '(2026-10-11T(10:(4|5)|11:0)|2026-10-11T1(4:(4|5)|5:0)|Oct 11 1(4:(4|5)|5:0))' /var/log/auth.log* 2>/dev/null | grep -Ei 'sshd|sudo|su:|session opened' | head -n 100
  echo "== root shell history"; grep -c '^#[0-9]\{10\}$' /root/.bash_history 2>/dev/null
  awk -v e=$EPOCH '/^#[0-9]{10}$/{t=substr($0,2);next} t>=e-900 && t<=e+900 {print t": "$0}' /root/.bash_history 2>/dev/null | grep -E 'python|options_122|94a71bd' | head -n 50
  echo "== cron"; grep -rnE 'options_122|python3' /etc/crontab /etc/cron.d /var/spool/cron/crontabs 2>/dev/null; journalctl -u cron --since "$S" --until "$U" --no-pager -o short-iso | head -n 40
  echo "== atop/sar/audit"; ls /var/log/atop/atop_20261011* 2>/dev/null && atop -r /var/log/atop/atop_20261011 -b 10:50 -e 11:05 -P PRG 2>/dev/null | grep -i python | head -n 50
  ls /var/log/sysstat/sa11 2>/dev/null && TZ=UTC sar -q -f /var/log/sysstat/sa11 -s 14:50:00 -e 15:05:00
  command -v ausearch >/dev/null && ausearch -i -ts 10/11/2026 10:50:00 -te 10/11/2026 11:05:00 -x /usr/bin/python3.13 2>/dev/null | head -n 50; command -v ausearch >/dev/null && ausearch -i -ts 10/11/2026 10:50:00 -te 10/11/2026 11:05:00 -x "$VPY" 2>/dev/null | head -n 50
) 2>&1 | sed -E "$RED"
```
Expected: a python3.13 exec (system or the 94a71bd venv), session, or cron line within seconds of 10:58:13 ET. If the venv resolves to `/usr/bin/python3.13`, both `_EXE` matches are the same binary; the cmdline/cwd in the journal lines decides. That supports H1, H2, H3, or H4.

Send back the whole (redacted) output. The QA redaction rules are inlined as `$RED`, so nothing has to be copied to the VPS: they mask Bearer/Basic tokens, URL creds, secret/password/token/cookie values, `-p`/`--password`, and IPv4. History is limited to ±15 min and filtered to python/options_122/94a71bd lines. Imani still eyeballs the output before pasting. The result goes into G8 as `writer = <H# + evidence | unknown>`.

**D2. `/status/broker-account` (DONE, clean, Oct 19 21:40 ET).** The real keys are **`ok`, `env`, `position`, `reliability`**. The endpoint has no preflight or working-order fields. The result was `env=demo`, `position=None`, `reliability` {state HEALTHY, ready}.

This is a GET to the bot's local endpoint, with no new broker login and no credentials. It makes one cached (~30 s) live read-only Tradovate call (`webhook/app.py:1223-1260`), so run it once:
```bash
curl -fsS 127.0.0.1:8000/status/broker-account | python3 -B -c '
import json,sys;d=json.load(sys.stdin)
r=d.get("reliability")
for k in ("ok","env","position"):print(k,"=",repr(d[k]) if k in d else "<MISSING>")
print("reliability.state =",repr(r.get("state","<MISSING>")) if isinstance(r,dict) else "<NOT A DICT>","reliability.ready =",repr(r.get("ready","<MISSING>")) if isinstance(r,dict) else "<NOT A DICT>")
print("other_keys_count =",len(set(d)-{"ok","env","position","reliability"}))'
```
PASS exactly when all of these hold:
- `ok is True`
- `env == 'demo'`
- `position` is present and `None`/null
- `reliability` is a **dict** (`reliability_snapshot()`, `execution/tradovate_supervisor.py:59-81`) with `state == 'HEALTHY'` and `ready is True`

Anything else fails closed: a missing key, a missing `state`/`ready`, `ready` false, or a string reliability.

**D3. Console login + GRUB/apt reads.** In the VPS provider dashboard, Imani **only opens the web/serial console and logs in as root**. Confirm:
- a. the console opens
- b. the root login works there, independent of SSH

**Do not press reboot, rescue, reinstall, or power.** Log out. Then run, read-only, on the box:
```bash
grub-editenv list
grep -hE '^GRUB_(DEFAULT|TIMEOUT|TIMEOUT_STYLE)=' /etc/default/grub /etc/default/grub.d/*.cfg 2>/dev/null
dpkg -l 'linux-image*' | awk '/^ii/{print $2,$3}'
uname -r
apt list --upgradable 2>/dev/null | grep -E '^(linux-(image|generic|headers|modules|virtual)|python3\.13|libpython3\.13)' || echo NO_KERNEL_OR_PY313_UPGRADE
```
Expected:
- `saved_entry=…` or empty
- `GRUB_DEFAULT=0`, `GRUB_TIMEOUT=0`, `GRUB_TIMEOUT_STYLE=hidden` (cloud default)
- one or more `linux-image-*`, with one matching `uname -r`
- a list of upgrades, or `NO_KERNEL_OR_PY313_UPGRADE`

Send back a/b (yes/no) and the outputs.
**D3 results (NOTES.md):** `GRUB_DEFAULT=0`, `GRUB_TIMEOUT_STYLE=hidden`, `GRUB_TIMEOUT=0`. Running kernel **7.0.0-31**; **7.0.0-38 installed, never booted**. `NO_KERNEL_OR_PY313_UPGRADE`. The venv is 3.13.16, so that check covers the right interpreter (`python3.13`/`libpython3.13`); no python upgrade is pending. The hold stays as protection.

So **any reboot boots 7.0.0-38 with no visible menu.** D-4 is DEFERRED (CONSOLE_OK=NO); see risk RK-1 in §13.

**Console a/b: operator decision CONSOLE_OK = NO for this window (Oct 19 21:51 ET).** These reads are for a future window only:
- `grep -hE '^GRUB_(TERMINAL|SERIAL_COMMAND|CMDLINE_LINUX)' /etc/default/grub /etc/default/grub.d/*.cfg`, which shows whether GRUB draws on the serial console the provider shows
- `grep -oE "gnulinux-[^']*7\.0\.0-31[^']*advanced[^']*" /boot/grub/grub.cfg | head -n1`, the 31 entry id used in the rollback

---

## 3. Step library: save once as `/root/afs-maint-lib.sh` (mode 700), then `. /root/afs-maint-lib.sh`

How the library is built (B3–B5):
- It sets **no global `set -e`**. Each step body runs in a subshell `( … )` and every gate is `cond || { fail …; return 1; }`. A failure returns nonzero to the prompt and Imani's shell survives.
- State lives in `/root/afs-maint-state.env` (mode 600), written at first use. After the reboot, re-source the library and run `state_load`. **Never** re-generate STAMP.
- The deploy lock is taken only inside the switch step's subshell. `trap lock_release EXIT` releases it however the subshell ends, **after** the 10-minute post-gate.

```bash
# /root/afs-maint-lib.sh — source with:  . /root/afs-maint-lib.sh
SHA=94a71bdbb975b53f21a81b7084482ea3c5270ceb; SHORT=94a71bdbb975; TREE_EXPECT=39903f485defa04341c4378f0ce20099ae5910ac
LINK=/root/autonomous-futures-system; RELEASES=/root/afs-releases; SHARED=/root/afs-shared
STATE=${STATE:-/root/afs-maint-state.env}
W_START=202610301705; LATE_ABORT=202610301715; CUT_STOP=202610301725; SWITCH_STOP=202610301730; RB_STOP=202610301745
LIVE_EXPECT=${LIVE_EXPECT:-/root/afs-releases/94a71bdbb975-recut-20261029-210507}
ROLLBACK=${ROLLBACK:-}    # persisted via state_set; prechecks default it to OLD only after rollback_ok
STRAY_REL=scripts/__pycache__/options_122_prospective_collect.cpython-313.pyc
export PYTHONDONTWRITEBYTECODE=1

et_now(){ TZ=America/New_York date +%Y%m%d%H%M; }
et(){ TZ=America/New_York date '+%F %T ET'; }
fail(){ echo "GATE_FAIL: $*" >&2; return 1; }
log(){ echo "$(et) $*" | tee -a "$EV/log.txt"; }
# gate_window <stop>: now >= window start AND now < stop (full date+time, B2)
gate_window(){ local n; n=$(et_now)
  [ "$n" -ge "$W_START" ] || { fail "before window start $W_START (now $n)"; return 1; }
  [ "$n" -lt "$1" ]       || { fail "at/after stop $1 (now $n)"; return 1; }; }

state_init(){ [ ! -e "$STATE" ] || { fail "state file exists ($STATE): use state_load"; return 1; }
  local s; s=$(date -u +%Y%m%d-%H%M%S)
  ( umask 077; printf '%s\n' "STAMP=$s" "EV=/root/afs-maint-evidence-$s" "NEW=$RELEASES/${SHORT}-recut-$s" \
      "SRC=/root/afs-recut-src-$s" "DROPIN_MARKER=/root/afs-dropin-marker-$s" "SWITCH_MARKER=/root/afs-switch-marker-$s" \
      "DROPIN_BACKUP=/root/afs-maint-evidence-$s/dropin_backup" "LOCK_OWNER=maint-recut-$s" \
      "LIVE_EXPECT=$LIVE_EXPECT" "ROLLBACK=${ROLLBACK:-$LIVE_EXPECT}" > "$STATE" )
  chmod 600 "$STATE" && state_load && mkdir -p "$EV" && chmod 700 "$EV"; }
state_load(){ [ -s "$STATE" ] || { fail "no state file $STATE"; return 1; }
  set -a; . "$STATE"; set +a
  [ -n "${STAMP:-}" ] && [ -n "${EV:-}" ] && [ "$NEW" = "$RELEASES/${SHORT}-recut-$STAMP" ] || { fail "state incomplete/inconsistent"; return 1; }; }
state_set(){ [ -s "$STATE" ] || { fail "no state"; return 1; }
  ( umask 077; grep -v "^$1=" "$STATE" > "$STATE.tmp"; printf '%s=%s\n' "$1" "$2" >> "$STATE.tmp" ) && chmod 600 "$STATE.tmp" && mv -f "$STATE.tmp" "$STATE" && export "$1=$2"; }

lock_take(){ mkdir "$SHARED/deploy.lock" 2>/dev/null || { fail "deploy.lock held: $(cat "$SHARED/deploy.lock/meta.txt" 2>/dev/null)"; return 1; }
  printf 'owner=%s\npid=%s\nstarted_utc=%s\nsha=%s\n' "$LOCK_OWNER" "$$" "$(date -u +%FT%TZ)" "$SHA" > "$SHARED/deploy.lock/meta.txt"
  trap lock_release EXIT; echo LOCK_TAKEN; }
lock_release(){ if [ "${LOCK_HOLD:-0}" = 1 ]; then echo "HOLD: deploy.lock kept (rollback verify failed; R-fail)"; trap - EXIT; return 0; fi; if grep -qx "owner=$LOCK_OWNER" "$SHARED/deploy.lock/meta.txt" 2>/dev/null; then rm -rf -- "$SHARED/deploy.lock" && echo LOCK_RELEASED; fi; trap - EXIT; }

fb_pid(){ systemctl show -p MainPID --value futures-bot; }
# integrity <dir> <outfile>: run from <dir> (B6); returns the CLI exit code
integrity(){ local d=$1 o=$2 p; p=$(fb_pid); [ "${p:-0}" -gt 0 ] || { fail "futures-bot has no MainPID"; return 1; }
  ( cd "$d" && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$d" \
      $(tr '\0' '\n' </proc/"$p"/environ | grep -E '^(EXPECTED_LIVE_COMMIT|EXPECTED_RELEASE_FINGERPRINT|EXPECTED_RISK_RULES_SHA256|RELEASE_INTEGRITY_ENFORCED)=') \
      "$d/.venv/bin/python" -S -B -m ops.release_integrity --repo-root "$d" ) > "$o" 2>&1; }
# integrity_ok <dir> <outfile>: exit 0 AND no problem lines (strings to be confirmed from D1 output)
# strings from ops/release_integrity.py@94a71bd: summary :377-380 "release integrity: <STATUS> — N files checked, release …"
# STATUS is OK|FAIL|UNPINNED (:250,:312,:319,:322); problems print "  ✗ …" (:383,:387); exit 0 only if ok (:388)
integrity_ok(){ integrity "$1" "$2" || { fail "integrity nonzero: $(head -c 400 "$2")"; return 1; }
  grep -qE '^release integrity: OK — [0-9]+ files checked' "$2" || { fail "no OK summary line"; return 1; }
  [ "$(grep -c '✗' "$2")" -eq 0 ] || { fail "integrity ✗ lines present"; return 1; }; echo INTEGRITY_OK; }
# Four pins release_integrity reads. Saved at precheck from the live process environ.
# rollback_ok uses the saved file so a crash loop (MainPID 0, no /proc) can still be checked.
# If the file is missing, the same four keys are taken from `systemctl show -p Environment`.
# Never read the shared secret file.
INTEGRITY_PIN_RE='^(EXPECTED_LIVE_COMMIT|EXPECTED_RELEASE_FINGERPRINT|EXPECTED_RISK_RULES_SHA256|RELEASE_INTEGRITY_ENFORCED)='
_pin_file_ok(){ local f=$1
  case "$f" in *.env|*/.env) fail "refusing pin path $f"; return 1;; esac
  [ -s "$f" ] || return 1
  grep -q '^EXPECTED_LIVE_COMMIT=' "$f" && grep -q '^EXPECTED_RELEASE_FINGERPRINT=' "$f" \
    && grep -q '^EXPECTED_RISK_RULES_SHA256=' "$f" && grep -q '^RELEASE_INTEGRITY_ENFORCED=' "$f"; }
capture_integrity_pins(){ local p out="${EV:?}/integrity_pins.txt"
  case "$out" in *.env|*/.env) fail "refusing pin path $out"; return 1;; esac
  p=$(fb_pid)
  if [ "${p:-0}" -gt 0 ] && [ -r "/proc/$p/environ" ]; then
    tr '\0' '\n' </proc/"$p"/environ | grep -E "$INTEGRITY_PIN_RE" | sort > "$out"
  else
    systemctl show futures-bot -p Environment --value | tr ' ' '\n' | grep -E "$INTEGRITY_PIN_RE" | sort > "$out"
  fi
  _pin_file_ok "$out" || { fail "integrity pins incomplete"; return 1; }
  state_set INTEGRITY_PINS "$out"; }
ensure_integrity_pins(){ local pins="${INTEGRITY_PINS:-${EV:-}/integrity_pins.txt}"
  case "$pins" in *.env|*/.env) fail "refusing pin path $pins"; return 1;; esac
  _pin_file_ok "$pins" && return 0
  capture_integrity_pins; }
integrity_from_pins(){ local d=$1 o=$2 pins
  ensure_integrity_pins || return 1
  pins="${INTEGRITY_PINS:-$EV/integrity_pins.txt}"
  case "$pins" in *.env|*/.env) fail "refusing pin path $pins"; return 1;; esac
  ( cd "$d" && env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$d" \
      $(grep -E "$INTEGRITY_PIN_RE" "$pins") \
      "$d/.venv/bin/python" -S -B -m ops.release_integrity --repo-root "$d" ) > "$o" 2>&1; }
integrity_ok_from_pins(){ integrity_from_pins "$1" "$2" || { fail "integrity nonzero: $(head -c 400 "$2")"; return 1; }
  grep -qE '^release integrity: OK — [0-9]+ files checked' "$2" || { fail "no OK summary line"; return 1; }
  [ "$(grep -c '✗' "$2")" -eq 0 ] || { fail "integrity ✗ lines present"; return 1; }
  echo INTEGRITY_OK; }
# journal_err_clean <file>: 0 = clean (empty, whitespace-only, or only journalctl's "-- No entries --" line);
# 1 = at least one real line; 2 = file missing. Never matches on substrings: a real line that contains "No entries" is still an error.
journal_err_clean(){ [ -f "$1" ] || return 2; ! grep -vxE '[[:space:]]*(-- No entries --)?[[:space:]]*' "$1" >/dev/null; }
# rollback_ok <dir>: manifest, no pyc outside .venv, read-only, integrity from saved pins.
# Does not call fb_pid. Safe while futures-bot is crash-looping.
rollback_ok(){ local d=$1
  [ -d "$d" ] && [ -s "$d/release_manifest.json" ] || { fail "rollback target missing/manifest missing: $d"; return 1; }
  [ -z "$(find "$d" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) -print)" ] || { fail "rollback target has pycache: $d"; return 1; }
  [ -z "$(find "$d" -mindepth 1 \( -name .venv -prune \) -o -perm -u=w -print -quit)" ] || { fail "rollback target not read-only: $d"; return 1; }
  integrity_ok_from_pins "$d" "$EV/integrity_rollback_$(basename "$d").txt" || { fail "rollback target fails integrity: $d"; return 1; }
  echo ROLLBACK_TARGET_OK; }
# p4_exact <outfile>: OLD tree must FAIL with exactly one ✗ line that is EXACTLY the known stray (all not-in-manifest entries share one line: release_integrity.py:299-308)
p4_exact(){ grep -qE '^release integrity: FAIL — [0-9]+ files checked' "$1" && [ "$(grep -c '✗' "$1")" -eq 1 ] && grep -qxF "  ✗ not in release manifest: $STRAY_REL" "$1"; }
# C3 report-only: hidden apt/reboot paths. Changes nothing; an enabled auto-reboot is an RK-1 trigger for an Imani/PM decision
apt_auto_report(){ local u; systemctl list-timers --all --no-pager apt-daily.timer apt-daily-upgrade.timer 2>&1
  for u in apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service; do echo "$u enabled=$(systemctl is-enabled "$u" 2>&1) active=$(systemctl is-active "$u" 2>&1)"; done
  grep -rnE 'Unattended-Upgrade::Automatic-Reboot|APT::Periodic' /etc/apt/apt.conf.d/ 2>/dev/null
  grep -rhqE '^[[:space:]]*Unattended-Upgrade::Automatic-Reboot[[:space:]]+"(true|1)"' /etc/apt/apt.conf.d/ 2>/dev/null && echo "FLAG RK-1 TRIGGER: Unattended-Upgrade::Automatic-Reboot enabled -> Imani/PM decision (runbook does not change it)"
  grep -rhqE '^[[:space:]]*APT::Periodic::Unattended-Upgrade[[:space:]]+"[1-9]' /etc/apt/apt.conf.d/ 2>/dev/null && echo "FLAG: unattended upgrades enabled -> may patch python3.13/kernel before or inside the window (cut fails safe on the 3.13.16 check) -> Imani/PM decision"
  return 0; }
pins_snapshot(){ tr '\0' '\n' </proc/"$(fb_pid)"/environ | grep -E '^(BROKER|PAPER_MODE|LIVE_TRADING_ENABLED|TRADOVATE_ENV|SCHEDULE_MODE|MAX_CONTRACTS_HARD_CAP|WIDE_STOP_LEDGER_MODE|WIDE_STOP_LEDGER_EXECUTION_ROUTE|WIDE_STOP_DEMO_EXECUTION_ENABLED|EXPECTED_PROOF_WIDE_STOP_DEMO_EXECUTION_ENABLED|WIDE_STOP_DEMO_SESSIONS|RELEASE_INTEGRITY_ENFORCED|EXPECTED_LIVE_COMMIT)=' | sort; }
flat_check(){ curl -fsS 127.0.0.1:8000/status/broker-account | python3 -B -c '
import json,sys;d=json.load(sys.stdin)
r=d.get("reliability"); ok=d.get("ok") is True and d.get("env")=="demo" and ("position" in d and d["position"] is None) and isinstance(r,dict) and r.get("state")=="HEALTHY" and r.get("ready") is True
for k in ("ok","env","position"):print(k,"=",repr(d[k]) if k in d else "<MISSING>")
print("reliability.state =",repr(r.get("state","<MISSING>")) if isinstance(r,dict) else "<NOT A DICT>","reliability.ready =",repr(r.get("ready","<MISSING>")) if isinstance(r,dict) else "<NOT A DICT>")
sys.exit(0 if ok else 1)'; }   # keys from D2 (Oct 19 21:40 ET); anything else fails closed
flat_retry(){ local t=0; until flat_check > "$1"; do t=$((t+1)); [ "$t" -ge 6 ] && { fail "not flat/ok after 6 tries (~3 min)"; return 1; }; sleep 30; done; }
PINS="options-122-prospective:db9bc7e options-scanner:47ae01a six-micro-daily-tsmom:4f10d7d"   # NOTES.md; never moved/deleted by this runbook
pinned_check(){ local e u s p; for e in $PINS; do u=${e%%:*}; s=${e#*:}
    systemctl show "$u.service" -p WorkingDirectory -p ExecStart -p DropInPaths | grep -q "$RELEASES/$s" || { fail "$u not pinned to $s"; return 1; }
    p=$(systemctl show -p MainPID --value "$u.service"); [ "${p:-0}" -eq 0 ] || readlink "/proc/$p/cwd" | grep -q "$RELEASES/$s" || { fail "$u running outside $s"; return 1; }
    ls -d "$RELEASES/$s"* >/dev/null 2>&1 || { fail "pinned dir $s missing"; return 1; }; echo "PIN_OK $u $s"; done; }
pyc_scan(){ find "$RELEASES" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) "$@" -print 2>/dev/null; }
# guarded removal of the new dir (N8)
safe_rm_new(){ local live; live=$(readlink -f "$LINK")
  [ -n "${STAMP:-}" ] && [ "$NEW" = "$RELEASES/${SHORT}-recut-$STAMP" ] || { fail "NEW not the expected recut path"; return 1; }
  [ "$NEW" != "$live" ] && [ "$NEW" != "${OLD:-}" ] || { fail "refusing: NEW is live target or OLD"; return 1; }
  case "$NEW" in "$RELEASES"/db9bc7e*|"$RELEASES"/47ae01a*|"$RELEASES"/4f10d7d*) fail "refusing: pinned dir"; return 1;; esac
  [ -e "$NEW" ] || { echo NEW_ABSENT; return 0; }
  chmod -R u+w -- "$NEW" && rm -rf -- "$NEW" && echo NEW_REMOVED; }

# ---------- P1-P9 (read-only) ----------
_prechecks(){ local f=0 p
  gate_window "$LATE_ABORT" || return 1
  { [ ! -e "$SHARED/deploy.lock" ] && echo LOCK_ABSENT; } || { fail "P1 lock present"; f=1; }
  ! pgrep -af 'atomic_release|afs-deploy|release_manifest|pip install' || { fail "P1 release process running"; f=1; }
  LIVE=$(readlink -f "$LINK"); p=$(fb_pid); [ -n "${OLD:-}" ] || OLD=$LIVE   # NEW-3: never overwrite a persisted OLD
  [ "$LIVE" = "$LIVE_EXPECT" ] && [ "$OLD" = "$LIVE" ] || { fail "P2 live target must be pinned LIVE_EXPECT (OLD=$OLD live=$LIVE expect=$LIVE_EXPECT)"; return 1; }; state_set OLD "$OLD"
  [ -n "${ROLLBACK:-}" ] || ROLLBACK=$OLD
  capture_integrity_pins || { fail "P2 integrity pins not captured"; return 1; }
  rollback_ok "$ROLLBACK" || { fail "P2 no verified rollback target"; return 1; }
  state_set ROLLBACK "$ROLLBACK"
  [ "${p:-0}" -gt 0 ] && [ -d "/proc/$p" ] || { fail "P2 futures-bot MainPID=$p not running (MainPID is authoritative, not ExecStart pid)"; return 1; }
  [ "$(readlink /proc/"$p"/cwd)" = "$OLD" ] || { fail "P2 /proc/MainPID/cwd != OLD"; f=1; }
  python3 -B -c 'import json,sys;d=json.load(open(sys.argv[1]));c=d["repo"]["commit"];print(c,d["fingerprint_sha256"]);sys.exit(0 if c==sys.argv[2] else 1)' "$OLD/release_manifest.json" "$SHA" > "$EV/old_manifest_id.txt" || { fail "P2 manifest commit"; f=1; }
  systemctl show futures-bot -p WorkingDirectory -p ExecStart -p NRestarts -p MainPID > "$EV/fb_pre.txt"
  grep -qx "WorkingDirectory=$LINK" "$EV/fb_pre.txt" || { fail "P3 futures-bot WorkingDirectory is not the link"; f=1; }
  sha256sum /etc/systemd/system/futures-bot.service.d/*.conf > "$EV/dropins_sha_pre.txt" 2>&1 || { fail "P3 drop-ins"; f=1; }
  integrity "$OLD" "$EV/integrity_pre.txt" || echo "P4_INFO integrity rc nonzero"
  if [ "$OLD" = "$RELEASES/$SHA" ]; then
    if p4_exact "$EV/integrity_pre.txt"; then echo "P4_INFO known-polluted dir: exactly the stray pyc"; else echo "P4_INFO known-polluted dir is not the single-stray shape (record only)"; fi
  else
    echo "P4_INFO live tree is not the known-polluted dir; rollback_ok is the hard gate"
  fi
  pins_snapshot > "$EV/pins_pre.txt"
  for kv in LIVE_TRADING_ENABLED=false TRADOVATE_ENV=demo MAX_CONTRACTS_HARD_CAP=1 WIDE_STOP_LEDGER_MODE=paper_sim WIDE_STOP_DEMO_EXECUTION_ENABLED=true "EXPECTED_LIVE_COMMIT=$SHA"; do grep -qx "$kv" "$EV/pins_pre.txt" || { fail "P5 pin $kv"; f=1; }; done
  [ "$(grep -n 'max_stop_ticks=400.0' "$OLD/context/wide_stop_ledger_paper.py" | head -n1 | cut -d: -f1)" = 86 ] || { fail "P5 400-tick line"; f=1; }
  [ "$(grep -n '^DEMO_MAX_STOP_TICKS = 300.0' "$OLD/context/wide_stop_execution.py" | cut -d: -f1)" = 35 ] || { fail "P5 300-tick line"; f=1; }
  sha256sum "$OLD/context/wide_stop_ledger_paper.py" "$OLD/context/wide_stop_execution.py" | awk '{print $1}' > "$EV/cap_files_sha_pre.txt"
  flat_check > "$EV/flat_pre.txt" || { fail "P6 not ok/demo/position None/HEALTHY"; f=1; }
  systemctl --failed --no-legend | awk '{print $1}' | sort > "$EV/failed_pre.txt"   # baseline (forward-lanes status=1 is known)
  pinned_check > "$EV/pinned_pre.txt" || f=1
  df -P /root | awk 'NR==2{exit !($4>3000000)}' || { fail "P7 <3GB free"; f=1; }
  apt list --upgradable 2>/dev/null > "$EV/apt_upgradable.txt"; dpkg -l 'linux-image-*' | awk '/^ii/{print $2,$3}' > "$EV/kernels_pre.txt"; uname -r > "$EV/kernel_pre.txt"
  systemctl list-units --type=service --state=running --no-legend | awk '{print $1}' | sort > "$EV/svc_running_pre.txt"
  systemctl list-timers --all --no-pager > "$EV/timers_pre.txt"
  systemctl show options-122-prospective.service -p Environment -p WorkingDirectory -p ExecStart -p DropInPaths > "$EV/opt122_pre.txt"
  pyc_scan -printf '%T@ %p\n' > "$EV/pyc_pre.txt"
  apt_auto_report > "$EV/apt_auto.txt"; grep '^FLAG' "$EV/apt_auto.txt" || echo "P11 apt auto-update/reboot: no flags (report-only)"
  [ "$f" -eq 0 ] || { fail "PRECHECKS FAILED — HARD ABORT"; return 1; }; echo PRECHECKS_PASS; }
step_prechecks(){ ( set -uo pipefail; state_load || return 1; _prechecks ) ; }

# ---------- §5A drop-in (only on D1 branch A) ----------
_dropin(){ local U=options-122-prospective.service D=/etc/systemd/system/options-122-prospective.service.d C
  C=$D/10-nobytecode.conf; gate_window "$SWITCH_STOP" || return 1
  systemctl show "$U" -p Environment | grep -q 'PYTHONDONTWRITEBYTECODE=1' && { fail "env already present: §5A is a no-op, use §5B"; return 1; }
  [ ! -e "$C" ] || { fail "$C already exists"; return 1; }
  [ "$(systemctl is-active "$U")" != active ] || { fail "$U run in progress; wait, do not stop it"; return 1; }
  mkdir -p "$DROPIN_BACKUP" && systemctl cat "$U" > "$DROPIN_BACKUP/$U.cat.before" && systemctl show "$U" -p Environment -p DropInPaths > "$EV/dropin_env_before.txt" || return 1
  mkdir -p "$D" && printf '[Service]\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n' > "$C" && chmod 0644 "$C" || return 1
  state_set DROPIN_APPLIED 1; touch "$DROPIN_MARKER" && systemctl daemon-reload || return 1
  systemctl show "$U" -p Environment -p DropInPaths | tee "$EV/dropin_env_after.txt" | grep -q 'PYTHONDONTWRITEBYTECODE=1' || { fail "env not visible after reload"; return 1; }
  diff "$EV/dropin_env_before.txt" "$EV/dropin_env_after.txt" > "$EV/dropin_env_diff.txt"; echo DROPIN_OK; }
step_dropin(){ ( set -uo pipefail; state_load || return 1; _dropin ) ; }
step_dropin_rollback(){ ( set -uo pipefail; state_load || return 1
  rm -f /etc/systemd/system/options-122-prospective.service.d/10-nobytecode.conf && rmdir --ignore-fail-on-non-empty /etc/systemd/system/options-122-prospective.service.d && systemctl daemon-reload || return 1
  systemctl show options-122-prospective.service -p Environment -p DropInPaths | tee "$EV/dropin_rollback.txt"; state_set DROPIN_APPLIED 0 ) ; }
# writer check: no NEW pyc anywhere under releases since <marker>; old stray may remain
step_pyc_since(){ ( set -uo pipefail; state_load || return 1; local m=${1:?marker}; [ -e "$m" ] || { fail "marker $m missing"; return 1; }
  systemctl show options-122-prospective.service -p ExecMainExitTimestamp -p Result | tee -a "$EV/pyc_checks.txt"
  pyc_scan -newer "$m" > "$EV/pyc_new_since_$(basename "$m").txt"
  [ ! -s "$EV/pyc_new_since_$(basename "$m").txt" ] || { cat "$EV/pyc_new_since_$(basename "$m").txt"; fail "NEW bytecode written since $m"; return 1; }
  echo NO_NEW_PYC ) ; }

# ---------- §6 OS ----------
_apt(){ gate_window "$CUT_STOP" || return 1; local PY PV H KH
  [ "${CONSOLE_OK:-0}" = 1 ] || { fail "D3 console login not confirmed: skip the OS step (recut only)"; return 1; }
  grub_terminal_check || { fail "GRUB terminal does not match the provider console: skip the OS step"; return 1; }
  apt-get update > "$EV/apt_update.txt" 2>&1 || { fail "apt-get update"; return 1; }
  PY=$(readlink -f "$OLD/.venv/bin/python"); PV=$("$OLD/.venv/bin/python" -S -c 'import sys;print("%d.%d"%sys.version_info[:2])') || { fail "venv python"; return 1; }
  if dpkg -S "$PY" > "$EV/py_owner.txt" 2>&1; then H=$(dpkg-query -W -f='${Package} ${db:Status-Abbrev}\n' "python$PV*" "libpython$PV*" 2>/dev/null | awk '$2 ~ /^ii/ {print $1}' | sort -u); else H=; echo "venv interpreter $PY not apt-managed: no hold" | tee -a "$EV/log.txt"; fi
  KH=$(dpkg-query -W -f='${Package} ${db:Status-Abbrev}\n' 'linux-generic*' 'linux-image-generic*' 'linux-headers-generic*' 'linux-virtual*' 'linux-image-virtual*' 2>/dev/null | awk '$2 ~ /^ii/ {print $1}' | sort -u)
  [ -z "$H$KH" ] || apt-mark hold $H $KH > "$EV/apt_hold.txt" 2>&1 || { fail "apt-mark hold"; return 1; }
  apt-mark showhold > "$EV/apt_holds.txt"; for h in $H $KH; do grep -qx "$h" "$EV/apt_holds.txt" || { fail "$h not held"; return 1; }; done
  apt list --upgradable 2>/dev/null > "$EV/apt_upgradable2.txt"
  DEBIAN_FRONTEND=noninteractive apt-get -y -o Dpkg::Options::=--force-confold upgrade > "$EV/apt_upgrade.txt" 2>&1 || { fail "apt-get upgrade rc!=0 (see $EV/apt_upgrade.txt)"; return 1; }
  dpkg -l 'linux-image-*' | awk '/^ii/{print $2,$3}' > "$EV/kernels_post.txt"
  grep -q '7\.0\.0-31' "$EV/kernels_post.txt" && grep -q '7\.0\.0-38' "$EV/kernels_post.txt" || { fail "kernel 31 (fallback) or 38 not installed"; return 1; }
  grub_prep || return 1
  echo APT_OK_READY_TO_REBOOT; }
# D-4 option (i): visible menu so the console can pick 7.0.0-31 if 38 fails
grub_prep(){ cp -p /etc/default/grub "$EV/grub.default.bak" && cp -p /boot/grub/grub.cfg "$EV/grub.cfg.bak" || { fail "grub backup"; return 1; }
  printf 'GRUB_TIMEOUT_STYLE=menu\nGRUB_TIMEOUT=5\nGRUB_RECORDFAIL_TIMEOUT=5\n' > /etc/default/grub.d/99-afs-maint-menu.cfg && state_set GRUB_PREP 1 || return 1
  update-grub > "$EV/update_grub.txt" 2>&1 || { fail "update-grub"; return 1; }
  grep -q 'set timeout=5' /boot/grub/grub.cfg || { fail "grub.cfg timeout not 5"; return 1; }
  grep -q 'vmlinuz-7\.0\.0-31' /boot/grub/grub.cfg && grep -q 'vmlinuz-7\.0\.0-38' /boot/grub/grub.cfg || { fail "grub.cfg missing 31 or 38"; return 1; }
  grep -oE "gnulinux-[^']*7\.0\.0-31[^']*advanced[^']*" /boot/grub/grub.cfg | head -n1 > "$EV/grub_entry_31.txt"; [ -s "$EV/grub_entry_31.txt" ] || { fail "31 entry id"; return 1; }
  echo GRUB_MENU_OK; }
grub_restore(){ rm -f /etc/default/grub.d/99-afs-maint-menu.cfg /etc/default/grub.d/98-afs-kernel31.cfg && update-grub > "$EV/update_grub_restore.txt" 2>&1 && state_set GRUB_PREP 0 && echo GRUB_RESTORED; }
# read-only: does GRUB draw on the console type the provider shows? (last definition wins: /etc/default/grub, then grub.d/*.cfg)
grub_terminal_check(){ local GT SC
  GT=$(grep -hE '^GRUB_TERMINAL(_OUTPUT)?=' /etc/default/grub /etc/default/grub.d/*.cfg 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d "\"'")
  SC=$(grep -hE '^GRUB_SERIAL_COMMAND=' /etc/default/grub /etc/default/grub.d/*.cfg 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d "\"'")
  echo "CONSOLE_TYPE=${CONSOLE_TYPE:-unset} GRUB_TERMINAL=${GT:-unset} GRUB_SERIAL_COMMAND=${SC:-unset}"
  case "${CONSOLE_TYPE:-}" in
    vnc)    [ -z "$GT" ] || echo "$GT" | grep -qwE 'console|gfxterm' || { fail "VNC console but GRUB_TERMINAL=$GT"; return 1; } ;;
    serial) echo "$GT" | grep -qw serial && echo "$SC" | grep -q -- "--unit=${SERIAL_UNIT:?}" && echo "$SC" | grep -q -- "--speed=${SERIAL_SPEED:?}" || { fail "serial console but GRUB serial not configured for unit/speed"; return 1; } ;;
    *)      fail "CONSOLE_TYPE not recorded"; return 1 ;;
  esac; echo GRUB_TERMINAL_MATCH; }
console_ok_set(){ ( set -uo pipefail; state_load || return 1
  [ "${D3A:-}" = yes ] && [ "${D3B:-}" = yes ] || { fail "D3 a/b not recorded yes"; return 1; }
  grub_terminal_check || return 1; state_set CONSOLE_OK 1 && echo CONSOLE_OK_SET ) ; }
# R-OS kernel rollback, after booting 7.0.0-31 by hand from the console menu: make 31 the default and defer 38
grub_pin31(){ local id; case "$(uname -r)" in 7.0.0-31*) ;; *) fail "not booted on 7.0.0-31 ($(uname -r))"; return 1;; esac
  id=$(grep -oE "gnulinux-advanced-[^']*" /boot/grub/grub.cfg | head -n1)
  [ -n "$id" ] && [ -s "$EV/grub_entry_31.txt" ] || { fail "entry ids"; return 1; }
  printf 'GRUB_DEFAULT="%s>%s"\n' "$id" "$(cat "$EV/grub_entry_31.txt")" > /etc/default/grub.d/98-afs-kernel31.cfg && update-grub > "$EV/update_grub_pin31.txt" 2>&1 && echo PINNED_31; }
step_apt(){ echo "SKIPPED: CONSOLE_OK=NO (operator 2026-10-19)" >&2; return 1; }   # unconditional this window; _apt kept for a future console-tested window
_postboot(){ local f=0
  systemctl is-system-running | tee "$EV/sysstate_postboot.txt" | grep -qE '^(running|degraded)$' || { fail "system state"; f=1; }
  systemctl --failed --no-legend | awk '{print $1}' | sort > "$EV/failed_postboot.txt"; ! comm -13 "$EV/failed_pre.txt" "$EV/failed_postboot.txt" | grep -E 'futures-bot|options|afs-|nginx|six-micro' || { fail "NEW AFS unit failure since pre-checks"; f=1; }
  uname -r | tee "$EV/kernel_booted.txt" | grep -q '7\.0\.0-38' || echo "NOTE: booted $(uname -r), not 7.0.0-38 (record; not a fail by itself)"
  T=$(readlink -f "$LINK"); [ "$T" = "$OLD" ] || [ "$T" = "${NEW:-}" ] || { fail "link target $T is neither OLD nor NEW"; f=1; }   # valid before or after the switch (unplanned reboot)
  p=$(fb_pid); [ "${p:-0}" -gt 0 ] && [ "$(readlink /proc/"$p"/cwd)" = "$T" ] || { fail "futures-bot MainPID/cwd != link target"; f=1; }
  pinned_check > "$EV/pinned_postboot.txt" || f=1
  [ "$(systemctl is-active futures-bot)" = active ] || { fail "futures-bot not active"; f=1; }
  if [ "$T" = "${NEW:-}" ]; then integrity_ok "$T" "$EV/integrity_postboot.txt" || f=1; else integrity "$T" "$EV/integrity_postboot.txt"; p4_exact "$EV/integrity_postboot.txt" || { fail "postboot OLD integrity is not exactly the one known ✗"; f=1; }; fi
  systemctl list-units --type=service --state=running --no-legend | awk '{print $1}' | sort > "$EV/svc_running_postboot.txt"
  diff "$EV/svc_running_pre.txt" "$EV/svc_running_postboot.txt" > "$EV/svc_diff_postboot.txt" || { fail "running service set changed (review $EV/svc_diff_postboot.txt)"; f=1; }
  pins_snapshot > "$EV/pins_postboot.txt"; diff "$EV/pins_pre.txt" "$EV/pins_postboot.txt" || { fail "pins changed"; f=1; }
  flat_retry "$EV/flat_postboot.txt" || f=1
  systemctl list-timers --all --no-pager > "$EV/timers_postboot.txt"
  journalctl -b -u futures-bot -u options-scanner -u afs-watcher --no-pager -p warning > "$EV/journal_postboot_warn.txt"
  # report only: no -s test. journal_err_clean is not applied here.
  uname -r > "$EV/kernel_postboot.txt"
  [ "$f" -eq 0 ] || { fail "POSTBOOT FAILED -> §10 R-OS"; return 1; }; echo POSTBOOT_OK; }
step_postboot(){ ( set -uo pipefail; state_load || return 1; _postboot ) ; }

# ---------- §7 CUT ----------
_cut(){ gate_window "$CUT_STOP" || return 1; local remote
  [ -n "${OLD:-}" ] || { fail "OLD not in state"; return 1; }
  [ ! -e "$NEW" ] && [ ! -e "$SRC" ] || { fail "NEW/SRC already exist"; return 1; }
  remote=$(git -C "$OLD" remote get-url origin 2>/dev/null || echo https://github.com/imanisworld/autonomous-futures-system.git)
  mkdir "$SRC" && git -C "$SRC" init -q && git -C "$SRC" fetch -q --depth 1 "$remote" "$SHA" && git -C "$SRC" checkout -q --detach FETCH_HEAD || { fail "fetch"; return 1; }
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$SHA" ] || { fail "HEAD != SHA"; return 1; }
  [ "$(git -C "$SRC" rev-parse 'HEAD^{tree}')" = "$TREE_EXPECT" ] || { fail "tree mismatch"; return 1; }; echo TREE_OK
  mkdir "$NEW" && git -C "$SRC" archive "$SHA" | tar -x -C "$NEW" || { fail "archive"; return 1; }
  ( cd "$NEW" && "$(readlink -f "$OLD/.venv/bin/python")" -m venv .venv ) || { fail "venv"; return 1; }   # same interpreter as OLD (3.13.16 per D1 top)
  [ "$("$NEW/.venv/bin/python" -V 2>&1)" = "$("$OLD/.venv/bin/python" -V 2>&1)" ] && [ "$("$NEW/.venv/bin/python" -V 2>&1)" = "Python 3.13.16" ] || { fail "venv python is not Python 3.13.16 / differs from OLD"; return 1; }
  ( cd "$NEW" && .venv/bin/pip install --no-deps -r requirements.lock ) > "$EV/pip_install.txt" 2>&1 || { fail "pip install rc!=0 (see $EV/pip_install.txt)"; return 1; }
  ( cd "$NEW" && .venv/bin/pip check ) > "$EV/pip_check.txt" 2>&1 || { fail "pip check"; return 1; }
  ( cd "$SRC" && env -i PATH=/usr/bin:/bin HOME=/root PYTHONDONTWRITEBYTECODE=1 RELEASE_BRANCH=main \
      "$NEW/.venv/bin/python" -B -m ops.release_manifest --repo-root "$SRC" --output "$NEW/release_manifest.json" ) > "$EV/manifest_new.txt" 2>&1 || { fail "manifest build"; return 1; }
  python3 -B -c 'import json,sys;a=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]));ok=a["fingerprint_sha256"]==b["fingerprint_sha256"] and a["repo"]["commit"]==b["repo"]["commit"];print("FP_MATCH" if ok else "FP_DIFF");sys.exit(0 if ok else 1)' "$OLD/release_manifest.json" "$NEW/release_manifest.json" || { fail "FP_DIFF"; return 1; }
  "$NEW/.venv/bin/pip" freeze | sort > "$EV/freeze_new.txt" && "$OLD/.venv/bin/pip" freeze | sort > "$EV/freeze_old.txt" || { fail "freeze"; return 1; }
  diff "$EV/freeze_old.txt" "$EV/freeze_new.txt" > "$EV/freeze_diff.txt" || { fail "FREEZE differs"; return 1; }; echo FREEZE_SAME
  diff -rq --exclude=.venv --exclude=__pycache__ --exclude=release_manifest.json "$OLD" "$NEW" > "$EV/tree_diff.txt"; [ ! -s "$EV/tree_diff.txt" ] || { fail "TREE differs"; return 1; }; echo TREE_SAME
  [ -z "$(find "$NEW" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) -print)" ] || { fail "pycache in NEW"; return 1; }; echo NO_PYCACHE
  integrity_ok "$NEW" "$EV/integrity_new.txt" || return 1
  chmod -R a-w "$NEW" && state_set CUT_OK 1 && echo CUT_OK; }
step_cut(){ ( set -uo pipefail; state_load || return 1; _cut ) || { echo "CUT FAILED -> run: step_cut_cleanup (R0)"; return 1; } ; }
step_cut_cleanup(){ ( set -uo pipefail; state_load || return 1; safe_rm_new && rm -rf -- "$SRC" && echo R0_DONE ) ; }

# ---------- §8 SWITCH + §9 10-min post-gate, under one lock (N3) ----------
_hold_dump(){
  echo "HOLD unit: $(systemctl show futures-bot -p ActiveState -p SubState -p NRestarts -p MainPID | tr '\n' ' ')"
  journalctl -q -u futures-bot -n 30 --no-pager -o short-iso || true
}
_apt_daily_warn(){ local N NS NOW
  N=$(systemctl show -p NextElapseUSecRealtime --value apt-daily.timer 2>/dev/null || true)
  NOW=$(date +%s)
  NS=$(date -d "$N" +%s 2>/dev/null || echo)
  if [ -n "${NS:-}" ] && [ "$NS" -ge "$NOW" ] && [ $((NS-NOW)) -le 900 ]; then echo "WARNING: apt-daily.timer NEXT $N is within 15 min of rollback"; fi
  return 0; }
# Three active samples across 60s. PID and NRestarts must stay at the post-restart values. Port 8000 must answer.
_stable_verify(){ local want=$1 i pid0 nr0 pid nr
  pid0=$(fb_pid); nr0=$(systemctl show -p NRestarts --value futures-bot)
  i=1
  while [ "$i" -le 3 ]; do
    [ "$(systemctl is-active futures-bot)" = active ] || { fail "stable sample $i not active"; return 1; }
    pid=$(fb_pid); nr=$(systemctl show -p NRestarts --value futures-bot)
    [ "${pid:-0}" -gt 0 ] && [ "$pid" = "$pid0" ] && [ "$nr" = "$nr0" ] || { fail "stable sample $i pid/NRestarts moved (pid ${pid0}->${pid} nrestarts ${nr0}->${nr})"; return 1; }
    curl -fsS -o /dev/null 127.0.0.1:8000/status/broker-account || { fail "stable sample $i port 8000 not answering"; return 1; }
    [ "$i" -lt 3 ] && sleep 30
    i=$((i+1))
  done
  [ "$(readlink /proc/"$(fb_pid)"/cwd)" = "$want" ] || { fail "cwd != $want"; return 1; }
  return 0; }
_switch_back(){
  _apt_daily_warn
  rollback_ok "$ROLLBACK" || { fail "no verified rollback target: not restarting"; state_set LOCK_HOLD 1; echo "HOLD: deploy.lock kept; do not deploy; page Imani/PM"; _hold_dump; return 1; }
  ln -sfn "$ROLLBACK" "$LINK.rb-$STAMP" && mv -Tf "$LINK.rb-$STAMP" "$LINK" && systemctl restart futures-bot || { fail "rollback restart failed"; state_set LOCK_HOLD 1; echo "HOLD: deploy.lock kept; do not deploy; page Imani/PM"; _hold_dump; return 1; }
  sleep 20
  _stable_verify "$ROLLBACK" && flat_retry "$EV/flat_rollback.txt" \
    && pins_snapshot > "$EV/pins_rollback.txt" && diff "$EV/pins_pre.txt" "$EV/pins_rollback.txt" && echo ROLLBACK_VERIFIED && state_set LOCK_HOLD 0 \
    || { fail "ROLLBACK VERIFY FAILED -> R-fail"; state_set LOCK_HOLD 1; echo "HOLD: deploy.lock kept; do not deploy; page Imani/PM"; _hold_dump; return 1; }
  [ "$(et_now)" -lt "$RB_STOP" ] || echo "WARNING: rollback finished at/after RB_STOP $RB_STOP"; }
_postgate(){ local i n0 n p
  n0=$(systemctl show futures-bot -p NRestarts --value)
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [ "$(systemctl is-active futures-bot)" = active ] || { fail "T+$i not active"; return 1; }
    p=$(fb_pid); [ "$(readlink /proc/"$p"/cwd)" = "$NEW" ] || { fail "T+$i cwd != NEW"; return 1; }
    n=$(systemctl show futures-bot -p NRestarts --value); [ "$n" = "$n0" ] || { fail "T+$i NRestarts $n0->$n"; return 1; }
    curl -fsS -o /dev/null 127.0.0.1:8000/status/broker-account 2>/dev/null || [ "$i" -lt 3 ] || { fail "T+$i bot not answering"; return 1; }
    sleep 60; done
  integrity_ok "$NEW" "$EV/integrity_post.txt" || return 1
  pins_snapshot > "$EV/pins_post.txt"; diff "$EV/pins_pre.txt" "$EV/pins_post.txt" || { fail "pins differ"; return 1; }
  sha256sum "$NEW/context/wide_stop_ledger_paper.py" "$NEW/context/wide_stop_execution.py" | awk '{print $1}' | diff "$EV/cap_files_sha_pre.txt" - || { fail "cap files differ"; return 1; }
  flat_retry "$EV/flat_post.txt" || return 1
  local jrc; journalctl -q -u futures-bot --since "@$(stat -c %Y "$SWITCH_MARKER")" --no-pager -p err -o short-iso > "$EV/fb_err_post.txt" 2> "$EV/fb_err_post.stderr"; jrc=$?
  [ "$jrc" -eq 0 ] || { fail "journalctl rc=$jrc (see fb_err_post.stderr)"; return 1; }
  journal_err_clean "$EV/fb_err_post.txt" || { fail "journal errors: $(grep -vxE '[[:space:]]*(-- No entries --)?[[:space:]]*' "$EV/fb_err_post.txt" | head -n 5)"; return 1; }
  echo JOURNAL_CLEAN
  echo POSTGATE_PASS; }
_switch(){ gate_window "$SWITCH_STOP" || return 1
  [ "${CUT_OK:-0}" = 1 ] && [ -d "$NEW" ] && [ -n "${OLD:-}" ] || { fail "cut not OK / state missing"; return 1; }
  [ "$(readlink -f "$LINK")" = "$OLD" ] || { fail "link not at OLD"; return 1; }
  flat_check > "$EV/flat_preswitch.txt" || { fail "not ok/demo/position None/HEALTHY"; return 1; }
  rollback_ok "$ROLLBACK" || { fail "no verified rollback target: STOP, no switch"; return 1; }
  lock_take || return 1
  readlink -f "$LINK" > "$SHARED/current.previous"
  ln -sfn "$NEW" "$LINK.tmp-$STAMP" && mv -Tf "$LINK.tmp-$STAMP" "$LINK" && state_set SWITCHED 1 || { fail "symlink swap"; return 1; }
  sha256sum /etc/systemd/system/futures-bot.service.d/*.conf | diff "$EV/dropins_sha_pre.txt" - > /dev/null || { fail "futures-bot drop-ins changed: STOP, rolling back"; _switch_back; return 1; }
  touch "$SWITCH_MARKER" && systemctl restart futures-bot && sleep 20 || { fail "restart"; _switch_back; return 1; }
  _postgate || { echo "POSTGATE FAILED -> automatic R1 rollback under lock"; _switch_back; return 1; }
  echo SWITCH_DONE; }   # lock released by EXIT trap after the post-gate
step_switch(){ ( set -uo pipefail; state_load || return 1; _switch ) ; }
step_rollback(){ ( set -uo pipefail; state_load || return 1; [ -n "${OLD:-}" ] || { fail "OLD missing in state"; return 1; }
  lock_take || { grep -qx "owner=$LOCK_OWNER" "$SHARED/deploy.lock/meta.txt" && trap lock_release EXIT; } || return 1
  _switch_back ) ; }
# Re-point to NEW under the lock already held by this run. Only after Imani's GO that the post-gate fail was a proven false positive.
# Does not release the lock. lock_release_hold is the only way to drop LOCK_HOLD.
step_reswitch_new(){ ( set -uo pipefail; state_load || return 1
  [ "${RESWITCH_FALSE_POSITIVE:-}" = 1 ] || { fail "RESWITCH_FALSE_POSITIVE=1 required (proven false post-gate only)"; return 1; }
  [ "${CUT_OK:-0}" = 1 ] && [ -d "$NEW" ] || { fail "cut not OK"; return 1; }
  grep -qx "owner=$LOCK_OWNER" "$SHARED/deploy.lock/meta.txt" || { fail "held lock owner mismatch"; return 1; }
  rollback_ok "$NEW" || return 1
  touch "$SWITCH_MARKER" && ln -sfn "$NEW" "$LINK.tmp-$STAMP" && mv -Tf "$LINK.tmp-$STAMP" "$LINK" && systemctl restart futures-bot || { fail "reswitch restart failed"; _hold_dump; return 1; }
  sleep 20
  _stable_verify "$NEW" || { _hold_dump; return 1; }
  echo RESWITCH_DONE ) ; }
lock_release_hold(){ ( set -uo pipefail; state_load || return 1
  [ "${LOCK_HOLD:-0}" = 1 ] || { fail "LOCK_HOLD is not 1"; return 1; }
  grep -qx "owner=$LOCK_OWNER" "$SHARED/deploy.lock/meta.txt" || { fail "lock owner mismatch"; return 1; }
  state_set LOCK_HOLD 0 || return 1
  rm -rf -- "$SHARED/deploy.lock" && echo LOCK_RELEASED ) ; }
```

### Running it
```bash
. /root/afs-maint-lib.sh
state_init                 # first run only; refuses if the state file exists
# D3 record, ONLY after Imani confirmed a/b in the provider console:
#   state_set D3A yes; state_set D3B yes; state_set CONSOLE_TYPE vnc      # graphical/VNC console
#   (serial console instead: state_set CONSOLE_TYPE serial; state_set SERIAL_UNIT 0; state_set SERIAL_SPEED 115200  <- provider's values)
# console_ok_set           # FUTURE WINDOW ONLY. This window: CONSOLE_OK=NO, §6 skipped
step_prechecks             # P1-P10 -> must print PRECHECKS_PASS
# then directly: step_cut  (§7)  ->  step_switch  (§8-9).  No step_apt, no reboot, no step_postboot.
# NOT in the normal order. Only after Imani's GO for a proven false post-gate, with the lock still held:
#   RESWITCH_FALSE_POSITIVE=1 step_reswitch_new && lock_release_hold
```

---

## 4. PRE-CHECKS P1–P9 = `step_prechecks` (read-only)

Each check, its PASS condition, and where the evidence goes in `$EV`:
- **P1:** no `deploy.lock`, and no release/pip process running.
- **P2:** the link target is the pinned `LIVE_EXPECT` (`/root/afs-releases/94a71bdbb975-recut-20261029-210507` after AFS-0454), futures-bot's cwd equals it, and the manifest `repo.commit` is still `$SHA`. It is persisted as `OLD`. `capture_integrity_pins` saves the four integrity pins from the live process, then `rollback_ok "$ROLLBACK"` is a hard gate. Later rollback does not need a MainPID. The literal `$RELEASES/$SHA` directory is the polluted forensic tree and is not the live target.
- **P3:** futures-bot `WorkingDirectory` is the link; the drop-in hashes are snapshotted.
- **P4:** informational only. If the live tree is still the known-polluted `$RELEASES/$SHA` dir, record whether `p4_exact` matches the single stray `.pyc`. It does not authorize that dir as a rollback target. The hard gate is `rollback_ok`.
- **P5:** the pins `LIVE_TRADING_ENABLED=false`, `TRADOVATE_ENV=demo`, `MAX_CONTRACTS_HARD_CAP=1`, `WIDE_STOP_LEDGER_MODE=paper_sim`, `WIDE_STOP_DEMO_EXECUTION_ENABLED=true`, `EXPECTED_LIVE_COMMIT=$SHA`. The cap lines must sit at `wide_stop_ledger_paper.py:86` and `wide_stop_execution.py:35`, and the sha256 of both cap files is saved (N1).
- **P6:** `flat_check` on the D2 keys: `ok is True`, `env=="demo"`, `position` present and None, `reliability` is a dict with `state=="HEALTHY"` and `ready is True`. Anything else, including a missing key or a non-dict reliability, is an ABORT. The endpoint exposes no working-order field (see G4).
- **P2b (MainPID):** MainPID > 0, `/proc/<MainPID>` exists, and its cwd == OLD.
- **P11 (report-only, C3):** `apt_auto_report` covers the apt-daily and apt-daily-upgrade timers, unattended-upgrades enabled/active, and the `Unattended-Upgrade::Automatic-Reboot` / `APT::Periodic` settings. A `FLAG` line is an RK-1 trigger for an Imani/PM decision. **Nothing is disabled by this runbook.**
- **P10 (pins):** `pinned_check`. options-122 → `db9bc7e`, options-scanner → `47ae01a`, six-micro → `4f10d7d` (NOTES.md). They do not move with the futures switch. coverage (`afs-shared/coverage/current`) and the watcher (`afs-shared`) are outside `$RELEASES` and unaffected. The failed-unit baseline is saved.
- **P7:** at least 3 GB free; the apt list and kernels are saved.
- **P8/P9:** the running services, timers, the options-122 env/WorkingDirectory/drop-ins, and a baseline of every pyc under `$RELEASES` with mtimes.

## ███ HARD ABORT GATE ███
`step_prechecks` must print **PRECHECKS_PASS**. Any `GATE_FAIL` line, or a nonzero return, means **STOP and change nothing**. Do not apply the drop-in, take the lock, cut, or switch. Save `$EV` and report to PM and Imani; never fix-and-continue inside the window.

The same function enforces the window: it refuses before Fri 2026-10-30 17:05 ET (`202610301705`) and at or after **17:15 ET (`202610301715`)**, the late-start abort. Switch stop is 17:30 ET. Rollback warns at `RB_STOP` 17:45 ET.

**Run order this window:** `step_prechecks` → (PASS) → `step_cut` → `step_switch`. The cut depends only on state set by the pre-checks (`OLD`) and its own gates; it reads no reboot or post-boot flag. `step_switch` needs `CUT_OK` from `step_cut`.

---

## 5. Writer decision (B8), set from the D1 results before the window

- **Branch A (NOT TAKEN; kept for reference): D1 shows options-122 has no `PYTHONDONTWRITEBYTECODE=1` and no `ProtectHome=read-only`.** Run `step_dropin` first in the window.
  - Approval is PM-reported (Imani, Oct 19 ~21:16 ET), scoped to options-122 only, and Imani confirms it at Step 0.
  - No restart and no manual start; it takes effect at the next timer fire.
  - It refuses if the env var is already present, if the conf exists, or if a run is in progress.
  - It saves the env before and after, and the diff, to `$EV` (N4).
  - Rollback: `step_dropin_rollback`, output saved.
  - Before the cut, if the timer fires inside the window: `step_pyc_since "$DROPIN_MARKER"` must print `NO_NEW_PYC`. It scans **all of `$RELEASES`**, not only the link target. The old stray file stays. New output means the drop-in did not fix the writer: run `step_dropin_rollback`, **do not cut**, and report. If there is no fire before the cut, record "pre-cut check NOT RUN".
- **Branch B: CONFIRMED by D1 (NOTES.md). §5A SKIPPED; AFS-0266 whitelist KEPT.** D1 shows the env var present (expected, matching the repo `ops/systemd/options-122-prospective.service:10`, with `ProtectHome=read-only` at :16).** **§5A is a no-op and is not applied.** Do §5B instead.
- **§5B Investigation (read-only; can and should run pre-window via D1/D1b):**
  1. Compare the stray `.pyc` mtime with the options-122 journal and timer LAST runs, and with SSH/sudo/interactive sessions in that minute (D1b).
  2. Check the D1 unit list for any unit that runs from a release path without the var.
  3. Record the conclusion as `writer = <unit|manual|unknown>`.

  The polluted tree stays on disk. Do not delete the stray `.pyc` in place. The AFS-0266 whitelist **stays** until both §9 pycache checks are clean over at least one weekday options-122 run. It does not make a restart of that tree succeed. Fix any identified writer as its own approved change.

Follow-up candidates only (no change now, each needs its own approval). These are the Python units in the repo templates:
- `ops/systemd/`: `afs-paper-collection-eod.service`, `afs-paper-collection-eow.service`. `options-setup-capture` was not found on the box (NOTES.md).
- `ops/systemd` (bda553b and later): `afs-coverage-collector`, `afs-wide-stop-demo-eod-fallback` (on the LINK). `afs-day-only-exit` and `afs-external-heartbeat` were not found on the box.
- box-only: `options-scanner`, `six-micro-daily-tsmom`, `afs-forward-lanes-daily-proof`, `afs-watcher`

## 6. OS step: SKIPPED THIS WINDOW (CONSOLE_OK=NO, operator 2026-10-19). Kept for a future console-tested window; not in the run order

The interpreter hold below is **moot this window**, since no apt runs. The Python 3.13.16 facts still drive the cut's venv check (§7).

Order rationale:
- The reboot lands on the known-good old release, so OS faults are not mixed up with tree faults.
- The integrity FAIL is whitelisted (AFS-0266).
- The market is closed.

**Interpreter hold (N6), corrected again:** the OLD venv runs **Python 3.13.16** (D1 top). The stray file is `cpython-313`, so this interpreter matters twice.
- `_apt` derives X.Y = 3.13 from `$OLD/.venv/bin/python` and holds only the installed `python3.13*`/`libpython3.13*` packages, **and only if `dpkg -S` owns the resolved binary** (expected `/usr/bin/python3.13`). If it is a non-apt build, apt cannot change it, so no hold is applied and that is logged.
- Whether the venv base is apt-managed is PENDING (the small D1 add-on). The code handles both cases.
- The recut venv is built with that same interpreter and must report exactly `Python 3.13.16`.
- Reason: the old venv (the rollback target) and the new one share the interpreter. Upgrading it in the same window would add a second variable.

**D-4 decision: option (i). Accept 7.0.0-38, with a visible GRUB menu and 7.0.0-31 kept as the console fallback.**

Why (i) over (ii), pinning 31 and deferring 38:
1. 38 is already installed and is entry 0. **Any** reboot boots it, including an unplanned one during a trading week. Deferring only moves that risk to an unattended moment.
2. Taking it now, in a market-closed window with Imani on the console, is the safest time.
3. Option (ii) also edits GRUB, and a wrong entry id silently falls back to 38 anyway.
4. A "System restart required" flag is pending (AFS-0275), and the kernel is the likely cause.

Mechanics:
- The kernel meta packages are held, so no third kernel arrives in the upgrade.
- `grub_prep` writes `/etc/default/grub.d/99-afs-maint-menu.cfg` (`GRUB_TIMEOUT_STYLE=menu`, `GRUB_TIMEOUT=5`, `GRUB_RECORDFAIL_TIMEOUT=5`), runs `update-grub`, and verifies `timeout=5` and both kernels in `grub.cfg`. It saves the 31 entry id.
- Backups go to `$EV`.

**Hard prerequisite:** D3 a/b both "yes", so `state_set CONSOLE_OK 1`. If not, `step_apt` refuses. Then **skip §6 entirely (no apt, no reboot)** and do the recut only. Record that 38 remains pending, and that any reboot boots it.

During the reboot, Imani watches the console. Rollback:
1. If 38 fails to boot or `step_postboot` fails on kernel symptoms, pick *Advanced options → 7.0.0-31* in the 5 s menu.
2. Once it is up, run `. /root/afs-maint-lib.sh && state_load && grub_pin31` to make 31 the default and defer 38, and keep the kernel holds.
3. To undo the GRUB changes later, run `grub_restore`.

Keeping the menu visible after a successful boot is harmless (5 s). Restore it after Monday as a follow-up.
```bash
# FUTURE WINDOW ONLY. This window step_apt refuses unconditionally ("SKIPPED: CONSOLE_OK=NO (operator 2026-10-19)")
. /root/afs-maint-lib.sh && state_load
step_apt && echo "reboot at $(et)" >> "$EV/log.txt" && systemctl reboot
# --- after reboot, new shell ---
. /root/afs-maint-lib.sh && state_load     # B4: STAMP/EV/OLD/NEW/markers come from /root/afs-maint-state.env
step_postboot                              # must print POSTBOOT_OK, else §10 R-OS
```
After boot, also confirm by hand that every options/afs timer shows a NEXT in `$EV/timers_postboot.txt`, and that `pinned_postboot.txt` shows PIN_OK for all three pinned units. The first options-scanner cycle runs Mon in market hours, so its fresh write is checked Monday (G5).

## 7. CUT = `step_cut`

Why not `scripts/atomic_release.sh`: it refuses an existing `$RELEASES/<sha>`, clears that SHA's marker, and rewrites pins and drop-ins on promote. The manual cut is a deviation and needs D-2 acceptance.

The cut mirrors its build steps. Every gate below is hard (B3):
- window and `CUT_STOP` (Fri 2026-10-30 17:25 ET)
- `TREE_OK` (HEAD and tree checked against GitHub)
- the manifest rebuilt from the cwd `$SRC` (B6) and `FP_MATCH`, else a nonzero return
- venv created; `pip install --no-deps -r requirements.lock` **without `--require-hashes`**, since the lock is version-only (`requirements.lock:6`), with its exit code checked directly (B7)
- `pip check`, then `FREEZE_SAME` against the old venv
- `TREE_SAME` (diff of the full tree minus `.venv`/`__pycache__`), then `NO_PYCACHE`
- **`integrity_ok` on NEW**, which requires a zero exit code and no problem lines (B3)
- `chmod -R a-w`

On failure, run `step_cut_cleanup` (R0). It removes `$NEW` only through `safe_rm_new`: the path must be exactly `$RELEASES/94a71bdbb975-recut-$STAMP` with a non-empty STAMP, must not be the live target, and must not be OLD (N8). It also removes `$SRC`. Nothing has been switched at that point.

## 8–9. SWITCH, RESTART, 10-minute POST-GATE = `step_switch`

1. Window and `SWITCH_STOP` (**Fri 2026-10-30 17:30 ET**), `CUT_OK`, link at OLD, flat, and `rollback_ok` on `$ROLLBACK` before the lock is taken. That check uses the precheck pin file, so it still passes if MainPID is 0.
2. **Lock taken**, with `trap lock_release EXIT`.
3. `current.previous` written, then an atomic `ln -sfn` + `mv -T`.
4. The futures-bot drop-in hashes must be unchanged, so no `daemon-reload`.
5. The switch marker is touched **before** `systemctl restart futures-bot`, so startup errors fall inside the post-gate `--since` window.
6. The 10 × 60 s post-gate: active, cwd == NEW, NRestarts unchanged, endpoint answering. Then integrity OK, pins equal, cap-file sha256 equal, flat, and `JOURNAL_CLEAN` (empty journalctl output, or only `-- No entries --`, with `journalctl` rc 0).
7. Any failure **automatically rolls back to `$ROLLBACK` under the same lock** (the verified clean tree, not the polluted full-SHA directory).
8. The lock is released by the trap **only after** the gate (N3). The prompt stays alive either way.

options-scanner and afs-watcher keep running on their old cwd. The content is identical; note it in the record. Timers resolve the link at their next run, unless D1 showed a unit pinned to a release dir (`10-release.conf.template`). Such a unit keeps running from OLD; record which ones.

**Pycache check after the switch** (after the next options-122 fire, often Mon; read-only):
```bash
. /root/afs-maint-lib.sh && state_load && step_pyc_since "$SWITCH_MARKER"
find "$NEW" -mindepth 1 \( -name .venv -prune \) -o \( -name __pycache__ -o -name '*.pyc' \) -print   # expect nothing
```
- If `NO_PYCACHE` holds in NEW and `NO_NEW_PYC` across `$RELEASES` after one or more options-122 runs (`ExecMainExitTimestamp` after the marker, `Result=success`), Ops recommends that the **AFS-0266 whitelist owner** remove the whitelist. That is the owner's action.
- Otherwise keep the whitelist, name the writer from the path, and propose a separate fix. Never delete inside a release.
- A second re-cut is only allowed inside a new window that respects HARD STOP 1 (N7).

## 10. ROLLBACK

- **R0, cut failed:** `step_cut_cleanup`. The system is unchanged.
- **R1, the switched release is bad** (automatic inside `step_switch`, or manual at any time):
  ```bash
  . /root/afs-maint-lib.sh && state_load && step_rollback   # link -> $ROLLBACK (verified clean tree), restart, stable pid/NRestarts/port check
  ```
  It is never blocked by the clock. It warns if it finishes at or after `RB_STOP` (Fri 2026-10-30 17:45 ET). Keep `$NEW` for diagnosis. Do not roll back onto the polluted `94a71bd` directory: a restart there refuses on the stray `.pyc`. The AFS-0266 whitelist does not make that startup succeed. Before `_switch_back` restarts, `rollback_ok` runs again. It uses the pins saved at precheck, or the systemd unit Environment, and it does not need a MainPID, so a crash loop does not block the rollback. If `rollback_ok` fails, the bot is left where it is, `LOCK_HOLD=1`, and the HOLD lines include `systemctl show` plus `journalctl -q -n 30`. On HOLD with a crash-looping unit, do not restart again. `step_reswitch_new` uses the same pin path, so it can repoint at NEW while MainPID is 0.
- **§5A:** skipped (Branch B), so there is nothing to roll back.
- **R-OS, reboot/upgrade fault: SKIPPED THIS WINDOW** (no apt, no planned reboot). Kept for the future window. For an *unplanned* reboot, see RK-1 in §13.
  1. Try one `systemctl restart <unit>` and read `journalctl -b -u <unit>`.
  2. For a kernel fault, use the console GRUB menu (visible for 5 s, per `grub_prep`): Advanced options → 7.0.0-31. Then run `grub_pin31`. Undo later with `grub_restore`.
  3. For a package fault, `apt-get install <pkg>=<old>` from `/var/log/apt/history.log`.
- **R-fail, rollback verify fails:** stop changing things. The HOLD message already prints unit state and the last 30 journal lines. Do not restart again. Send `$EV` to Imani and PM. A false post-gate has a documented path: `RESWITCH_FALSE_POSITIVE=1 step_reswitch_new`, then `lock_release_hold`, only with Imani's GO. The Mon 09:30 Track B window must not run on an unverified tree.

## 10b. Time estimate (recut only; OS phases removed)

| Phase | Nominal | Notes |
|---|---|---|
| Step 0 GO + state_init + pre-checks + HARD ABORT gate | 15 min | |
| §5A drop-in / §6 apt+GRUB+reboot | 0 | skipped (Branch B / CONSOLE_OK=NO) |
| Cut: fetch, archive, venv, pip, check, manifest, diffs, integrity | 25 min | PyPI reachable (D1) |
| Switch + restart + 10-min post-gate | 15 min | |
| QA verification + record | 15 min | |
| **Subtotal** | **1 h 10 min** | |
| Contingency (+50%) | 35 min | |
| Rollback reserve (R1) | 15 min | R1 ~5 min plus verification |
| **Total worst case** | **~2 h** | |

- v11's clock is Fri 2026-10-30 17:05 ET through switch stop 17:30 ET, with `RB_STOP` at 17:45 ET so a late rollback does not land on the 17:50 apt-daily slot. The nominal cut-plus-switch does not fit inside that short window if the cut takes the historical ~25 min. The gates fail safe: a cut that cannot start `step_switch` before 17:30 does not switch. Do not stretch the gates to make it fit.
- `_switch_back` prints a read-only warning when `apt-daily.timer` is within 15 minutes.

## 11. What Imani does
- Before Fri: send the two small D1 add-ons (venv base `readlink -f`+`dpkg -S`; futures-bot Environment), and run D1b. D2 is done. D3 is not needed this window (CONSOLE_OK=NO); test the console before any future OS window.
- Paste the Step 0 GO line.
- Be reachable from Fri 2026-10-30 17:05 ET until 17:45 ET if this window is the one she authorizes. This file is not that authorization.
- Mon 09:25 ET: run `flat_check` once.
- After the first Mon options-122 run: run the §9 pycache check.

## 12. QA verification (read-only, after step_switch)
- `$EV` holds: `PRECHECKS_PASS`, `TREE_OK`, `FP_MATCH`, `FREEZE_SAME`, `TREE_SAME`, `NO_PYCACHE`, `INTEGRITY_OK` ×2, `JOURNAL_CLEAN`, `POSTGATE_PASS`, `LOCK_RELEASED`. `ROLLBACK_TARGET_OK` is printed by the pre-switch `rollback_ok`.
- `readlink -f $LINK` == NEW == futures-bot cwd; no `deploy.lock`.
- `pins_pre` == `pins_post`, and the cap-file sha256 is unchanged.
- The state file is mode 600 and holds OLD, NEW, STAMP, the markers, and DROPIN_*.
- No apt, GRUB, or reboot activity in the window: `/var/log/apt/history.log` has no window entries, and `uname -r` is still 7.0.0-31.

## 13. Decisions needed / open gaps
Decisions:
- **D-1:** recut 94a71bd (recommended).
- **D-2:** CLOSED. The manual cut (§7) is accepted (AFS-0300).
- **D-3:** settled. Branch B confirmed: §5A skipped, AFS-0266 whitelist kept, §5B/D1b investigation open.
- **D-4:** **SKIPPED THIS WINDOW / DEFERRED** (CONSOLE_OK=NO). Option (i) stays the recommendation for a future console-tested window.
- **D-5:** MOOT this window, since no apt runs. For the future OS window: hold `python3.13*`/`libpython3.13*` if apt-managed (venv = 3.13.16).

Gaps:
- **G1:** CLOSED (D-2 accepted, AFS-0300).
- **G13:** CLOSED. All 74 manifest runtime overrides are unset (D1 top), so building the manifest under `env -i` needs no pass-through.
- **G3:** DEFERRED with the OS step (see RK-1).
- **G4:** the integrity strings are now used exactly (`release_integrity.py:377-388`; PASS reads `OK`, not "PASS", per :322). The `/status` keys are mapped from D2 (`ok`/`env`/`position`/`reliability`). **The endpoint exposes no working-orders field**, so "no working orders" rests on `ok`+`position None` and the PM's Track B flat statement. The top of D1 is DONE (21:49 ET).
- **G5:** the options-122 timer may not fire on the weekend, so the pycache checks and the options-scanner fresh write move to Monday. The whitelist stays until then.
- **G6/G7:** CLOSED by D1: `git ls-remote` returned 1747 lines and PyPI returned HTTP/2 200. The pip lock has no hashes, mitigated by `FREEZE_SAME`.
- **G8:** the writer is UNKNOWN. It was a Python 3.13 at 10:58:13 ET on Oct 11: system python3.13 **or the 94a71bd venv's own interpreter** (H1–H4, §2). D1b decides which.
- **G11:** `afs-forward-lanes-daily-proof` last exited with status=1 (a known retired-lane issue). It is in the failed baseline, so post-boot only flags *new* failures.
- **G12:** superseded by RK-1.

**OPEN / DEFERRED risk RK-1: unplanned reboot onto an untested kernel**
- The risk:
  - 7.0.0-38 is installed but has never been booted, and it is the GRUB default (entry 0, menu hidden, timeout 0). **Any unplanned reboot lands on 38.**
  - Console access is untested, so there is no in-band kernel fallback.
  - The 14 pending apt updates are deferred.
- Mitigation:
  - **No reboot until a console-tested window.** No apt this window; `step_apt` refuses.
  - If an unplanned reboot happens, run the post-boot checks and report to Imani/PM: services, failed units, pins, flat, integrity, and the kernel booted.
    - With a state file: `. /root/afs-maint-lib.sh && state_load && step_postboot`. It accepts the link at OLD or NEW.
    - Without one, read-only:
```bash
( . /root/afs-maint-lib.sh; uname -r; systemctl is-system-running; systemctl --failed --no-legend
  T=$(readlink -f "$LINK"); echo "link=$T"; p=$(fb_pid); echo "MainPID=$p cwd=$(readlink /proc/$p/cwd 2>/dev/null)"
  pinned_check; flat_retry /tmp/afs_flat_unplanned.txt && cat /tmp/afs_flat_unplanned.txt
  integrity "$T" /tmp/afs_integrity_unplanned.txt; cat /tmp/afs_integrity_unplanned.txt
  case "$T" in "$RELEASES/$SHA") p4_exact /tmp/afs_integrity_unplanned.txt && echo "OLD_INTEGRITY_AS_KNOWN" || echo "FAIL: OLD integrity is not exactly the one known ✗";;
    *) grep -qE '^release integrity: OK — [0-9]+ files checked' /tmp/afs_integrity_unplanned.txt && [ "$(grep -c '✗' /tmp/afs_integrity_unplanned.txt)" -eq 0 ] && echo INTEGRITY_OK || echo "FAIL: NEW integrity not OK";; esac
  apt_auto_report
  systemctl list-timers --all --no-pager | grep -E 'options|afs-|six-micro' )
```
- Owner: Imani/PM, who schedule the console test and the OS window.
- **G9:** `release-complete` and `release_history` are deliberately untouched; `current.previous` is written.
- **G10:** CLOSED for the empty-journal false fail (AFS-0454). `journal_err_clean` plus `journalctl -q` and a non-zero `journalctl` rc check. A real error line still fails the post-gate. Benign startup errors, if any, still need a separate ruling before they are allowlisted.
