# Corrected Corpus v1 loss-attribution audit

**Canonical verdict remains: HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST.**

This audit decomposes the exact merged PR #346 population. It changes no strategy, runtime, Pine, risk, broker, execution, configuration, or deployment behavior.

## Frozen reproduction

- 165 unique attempts = 97 fills + 68 IOC no-fills; 97 resolved, 0 open.
- Net before commission: **$-658.72**.
- Commission: **$143.56**.
- Net after commission: **$-802.28**; PF **0.753**.
- Breakers: MNQ 2025-09-08; MES 2025-12-11. No attempt is at or after its instrument's first max-drawdown rejection timestamp.

## Strategy (worst to best)

| strategy | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim | 131 | 86 | 45 | 65.6% | 25-61 | 29.1% | 0.803 | $-588.28 | $-4.49 | $-6.84 | 73.3% |
| vwap_reclaim | 10 | 5 | 5 | 50.0% | 0-5 | 0.0% | 0.000 | $-137.12 | $-13.71 | $-27.42 | 17.1% |
| pdl_reclaim | 1 | 1 | 0 | 100.0% | 0-1 | 0.0% | 0.000 | $-31.98 | $-31.98 | $-31.98 | 4.0% |
| orb_rejection | 3 | 2 | 1 | 66.7% | 0-2 | 0.0% | 0.000 | $-26.46 | $-8.82 | $-13.23 | 3.3% |
| orb_breakout | 20 | 3 | 17 | 15.0% | 1-2 | 33.3% | 0.723 | $-18.44 | $-0.92 | $-6.15 | 2.3% |

## Instrument

| instrument | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES | 104 | 75 | 29 | 72.1% | 22-53 | 29.3% | 0.831 | $-441.00 | $-4.24 | $-5.88 | 55.0% |
| MNQ | 61 | 22 | 39 | 36.1% | 4-18 | 18.2% | 0.434 | $-361.28 | $-5.92 | $-16.42 | 45.0% |

## Strategy × instrument

| strategy / instrument | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim / MES | 104 | 75 | 29 | 72.1% | 22-53 | 29.3% | 0.831 | $-441.00 | $-4.24 | $-5.88 | 55.0% |
| orb_reclaim / MNQ | 27 | 11 | 16 | 40.7% | 3-8 | 27.3% | 0.608 | $-147.28 | $-5.45 | $-13.39 | 18.4% |
| vwap_reclaim / MNQ | 10 | 5 | 5 | 50.0% | 0-5 | 0.0% | 0.000 | $-137.12 | $-13.71 | $-27.42 | 17.1% |
| pdl_reclaim / MNQ | 1 | 1 | 0 | 100.0% | 0-1 | 0.0% | 0.000 | $-31.98 | $-31.98 | $-31.98 | 4.0% |
| orb_rejection / MNQ | 3 | 2 | 1 | 66.7% | 0-2 | 0.0% | 0.000 | $-26.46 | $-8.82 | $-13.23 | 3.3% |
| orb_breakout / MNQ | 20 | 3 | 17 | 15.0% | 1-2 | 33.3% | 0.723 | $-18.44 | $-0.92 | $-6.15 | 2.3% |

## Session

| session | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| london | 83 | 58 | 25 | 69.9% | 14-44 | 24.1% | 0.653 | $-612.18 | $-7.38 | $-10.55 | 76.3% |
| asian | 10 | 6 | 4 | 60.0% | 1-5 | 16.7% | 0.431 | $-111.76 | $-11.18 | $-18.63 | 13.9% |
| new_york | 72 | 33 | 39 | 45.8% | 11-22 | 33.3% | 0.939 | $-78.34 | $-1.09 | $-2.37 | 9.8% |

Strategy × session (all populated cells):

