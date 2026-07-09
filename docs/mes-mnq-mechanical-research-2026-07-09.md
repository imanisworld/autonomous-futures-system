# MES/MNQ Mechanical Research - 2026-07-09

Read-only research output. No production behavior, broker routing, risk, config, proof_builder, GEX, runner, fill-resolver, or strategy code was changed.

## Exact data sources used

- `logs/replay_622d_market_static/{MES,MNQ}/journal_*.jsonl`: current-rule decision rows, failed gates, executable blocked setups, and shadow candidates.
- `logs/replay_622d_nodd_ioc_limit_static/{MES,MNQ}/journal_*.jsonl`: honest IOC-fill static-exit trades with drawdown breaker disabled for full-period measurement.
- `logs/replay_622d_nodd_ioc_limit_runner/{MES,MNQ}/journal_*.jsonl`: honest IOC-fill runner-exit comparison trades.
- `data/replay_polygon/{MES,MNQ}/{INSTR}_YYYY-MM-DD.jsonl`: 15-minute Polygon replay bars used for forward target/stop resolution.
- Existing reports reviewed: `docs/ioc-faithful-baseline-622d-2026-07-06.md`, `docs/orb-market-entry-study-2026-07-02.md`, `docs/mes-orb-reclaim-deepdive-2026-07-06.md`, `docs/missed-move-gate-sweep-622d-2026-07-09.md`, `docs/entry-detached-sweep-622d-2026-07-09.md`, `docs/execution-parity-study-2026-07-02.md`, and `docs/strategy-audit-handoff-2026-07-08.md`.

## What existing reports already answered

- Honest IOC fills make the full current book zero-to-negative. Under static exits MES is -$1,550 and MNQ is -$1,523; under runner exits both are roughly flat and fail second-half stability.
- MES `orb_reclaim` is the only honest walk-forward-robust replay cell already identified: positive in both halves under both exits, and 7 of 8 quarters positive under runner.
- MNQ has no static-exit honest strategy ready for production. MNQ `vwap_reclaim` under runner is watchlist-only; MNQ ORB market entry only worked in a separate runner-exit market-entry study, not under static exits.
- `ENTRY_DETACHED_FROM_PRICE` is not solved by looser causal fill models: full-scale stop-market recovery filled only 2 of 5,268 cases.
- Large-move windows are heavily overfiltered by trend/volume/regime gates, but the prior report did not prove those blocked structures would have positive expectancy.

## New analysis added here

This script tests candidate-shaped blocked rows with resting-entry fills and pessimistic stop-first same-bar handling. It uses two populations: explicit executable `setup` rows and `shadow_candidates`. It does not invent setups for rows that only say `NO_TRADE` with no bracket.

## Candidate inventory

| instrument | source | gate | candidates |
|---|---|---|---:|
| MNQ | shadow_candidate | SIGNAL_BAR_VOLUME_TOO_LOW | 14692 |
| MES | shadow_candidate | SIGNAL_BAR_VOLUME_TOO_LOW | 14351 |
| MNQ | shadow_candidate | TREND_STRENGTH_BELOW_REQUIRED | 8870 |
| MNQ | shadow_candidate | WEAK_BAR_CLOSE | 4242 |
| MNQ | shadow_candidate | NO_GATE | 3496 |
| MES | shadow_candidate | WEAK_BAR_CLOSE | 3458 |
| MES | shadow_candidate | NO_GATE | 3081 |
| MNQ | executable_setup | ENTRY_DETACHED_FROM_PRICE | 2788 |
| MNQ | shadow_candidate | ENTRY_DETACHED_FROM_PRICE | 2726 |
| MNQ | shadow_candidate | MARKET_CONDITION_NOT_TRADABLE | 2211 |
| MNQ | shadow_candidate | EMA_STACK_NOT_ALIGNED | 2001 |
| MES | shadow_candidate | EMA_STACK_NOT_ALIGNED_SOFT | 1907 |
| MES | shadow_candidate | MARKET_CONDITION_NOT_TRADABLE | 1630 |
| MES | shadow_candidate | EMA_STACK_NOT_ALIGNED | 1558 |
| MES | shadow_candidate | TREND_STRENGTH_BELOW_REQUIRED | 1444 |
| MES | executable_setup | ENTRY_DETACHED_FROM_PRICE | 1142 |
| MNQ | shadow_candidate | EMA_STACK_NOT_ALIGNED_SOFT | 1096 |
| MES | shadow_candidate | ENTRY_DETACHED_FROM_PRICE | 1063 |
| MNQ | shadow_candidate | MARKET_CONDITION_NOT_TRENDING | 954 |
| MNQ | executable_setup | WEAK_BAR_CLOSE | 802 |
| MES | shadow_candidate | MARKET_CONDITION_NOT_TRENDING | 692 |
| MES | executable_setup | WEAK_BAR_CLOSE | 349 |
| MES | executable_setup | EMA_STACK_NOT_ALIGNED_SOFT | 153 |
| MNQ | executable_setup | EMA_STACK_NOT_ALIGNED_SOFT | 34 |
| MES | shadow_candidate | STRAT_DIRECTION_CONFLICT | 2 |

