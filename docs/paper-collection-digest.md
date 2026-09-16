# Paper Collection Digest (daily / weekly, read-only)

One place that answers, for every currently collecting evidence lane, "what did we
collect?" (daily, operational sanity) and "is this evidence going anywhere?" (weekly,
progress). It is **not** a collector and duplicates no evidence: it composes the
existing authoritative reports and counts rows that already exist inside a date window.

Code: `ops/paper_collection_digest.py` (builders + formatter), `scripts/paper_collection_digest.py` (CLI).
Reuses: `ops.evidence_lane_health`, `ops.collector_census`, the cross-instrument quality/feed
reports, `scripts.weekly_review` journal helpers, `context.mes_122_paper_lane.ledger_status`,
`alert_ranker.v1_diagnostics` lane/financial helpers, `alert_ranker.coverage_collector.read_ledger`.

## Discord destinations (optional)

Logical routes in `config/notification_routes.yaml`, delivered through the existing `DiscordRouter`:

- `paper_collection_futures` → `DISCORD_ROUTE_PAPER_COLLECTION_FUTURES`
- `paper_collection_options` → `DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS`

Unset = the digest prints locally and posts nothing. A delivery failure is logged by the router
and never raised. Webhook values live only in `/root/afs-shared/.env`; they are never printed.
Without `--post` nothing is sent regardless of environment.

## What is reported

**Futures** (each lane: instrument, mode, campaign id / epoch, window counts, cumulative counts,
open position, last evidence time, health, stalled flag): the #595 MNQ Asia D+EMA cohort (own
section, CME observation day, review gate 30 resolved / 10 days as written in the current-state
handoff), the three wide-stop hypothetical ledgers, MES 15m 1-2-2 (realistic ledger), the
cross-instrument observation campaign (authoritative quality report + feed trust), the five
Strat / MES evidence lanes, the forward A/B campaign, the real-book decision journal, the
observation-only evidence files, and collector freshness as data health.

**Options**: Paper V1 (scans, journal rows by ACTIVE / COUNTERFACTUAL, financial outcome from the
**sign of recorded P&L, never the journal status label**, the pre-existing
INSUFFICIENT / EARLY / REVIEWABLE sample labels), the coverage collector ledger (per-session final
status DONE / FAILED; health follows the newest session's *final* status, so a failed run that was
retried to DONE is healthy), and the options companion ledger (visible, expected zero).

Daily = concise: quiet, healthy lanes collapse into one "zero activity" line. Weekly = every lane,
cumulative counts, days represented, and only thresholds that already exist elsewhere.

Windows are derived strictly from row timestamps (daily = one UTC calendar date, the #595 cohort
by its CME observation day; weekly = the ISO week Monday..Sunday). There is no checkpoint file,
so the first run cannot claim historical rows were "collected today". The digest never writes.

## Schedule

`deploy/systemd/`:

- `afs-paper-collection-daily.timer` — Mon–Fri 17:15 America/New_York (after the 16:45 ET options
  coverage collector and the 17:00 ET CME pause). The service runs futures then options.
- `afs-paper-collection-weekly.timer` — Friday 17:25 America/New_York.

Same `America/New_York` calendar as `afs-coverage-collector.timer`, so DST needs no re-pin.
Independent oneshots: installing or enabling them restarts nothing.

## Activation (separate from merging)

After the release containing these files is live on the box:

```bash
cp deploy/systemd/afs-paper-collection-daily.service /etc/systemd/system/
cp deploy/systemd/afs-paper-collection-daily.timer /etc/systemd/system/
cp deploy/systemd/afs-paper-collection-weekly.service /etc/systemd/system/
cp deploy/systemd/afs-paper-collection-weekly.timer /etc/systemd/system/
systemctl daemon-reload
```

Read-only smoke first (prints, sends nothing):

```bash
cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m scripts.paper_collection_digest --domain futures --period daily --log-dir /root/afs-shared/logs
cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m scripts.paper_collection_digest --domain options --period daily --log-dir /root/afs-shared/logs --scanner-db /root/afs-shared/logs/options_scanner.sqlite --coverage-dir /root/afs-shared/coverage
```

Then one manual service run and confirm one message in each configured channel, then enable:

```bash
systemctl start afs-paper-collection-daily.service
journalctl -u afs-paper-collection-daily.service -n 100 --no-pager
systemctl enable --now afs-paper-collection-daily.timer afs-paper-collection-weekly.timer
```