| strategy / session | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim / london | 67 | 52 | 15 | 77.6% | 13-39 | 25.0% | 0.679 | $-521.96 | $-7.79 | $-10.04 | 65.1% |
| vwap_reclaim / asian | 6 | 3 | 3 | 50.0% | 0-3 | 0.0% | 0.000 | $-87.32 | $-14.55 | $-29.11 | 10.9% |
| vwap_reclaim / london | 3 | 2 | 1 | 66.7% | 0-2 | 0.0% | 0.000 | $-49.80 | $-16.60 | $-24.90 | 6.2% |
| orb_reclaim / new_york | 61 | 31 | 30 | 50.8% | 11-20 | 35.5% | 0.967 | $-41.88 | $-0.69 | $-1.35 | 5.2% |
| pdl_reclaim / london | 1 | 1 | 0 | 100.0% | 0-1 | 0.0% | 0.000 | $-31.98 | $-31.98 | $-31.98 | 4.0% |
| orb_breakout / new_york | 9 | 1 | 8 | 11.1% | 0-1 | 0.0% | 0.000 | $-27.98 | $-3.11 | $-27.98 | 3.5% |
| orb_reclaim / asian | 3 | 3 | 0 | 100.0% | 1-2 | 33.3% | 0.776 | $-24.44 | $-8.15 | $-8.15 | 3.0% |
| orb_rejection / london | 2 | 1 | 1 | 50.0% | 0-1 | 0.0% | 0.000 | $-17.98 | $-8.99 | $-17.98 | 2.2% |
| orb_rejection / new_york | 1 | 1 | 0 | 100.0% | 0-1 | 0.0% | 0.000 | $-8.48 | $-8.48 | $-8.48 | 1.1% |
| vwap_reclaim / new_york | 1 | 0 | 1 | 0.0% | 0-0 | — | — | $0.00 | $0.00 | — | -0.0% |
| orb_breakout / asian | 1 | 0 | 1 | 0.0% | 0-0 | — | — | $0.00 | $0.00 | — | -0.0% |
| orb_breakout / london | 10 | 2 | 8 | 20.0% | 1-1 | 50.0% | 1.248 | $9.54 | $0.95 | $4.77 | -1.2% |

## Direction

| direction | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 154 | 93 | 61 | 60.4% | 26-67 | 28.0% | 0.774 | $-715.86 | $-4.65 | $-7.70 | 89.2% |
| SHORT | 11 | 4 | 7 | 36.4% | 0-4 | 0.0% | 0.000 | $-86.42 | $-7.86 | $-21.61 | 10.8% |

Strategy × direction (all populated cells):

| strategy / direction | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim / LONG | 131 | 86 | 45 | 65.6% | 25-61 | 29.1% | 0.803 | $-588.28 | $-4.49 | $-6.84 | 73.3% |
| vwap_reclaim / LONG | 10 | 5 | 5 | 50.0% | 0-5 | 0.0% | 0.000 | $-137.12 | $-13.71 | $-27.42 | 17.1% |
| pdl_reclaim / SHORT | 1 | 1 | 0 | 100.0% | 0-1 | 0.0% | 0.000 | $-31.98 | $-31.98 | $-31.98 | 4.0% |
| orb_breakout / SHORT | 7 | 1 | 6 | 14.3% | 0-1 | 0.0% | 0.000 | $-27.98 | $-4.00 | $-27.98 | 3.5% |
| orb_rejection / SHORT | 3 | 2 | 1 | 66.7% | 0-2 | 0.0% | 0.000 | $-26.46 | $-8.82 | $-13.23 | 3.3% |
| orb_breakout / LONG | 13 | 2 | 11 | 15.4% | 1-1 | 50.0% | 1.248 | $9.54 | $0.73 | $4.77 | -1.2% |

## Time

| half | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 165 | 97 | 68 | 58.8% | 26-71 | 26.8% | 0.753 | $-802.28 | $-4.86 | $-8.27 | 100.0% |
| H2 | 0 | 0 | 0 | — | 0-0 | — | — | $0.00 | — | — | -0.0% |

| quarter | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 125 | 70 | 55 | 56.0% | 20-50 | 28.6% | 0.847 | $-334.82 | $-2.68 | $-4.78 | 41.7% |
| Q2 | 40 | 27 | 13 | 67.5% | 6-21 | 22.2% | 0.559 | $-467.46 | $-11.69 | $-17.31 | 58.3% |
| Q3 | 0 | 0 | 0 | — | 0-0 | — | — | $0.00 | — | — | -0.0% |
| Q4 | 0 | 0 | 0 | — | 0-0 | — | — | $0.00 | — | — | -0.0% |

| month | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025-09 | 37 | 28 | 9 | 75.7% | 7-21 | 25.0% | 0.592 | $-406.19 | $-10.98 | $-14.51 | 50.6% |
| 2025-12 | 10 | 7 | 3 | 70.0% | 0-7 | 0.0% | 0.000 | $-337.86 | $-33.79 | $-48.27 | 42.1% |
| 2025-10 | 30 | 24 | 6 | 80.0% | 6-18 | 25.0% | 0.804 | $-160.53 | $-5.35 | $-6.69 | 20.0% |
| 2025-07 | 18 | 8 | 10 | 44.4% | 2-6 | 25.0% | 0.853 | $-28.85 | $-1.60 | $-3.61 | 3.6% |
| 2025-11 | 20 | 10 | 10 | 50.0% | 4-6 | 40.0% | 1.071 | $25.83 | $1.29 | $2.58 | -3.2% |
| 2025-08 | 50 | 20 | 30 | 40.0% | 7-13 | 35.0% | 1.198 | $105.32 | $2.11 | $5.27 | -13.1% |