## Target sizing - blocked/candidate-shaped rows

| group | n | resolved | WR | exp | net | max DD |
|---|---:|---:|---:|---:|---:|---:|
| 0.5R | 74742 | 61263 | 60.5% | $-3.08 | $-188,916.55 | $-191,200.67 |
| 0.75R | 74742 | 60234 | 54.0% | $-1.12 | $-67,511.00 | $-76,181.65 |
| 1.0R | 74742 | 59248 | 48.7% | $0.03 | $1,633.40 | $-28,014.90 |
| current | 74742 | 56157 | 32.5% | $-1.39 | $-78,082.10 | $-102,223.76 |
| next_level | 72826 | 59308 | 54.7% | $-3.26 | $-193,317.25 | $-197,669.29 |

## Honest baseline by instrument/strategy/session


### ioc_limit_static

| group | n | resolved | WR | exp | net | max DD |
|---|---:|---:|---:|---:|---:|---:|
| MES|orb_breakout|london | 17 | 17 | 41.2% | $4.26 | $72.50 | $-130.00 |
| MES|orb_breakout|new_york | 34 | 34 | 32.4% | $-3.79 | $-128.75 | $-266.25 |
| MES|orb_reclaim|asian | 18 | 18 | 16.7% | $-28.09 | $-505.62 | $-505.62 |
| MES|orb_reclaim|london | 34 | 34 | 41.2% | $14.59 | $496.10 | $-285.00 |
| MES|orb_reclaim|new_york | 105 | 105 | 40.0% | $9.87 | $1,036.79 | $-623.75 |
| MES|pdh_reclaim|asian | 30 | 30 | 26.7% | $-14.34 | $-430.25 | $-430.25 |
| MES|pdh_reclaim|london | 12 | 12 | 25.0% | $-15.33 | $-184.00 | $-192.25 |
| MES|pdh_reclaim|new_york | 36 | 36 | 22.2% | $-21.95 | $-790.25 | $-866.00 |
| MES|vwap_hold|asian | 115 | 115 | 27.0% | $-10.10 | $-1,161.75 | $-1,312.40 |
| MES|vwap_hold|london | 58 | 58 | 24.1% | $-14.49 | $-840.55 | $-936.80 |
| MES|vwap_hold|new_york | 105 | 105 | 39.1% | $8.68 | $911.05 | $-583.25 |
| MNQ|orb_reclaim|london | 18 | 18 | 27.8% | $-9.58 | $-172.42 | $-260.50 |
| MNQ|orb_reclaim|new_york | 28 | 28 | 17.9% | $-22.98 | $-643.54 | $-734.04 |
| MNQ|pdh_reclaim|asian | 10 | 10 | 50.0% | $0.00 | $0.00 | $-29.00 |
| MNQ|pdh_reclaim|new_york | 10 | 10 | 20.0% | $-14.04 | $-140.40 | $-143.70 |
| MNQ|vwap_hold|asian | 22 | 22 | 45.5% | $4.81 | $105.82 | $-120.60 |
| MNQ|vwap_hold|london | 37 | 37 | 43.2% | $2.23 | $82.36 | $-97.08 |
| MNQ|vwap_hold|new_york | 28 | 28 | 21.4% | $-10.69 | $-299.18 | $-299.18 |
| MNQ|vwap_reclaim|asian | 28 | 28 | 46.4% | $2.91 | $81.46 | $-139.24 |
| MNQ|vwap_reclaim|london | 62 | 62 | 29.0% | $-6.39 | $-395.94 | $-528.68 |
| MNQ|vwap_reclaim|new_york | 25 | 25 | 36.0% | $-3.94 | $-98.62 | $-183.20 |

### ioc_limit_runner

