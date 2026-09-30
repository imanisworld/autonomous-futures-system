# Futures runtime reconciliation — 2026-09-30

Read-only re-verification plus one reporting-file install. This does not
change strategy status. `docs/strategy-rules/Strategy_Inventory.md` still wins
if anything here disagrees with it.

Posture: paper, shadow, and guarded Tradovate DEMO only. Live execution is
not approved. A market printed as zero evidence is not an execution market.

## Repository

`main` is `07ceaf465cf93dd29e171d1e5292de6fdf7e06d7`, the merge of PR #1078.
#1068 remains `715e03956f402fd553107a84125537768841d97c`. The three #1068
reporting files on that merge are byte-identical to this `main`.

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

### Paper collection — not flipped

`/root/afs-shared/paper_collection/current` still points at
`releases/b60931a6a9f8-reporter-6512dc3578e9`.

That pin is the documented #899 curated overlay:

- base `b60931a6a9f8839ab9bbadb88eb6c5cb93cccd28`
- overlay `6512dc3578e9de59d8d6a8ceea8e78a39b9cb8e4`
- overlay file `scripts/paper_collection_report.py`
- reporter sha256
  `d9c9c771926db95e6e1fae72d4cced2a12907d2ac82aaeb40310cdb287620ff0`

`MANIFEST.sha256` matched the four pinned files. EOD and EOW units still use
`WorkingDirectory=/root/afs-shared/paper_collection/current`. Their timers
and unit files were not changed.

A scratch clone received only the exact #1068 reporter
(`f364d275726150bf38f9fbe244244e9dc1d3dc536f222390b83f4360d9f12e1a`). The
pinned `ops/__init__.py`, `ops/collector_census.py`, and
`ops/evidence_registry.py` stayed identical to the current pin. Import
failed: the unit's `/usr/bin/python3` has no `notifications` package, and
the pinned registry does not export `REGISTRY_HEADER`. The symlink was not
flipped. The prior pin remains the rollback target if a later overlay is
activated. This is source-updated by #1068, with no runtime repin.

### Gate condition — not installed as its own copy

Cron runs `python -m ops.gate_condition_report` from the live futures
release. There is no gate-condition systemd unit. A loose
`/root/afs-shared/gate_condition_report.py` exists and is not what cron
executes. Its sha256 is
`b3a523aca69cb7e745943aaca0c20ac399a54b966432d3620c4c01ebe83f947a`. The live
release file sha256 is
`0b58b2260a17482d7ffef6267cf09af5521971c891a6a1bc9bfac35c7b1932dd`. The
#1068 file sha256 is
`3643571f853c34617d4fab5b2bd2e9a594dc9ef9e4e4bf35e949ead4981d2676`.

Editing the live release would change the trading tree. Creating a new cron
or unit was not done. Source updated by #1068; no runtime repin.

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

## Still open

- map current access
- MacBook replacement operator path
- phone path
- constrained agent access proof
- provider-console break-glass proof
- old-access lockdown after replacement proof
- broader #1037 governance
- #994 research remains WAIT
- paper-collection and gate-condition #1068 runtime surfaces, as described above