## Market state (journal fields only)

| market_condition | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TRENDING | 165 | 97 | 68 | 58.8% | 26-71 | 26.8% | 0.753 | $-802.28 | $-4.86 | $-8.27 | 100.0% |

| regime | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FULL_LONG | 123 | 75 | 48 | 61.0% | 20-55 | 26.7% | 0.731 | $-701.32 | $-5.70 | $-9.35 | 87.4% |
| RESTRICTED | 39 | 20 | 19 | 51.3% | 6-14 | 30.0% | 0.878 | $-74.50 | $-1.91 | $-3.73 | 9.3% |
| FULL_SHORT | 3 | 2 | 1 | 66.7% | 0-2 | 0.0% | 0.000 | $-26.46 | $-8.82 | $-13.23 | 3.3% |

| trend_direction | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| UP | 154 | 93 | 61 | 60.4% | 26-67 | 28.0% | 0.774 | $-715.86 | $-4.65 | $-7.70 | 89.2% |
| DOWN | 11 | 4 | 7 | 36.4% | 0-4 | 0.0% | 0.000 | $-86.42 | $-7.86 | $-21.61 | 10.8% |

Every approved attempt was journal-classified TRENDING. RANGE_BOUND, CHOPPY, and DEAD therefore have no approved-attempt rows; no missing classification was invented.

## IOC / market-fill attribution

**NON-CANONICAL SAME-CODE MARKET-FILL DIAGNOSTIC ONLY**

- Exact candidate fingerprints in common: 153.
- Matched market net: $3,402.44; matched IOC net: $-785.34; delta: $-4,187.78.
- Transitions: `{"MARKET_LOSS -> IOC_LOSS": 69, "MARKET_LOSS -> IOC_NO_FILL": 22, "MARKET_WIN -> IOC_NO_FILL": 37, "MARKET_WIN -> IOC_WIN": 25}`.
- No-fill selection delta: $-4,059.68; changed-fill P&L delta: $-128.10. Cancellations, not changed P&L on filled candidates, dominate this paired delta.
- Direct identity match to the old 747-row headline is impossible: The superseded ledger has date/instrument/strategy/result/P&L only; it has no bar_ts, direction, order ID, or candidate fingerprint.
- Therefore the full $54,927.21 old-headline delta cannot be assigned to execution alone: the old study is also parity-invalid and has a different 747-attempt population. Only the 153 exact fingerprints above support candidate-level execution attribution.

The matched diagnostic isolates fill-model effects only where the candidate fingerprint is identical. Population-only rows reflect divergent shared-account breaker paths and are not treated as paired causality.

| Strategy / instrument | Matched | No-fill | Win→no-fill | Loss→no-fill | Market net | IOC net | No-fill Δ | Filled Δ | Total Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim / MES | 101 | 27 | 16 | 11 | $2,242.40 | $-414.52 | $-2,447.54 | $-209.38 | $-2,656.92 |
| orb_reclaim / MNQ | 25 | 14 | 9 | 5 | $647.50 | $-147.28 | $-871.28 | $76.50 | $-794.78 |
| orb_breakout / MNQ | 13 | 12 | 8 | 4 | $481.26 | $-27.98 | $-508.74 | $-0.50 | $-509.24 |
| vwap_reclaim / MNQ | 10 | 5 | 4 | 1 | $92.20 | $-137.12 | $-243.60 | $14.28 | $-229.32 |
| pdl_reclaim / MNQ | 1 | 0 | 0 | 0 | $-31.48 | $-31.98 | $0.00 | $-0.50 | $-0.50 |
| orb_rejection / MNQ | 3 | 1 | 0 | 1 | $-29.44 | $-26.46 | $11.48 | $-8.50 | $2.98 |

## Drawdown breaker

- Post-breaker qualified setups rejected: MNQ 444; MES 180.
- MNQ rejected by strategy: `{"orb_breakout": 105, "orb_reclaim": 265, "orb_rejection": 21, "pdl_reclaim": 13, "vwap_reclaim": 33, "vwap_rejection": 7}`.
- MES rejected by strategy: `{"orb_reclaim": 180}`.
- MNQ accumulated its canonical admitted-trade loss over 46 calendar days; leading after-commission contributors were ORB Reclaim ($-147.28) and VWAP Reclaim ($-137.12). No single MNQ strategy exclusively caused the halt.
- MES accumulated its canonical admitted-trade loss over 140 calendar days; every admitted and later rejected MES setup was ORB Reclaim. ORB Reclaim therefore shut down evidence collection for the MES account lane.
- These counts prove material evidence censorship without disabling the breaker; they do not assign hypothetical outcomes to rejected setups. Commission is analysis-layer only and did not accelerate the configured breaker.