| group | n | resolved | WR | exp | net | max DD |
|---|---:|---:|---:|---:|---:|---:|
| MES|orb_breakout|london | 22 | 22 | 36.4% | $-9.18 | $-201.85 | $-386.24 |
| MES|orb_breakout|new_york | 44 | 44 | 52.3% | $10.01 | $440.64 | $-322.49 |
| MES|orb_reclaim|asian | 20 | 20 | 50.0% | $-2.75 | $-54.99 | $-435.00 |
| MES|orb_reclaim|london | 43 | 43 | 53.5% | $17.97 | $772.79 | $-337.50 |
| MES|orb_reclaim|new_york | 131 | 131 | 57.2% | $14.57 | $1,908.72 | $-404.86 |
| MES|pdh_reclaim|asian | 31 | 31 | 29.0% | $-22.58 | $-700.12 | $-700.12 |
| MES|pdh_reclaim|london | 13 | 13 | 30.8% | $-25.10 | $-326.25 | $-326.25 |
| MES|pdh_reclaim|new_york | 40 | 40 | 32.5% | $-16.14 | $-645.74 | $-767.88 |
| MES|vwap_hold|asian | 123 | 123 | 43.1% | $-7.80 | $-959.68 | $-1,295.97 |
| MES|vwap_hold|london | 76 | 76 | 44.7% | $-4.41 | $-335.53 | $-780.70 |
| MES|vwap_hold|new_york | 129 | 129 | 48.8% | $2.87 | $370.51 | $-942.77 |
| MNQ|orb_breakout|new_york | 11 | 11 | 27.3% | $5.64 | $62.00 | $-163.75 |
| MNQ|orb_reclaim|asian | 14 | 14 | 71.4% | $26.34 | $368.75 | $-138.00 |
| MNQ|orb_reclaim|london | 27 | 27 | 33.3% | $-16.63 | $-448.92 | $-793.50 |
| MNQ|orb_reclaim|new_york | 36 | 36 | 27.8% | $-12.80 | $-460.79 | $-751.29 |
| MNQ|pdh_reclaim|asian | 16 | 16 | 37.5% | $-11.95 | $-191.25 | $-191.25 |
| MNQ|pdh_reclaim|new_york | 12 | 12 | 25.0% | $-12.71 | $-152.50 | $-172.75 |
| MNQ|vwap_hold|asian | 27 | 27 | 55.6% | $10.83 | $292.51 | $-210.45 |
| MNQ|vwap_hold|london | 50 | 50 | 40.0% | $4.81 | $240.75 | $-193.13 |
| MNQ|vwap_hold|new_york | 33 | 33 | 27.3% | $-2.26 | $-74.57 | $-246.35 |
| MNQ|vwap_reclaim|asian | 28 | 28 | 53.6% | $4.65 | $130.13 | $-140.84 |
| MNQ|vwap_reclaim|london | 82 | 82 | 36.6% | $-2.11 | $-172.75 | $-353.99 |
| MNQ|vwap_reclaim|new_york | 29 | 29 | 48.3% | $16.10 | $466.96 | $-230.74 |

## Gate classification

| instrument | gate | source | class | n | resolved | WR | exp current | exp 1R |
|---|---|---|---|---:|---:|---:|---:|---:|
| MES | EMA_STACK_NOT_ALIGNED_SOFT | executable_setup | MIXED | 153 | 63 | 28.6% | $4.25 | $-4.49 |
| MES | EMA_STACK_NOT_ALIGNED_SOFT | shadow_candidate | MIXED | 1907 | 1553 | 34.1% | $0.04 | $-0.47 |
| MES | EMA_STACK_NOT_ALIGNED | shadow_candidate | VALID_PROTECTION | 1558 | 1304 | 32.8% | $-0.29 | $-0.08 |
| MES | ENTRY_DETACHED_FROM_PRICE | executable_setup | VALID_PROTECTION | 1142 | 372 | 33.1% | $7.74 | $-5.26 |
| MES | ENTRY_DETACHED_FROM_PRICE | shadow_candidate | MIXED | 1063 | 850 | 32.6% | $-5.55 | $0.90 |
| MES | MARKET_CONDITION_NOT_TRADABLE | shadow_candidate | MIXED | 1630 | 1096 | 34.0% | $0.14 | $1.54 |
| MES | MARKET_CONDITION_NOT_TRENDING | shadow_candidate | VALID_PROTECTION | 692 | 568 | 30.1% | $-5.18 | $-1.43 |
| MES | NO_GATE | shadow_candidate | VALID_PROTECTION | 3081 | 2619 | 32.0% | $-2.91 | $-0.13 |
| MES | SIGNAL_BAR_VOLUME_TOO_LOW | shadow_candidate | VALID_PROTECTION | 14351 | 10441 | 31.3% | $-1.77 | $-0.26 |
| MES | STRAT_DIRECTION_CONFLICT | shadow_candidate | INSUFFICIENT_DATA | 2 | 2 | 0.0% | $-32.50 | $-32.50 |
| MES | TREND_STRENGTH_BELOW_REQUIRED | shadow_candidate | VALID_PROTECTION | 1444 | 1177 | 33.0% | $-1.39 | $-1.38 |
| MES | WEAK_BAR_CLOSE | executable_setup | MIXED | 349 | 109 | 34.9% | $9.68 | $-3.61 |
| MES | WEAK_BAR_CLOSE | shadow_candidate | MIXED | 3458 | 2722 | 32.0% | $-1.13 | $0.97 |
| MNQ | EMA_STACK_NOT_ALIGNED_SOFT | executable_setup | MIXED | 34 | 15 | 33.3% | $0.08 | $-6.40 |
| MNQ | EMA_STACK_NOT_ALIGNED_SOFT | shadow_candidate | VALID_PROTECTION | 1096 | 918 | 33.8% | $-3.31 | $-4.23 |
| MNQ | EMA_STACK_NOT_ALIGNED | shadow_candidate | VALID_PROTECTION | 2001 | 1691 | 32.2% | $-1.62 | $-0.85 |
| MNQ | ENTRY_DETACHED_FROM_PRICE | executable_setup | VALID_PROTECTION | 2788 | 1525 | 25.5% | $-1.25 | $-7.27 |
| MNQ | ENTRY_DETACHED_FROM_PRICE | shadow_candidate | MIXED | 2726 | 2092 | 32.3% | $-8.05 | $1.32 |
| MNQ | MARKET_CONDITION_NOT_TRADABLE | shadow_candidate | MIXED | 2211 | 1479 | 32.6% | $-2.46 | $0.94 |
| MNQ | MARKET_CONDITION_NOT_TRENDING | shadow_candidate | MIXED | 954 | 832 | 35.0% | $0.61 | $-0.70 |
| MNQ | NO_GATE | shadow_candidate | VALID_PROTECTION | 3496 | 3075 | 34.4% | $-0.17 | $-0.72 |
| MNQ | SIGNAL_BAR_VOLUME_TOO_LOW | shadow_candidate | MIXED | 14692 | 10562 | 33.6% | $0.11 | $2.80 |
| MNQ | TREND_STRENGTH_BELOW_REQUIRED | shadow_candidate | VALID_PROTECTION | 8870 | 7251 | 32.6% | $-1.96 | $-1.40 |
| MNQ | WEAK_BAR_CLOSE | executable_setup | VALID_PROTECTION | 802 | 435 | 25.5% | $-1.19 | $-7.47 |
| MNQ | WEAK_BAR_CLOSE | shadow_candidate | MIXED | 4242 | 3406 | 33.4% | $-0.53 | $0.70 |

