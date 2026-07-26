# Corrected Corpus v1 + IOC evidence pass

**Verdict: HISTORICAL EDGE DOES NOT SURVIVE CORRECTED IOC TEST**

Pinned code: `e8f2fe23fa05e488d5aad3427a277642ed7d2c56` (PR #342 ancestor: `True`)
Corpus: `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (626 files)
Range: 2025-07-24 → 2026-07-23

## Canonical posture

- Corrected post-#338 `market_condition` corpus, verified with zero parity mismatches.
- Post-#339/#342 ReplayEngine position identity and cross-day resolution behavior.
- `entry_fill_model=ioc_limit` applied in memory only.
- IOC tolerance: MES=16 ticks; MNQ=32 ticks.
- Static exits; 1-tick adverse PaperBroker slippage; pessimistic stop-first same-bar resolution.
- $1.48 round-trip commission deducted only at the analysis layer.
- Frozen strategy rules, permissions, selection order, sizing, and risk controls.

## Full funnel

| Stage | Count |
|---|---:|
| Raw candidate bars (journal-visible) | 12425 |
| Regime-admitted candidate bars | 5553 |
| Market-condition-blocked candidate bars | 6872 |
| Orders attempted | 165 |
| IOC filled | 97 |
| IOC cancelled / no-fill | 68 |
| Resolved trades | 97 |
| Open trades | 0 |

“Raw candidate” is one decision bar with at least one candidate exposed by `candidate_audit` or the observation-only `blocked_candidate_audit`. CHOPPY/DEAD return before candidate collection and therefore cannot be invented into this count; the report separately preserves those gate counts.

## Overall

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| COMBINED | 165 | 97 | 58.8% | 68 | 97 | 0 | 26.8% | $-658.72 | $-802.28 | $-8.27 | 0.753 | $1,073.61 |

## By instrument

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 61 | 22 | 36.1% | 39 | 22 | 0 | 18.2% | $-328.72 | $-361.28 | $-16.42 | 0.434 | $361.28 |
| MES | 104 | 75 | 72.1% | 29 | 75 | 0 | 29.3% | $-330.00 | $-441.00 | $-5.88 | 0.831 | $848.15 |

## By strategy

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 20 | 3 | 15.0% | 17 | 3 | 0 | 33.3% | $-14.00 | $-18.44 | $-6.15 | 0.723 | $66.46 |
| orb_reclaim | 131 | 86 | 65.6% | 45 | 86 | 0 | 29.1% | $-461.00 | $-588.28 | $-6.84 | 0.803 | $1,025.57 |
| orb_rejection | 3 | 2 | 66.7% | 1 | 2 | 0 | 0.0% | $-23.50 | $-26.46 | $-13.23 | 0.000 | $26.46 |
| pdh_reclaim | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |
| pdl_reclaim | 1 | 1 | 100.0% | 0 | 1 | 0 | 0.0% | $-30.50 | $-31.98 | $-31.98 | 0.000 | $31.98 |
| vwap_hold | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |
| vwap_reclaim | 10 | 5 | 50.0% | 5 | 5 | 0 | 0.0% | $-129.72 | $-137.12 | $-27.42 | 0.000 | $137.12 |
| vwap_rejection | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |

## By direction

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 154 | 93 | 60.4% | 61 | 93 | 0 | 28.0% | $-578.22 | $-715.86 | $-7.70 | 0.774 | $1,063.19 |
| SHORT | 11 | 4 | 36.4% | 7 | 4 | 0 | 0.0% | $-80.50 | $-86.42 | $-21.61 | 0.000 | $86.42 |

## H1 / H2

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 165 | 97 | 58.8% | 68 | 97 | 0 | 26.8% | $-658.72 | $-802.28 | $-8.27 | 0.753 | $1,073.61 |
| H2 | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |

## Quarter

| Scope | Attempts | Fills | Fill rate | No-fill | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Max DD net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 125 | 70 | 56.0% | 55 | 70 | 0 | 28.6% | $-231.22 | $-334.82 | $-4.78 | 0.847 | $893.35 |
| Q2 | 40 | 27 | 67.5% | 13 | 27 | 0 | 22.2% | $-427.50 | $-467.46 | $-17.31 | 0.559 | $514.70 |
| Q3 | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |
| Q4 | 0 | 0 | — | 0 | 0 | 0 | — | $0.00 | $0.00 | — | — | $0.00 |

## Tail and concentration

- Largest win after commission: $123.52
- Largest loss after commission: $-72.73
- Top-1 winner concentration: 5.1%
- Top-3 winner concentration: 15.1%
- Top-5 winner concentration: 24.8%

## Comparisons

- Superseded market-fill Corpus v1: 747 attempts, 45.2% WR, PF 1.959, $54,124.93 reported net. It remains **SUPERSEDED / parity-invalid** and is not rehabilitated by this rerun.
- Corrected IOC pass: 165 attempts, 58.8% fill rate, 26.8% WR, PF 0.753, $-802.28 after commission.
- Delta versus the old headline P&L: $-54,927.21.
- Prior 622-day IOC static study (different period/code, breaker-off) was negative on both instruments: MES -$1,550 / MNQ -$1,523 with 36–37% fills. It is context, not a matched rerun.

## Audit and limitations

- Risk rejections: `{"max_drawdown": 624}`.
- Drawdown-breaker audit: `{"MES": {"first_rejection_bar_ts": "2025-12-11T16:30:00+00:00", "first_rejection_date": "2025-12-11", "last_order_attempt_date": "2025-12-11", "reason": "Account drawdown 22.0% exceeds max 20.0% from peak $1,500.00."}, "MNQ": {"first_rejection_bar_ts": "2025-09-08T09:45:00+00:00", "first_rejection_date": "2025-09-08", "last_order_attempt_date": "2025-09-08", "reason": "Account drawdown 21.9% exceeds max 20.0% from peak $1,500.00."}}`.
- MNQ stopped admitting orders on 2025-09-08 and MES on 2025-12-11; therefore H2/Q3/Q4 have zero attempts by design, not missing replay files.
- The primary run preserves the configured 20% drawdown breaker. No breaker-off or other post-result diagnostic was run because that would no longer be the frozen system.
- Commission is analysis-layer only, so it does not accelerate the ReplayEngine's drawdown halt; it only makes reported expectancy and P&L more conservative.
- Coverage audit: `{"input_files": 626, "journal_files": 623, "missing_replay_reports": [], "replay_reports": 626, "zero_decision_files_without_journal": ["MES/2025-09-21", "MES/2025-09-28", "MES/2025-11-30"]}`.
- Combined drawdown sequences the two independently replayed instrument lanes by historical decision time; per-instrument drawdowns are the account-specific values.
- Dollar magnitudes are replay-scale. This is historical evidence, not live-fill proof.

## Reproduction

```bash
python scripts/corrected_ioc_corpus_evidence.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --logs /private/tmp/corrected_ioc_corpus_logs \
  --out scripts/corrected_ioc_corpus_results.json \
  --raw scripts/corrected_ioc_corpus_raw_trades.jsonl \
  --report docs/corrected-ioc-corpus-evidence-2026-07-26.md
```
