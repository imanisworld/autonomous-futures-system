# Paper Collection EOD / EOW Rollups

Purpose: keep one read-only inventory of what the futures and options evidence collectors actually wrote, without creating another strategy collector.

## Discord destinations

The report reads these optional environment variables at send time. Real webhook values stay only in `/root/afs-shared/.env` and are never committed.

- `DISCORD_ROUTE_PAPER_COLLECTION_FUTURES`
- `DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS`

If either variable is unset, that side is printed/written locally and simply is not posted.

## What is reported

### Futures

- expected collector freshness from `ops.collector_census`
- every JSONL evidence stream that actually wrote during the day/week, including newly added campaign files that are not yet named in the census
- configured forward A/B candidate counts
- isolated hypothetical-paper lanes and current open-position names
- explicit warning if an MNQ hypothetical paper position is exposed without fresh 5-minute bars

### Options

- scanner rows written during the day/week
- shadow-journal rows/statuses and ENTRY_LATE episode-block count
- coverage-observer sessions, raw structural event counts, and per-session observable/requested symbol counts
- cumulative post-2026-09-15 raw observer counts for `strat_212_reversal` and `strat_122`

The prospective family counts are deliberately labelled **raw observer events**. They are not independent reducer episodes and cannot be used as validation or promotion statistics. The frozen prospective-analysis process remains authoritative for that question.

## Local artifacts

Each run writes its own reporting artifact only:

- `logs/paper_collection_reports/paper_collection_daily_YYYY-MM-DD.json`
- `logs/paper_collection_reports/paper_collection_weekly_YYYY-Www.json`

No trading state, evidence source, scanner database, collector state, or epoch is mutated.

## Schedule

Two systemd timers are provided:

- `afs-paper-collection-daily.timer` — Monday–Friday at 17:15 America/New_York
- `afs-paper-collection-weekly.timer` — Friday at 17:25 America/New_York

The daily time is after the 16:35 options coverage collector and after the 17:00 CME equity-futures pause, so both sides have had time to persist their end-of-day evidence.

## Activation

Activation is separate from merging the code. After the release containing these files is on the box:

```bash
sudo cp deploy/systemd/afs-paper-collection-daily.service /etc/systemd/system/
sudo cp deploy/systemd/afs-paper-collection-daily.timer /etc/systemd/system/
sudo cp deploy/systemd/afs-paper-collection-weekly.service /etc/systemd/system/
sudo cp deploy/systemd/afs-paper-collection-weekly.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

First run a read-only smoke without Discord:

```bash
PYTHONPATH=. .venv/bin/python -m scripts.paper_collection_rollup \
  --period daily \
  --log-dir /root/afs-shared/logs \
  --coverage-db /root/afs-shared/coverage/options_coverage_observer.sqlite \
  --report-dir /root/afs-shared/logs/paper_collection_reports \
  --no-send
```

Then run one manual service smoke and verify one message arrives in each configured collection channel:

```bash
sudo systemctl start afs-paper-collection-daily.service
journalctl -u afs-paper-collection-daily.service -n 100 --no-pager
```

Only after that smoke passes:

```bash
sudo systemctl enable --now afs-paper-collection-daily.timer
sudo systemctl enable --now afs-paper-collection-weekly.timer
systemctl list-timers 'afs-paper-collection-*'
```

No futures-bot or options-scanner restart is required. The timers are independent read-only oneshots.