## Stop/timing behavior

Honest IOC/static resolved trade count analyzed: 866. Losses that later reached the original target after the stop: 241.

Important limitation: the wider-stop table below is an exit-path screen over already-approved trades. It does not rerun IOC entry fills, live sizing, commissions, or PaperBroker slippage, so its absolute P&L is not comparable to the IOC-faithful baseline. Use it only to flag whether wider stops deserve a stricter PaperBroker replay, not as proof that wider stops work.

### Wider-stop sweep

| group | n | resolved | WR | exp | net | max DD |
|---|---:|---:|---:|---:|---:|---:|
| 1.0 | 866 | 866 | 32.7% | $6.86 | $5,940.85 | $-908.50 |
| 1.25 | 866 | 854 | 36.4% | $5.94 | $5,072.54 | $-899.13 |
| 1.5 | 866 | 841 | 41.3% | $7.39 | $6,211.52 | $-963.50 |
| 2.0 | 866 | 820 | 47.8% | $7.55 | $6,191.80 | $-1,093.00 |

### Later-target stopout examples

| instrument | ts | session | strategy | entry | stop | target | stop dist | MAE R | MFE R | bars after stop |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| MES | 2024-07-03T09:30:00+00:00 | london | pdh_reclaim | 5570.25 | 5563.25 | 5585.65 | 7.00 | 1.46 | 2.29 | 15 |
| MES | 2024-07-05T05:45:00+00:00 | asian | orb_reclaim | 5596.00 | 5590.00 | 5611.00 | 6.00 | 1.83 | 2.75 | 19 |
| MES | 2024-07-11T09:00:00+00:00 | london | vwap_hold | 5683.24 | 5690.74 | 5660.74 | 7.50 | 3.27 | 3.63 | 9 |
| MES | 2024-07-23T02:15:00+00:00 | asian | vwap_hold | 5604.87 | 5612.37 | 5582.37 | 7.50 | 3.32 | 4.15 | 42 |
| MES | 2024-08-06T07:00:00+00:00 | london | vwap_hold | 5266.83 | 5274.33 | 5244.33 | 7.50 | 2.32 | 4.11 | 7 |
| MES | 2024-08-07T07:45:00+00:00 | london | vwap_hold | 5290.77 | 5298.27 | 5268.27 | 7.50 | 9.13 | 3.47 | 37 |
| MES | 2024-08-13T06:30:00+00:00 | asian | orb_reclaim | 5384.25 | 5374.25 | 5409.25 | 10.00 | 1.4 | 2.5 | 8 |
| MES | 2024-08-13T13:45:00+00:00 | new_york | orb_reclaim | 5415.75 | 5405.75 | 5440.75 | 10.00 | 1.45 | 2.5 | 8 |
| MES | 2024-08-14T06:30:00+00:00 | asian | pdh_reclaim | 5461.75 | 5454.75 | 5477.15 | 7.00 | 3.29 | 2.46 | 30 |
| MES | 2024-08-15T02:00:00+00:00 | asian | pdh_reclaim | 5488.25 | 5481.25 | 5503.65 | 7.00 | 2.36 | 4.79 | 25 |
| MES | 2024-08-19T13:30:00+00:00 | new_york | pdh_reclaim | 5586.75 | 5579.75 | 5602.15 | 7.00 | 1.86 | 2.21 | 2 |
| MES | 2024-08-29T13:45:00+00:00 | new_york | orb_reclaim | 5638.00 | 5628.00 | 5663.00 | 10.00 | 1.0 | 2.5 | 9 |
| MES | 2024-09-03T02:00:00+00:00 | asian | vwap_hold | 5657.57 | 5665.07 | 5635.07 | 7.50 | 1.16 | 3.54 | 6 |
| MES | 2024-09-05T03:15:00+00:00 | asian | vwap_hold | 5528.63 | 5536.13 | 5506.13 | 7.50 | 3.82 | 4.18 | 31 |
| MES | 2024-09-10T03:15:00+00:00 | asian | vwap_hold | 5480.69 | 5488.19 | 5458.19 | 7.50 | 2.77 | 3.36 | 15 |
| MES | 2024-09-18T04:30:00+00:00 | asian | vwap_hold | 5704.15 | 5711.65 | 5681.65 | 7.50 | 6.95 | 3.79 | 9 |
| MES | 2024-10-01T06:00:00+00:00 | asian | vwap_hold | 5811.45 | 5818.95 | 5788.95 | 7.50 | 1.07 | 5.46 | 10 |
| MES | 2024-10-07T11:00:00+00:00 | london | vwap_hold | 5775.58 | 5783.08 | 5753.08 | 7.50 | 1.72 | 3.84 | 20 |
| MES | 2024-10-23T06:00:00+00:00 | asian | vwap_hold | 5885.85 | 5893.35 | 5863.35 | 7.50 | 1.09 | 3.51 | 27 |
| MES | 2024-11-04T08:45:00+00:00 | london | vwap_hold | 5763.63 | 5771.13 | 5741.13 | 7.50 | 1.45 | 3.35 | 16 |
| MES | 2024-11-07T05:30:00+00:00 | asian | pdh_reclaim | 5967.50 | 5960.50 | 5982.90 | 7.00 | 1.18 | 2.39 | 29 |
| MES | 2024-11-11T15:00:00+00:00 | new_york | orb_breakout | 6036.25 | 6040.75 | 6021.25 | 4.50 | 1.61 | 4.33 | 10 |
| MES | 2024-11-12T02:45:00+00:00 | asian | vwap_hold | 6026.65 | 6034.15 | 6004.15 | 7.50 | 1.31 | 3.05 | 11 |
| MES | 2024-11-21T16:15:00+00:00 | new_york | pdh_reclaim | 5958.00 | 5951.00 | 5973.40 | 7.00 | 1.93 | 2.54 | 5 |
| MES | 2024-11-22T05:15:00+00:00 | asian | vwap_hold | 5966.72 | 5974.22 | 5944.22 | 7.50 | 1.27 | 3.4 | 8 |

