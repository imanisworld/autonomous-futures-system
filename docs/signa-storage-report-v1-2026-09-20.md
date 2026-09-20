# Signa Storage Report v1 — 2026-09-20

## Verdict

**APPROVE AS READ-ONLY MEASUREMENT ONLY.**

This report is the first cleanup step before any Signa retention or pruning rule.
It measures shared Signa storage growth and data quality. It does not delete,
compact, mutate, schedule, trade, route, or authorize anything.

## Command

```bash
python3 scripts/signa_storage_report.py \
  --db /root/afs-shared/logs/options_scanner.sqlite \
  --format json
```

Markdown output is also available:

```bash
python3 scripts/signa_storage_report.py \
  --db /root/afs-shared/logs/options_scanner.sqlite \
  --format markdown
```

## What it reports

- SQLite integrity and quick check.
- Database file sizes, including WAL/SHM if present.
- Row count, oldest timestamp, newest timestamp, and estimated rows/day for:
  - `signa_snapshots`
  - `options_signa_context`
- Counts by source, status, symbol/ticker, and timeframe.
- Authority flag distribution.
- Endpoint health and error rate.
- Duplicate logical snapshot keys.
- Duplicate context candidate keys.

## What it does not do

- It does not create tables.
- It does not initialize stores.
- It does not write rows.
- It does not prune rows.
- It does not call Signa.
- It does not send Discord.
- It does not touch futures runtime, options scheduler, broker, risk, execution, or strategy logic.

## Use policy

Use this report to decide whether a future retention proposal is necessary.
Do not add pruning from one off-session sample. Compare multiple reports across
normal market days first.

The correct order is:

```text
measure storage growth
→ review evidence needs
→ propose retention/archive policy
→ test on copied DB
→ only then consider production pruning
```
