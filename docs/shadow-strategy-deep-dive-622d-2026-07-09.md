# Shadow Strategy Deep-Dive — impulse_first_pullback_observed + strat_22_reversal_observed

Follow-up to `docs/shadow-gate-choke-sweep-622d-2026-07-09.md`, which found the aggregate `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` population is `BAD_COUNTERFACTUAL` but these two strategies were net-positive within it. This checks whether that holds up across instruments, walk-forward halves, and outlier dependence, or was a fluke of a few big winners. Same population and data source as the prior sweep — a drill-down, not a new study.

**Fill-realism note**: every W/L/`pnl_ticks` figure below comes from `strategy/shadow_setups.py:resolve_shadow_candidate` — its entry-fill test requires a forward bar to actually trade through the entry price (not an always-fills assumption) and uses pessimistic same-bar resolution, same convention as the rest of this codebase. It does **not** model slippage or commissions — real executed P&L would likely run somewhat lower than shown here.

## `impulse_first_pullback_observed`

**Classification: `PROMISING_BUT_UNPROVEN`**

### Combined

| n | wins | losses | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 797 | 286 | 511 | 36% | 6044.00 | 7.58 | -70.00 | 51% | 8699.75 |

### By instrument

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| MES | 329 | 33% | 527.50 | 1.60 | -58.75 | 392% | 2010.00 |
| MNQ | 468 | 38% | 5516.50 | 11.79 | -86.75 | 55% | 6862.00 |

### By walk-forward half

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| H1 | 390 | 38% | 8036.25 | 20.61 | -65.88 | 38% | 3834.50 |
| H2 | 407 | 34% | -1992.25 | -4.89 | -72.50 | -108% | 7067.75 |

### By session

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| asian | 124 | 41% | 2550.50 | 20.57 | -60.75 | 28% | 1623.50 |
| london | 418 | 35% | -367.75 | -0.88 | -61.88 | -452% | 3962.25 |
| new_york | 255 | 34% | 3861.25 | 15.14 | -83.75 | 79% | 4882.00 |

### Co-occurring real (rejected) candidate strategy on the same bar

| real strategy | count |
|---|---:|
| (none logged) | 589 |
| orb_breakout | 122 |
| vwap_hold | 65 |
| pdh_reclaim | 18 |
| pdl_reclaim | 3 |

## `strat_22_reversal_observed`

**Classification: `PROMISING_BUT_UNPROVEN`**

### Combined

| n | wins | losses | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 351 | 131 | 220 | 37% | 4385.25 | 12.49 | -48.75 | 39% | 2965.50 |

### By instrument

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| MES | 150 | 36% | 986.25 | 6.58 | -40.62 | 105% | 1240.00 |
| MNQ | 201 | 38% | 3399.00 | 16.91 | -59.50 | 50% | 2186.00 |

### By walk-forward half

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| H1 | 159 | 35% | 180.25 | 1.13 | -50.00 | 728% | 1608.00 |
| H2 | 192 | 39% | 4205.00 | 21.90 | -47.00 | 40% | 2965.50 |

### By session

| group | n | win rate | net $ | mean $ | median $ | outlier share | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| asian | 35 | 29% | -1042.75 | -29.79 | -72.50 | 13% | 1049.00 |
| london | 153 | 37% | 1200.25 | 7.84 | -42.50 | 102% | 1729.00 |
| new_york | 163 | 40% | 4227.75 | 25.94 | -61.25 | 40% | 1576.75 |

### Co-occurring real (rejected) candidate strategy on the same bar

| real strategy | count |
|---|---:|
| (none logged) | 322 |
| vwap_hold | 20 |
| orb_breakout | 5 |
| pdh_reclaim | 4 |

## Notes

- Classification requires n >= 15 per breakdown cell to count toward walk-forward-consistency; outlier-dependent means the top-3 trades account for more than 40% of total net $.
- `VALIDATED_SHADOW_CANDIDATE` here is a research finding, not a promotion — no config, risk, or strategy file is changed by this script regardless of the label.
- This is docs/script/tests only — zero changes to execution/, risk/, config/, risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo orders, no trade-cap changes, no proof_builder, no strategy promotion or demotion.
