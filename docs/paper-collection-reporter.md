# Paper-collection reporter (EOD / EOW rollups) — read-only, pinned oneshot + timers

`scripts/paper_collection_report.py` is the daily/weekly evidence rollup for the
futures paper lanes and the options paper lanes. It reads what the existing
collectors have already written (journal rows, shadow outcomes, the options
scanner database, the coverage collector ledger, the collector census) and
posts one card per family to two optional Discord routes:

- `DISCORD_ROUTE_PAPER_COLLECTION_FUTURES`
- `DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS`

It never collects market data, never touches strategy, broker or execution
state, never promotes anything, and never modifies the evidence it reads. Its
only write is the JSON artifact `paper_collection_<period>_<date>.json` in the
log directory. Missing or unreadable inputs are printed explicitly, not
inferred as zero.

Lineage: #603 (EOD/EOW rollups) → #604 (real journal row shapes, session-bound
collectors judged at the close, honest status labels) → #608 (readable Discord
cards) → #609 (EOW master evidence registry) → #610 (retired the obsolete
`overnight watch` census registration that produced a permanent false DEAD).
#602 and #605 were competing drafts, closed unmerged.

```
python -m scripts.paper_collection_report --period eod
python -m scripts.paper_collection_report --period eow
python -m scripts.paper_collection_report --period eod --no-discord   # build artifacts only
```

## Schedule

`ops/systemd/afs-paper-collection-eod.timer` fires **Mon–Fri 17:10
America/New_York**; `afs-paper-collection-eow.timer` fires **Fri 17:20**.
Both are `Persistent=true`, so a missed firing runs at the next boot.

17:10 ET sits 25 minutes after the options coverage collector's 16:45 ET
firing (`deploy/systemd/afs-coverage-collector.timer`). Every completed
collector run on record finished in under 10 s (a five-session catch-up batch
in 45 s; failed runs fail fast, ~20 s), so the margin is on the order of 70×.
The reporter does not wait on the collector: if the collector has not
finished, the card says so through the coverage ledger status instead of
guessing.

## How it is installed: a pinned copy, not the trading release

The unit files in `ops/systemd/` describe the *intended* contract (working
directory = the repo, `.venv` python). On the box the two `.service` files are
installed with a different `WorkingDirectory` and interpreter on purpose:

```
WorkingDirectory=/root/afs-shared/paper_collection/current
ExecStart=/usr/bin/python3 -m scripts.paper_collection_report --period eod ...
```

`current` is a symlink to `releases/<full-commit-sha>/`, a read-only copy of
exactly four files from one commit of `main`:

```
scripts/paper_collection_report.py
ops/__init__.py
ops/collector_census.py
ops/evidence_registry.py
MANIFEST.sha256     # sha256 of the four files
PIN_INFO.txt        # source_commit, source_repo, pinned_at, interpreter, purpose
```

with `release_history.txt` beside `releases/` recording `<sha> <utc>
<release-dir>` for every pin. The timers are byte-identical to the repo's.

Why: the reporter landed on `main` after the currently deployed trading
release, and the trading service is under a no-restart hold. Running the
reporter out of the deployed release would either fail (the script is not in
that release) or require a trading-service release/restart to ship a
read-only report. The pinned copy decouples the two: the reporter can follow
`main` while the trading release stays frozen, and nothing the reporter does
can alter the release directory. Retire the pinned copy at the next sanctioned
trading release, when the units can point back at the repo.

The same pattern is used for the options coverage collector
(`docs/options-coverage-observer.md`, "After-close collector").

## Re-pinning to a newer commit (no service restart)

1. Export the four files from the target commit: `git archive <sha> scripts/paper_collection_report.py ops/__init__.py ops/collector_census.py ops/evidence_registry.py`.
2. Create `releases/<full-sha>/`, write `MANIFEST.sha256` and `PIN_INFO.txt`, `chmod -R a-w`, and check `sha256sum -c MANIFEST.sha256` against hashes computed from `git show <sha>:<path>` locally.
3. **Smoke before flipping** (see below) from the new directory and, for a
   behaviour change, from the old one too — the diff between the two outputs
   should be exactly the change the commit claims.
4. Flip atomically: `ln -sfn releases/<full-sha> current.tmp && mv -T current.tmp current`; append `release_history.txt`.
5. Confirm `systemctl show afs-paper-collection-eod.service -p WorkingDirectory`
   still resolves through `current`, `systemctl list-timers` is unchanged, and
   the trading service's `MainPID` / `NRestarts` did not move.

Oneshot units read the working directory at each firing, so the flip takes
effect at the next timer without `daemon-reload` or any restart.

## Smoking the reporter without side effects

The report writes its artifact into `--log-dir`, and the real log directory
already holds today's artifact. To exercise the real inputs without posting
or overwriting anything:

```
SM=$(mktemp -d) && mkdir "$SM/logs" && cp -rs /root/afs-shared/logs/. "$SM/logs/"
find "$SM/logs" -maxdepth 1 -name 'paper_collection_*.json' -type l -delete   # never write through a symlink
cd /root/afs-shared/paper_collection/current && set -a && . /root/afs-shared/.env && set +a
/usr/bin/python3 -m scripts.paper_collection_report --period eod --no-discord \
    --log-dir "$SM/logs" --options-db /root/afs-shared/logs/options_scanner.sqlite
```

`--no-discord` skips both webhooks; the artifact lands in the scratch
directory; the real journals, database and coverage ledger are only read.
Collector freshness is judged against the wall clock, so a smoke run outside
the scheduled window (for example during the 17:00–18:00 ET futures break)
will legitimately show bars and journals as stale that the 17:10 ET run
reports fresh.

## Status

Installed and enabled on the box 2026-09-16; first unattended EOD firing
2026-09-16 17:10 ET posted both cards. Pinned copy re-pinned the same evening
to `ce9df48` (#610), verified by an old-vs-new smoke whose only difference was
the retired `overnight watch` DEAD line. Ruled operationally complete: leave
the units, timers and pinned copy alone unless a report itself exposes a
defect.
