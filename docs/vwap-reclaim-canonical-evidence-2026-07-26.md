# VWAP Reclaim — canonical evidence, isolated + honest-fill walk-forward

**Verdict: WAIT — fails both-halves-positive walk-forward under honest fills (H1 net=243.1, H2 net=-82.39); MNQ sample below 30-trade minimum (n=21); fails 1/2/3-tick slippage sensitivity (edge does not survive 3-tick adverse)**

Pinned code: `4526cd0988749bd44dd68465640605ddb59d6df2`
Corpus: `data/replay_corpus_v1_market_condition_fixed` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)
Range: 2025-07-24 → 2026-07-23

## Method

- **Isolated** single-strategy replay (`enabled_concepts=["vwap_reclaim"]` only, `disabled_concepts_per_instrument` cleared) — own fresh account per run, so the frozen 20% drawdown breaker (if it trips) reflects only `vwap_reclaim`'s own P&L, never contamination from other strategies sharing the combined book.
- `entry_fill_model="ioc_limit"` in memory only (PR #346's corrected posture) — canonical per-root tolerance MES=16 / MNQ=32 ticks, not overridden.
- Primary pass: 1-tick adverse PaperBroker slippage (config default). Sensitivity passes: 2-tick and 3-tick, same isolation/corpus, only `fill_slippage_ticks` varied.
- $1.48 round-trip commission at the analysis layer only.
- Frozen strategy rules, sizing, and risk controls throughout. `risk_rules.yaml` hash verified unchanged before/after (`56677a0ab37bbf62…`).
- MES included (evidence-only — `disabled_concepts_per_instrument` cleared for THIS isolated run only, never on disk) because the audit doc flagged the production MES disable rationale ("40% WR" `risk_rules.yaml` comment) as unsourced/unreproducible. **This is diagnostic, not a recommendation to enable MES.**

## Primary pass (1-tick) — overall

| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| COMBINED | 136 | 70 | 51.5% | 70 | 37.1% | $264.31 | $160.71 | $2.30 | 1.074 | $480.43 | ✅ |

## By instrument (1-tick)

| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 50 | 21 | 42.0% | 21 | 38.1% | $-35.24 | $-66.32 | $-3.16 | 0.802 | $214.06 | ❌ |
| MES | 86 | 49 | 57.0% | 49 | 36.7% | $299.55 | $227.03 | $4.63 | 1.124 | $480.43 | ✅ |

## Walk-forward H1/H2 (1-tick)

| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 74 | 46 | 62.2% | 46 | 37.0% | $311.18 | $243.10 | $5.28 | 1.217 | $309.72 | ✅ |
| H2 | 62 | 24 | 38.7% | 24 | 37.5% | $-46.87 | $-82.39 | $-3.43 | 0.921 | $480.43 | ❌ |

Both halves positive: **False**

## Quarter (1-tick)

| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 42 | 28 | 66.7% | 28 | 25.0% | $-198.82 | $-240.26 | $-8.58 | 0.704 | $309.72 | ❌ |
| Q2 | 32 | 18 | 56.2% | 18 | 55.6% | $510.00 | $483.36 | $26.85 | 2.568 | $115.88 | ❌ |
| Q3 | 28 | 9 | 32.1% | 9 | 55.6% | $353.80 | $340.48 | $37.83 | 2.652 | $166.09 | ❌ |
| Q4 | 34 | 15 | 44.1% | 15 | 26.7% | $-400.67 | $-422.87 | $-28.19 | 0.497 | $480.43 | ❌ |

## Slippage sensitivity (1/2/3-tick, overall)

| Scope | Attempts | Fills | Fill rate | Resolved | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net | n≥30 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1tick | 136 | 70 | 51.5% | 70 | 37.1% | $264.31 | $160.71 | $2.30 | 1.074 | $480.43 | ✅ |
| 2tick | 136 | 70 | 51.5% | 70 | 37.1% | $122.24 | $18.64 | $0.27 | 1.009 | $382.33 | ✅ |
| 3tick | 136 | 70 | 51.5% | 70 | 37.1% | $71.34 | $-32.26 | $-0.46 | 0.985 | $397.33 | ✅ |

Survives 1/2/3-tick (PF>1 and net>0 at every tier): **False**

## Historical comparators (context only — NOT walk-forward-valid)

- n=29 (2026-07-09, MNQ NY, `ioc_limit_runner`): 48.3% WR, $466.96 net. provenance/context only -- not walk-forward split, predates #338/#339/#342 corpus corrections
- n=50 (2026-07-25, MNQ all-session, Corpus v1 market-fill): 56.0% WR, PF 4.309, $3,759.00 net. provenance/context only -- market-fill (not ioc_limit), whole-corpus H1/H2 split reported (not vwap_reclaim's own halves), predates #339/#342 corpus corrections, superseded as combined-book evidence by PR #346's 'HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST' finding
- Neither historical figure is comparable to this pass's numbers: different fill model (market vs ioc_limit), not walk-forward split, and (n=50) combined-book/pre-#339/#342 corpus.

## Reproduction

```bash
python scripts/vwap_reclaim_canonical_evidence.py \
  --logs logs/replay_vwap_reclaim_canonical \
  --out scripts/vwap_reclaim_canonical_evidence_results.json \
  --raw scripts/vwap_reclaim_canonical_evidence_raw_trades.jsonl \
  --report docs/vwap-reclaim-canonical-evidence-2026-07-26.md
```
