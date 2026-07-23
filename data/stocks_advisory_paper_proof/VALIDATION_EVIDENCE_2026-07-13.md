# Validation Replay Evidence — 2026-07-13 (NOT Official Day 1)

Status: **VALIDATION_REPLAY_NOT_OFFICIAL_DAY1 — 2026-07-13 — NO_TRADE — 0 of 20 minimum days, 0 of 30 minimum trades — NO LIVE EXECUTION**

**Reclassified from an initial (incorrect) "Official Day 1" labeling.**
`PROOF_MANIFEST_AMENDMENT_1_polygon_source.md` locked Polygon as the official
forward data source. When the Polygon fetch failed (see below), this run
fell back to manual TradingView/BATS export **without** a new approved
manifest amendment authorizing that source for official use. Per explicit
review, a run that doesn't follow its own frozen manifest cannot honestly
count as an official proof-window day, regardless of how clean the run
itself was or what the signal decided. This record is therefore preserved
exactly as produced (no values changed) but reclassified as validation
evidence only, matching the existing `validation_2026-07-10.jsonl` pattern.
It does **not** count toward the 20-day/30-trade proof-completion gates, and
2026-07-13 will not be rerun.

Originally saved as `DAY1_EVIDENCE_2026-07-13.md`; renamed to this file.
The underlying source directory was renamed from `day1_source_2026-07-13/`
to `validation_source_2026-07-13/`, and the journal record itself was moved
from `journal.jsonl` (official) to `validation_2026-07-13.jsonl`
(non-official), with only its `data_source` field's `OFFICIAL_DAY1:` prefix
corrected to `VALIDATION_REPLAY_NOT_OFFICIAL_DAY1:` — every other field
(decision, price, reason, timestamps) is untouched.

## Data source used for this run

Polygon was attempted first per Amendment 1, but its configured API key's
plan returned `HTTP 403 NOT_AUTHORIZED` ("Your plan doesn't include this data
timeframe") for any date at/after 2026-07-11 — confirmed via a direct query
and via Polygon's own `/v1/marketstatus/now` (market genuinely closed
normally today; this is a plan limitation, not a data-availability-timing
issue). The run then used the manual TradingView/BATS export path instead —
which is what makes this run non-official under the manifest as it stood at
the time.

**Session bars** (fresh exports covering today, operator-supplied, now under
`validation_source_2026-07-13/`):

| Symbol | Path | Rows read | RTH bars | Coverage | SHA-256 |
|---|---|---|---|---|---|
| QQQ | `validation_source_2026-07-13/QQQ_2026-07-13.csv` | 635 | 279 | 2026-07-08 .. 2026-07-13 | `6fce1669a9ea219570717efa1900dffaf6a9e5bf5be4d181ca7edc40ca435101` |
| TQQQ | `validation_source_2026-07-13/TQQQ_2026-07-13.csv` | 300 | 156 | 2026-07-10 .. 2026-07-13 | `f08a1a13113bb674bc826a59a79397f579a1945b9d89952c27b9cefabc659297` |
| SQQQ | `validation_source_2026-07-13/SQQQ_2026-07-13.csv` | 538 | 234 | 2026-07-09 .. 2026-07-13 | `378dd3a3c54c515c3579983c52bc682811accd787cb63cd3c09813593ecf8e56` |

**Relative-volume history** (existing, previously-hashed longer QQQ export,
reused unmodified — used only for the 20-session baseline, never for
decision bars):

| Symbol | Path | Rows read | RTH bars | Coverage | SHA-256 |
|---|---|---|---|---|---|
| QQQ (history) | `validation_source_2026-07-13/QQQ_rv_history.csv` | 20126 | 8190 | 2026-02-09 .. 2026-07-10 | `85d1e142fe1a8d56564d308aa5b9f3b96f63e56011c9930e178da5d2c615b97c` |

That hash matches the RV-history file recorded in
`PROOF_MANIFEST_AMENDMENT_1_polygon_source.md`, confirming it is the exact
same, unmodified file.

## Session-completeness check

`build_day_sessions()` (unmodified `stocks_advisory/csv_loader.py`) built
2026-07-13 as a complete session: 78 bars each for QQQ/TQQQ/SQQQ (09:30
through 15:55 ET), `qqq_previous_close=725.53`, `qqq_previous_high=726.39`,
`qqq_previous_low=716.98`.

## Relative-volume calculation (frozen method, unchanged)

```
QQQ cumulative RTH volume through the decision cutoff
÷
average QQQ cumulative RTH volume through the same cutoff
  over the prior 20 complete sessions
```

- Numerator (2026-07-13, from the fresh QQQ export): **1,287,018**
- Denominator: average over the 20 most recent clean prior sessions
  (2026-06-11 through 2026-07-10, from the RV-history file): **1,509,543.80**
- **relative_volume = 1,287,018 / 1,509,543.80 = 0.8526**

## CLI output (unchanged from the original run)

```
strategy_version:        tqqq_sqqq_decision_v1
date:                    2026-07-13
ok:                      True
journaled:               True
decision:                NO_TRADE
final_status:            no_trade
fee_only_net_pnl_dollars: None
net_pnl_dollars:         None
message:                 QQQ is inside the first-hour range
```

## Code-integrity confirmation

`git rev-parse HEAD` at run time: `497c7c0dbde3941203a2fc6b989aba1fe5617e0d`.
`git diff 5539a5e..HEAD --name-only` at that point showed only 4 files, all
in an unrelated release/webhook subsystem — zero changes to any
`stocks_advisory/` file, threshold, position-sizing, friction model, or the
decision engine.

## Corrected proof-window progress

- Official trading days recorded: **0 of 20 minimum**
- Completed trades: **0 of 30 minimum**
- 2026-07-13: preserved as non-official validation evidence (`NO_TRADE`)
- Live execution: disabled

Official Day 1 has not happened. It begins only after a new manifest
amendment locking the manual TradingView/BATS export path as the official
forward source is merged, and will apply to the next completed trading
session after that point — not 2026-07-13, which will not be rerun.

Not committed to git, not pushed — local evidence file only.
