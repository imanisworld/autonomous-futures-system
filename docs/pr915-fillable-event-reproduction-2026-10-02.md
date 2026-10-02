# #915 fillable-event reproduction — 2026-10-02

Reproduction of the archived #915 family controls only. This is not an
account-admission run and it does not open prereg #929.

Archive: `archive/pr915-mnq-combined-portfolio-audit-5a9f14b` at
`5a9f14baf714947b98a38a19b45f04a8d18fb365`.

Window: `2025-07-24` through `2026-06-26`. Common-day intersection: 290 days,
first `2025-07-24`, last `2026-06-26`.

## Why the earlier rebuild failed

The failed rebuild imported the audit script from current `main`. That script
is byte-identical to the archive. Two dependencies are not:

- `execution/paper_broker.py` on `main` refuses a fill when
  `MAX_CONTRACTS_HARD_CAP` is unset. The archive does not have that check.
  The first local attempt therefore produced no 4HR fills
  (`MAX_CONTRACTS_HARD_CAP is missing or empty`). Cause:
  `WRONG_CODE_VERSION`.
- After that variable was set to `1` for the local process only, the 4HR
  archived control passed and `60M_322_FIRST_LIVE` failed its net control.
  Fill count still matched. `scripts/edge_decomposition_audit.py`
  `resolve_bracket` on `main` treats a same-ET-date bar after the exact EOD
  bar as a missing EOD bar. The archive flattens only when the ET date
  changes. 3-2-2 is a day-only lane. Cause: `WRONG_CODE_VERSION`.

No corpus was swapped. No date window was changed. No tolerance was added.
The archived float tolerance remains `0.03` in `_assert_close`.

## Mismatch table

The archived control column is the `EXPECTED` entry in the archived script,
except Asia, which has no numeric `EXPECTED` entry and uses fixture parity.
"Observed" is the current-`main` dependency run. Families after 3-2-2 were
not reached because the script aborts on the first control failure.

| Family | Archived source | Attempted source | Archived control | Observed control | First divergence | Cause |
|---|---|---|---|---|---|---|
| `4HR_RETRIGGER` | `data/replay_corpus_v1_5m_4hr_audit`; archived `resolve_bracket` | same corpus; current `resolve_bracket`; cap `1` | fills 80, eod_bar_missing 1, net 1414.60, pf 1.299 | assertion passed | none | — |
| `60M_322_FIRST_LIVE` | 15m `data/replay_polygon`, 5m `data/replay_corpus_v1_5m_4hr_audit`; archived day-only exit | same corpora; current day-only exit | fills 33, net 2742.66 | fills matched; net 2503.64 | `resolve_bracket` same-day post-EOD walk | `WRONG_CODE_VERSION` |
| `DAILY_22_COMPLETED_CLOSE` | `data/replay_corpus_v1_5m_4hr_audit` | not reached | fills 34, net 13571.68, pf 1.9482 | not observed | aborted at 3-2-2 | `WRONG_CODE_VERSION` on the run, not a separate daily cause |
| `12HR_MIYAGI` | `data/replay_polygon_5m` | not reached | fills 8, net 425.33, pf 2.322 | not observed | aborted at 3-2-2 | same |
| `ASIA_D_EMA` | `data/replay_corpus_v1_market_condition_fixed`; fixture parity | not reached | fixture parity, no numeric `EXPECTED` | not observed | aborted at 3-2-2 | same |
| `SUSTAINED_TREND_V1` | 15m market-condition corpus, 5m `data/replay_corpus_v1_5m`; archived #912 code | not reached | fills 36, terminal 34, net 576.18, pf 1.762 | not observed | aborted at 3-2-2 | same |

## Reproduction gate

Archived code at `5a9f14b`, same local corpora, all six adapter controls
passed, including the 290-day intersection check. No new tolerance. The
archived sustained adapter's own standalone control also passed; that is the
archived #912 check inside the adapter, not `apply_account_admission` and not
a new six-family account score.

`replay_portfolio` for the six-family combined report was not called.
`apply_account_admission` was not called. Prereg #929 was not run.

## Artifact

`research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl`

- SHA-256 `d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942`
- 2257 rows
- `4HR_RETRIGGER` 37, `60M_322_FIRST_LIVE` 11, `DAILY_22_COMPLETED_CLOSE` 38,
  `12HR_MIYAGI` 1, `ASIA_D_EMA` 2134, `SUSTAINED_TREND_V1` 36
- first `eligible_fill_ts` `2025-07-24T00:15:00+00:00`
- last `eligible_fill_ts` `2026-06-26T06:15:00+00:00`
- sort: eligible fill time, archived tie order, `source_id`
- manifest: `research/artifacts/pr915-six-family-fillable-events-5a9f14b.manifest.json`

Corpus content hashes were not stored on the archive. The manifest records
hashes of the local files this reproduction read.

## Same-timestamp census

280 fillable-event pairs have `exit_ts` equal to another event's
`eligible_fill_ts`. Equal timestamps were not treated as an order. The list
is `research/artifacts/pr915-six-family-same-timestamp-collisions-5a9f14b.json`.

Family pairs: Asia to Asia 273, Asia to Daily 3, Sustained to Asia 1,
3-2-2 to 4HR 1, Sustained to Daily 1, Daily to Asia 1.

Count is greater than 0, so the account-admission draft stays
`SAME_TIMESTAMP_ORDER_BLOCKED`. Strict-before is not yet exact live parity
for this population.