## Gate classification — 10-way taxonomy

Pure relabeling of the gate classification above via `map_gate_label_to_10way()` — same underlying numbers, no new computation.

| instrument | gate | source | 10-way class | 5-way class | exp current | exp 1R |
|---|---|---|---|---|---:|---:|
| MES | EMA_STACK_NOT_ALIGNED_SOFT | executable_setup | WAIT | MIXED | $4.25 | $-4.49 |
| MES | EMA_STACK_NOT_ALIGNED_SOFT | shadow_candidate | WAIT | MIXED | $0.04 | $-0.47 |
| MES | EMA_STACK_NOT_ALIGNED | shadow_candidate | WAIT | VALID_PROTECTION | $-0.29 | $-0.08 |
| MES | ENTRY_DETACHED_FROM_PRICE | executable_setup | WAIT | VALID_PROTECTION | $7.74 | $-5.26 |
| MES | ENTRY_DETACHED_FROM_PRICE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $-5.55 | $0.90 |
| MES | MARKET_CONDITION_NOT_TRADABLE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $0.14 | $1.54 |
| MES | MARKET_CONDITION_NOT_TRENDING | shadow_candidate | WAIT | VALID_PROTECTION | $-5.18 | $-1.43 |
| MES | NO_GATE | shadow_candidate | WAIT | VALID_PROTECTION | $-2.91 | $-0.13 |
| MES | SIGNAL_BAR_VOLUME_TOO_LOW | shadow_candidate | WAIT | VALID_PROTECTION | $-1.77 | $-0.26 |
| MES | STRAT_DIRECTION_CONFLICT | shadow_candidate | INSUFFICIENT_DATA | INSUFFICIENT_DATA | $-32.50 | $-32.50 |
| MES | TREND_STRENGTH_BELOW_REQUIRED | shadow_candidate | WAIT | VALID_PROTECTION | $-1.39 | $-1.38 |
| MES | WEAK_BAR_CLOSE | executable_setup | WAIT | MIXED | $9.68 | $-3.61 |
| MES | WEAK_BAR_CLOSE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $-1.13 | $0.97 |
| MNQ | EMA_STACK_NOT_ALIGNED_SOFT | executable_setup | WAIT | MIXED | $0.08 | $-6.40 |
| MNQ | EMA_STACK_NOT_ALIGNED_SOFT | shadow_candidate | WAIT | VALID_PROTECTION | $-3.31 | $-4.23 |
| MNQ | EMA_STACK_NOT_ALIGNED | shadow_candidate | WAIT | VALID_PROTECTION | $-1.62 | $-0.85 |
| MNQ | ENTRY_DETACHED_FROM_PRICE | executable_setup | WAIT | VALID_PROTECTION | $-1.25 | $-7.27 |
| MNQ | ENTRY_DETACHED_FROM_PRICE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $-8.05 | $1.32 |
| MNQ | MARKET_CONDITION_NOT_TRADABLE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $-2.46 | $0.94 |
| MNQ | MARKET_CONDITION_NOT_TRENDING | shadow_candidate | WAIT | MIXED | $0.61 | $-0.70 |
| MNQ | NO_GATE | shadow_candidate | WAIT | VALID_PROTECTION | $-0.17 | $-0.72 |
| MNQ | SIGNAL_BAR_VOLUME_TOO_LOW | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $0.11 | $2.80 |
| MNQ | TREND_STRENGTH_BELOW_REQUIRED | shadow_candidate | WAIT | VALID_PROTECTION | $-1.96 | $-1.40 |
| MNQ | WEAK_BAR_CLOSE | executable_setup | WAIT | VALID_PROTECTION | $-1.19 | $-7.47 |
| MNQ | WEAK_BAR_CLOSE | shadow_candidate | TREND_MODIFIER_CANDIDATE | MIXED | $-0.53 | $0.70 |

