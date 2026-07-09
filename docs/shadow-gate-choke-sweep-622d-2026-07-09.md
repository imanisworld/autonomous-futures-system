# Shadow Gate-Choke Sweep — Which Gate Blocks the Best-Scoring Candidates

Follow-up to `docs/missed-move-gate-sweep-622d-2026-07-09.md`. That sweep found 80.5% of regime/structure-gate blocks during large moves still had a shadow-recognized candidate present (`STRUCTURE_PRESENT_BUT_NOT_QUALIFIED`), but only counted presence. This reads the box's own already-resolved `shadow_candidates[].outcome` for that exact subset — no new fill/resolution simulation was written; every W/L/pnl_ticks figure below is what the box's own shadow evaluator already computed and journaled.

Total `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` journal rows encountered: 3182
Shadow-candidate outcomes excluded (not `entry_filled` or not resolved WIN/LOSS — i.e. `OPEN`/`NO_FILL`/unresolved): 1274
Shadow-candidate outcomes included (filled + resolved WIN/LOSS): 3187

**Note on sizing**: `shadow_candidates` are journaled with their own `risk_tier`/`size_multiplier` (e.g. reduced size by design) — dollar figures below are `pnl_ticks` converted at the standard 1-contract `TICK_VALUE`, NOT the shadow lane's own live sizing. Read these as directional evidence, not a literal dollar P&L the shadow lane actually books.

**Overall verdict: `BAD_COUNTERFACTUAL`**

## Combined

| n | wins | losses | win rate | net $ | exp $ | classification |
|---:|---:|---:|---:|---:|---:|---|
| 3187 | 1074 | 2113 | 34% | -1373.28 | -0.43 | BAD_COUNTERFACTUAL |

## By exact gate

| group | n | wins | losses | win rate | net $ | exp $ | classification |
|---|---:|---:|---:|---:|---:|---:|---|
| WEAK_BAR_CLOSE | 3187 | 1074 | 2113 | 34% | -1373.28 | -0.43 | BAD_COUNTERFACTUAL |

## By instrument

| group | n | wins | losses | win rate | net $ | exp $ | classification |
|---|---:|---:|---:|---:|---:|---:|---|
| MES | 1313 | 439 | 874 | 33% | -0.10 | -0.00 | BAD_COUNTERFACTUAL |
| MNQ | 1874 | 635 | 1239 | 34% | -1373.18 | -0.73 | BAD_COUNTERFACTUAL |

## By shadow strategy

| group | n | wins | losses | win rate | net $ | exp $ | classification |
|---|---:|---:|---:|---:|---:|---:|---|
| ema_pullback_trend | 543 | 173 | 370 | 32% | -4627.53 | -8.52 | BAD_COUNTERFACTUAL |
| impulse_first_pullback_observed | 797 | 286 | 511 | 36% | 6044.00 | 7.58 | MIXED |
| strat_122_observed | 20 | 8 | 12 | 40% | 176.50 | 8.82 | MIXED |
| strat_122_pullback | 46 | 15 | 31 | 33% | -30.00 | -0.65 | BAD_COUNTERFACTUAL |
| strat_22_continuation_observed | 1189 | 386 | 803 | 32% | -4778.25 | -4.02 | BAD_COUNTERFACTUAL |
| strat_22_reversal_observed | 351 | 131 | 220 | 37% | 4385.25 | 12.49 | MIXED |
| strat_312_observed | 50 | 16 | 34 | 32% | -896.00 | -17.92 | BAD_COUNTERFACTUAL |
| strat_322_reversal_observed | 63 | 17 | 46 | 27% | -697.00 | -11.06 | BAD_COUNTERFACTUAL |
| strat_4hr_retrigger_observed | 15 | 6 | 9 | 40% | 130.00 | 8.67 | MIXED |
| trend_consolidation_break_observed | 113 | 36 | 77 | 32% | -1080.25 | -9.56 | BAD_COUNTERFACTUAL |

## By session

| group | n | wins | losses | win rate | net $ | exp $ | classification |
|---|---:|---:|---:|---:|---:|---:|---|
| asian | 393 | 144 | 249 | 37% | 2024.28 | 5.15 | MIXED |
| london | 1516 | 521 | 995 | 34% | -2826.80 | -1.86 | BAD_COUNTERFACTUAL |
| new_york | 1278 | 409 | 869 | 32% | -570.76 | -0.45 | BAD_COUNTERFACTUAL |

## By market condition

| group | n | wins | losses | win rate | net $ | exp $ | classification |
|---|---:|---:|---:|---:|---:|---:|---|
| TRENDING | 3187 | 1074 | 2113 | 34% | -1373.28 | -0.43 | BAD_COUNTERFACTUAL |

## Reading

Combined verdict is `BAD_COUNTERFACTUAL`, not `OVERFILTERED` — the presence-only finding in the prior sweep (a shadow candidate existed) does not mean the candidate was actually good. At full scale, 66% of these filled shadow candidates lose, and only `WEAK_BAR_CLOSE` produced enough resolved volume to classify at all (`REGIME_NOT_FULL` did not appear as the first-listed gate on any row in this move-window population in this dataset — plausible, not a bug: it's a generic catch-all gate, and large-range bars tend to produce strong, not weak, closes, so it makes sense it rarely fires here specifically).
That said, not everything is bad-counterfactual: `strat_22_reversal_observed` (n=351, 37% WR, exp $12.49); `strat_122_observed` (n=20, 40% WR, exp $8.82); `strat_4hr_retrigger_observed` (n=15, 40% WR, exp $8.67); `impulse_first_pullback_observed` (n=797, 36% WR, exp $7.58) are net-positive but fall just short of the strict `VALID_SHADOW_CANDIDATE` bar (win rate below 45% despite positive expectancy — low-win-rate/big-winner shape, more fragile than the bar is designed to accept on this evidence alone). These are the closest things to a real signal in this data and worth a closer, strategy-specific look rather than acting on the combined verdict alone.

## Smallest candidate rule (descriptive finding, not a proposed change)

No gate reached `VALID_SHADOW_CANDIDATE` status at the current cell-size threshold (n >= 15) — no dominant gate to report.

## Notes

- Cells with fewer than 15 resolved shadow trades are classified `INSUFFICIENT_DATA` rather than given a directional call.
- Classification taxonomy (per-cell): `VALID_SHADOW_CANDIDATE` (net positive, win rate >=45%) / `BAD_COUNTERFACTUAL` (net negative, win rate <=55%) / `MIXED` (neither) / `INSUFFICIENT_DATA`. The overall verdict `OVERFILTERED` is reserved for when the combined aggregate itself is `VALID_SHADOW_CANDIDATE` AND more resolved volume sits in `VALID_SHADOW_CANDIDATE` cells than `BAD_COUNTERFACTUAL` cells — a different, stricter bar than any single gate looking good in isolation.
- This is docs/script/tests only — zero changes to execution/, risk/, config/, risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo orders, no trade-cap changes, no proof_builder, no strategy promotion or demotion.