## Hindsight diagnostic — not promotion evidence

Current Strategy_Inventory cells rated PROMISING BUT UNPROVEN or better that actually occur in #346 are ORB Reclaim MNQ and ORB Reclaim MES.

| strategy / instrument | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_reclaim / MES | 104 | 75 | 29 | 72.1% | 22-53 | 29.3% | 0.831 | $-441.00 | $-4.24 | $-5.88 | 55.0% |
| orb_reclaim / MNQ | 27 | 11 | 16 | 40.7% | 3-8 | 27.3% | 0.608 | $-147.28 | $-5.45 | $-13.39 | 18.4% |

| half | Att | Fill | No-fill | Fill% | W-L | WR | PF | Net | Exp/att | Exp/fill | Share book |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 131 | 86 | 45 | 65.6% | 25-61 | 29.1% | 0.803 | $-588.28 | $-4.49 | $-6.84 | 73.3% |
| H2 | 0 | 0 | 0 | — | 0-0 | — | — | $0.00 | — | — | -0.0% |

Combined hindsight subset: 131 attempts, 86 fills, PF 0.803, net $-588.28. **This is hindsight-filtered and is not historical selection or promotion evidence.**

## Concentration and ranked root causes

- Top 1/3/5 losing trades consumed 2.2% / 6.7% / 11.1% of gross losing dollars.
- Largest losing day: 2025-09-08 ($-236.23); largest losing week: 2025-12-01 ($-236.15).
- Longest loss streak: 9 trades ($-410.82).

1. **Primary — broad negative filled-trade expectancy, led by ORB Reclaim and concentrated in London.** ORB Reclaim contributed $-588.28; London contributed $-612.18; the book had 71 losses versus 26 wins and both instruments were negative.
2. **Secondary — execution realism removed favorable as well as unfavorable candidates.** In the exact matched subset, no-fill selection accounts for $-4,059.68 of IOC-minus-market delta versus $-128.10 from changed filled-trade P&L; it does not misuse the identity-poor old ledger.
3. **Tertiary — commission deepened, but did not create, the loss.** Gross P&L was already -$658.72; $143.56 commission produced the -$802.28 net.

The shared-account breaker is a material **evidence-censoring interaction**, not a demonstrated cause of realized losses: it rejected 624 later qualified setups and removed all H2 observations.

## Explicit answers

1. The corrected book lost because filled trades had negative expectancy ($-8.27/fill), primarily ORB Reclaim, with commission adding $143.56 of loss.
2. Worst strategy: orb_reclaim ($-588.28).
3. Worst cell: orb_reclaim × MES ($-441.00).
4. Loss was broad across both instruments and most populated cells, but strategy-concentrated in ORB Reclaim; it was not a few-trade tail event.
5. MES lost more dollars ($-441.00) than MNQ ($-361.28), but neither instrument alone explains the book.
6. Cancellations dominate the matched execution effect. They removed 37 diagnostic market winners and 22 diagnostic market losses; changed P&L on filled candidates was only $-128.10. The old headline itself cannot support direct candidate causality.
7. Yes. The shared breaker materially censored qualified setups and all H2 evidence.
8. No. Current-PBU-or-better cells alone still lost $588.28.
9. No. This shared-account portfolio audit does not invalidate independently audited isolated-strategy results.
10. No new replay/execution correctness defect was found.

## Reproduction

```bash
CORPUS=/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/data/replay_corpus_v1_market_condition_fixed
IOC_LOGS=/private/tmp/corpus346_loss_audit_ioc_logs
MARKET_LOGS=/private/tmp/corpus346_loss_audit_market_logs

python3 scripts/corrected_ioc_corpus_evidence.py \
  --corpus "$CORPUS" --logs "$IOC_LOGS" \
  --out /private/tmp/corpus346-repro-results.json \
  --raw /private/tmp/corpus346-repro-raw.jsonl \
  --report /private/tmp/corpus346-repro-report.md

python3 scripts/corpus_v1_loss_attribution.py \
  --corpus "$CORPUS" \
  --ioc-logs "$IOC_LOGS" \
  --market-logs "$MARKET_LOGS" \
  --run-market-counterfactual
```