## Target ambition (TARGET_TOO_AMBITIOUS candidates)

Cells where the current target is negative-expectancy but a smaller R-multiple target is clearly positive and better, using the target-variant sweep above.

| group (instrument\|strategy\|session\|gate\|source) | too ambitious | current exp | best smaller mode | best smaller exp |
|---|---|---:|---|---:|
| MES|ema_pullback_trend|london|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-0.80 | 0.75R | $7.06 |
| MES|ema_pullback_trend|new_york|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-2.49 | 0.75R | $16.03 |
| MES|ema_pullback_trend|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-16.63 | 1.0R | $2.60 |
| MES|impulse_first_pullback_observed|asian|MARKET_CONDITION_NOT_TRENDING|shadow_candidate | YES | $-7.03 | 0.5R | $1.03 |
| MES|impulse_first_pullback_observed|london|EMA_STACK_NOT_ALIGNED_SOFT|shadow_candidate | YES | $-0.45 | 1.0R | $4.32 |
| MES|impulse_first_pullback_observed|new_york|EMA_STACK_NOT_ALIGNED|shadow_candidate | YES | $-20.49 | 0.75R | $4.71 |
| MES|impulse_first_pullback_observed|new_york|MARKET_CONDITION_NOT_TRENDING|shadow_candidate | YES | $-23.86 | 1.0R | $22.70 |
| MES|impulse_first_pullback_observed|new_york|WEAK_BAR_CLOSE|shadow_candidate | YES | $-7.03 | 1.0R | $17.90 |
| MES|strat_122_observed|london|NO_GATE|shadow_candidate | YES | $-5.67 | 0.5R | $0.72 |
| MES|strat_22_continuation_observed|asian|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-7.64 | 1.0R | $3.09 |
| MES|strat_22_continuation_observed|new_york|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-5.88 | 0.75R | $4.48 |
| MES|strat_22_continuation_observed|new_york|MARKET_CONDITION_NOT_TRENDING|shadow_candidate | YES | $-7.77 | 1.0R | $3.75 |
| MES|strat_22_continuation_observed|new_york|NO_GATE|shadow_candidate | YES | $-7.05 | 0.75R | $2.68 |
| MES|strat_22_continuation_observed|new_york|WEAK_BAR_CLOSE|shadow_candidate | YES | $-9.06 | 1.0R | $4.74 |
| MES|strat_22_reversal_observed|asian|EMA_STACK_NOT_ALIGNED_SOFT|shadow_candidate | YES | $-0.25 | 1.0R | $0.99 |
| MES|strat_22_reversal_observed|london|EMA_STACK_NOT_ALIGNED|shadow_candidate | YES | $-0.43 | 1.0R | $1.04 |
| MES|strat_22_reversal_observed|london|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-13.86 | 0.75R | $2.16 |
| MES|strat_22_reversal_observed|new_york|NO_GATE|shadow_candidate | YES | $-5.76 | 1.0R | $9.78 |
| MES|strat_322_reversal_observed|asian|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-0.56 | 1.0R | $0.47 |
| MES|strat_322_reversal_observed|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-16.74 | 1.0R | $3.59 |
| MES|trend_consolidation_break_observed|asian|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-13.88 | 1.0R | $6.47 |
| MES|trend_consolidation_break_observed|new_york|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-15.27 | 1.0R | $0.83 |
| MES|trend_consolidation_break_observed|new_york|WEAK_BAR_CLOSE|shadow_candidate | YES | $-9.00 | 0.75R | $16.27 |
| MNQ|ema_pullback_trend|london|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-17.55 | 1.0R | $1.06 |
| MNQ|ema_pullback_trend|new_york|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-16.79 | 0.75R | $29.11 |
| MNQ|ema_pullback_trend|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-29.08 | 0.75R | $0.59 |
| MNQ|impulse_first_pullback_observed|asian|EMA_STACK_NOT_ALIGNED|shadow_candidate | YES | $-9.38 | 0.75R | $0.89 |
| MNQ|impulse_first_pullback_observed|new_york|EMA_STACK_NOT_ALIGNED_SOFT|shadow_candidate | YES | $-108.62 | 0.75R | $29.51 |
| MNQ|impulse_first_pullback_observed|new_york|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-75.29 | 0.75R | $18.12 |
| MNQ|impulse_first_pullback_observed|new_york|MARKET_CONDITION_NOT_TRADABLE|shadow_candidate | YES | $-3.55 | 1.0R | $4.02 |
| MNQ|impulse_first_pullback_observed|new_york|NO_GATE|shadow_candidate | YES | $-36.45 | 1.0R | $14.58 |
| MNQ|impulse_first_pullback_observed|new_york|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-28.97 | 0.75R | $0.11 |
| MNQ|strat_122_observed|london|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-0.08 | 1.0R | $7.72 |
| MNQ|strat_22_continuation_observed|new_york|MARKET_CONDITION_NOT_TRADABLE|shadow_candidate | YES | $-5.41 | 0.75R | $6.03 |
| MNQ|strat_22_continuation_observed|new_york|MARKET_CONDITION_NOT_TRENDING|shadow_candidate | YES | $-14.57 | 0.75R | $5.30 |
| MNQ|strat_22_continuation_observed|new_york|NO_GATE|shadow_candidate | YES | $-24.52 | 0.5R | $2.19 |
| MNQ|strat_22_continuation_observed|new_york|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-12.45 | 0.75R | $5.39 |
| MNQ|strat_22_continuation_observed|new_york|WEAK_BAR_CLOSE|shadow_candidate | YES | $-0.52 | 1.0R | $14.99 |
| MNQ|strat_22_reversal_observed|asian|NO_GATE|shadow_candidate | YES | $-1.48 | 1.0R | $4.29 |
| MNQ|strat_22_reversal_observed|asian|WEAK_BAR_CLOSE|shadow_candidate | YES | $-3.07 | 1.0R | $0.09 |
| MNQ|strat_22_reversal_observed|new_york|EMA_STACK_NOT_ALIGNED_SOFT|shadow_candidate | YES | $-1.42 | 0.75R | $11.74 |
| MNQ|strat_22_reversal_observed|new_york|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-13.29 | 0.75R | $0.82 |
| MNQ|strat_312_observed|asian|TREND_STRENGTH_BELOW_REQUIRED|shadow_candidate | YES | $-0.61 | 1.0R | $1.31 |
| MNQ|strat_312_observed|new_york|MARKET_CONDITION_NOT_TRADABLE|shadow_candidate | YES | $-90.03 | 0.75R | $3.76 |
| MNQ|strat_312_observed|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-6.69 | 1.0R | $14.57 |
| MNQ|strat_322_reversal_observed|new_york|MARKET_CONDITION_NOT_TRADABLE|shadow_candidate | YES | $-66.03 | 0.75R | $38.87 |
| MNQ|strat_322_reversal_observed|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-28.28 | 0.75R | $1.38 |
| MNQ|trend_consolidation_break_observed|new_york|ENTRY_DETACHED_FROM_PRICE|shadow_candidate | YES | $-9.39 | 0.75R | $35.22 |
| MNQ|trend_consolidation_break_observed|new_york|SIGNAL_BAR_VOLUME_TOO_LOW|shadow_candidate | YES | $-5.45 | 1.0R | $13.48 |
| MNQ|trend_consolidation_break_observed|new_york|WEAK_BAR_CLOSE|shadow_candidate | YES | $-4.97 | 1.0R | $19.77 |

