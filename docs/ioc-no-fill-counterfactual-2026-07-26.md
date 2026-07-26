# Corrected IOC no-fill counterfactual settling test

**Verdict: MIXED / NEAR BREAKEVEN AFTER COSTS — WAIT**

There is no proof of recoverable edge and no basis to change IOC.

Pinned code: `bc03eaf015626b439333cec77f6afb3fc6762fbd`
Cohort source: `69ec77fd33834a437fec77a51249fa1d66030a16` (68 exact cancelled order identities)
Corpus: `/Users/djb.a.e/MAINVSCODE/autonomous-futures-system/data/replay_corpus_v1_market_condition_fixed`
Corpus hash: `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (626 files)

## Test contract

- Primary population is exactly the 68 IOC no-fills selected while the frozen 20% drawdown breaker was enabled in PR #346.
- Each trade is settled independently so counterfactual P&L cannot retroactively change the matched cohort.
- Original signal identity, timestamp, instrument, strategy, session, direction, contracts, stop, target, and static exit logic are unchanged.
- The sole counterfactual change is entry: decision-bar close plus one tick of adverse market slippage (higher for LONG, lower for SHORT).
- Stop exits receive the same one-tick adverse slippage; targets remain resting-limit fills.
- If one future bar contains both stop and target, the stop wins.
- $1.48 round-trip commission is deducted at the analysis layer.
- No breaker-off full-year diagnostic was run. No runtime, strategy, risk, broker, config, deployment, or Pine logic was changed.

## Primary result

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 68 NO-FILLS | 68 | 68 | 0 | 45-23-0 | 66.2% | $219.10 | $118.46 | $1.74 | $-13.10 to $16.26 | 1.069 | $633.11 |

## By strategy

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 17 | 17 | 0 | 12-5-0 | 70.6% | $-198.00 | $-223.16 | $-13.13 | $-31.92 to $3.70 | 0.349 | $242.70 |
| orb_reclaim | 45 | 45 | 0 | 29-16-0 | 64.4% | $416.62 | $350.02 | $7.78 | $-12.98 to $28.17 | 1.268 | $487.07 |
| orb_rejection | 1 | 1 | 0 | 0-1-0 | 0.0% | $-26.00 | $-27.48 | $-27.48 | $-27.48 to $-27.48 | 0.000 | $27.48 |
| vwap_reclaim | 5 | 5 | 0 | 4-1-0 | 80.0% | $26.48 | $19.08 | $3.82 | $-17.25 to $19.64 | 1.531 | $35.92 |

## By instrument

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MES | 29 | 29 | 0 | 18-11-0 | 62.1% | $153.12 | $110.20 | $3.80 | $-23.96 to $30.63 | 1.114 | $487.07 |
| MNQ | 39 | 39 | 0 | 27-12-0 | 69.2% | $65.98 | $8.26 | $0.21 | $-14.77 to $15.07 | 1.011 | $281.34 |

## By session

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 4 | 4 | 0 | 4-0-0 | 100.0% | $60.38 | $54.46 | $13.62 | $5.20 to $22.03 | ∞ | $0.00 |
| london | 25 | 25 | 0 | 16-9-0 | 64.0% | $181.18 | $144.18 | $5.77 | $-17.20 to $28.46 | 1.256 | $183.41 |
| new_york | 39 | 39 | 0 | 25-14-0 | 64.1% | $-22.46 | $-80.18 | $-2.06 | $-23.04 to $18.50 | 0.930 | $633.18 |

## By direction

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 61 | 61 | 0 | 40-21-0 | 65.6% | $261.10 | $170.82 | $2.80 | $-13.29 to $18.51 | 1.105 | $547.21 |
| SHORT | 7 | 7 | 0 | 5-2-0 | 71.4% | $-42.00 | $-52.36 | $-7.48 | $-29.77 to $10.16 | 0.443 | $90.42 |

## Conditional-selection diagnostic

This comparison is descriptive, not a causal portfolio rerun.

## Filled versus rejected cohorts

| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| REALIZED IOC FILLS | 97 | 97 | 0 | 26-71-0 | 26.8% | $-658.72 | $-802.28 | $-8.27 | $-20.84 to $4.63 | 0.753 | $1,073.61 |
| REJECTED → MARKET CF | 68 | 68 | 0 | 45-23-0 | 66.2% | $219.10 | $118.46 | $1.74 | $-13.10 to $16.26 | 1.069 | $633.11 |
| MECHANICAL UNION (NON-CAUSAL) | 165 | 165 | 0 | 71-94-0 | 43.0% | $-439.62 | $-683.82 | $-4.14 | $-13.78 to $5.58 | 0.862 | $1,355.83 |

## Interpretation boundary

- This test estimates the outcome of the signals IOC rejected, conditional on the frozen breaker-on selection path.
- It does not rehabilitate the legacy market-fill Corpus v1 result, because it neither reruns that population nor removes the corrected market-condition and replay semantics.
- It does not prove a live market order would receive exactly the modeled fill. The result is an adverse-slippage historical counterfactual, not live-fill evidence.
- Arithmetic combination with the 97 realized IOC fills is not a causal portfolio replay: filling these trades could suppress later attempts while positions are open and could alter breaker timing.

## Audit checks

- Exact stored no-fill identities joined: `68`.
- Unique identities: `68`.
- Decision candles found: `68`.
- Frozen brackets valid after market fill: `68`.
- Pessimistic both-hit outcomes: `1`.
- Open at corpus end: `0`.

## Reproduction

```bash
python scripts/ioc_no_fill_counterfactual.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --logs /private/tmp/corrected_ioc_corpus_logs \
  --source-raw scripts/corrected_ioc_corpus_raw_trades.jsonl \
  --out scripts/ioc_no_fill_counterfactual_results.json \
  --raw scripts/ioc_no_fill_counterfactual_trades.jsonl \
  --report docs/ioc-no-fill-counterfactual-2026-07-26.md
```
