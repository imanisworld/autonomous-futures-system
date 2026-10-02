# Futures runtime reconciliation — 2026-09-30

Read-only re-verification plus one reporting-file install. This does not
change strategy status. `docs/strategy-rules/Strategy_Inventory.md` still wins
if anything here disagrees with it.

Posture: paper, shadow, and guarded Tradovate DEMO only. Live execution is
not approved. A market printed as zero evidence is not an execution market.

## Repository

The watcher fix is on `main` at `07ceaf465cf93dd29e171d1e5292de6fdf7e06d7`
(PR #1078). The docs note before this closeout is
`dfe97910102bc08041a8aa93e0f23b1e27c6c81e` (PR #1079). #1068 remains
`715e03956f402fd553107a84125537768841d97c`. The three #1068 reporting files
on that merge are byte-identical to that `main`.

## Auth

The stale `Authorization` bearer on fallback login was the proven cause.
The repair merged through #1076. The running curated release contains that
reviewed repair byte-for-byte. Tradovate auth was `HEALTHY` on the re-read
after the reporting change. No new login was forced.

## Broker

Tradovate environment is DEMO. The pinned account read succeeded. The
broker-account snapshot showed no open position, open P&L 0, and realized
P&L 0. An earlier read of `/order/list` on the existing session returned
HTTP 200 with zero rows and zero working orders. This reporting pass did not
call `/order/list` again and did not place, cancel, or flatten anything.

The journal figure `-$59.75` is the cumulative sum of old journal `OUTCOME`
rows. It is not today's broker P&L. The last nonzero outcome day in that
sum was 2026-07-16.

## Watcher

`BLOCKED` downgraded to `WARN` was being treated as cleared, which sent a
false Resolved card while Tradovate was still `ACTION_REQUIRED`. The
watcher-only clear rule is installed in the persistent watcher source and
was already running before this reporting pass. PR #1078 merged that same
behavior onto `main`. The watcher was not restarted for #1068. Futures-bot
was not restarted for the watcher fix.

## Trading process

Before and after the shadow-report file replace:

- futures-bot MainPID `791194`
- `NRestarts=0`
- active since `2026-09-30 00:35:54 UTC`
- cwd and `/root/autonomous-futures-system` both resolve to
  `/root/afs-releases/75f10e4540aa1f25b51b77c1ec2a2da40381a188`
- `LIVE_TRADING_ENABLED=false`
- `TRADOVATE_ENV=demo`
- watcher supervisor still the process started `2026-09-30 02:35:16 UTC`

## #1068 reporting surfaces

### Paper collection — derived six-market backport installed

The full #1068 reporter was not installed. It imports `notifications.plain_english`
and `REGISTRY_HEADER`, which are outside the proven four-file pin. The active
pin is a derived minimal backport of the six-market display onto the #899
reporter. It is not a pure `715e039` tree.

Re-read 2026-09-30 after the flip:

- active pin `releases/b60931a6a9f8-reporter-1068-sixmarket-backport`
- previous pin `releases/b60931a6a9f8-reporter-6512dc3578e9`
- base reporter source `6512dc3578e9de59d8d6a8ceea8e78a39b9cb8e4`
- #1068 reference `715e03956f402fd553107a84125537768841d97c`
- changed path `scripts/paper_collection_report.py` only
- installed reporter sha256
  `e0382c38b1fd8b39da31066179ad4dc363469690faf50555eec812a6f0742771`
- previous reporter sha256
  `d9c9c771926db95e6e1fae72d4cced2a12907d2ac82aaeb40310cdb287620ff0`
- manifest sha256
  `4509508e001363017ee0c4dfa37037c4d89eddebe55a92b77426863b843b980b`
- rollback pin `releases/b60931a6a9f8-reporter-6512dc3578e9`
- history line `2026-09-30T03:41:24Z`

A frozen copy of the 2026-09-30 logs was read by the old and new reporters
with `/usr/bin/python3` and `--no-discord`. Both exited 0. Structured
artifacts matched except generation timestamps. Futures rows stayed 104,
MES 47 and MNQ 57. Row types, outcomes, decisions, strategies, lanes,
options, and collector status matched. The only report text change was the
futures market line, to `By market: MNQ **57** · MES **47** · M2K **0** ·
MBT **0** · MCL **0** · MGC **0**`.

One futures Discord post returned HTTP 200. The stored card contained that
same line. EOD and EOW units still use
`WorkingDirectory=/root/afs-shared/paper_collection/current`. Timer and unit
hashes did not change. Futures-bot stayed PID `791194` / `NRestarts=0` on
`75f10e4540aa1f25b51b77c1ec2a2da40381a188`. The watcher stayed PID `874999`.
A later re-read the same night still showed those process identities.

### Gate condition — deferred until the next sanctioned futures release

Final disposition: **DEFERRED UNTIL NEXT SANCTIONED FUTURES RELEASE**.

Cron runs `python -m ops.gate_condition_report` with `PYTHONPATH=.` from
`/root/autonomous-futures-system`, which is the immutable live release
`75f10e4540aa1f25b51b77c1ec2a2da40381a188`. There is no gate-condition
systemd unit. The loose file `/root/afs-shared/gate_condition_report.py`
is not the cron target. Its sha256 is
`b3a523aca69cb7e745943aaca0c20ac399a54b966432d3620c4c01ebe83f947a`. The
live module sha256 is
`0b58b2260a17482d7ffef6267cf09af5521971c891a6a1bc9bfac35c7b1932dd`. The
#1068 file sha256 is
`3643571f853c34617d4fab5b2bd2e9a594dc9ef9e4e4bf35e949ead4981d2676`.

No already-sanctioned reporting path can adopt that file without editing
the trading release or changing the scheduler. A new cron, a rewritten cron
command, or an in-place release edit was not created. This is not an urgent
defect. No runtime mutation was made in this review.

### Shadow daily P&L — installed

Cron runs `/root/afs-shared/shadow_daily_pnl_report.py` on weekdays at
22:10. That path is outside the trading release.

- previous sha256
  `d565146b780862b306f6de95b3f57b2ade91823a925e3b30d748a152e1864a94`
- installed sha256
  `557407fd1cd56d4a9a0c9865e7ab00acb070a0f0eb429f0cdb0ad20be9bf2267`
- source `715e03956f402fd553107a84125537768841d97c:ops/shadow_daily_pnl_report.py`
- backup `/root/afs-shared/shadow_daily_pnl_report.py.bak-20260930T031014Z`

The same captured journals for 2026-09-28, 2026-09-29, and 2026-09-30 were
compared for day 2026-09-29 with `--json` and no Discord. Both runs had 201
candidates. Closed, wins, losses, open, no-fill, gross, and net dollars
matched (`164 / 46 / 118 / 18 / 19 / -2247.61 / -2640.58`). Per-strategy
dollars matched. The new report added M2K, MBT, MCL, and MGC as explicit
zeros. MNQ and MES still carried the existing rows.

One controlled Discord post of that same day returned `discord: ok` and
showed MNQ, MES, M2K, MBT, MCL, and MGC, with the four empty markets labeled
as no shadow outcomes. The crontab line was not changed. Futures-bot was not
restarted.

## Access re-read — 2026-09-30

Read only. Nothing in SSH, sudo, firewall, or Tailscale was changed.

Proven on this re-read:

- public listeners on port 22
- `PasswordAuthentication yes`
- `PermitRootLogin prohibit-password` (root SSH password login prohibited)
- Tailscale absent
- password-locked non-root accounts include `grok-audit`, `grok-options-audit`, `grok-qa-audit`, and `claude-audit`
- all four `grok-audit` authorized-key lines use `command=` and `restrict`
- `claude-audit` shell is `/bin/bash`; no sudoers entry names that account
- live release contains `scripts/deploy_lock.sh`; `deploy.lock` was absent while idle
- rollback target file `/root/afs-shared/current.previous` points at `/root/afs-releases/41ae1881655c-20260924-124607`, and that directory exists

Preserved from the access audit, not re-tested tonight:

- MacBook current root-key login
- `claude-audit` cannot read the protected AFS tree

Requires the operator:

- phone SSH login
- Hetzner/provider console recovery

Not authorized yet: disabling password authentication, root-key cleanup, converting `claude-audit` to a forced command, restricting public SSH, or removing current access. SSH is not safe to harden until the phone path and provider-console recovery are proven.

Ready to test in a later operator window, not run tonight: candidate build/verify, and a rollback drill.

## Still open

- phone SSH login
- provider-console recovery
- candidate build/verify and rollback drill, in an operator window
- SSH hardening only after those proofs
- #1037 governance remains a separate HOLD; secret-discovery stays closed
- #994 research remains WAIT
- gate-condition #1068 runtime surface, deferred as described above