## Stop timing (STOP_TIMING_PROBLEM by instrument)

Losses that later reached target as a share of all 1.0x-stop losses (overall): 41.3%.

| instrument | STOP_TIMING_PROBLEM | widening helps net P&L | best wider mult | later-target share |
|---|---|---|---|---:|
| MES | False | False | 1.5 | 41.3% |
| MNQ | True | True | 2.0 | 41.3% |

## ORB role (orb_breakout / orb_reclaim / orb_rejection)

Note: sessions are shown for every cell for transparency, but only sessions with n >= 30 resolved trades count toward the classification decision itself — a small-n session can show a different sign than the classification without that being a contradiction.

### ioc_limit_static

| instrument\|strategy | classification | sessions (n, exp) |
|---|---|---|
| MES|orb_breakout | BAD_STRATEGY | london(17,$4.26), new_york(34,$-3.79) |
| MES|orb_reclaim | VALIDATED | asian(18,$-28.09), london(34,$14.59), new_york(105,$9.87) |
| MNQ|orb_reclaim | INSUFFICIENT_DATA | london(18,$-9.58), new_york(28,$-22.98) |

### ioc_limit_runner

| instrument\|strategy | classification | sessions (n, exp) |
|---|---|---|
| MES|orb_breakout | PROMISING_BUT_UNPROVEN | london(22,$-9.18), new_york(44,$10.01) |
| MES|orb_reclaim | VALIDATED | asian(20,$-2.75), london(43,$17.97), new_york(131,$14.57) |
| MNQ|orb_breakout | INSUFFICIENT_DATA | new_york(11,$5.64) |
| MNQ|orb_reclaim | BAD_STRATEGY | asian(14,$26.34), london(27,$-16.63), new_york(36,$-12.80) |

