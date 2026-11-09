# Preregistration — MNQ 60M 3-2-2 — AFS-0523 reproduction gate and out-of-sample slices — 2026-11-09

trial_id: T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01

Operator ledger id: **AFS-0523**. This file is the repository registration for that study. It is a preregistration only. Claude's review is required before any new data runs through the wrapper. `research/afs0523_322_repro_wrapper.py` sets `RUN_AUTHORIZED = False`, and `run_declared_slices()` refuses every call.

No vendor download, no corpus fetch, and no scoring run is part of this registration. The frozen strategy rules, the frozen harness, and `check_repro()` are not edited.

## Why the frozen pins are obsolete

`scripts/322_trigger_timing_ab_2026_09_18.py::check_repro()` still pins the pre-#1057 1-tick totals: plan net **2532.66** and IOC net **1859.40**. Those totals were produced by the resolver that could walk same-date evening bars when the exact 15:55 ET bar was missing. The corrected Oct 4 rerun (`T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01`) replaced them with plan net **2293.64** and IOC net **1622.38**. The harness file was left unchanged on purpose. Calling or patching `check_repro()` would either demand the obsolete totals or silently move the pin. This wrapper does neither.

## Reproduction gate of record

The gate of record for AFS-0523 is `compare_replay_to_oct4()` against the already committed ledger, not a new copy of the 34 rows:

`docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json`

SHA-256 `0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1` (commit `cf2ac0724a266198629a363fde23364ef00ce721`).

The comparison is all **306** outcome records: 34 candidates × 3 fill models × 3 slippage levels. The gate passes only when that count is present and every compared field matches the hash-verified file. Any difference fails.

| Wrapper label | Frozen ledger key | Fill model |
|---|---|---|
| original | `plan` | `market` |
| honest IOC | `ioc_close` | `ioc_limit`, tolerance 32 ticks |
| pre-armed | `prearmed_touch` | `stop_market` |

Slippage levels are 1, 2, and 3 adverse ticks. Each cell compares date, direction, trigger, stop, target, status, exit reason, fill price, exit price, and net. Any difference fails the gate.

2025-01-20 stays `UNRESOLVED` / `EOD_BAR_MISSING` on every model and every slippage. It is not given an exit price, a net, or a WIN/LOSS result. An entry price may be present because the entry triggered; that row is still unscored.

## What the wrapper may change

The wrapper imports the frozen detection and accounting modules and pins their file hashes. It does not reimplement them and it does not assign a point value. The only parameters are:

- the date window;
- the expected-count check (`None` reports the count; an integer aborts on any other count);
- the research 15-minute path and the audit 5-minute path.

The reproduction window is the frozen harness window, 2024-07-02 through 2026-06-26, expected count 34. NQ bars, when a later reviewed run supplies them, are read through the MNQ path and the unchanged MNQ lane. Dollars are MNQ-equivalent ($2/point). Point-value code is not touched.

## Declared slices (not authorized to run)

Reported separately. No retune. May 2019 belongs to the MNQ slice only, so the two windows do not overlap. The NQ window is best available evidence, not fully clean.

| Variant id | Window | Price source |
|---|---|---|
| `mnq-2019-05-to-2024-06` | 2019-05-01 through 2024-06-30 | MNQ |
| `nq-2010-06-to-2019-05` | 2010-06-01 through 2019-04-30 | NQ proxy. Every result in this window is labeled **MNQ-equivalent research, not executable historical MNQ**. It is best available evidence, not fully clean, and it is not executable historical MNQ. |
| `combined` | the two slices side by side | no third detection and no retune |

These windows end before the frozen 34-candidate sample (first candidate 2024-08-02). They are not a rescore of that sample. The machine manifest's three variant ids stay frozen. `DATA_GAP` handling and the 10AM-open gap policy apply to every variant. They are not extra variants.

## Data adapter

`research/databento_322_bar_adapter.py` accepts Databento GLBX.MDP3 ohlcv-1m rows plus definition rows (parent symbology) that the caller already has. It does not open a network client. It resamples 1-minute bars into the 15-minute research tree and the 5-minute audit tree, using bar-start UTC timestamps, America/New_York session boundaries, and the exact 15:55 ET bar the harness treats as the day-only close. The front contract is `sources.polygon_client.front_contract` with `roll_days=8`, not a Databento continuous symbol. Roll dates stay on the civil ET date of the bar start. That is not the CME session date and it is not Databento's continuous roll.

Databento omits no-trade minutes. A 5-minute or 15-minute bar is built from the minutes that exist, as the Polygon resampler builds a higher-timeframe bar from the sub-bars that exist. A missing minute is recorded and is not given a price. A bucket with no minutes is not written.

Any weekday missing a 15-minute bar in 07:00-11:00 ET, or a 5-minute bar in 10:00-16:00 ET, is `DATA_GAP`: counted, reported, and never scored. Full CME equity-index closures, and the session after a 13:00 ET early close, are `expected_holiday_closure` and are not data gaps. 2025-01-20 is an early close, so its missing 15:55 bar is a holiday closure. The Oct 4 reproduction comparison still requires that day to stay `UNRESOLVED` / `EOD_BAR_MISSING` when it is scored.

The wrapper checks the pinned frozen-file hashes at runtime and refuses on a mismatch. The 5-minute tree's day files must match the declared window. A wider tree is a window error. It does not fall through to the expected-count check and it is not ignored.

## Gap-open policy

Imani, 2026-11-09 10:10 America/New_York: gap-open days are scored at the 10AM open (`score_at_10am_open`), per the 3-2-2 rules, and the gap-open count is reported. This replaces the open decision. The production path does not offer exclude-and-count as a reporting variant. The Oct 4 population has no gap-open candidates, so the 306-record reproduction gate is unchanged under this policy.

A gap-open whose 10AM open is already outside the target-to-stop range is not scored. The research detector keeps that row. The production state machine (`strategy/strat_322_first_live.py`, the execution authority) rejects it as `INVALID_ENTRY_BRACKET`. The wrapper drops those research rows before the parity count, using that same rule: SHORT requires `target < entry < stop`, LONG requires `stop < entry < target`, and equality at either boundary is outside the bracket. The dropped count is reported next to the gap-open count. The variant manifest is unchanged: this is not a fourth variant.

## Slippage sensitivity and break-even

1-3 tick slippage sensitivity tests fill-price sensitivity only, not whether fills would have happened; spread, depth and fill probability remain separate unquantified uncertainties.

The break-even win rate is one shared rate for a fill model and a slippage level. Each resolved fill is priced twice, once at its own target and once at its own stop, with adverse slippage and round-turn commission on both exits. The rate is the value that sets the sum of those nets to zero. It is not the average of the per-trade break-even percentages. Unresolved rows, including 2025-01-20, are not trades in that rate. On the Oct 4 ledger at 3 ticks the reference is about 77.6% for the 32 pre-armed trades and about 73.8% for the 19 honest-IOC trades.

## Prohibited until Claude's review

No new-vendor run, no slice score, no parameter change, no edit to the frozen harness or `check_repro()`, no edit to strategy rules, and no promotion. A passing mechanical test on synthetic bars is not AFS-0523 evidence.