## VWAP role (vwap_hold / vwap_reclaim / vwap_rejection)

Same n >= 30 rule as the ORB section above.

### ioc_limit_static

| instrument\|strategy | classification | sessions (n, exp) |
|---|---|---|
| MES|vwap_hold | VWAP_CONTEXT_ONLY | asian(115,$-10.10), london(58,$-14.49), new_york(105,$8.68) |
| MNQ|vwap_hold | PROMISING_BUT_UNPROVEN | asian(22,$4.81), london(37,$2.23), new_york(28,$-10.69) |
| MNQ|vwap_reclaim | BAD_STRATEGY | asian(28,$2.91), london(62,$-6.39), new_york(25,$-3.94) |

### ioc_limit_runner

| instrument\|strategy | classification | sessions (n, exp) |
|---|---|---|
| MES|vwap_hold | VWAP_CONTEXT_ONLY | asian(123,$-7.80), london(76,$-4.41), new_york(129,$2.87) |
| MNQ|vwap_hold | VWAP_CONTEXT_ONLY | asian(27,$10.83), london(50,$4.81), new_york(33,$-2.26) |
| MNQ|vwap_reclaim | BAD_STRATEGY | asian(28,$4.65), london(82,$-2.11), new_york(29,$16.10) |

## Answers

**1. What actually works for MES?** `orb_reclaim` is the only strategy with a positive, walk-forward-robust honest-fill cell (both static and runner exits, strongest in New York — see ORB role table above). Everything else is negative, unstable, or shadow/candidate evidence only.

**2. What actually works for MNQ?** Nothing is validated on static exits. `vwap_reclaim` (New York, runner) is the closest promising cell but fails the static-exit leg and its session split is not walk-forward-proven — `PROMISING_BUT_UNPROVEN` at best.

**3. Should trend remain a hard blocker or become a modifier?** Neither extreme is supported yet. The gate-classification table shows several `TREND_MODIFIER_CANDIDATE` cells (blocked rows where a 1R target beats the current target), but none reach validated sample size/stability. Treat as a read-only shadow-test candidate, not a live rule change.

**4. Do reduced targets improve weak-trend setups?** In aggregate, yes directionally — see 'Target sizing' above (1.0R roughly flat/slightly positive vs. current/sub-1R negative overall) — but the per-cell 'Target ambition' table above is the one to trust for any specific instrument/strategy/session before acting, and most cells don't clear the sample-size bar.

**5. Are stops too tight, or are they correctly cutting bad trades?** Mixed, instrument-dependent — see the 'Stop timing' table above. Neither instrument shows `STOP_TIMING_PROBLEM=True` at the combined threshold used here (widening must both improve net P&L AND at least 15% of losses must have reached target later); many individual later-target stopouts exist ('Later-target stopout examples' table further above) but don't yet justify a blanket widen — this matches the existing `stop_multiplier_per_instrument` finding in `risk_rules.yaml` that blanket widening helps some setups and hurts others.

**6. Which gates are falsely rejecting good setups?** Rows classified `OVERFILTERED` or `TREND_MODIFIER_CANDIDATE` in the 10-way gate table above, restricted to cells with adequate sample (n >= 30).

**7. Which gates are correctly protecting the system?** Rows classified `WAIT` (mapped from `VALID_PROTECTION`) in the 10-way gate table — `ENTRY_DETACHED_FROM_PRICE` on `executable_setup` rows remains the clearest case, consistent with the separate full-scale entry-detached study (`docs/entry-detached-sweep-622d-2026-07-09.md`).

**8. What is the smallest behavior change worth testing next?** A read-only walk-forward shadow lane that treats weak trend as a target/confirmation modifier for only the single best-supported `TREND_MODIFIER_CANDIDATE` cell (by sample size and consistency), using IOC-realistic fills and the existing pessimistic same-bar rules. No live/demo routing, no config change, no gate/stop change.

Hard requirement status: this report does not recommend a production change. Anything here that looks promising still needs walk-forward stability, realistic fills, pessimistic same-bar handling, and adequate sample size before it can become a behavior change.
