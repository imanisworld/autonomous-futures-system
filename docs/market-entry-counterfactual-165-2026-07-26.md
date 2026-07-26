# Market-entry counterfactual on the 165 corrected-IOC attempts

**Verdict: MIXED — MISSED ATTEMPTS PROFITABLE AT HONEST MARKET ENTRY BUT FULL POPULATION STILL NEGATIVE**

Pinned code: `bc03eaf015626b439333cec77f6afb3fc6762fbd`
Corpus: `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (626 files — byte-identical to PR #346's corpus)
Attempt population: the 165 committed PR #346 order attempts (97 IOC-filled / 68 IOC-no-fill), joined 165/165 to the preserved #346 journals by `paper_order_id`.
Range: 2025-07-24 → 2026-07-23

## Question and posture

- PR #346 (system path, IOC-limit entries, breaker on) → PF 0.753, -$802.28: cannot separate a strategy problem from execution selection.
- This pass: SAME frozen order plans, honest AGGRESSIVE entry — fill at the decision bar's close ± adverse slippage via the production `force_market_entry` branch of `PaperBroker` (the live #259 proof-mode semantics). Stop/target stay at ordered prices; pessimistic stop-first same-bar resolution; stop exits pay adverse slippage; target fills clean.
- Isolated per-attempt simulation (fresh broker each attempt): no account path, no breaker — deliberate, because #346 already measured the system path and its breaker censored H2 to zero attempts. Attempt-matched means H1-weighted by construction (all 165 attempts are H1).
- $1.48 round-trip commission at the analysis layer; slippage sensitivity at 1/2/3/4 ticks (1 tick = primary, the #346 canonical posture).
- Evidence orchestration only: zero strategy/replay/broker/risk/config/deployment/Pine edits.

## The discriminating answer (1-tick slippage)

| Cohort | Attempts | Resolved | WR | Net after commission | PF | Verdict input |
|---|---:|---:|---:|---:|---:|---|
| The 68 IOC no-fills | 68 | 68 | 66.2% | $118.46 | 1.069 | primary discriminator |
| The 97 IOC fills | 97 | 97 | 26.8% | $-807.78 | 0.751 | vs $-802.28 IOC actual (same 97) |
| All 165 | 165 | 165 | 43.0% | $-689.32 | 0.861 | signal-level headline |

### Paired comparison on the 97 IOC-filled attempts

- IOC actual (PR #346, same 97 rows): $-802.28
- Market-entry counterfactual, same 97: $-807.78
- Mean per-attempt delta (CF − IOC): $-0.06
- Attempts where CF result class differs from IOC: 0

## Cohorts (1-tick slippage)

| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ALL 165 | 165 | 165 | 0 | 43.0% | $-445.12 | $-689.32 | $-4.18 | 0.861 | 30.2029 | 0 | 0 |
| IOC_NO_FILL 68 | 68 | 68 | 0 | 66.2% | $219.10 | $118.46 | $1.74 | 1.069 | 61.4418 | 0 | 0 |
| IOC_FILLED 97 | 97 | 97 | 0 | 26.8% | $-664.22 | $-807.78 | $-8.33 | 0.751 | 8.3035 | 0 | 0 |

## By instrument (all 165, 1-tick)

| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 61 | 61 | 0 | 50.8% | $-263.24 | $-353.52 | $-5.80 | 0.745 | 55.2702 | 0 | 0 |
| MES | 104 | 104 | 0 | 38.5% | $-181.88 | $-335.80 | $-3.23 | 0.906 | 15.5 | 0 | 0 |

## By strategy (all 165, 1-tick)

| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 20 | 20 | 0 | 65.0% | $-212.00 | $-241.60 | $-12.08 | 0.410 | 74.85 | 0 | 0 |
| orb_reclaim | 131 | 131 | 0 | 41.2% | $-49.88 | $-243.76 | $-1.86 | 0.943 | 22.916 | 0 | 0 |
| orb_rejection | 3 | 3 | 0 | 0.0% | $-49.50 | $-53.94 | $-17.98 | 0.000 | 24.0 | 0 | 0 |
| pdl_reclaim | 1 | 1 | 0 | 0.0% | $-30.50 | $-31.98 | $-31.98 | 0.000 | 32.0 | 0 | 0 |
| vwap_reclaim | 10 | 10 | 0 | 40.0% | $-103.24 | $-118.04 | $-11.80 | 0.318 | 38.048 | 0 | 0 |

## By direction (all 165, 1-tick)

| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| LONG | 154 | 154 | 0 | 42.9% | $-322.62 | $-550.54 | $-3.57 | 0.885 | 28.2239 | 0 | 0 |
| SHORT | 11 | 11 | 0 | 45.5% | $-122.50 | $-138.78 | $-12.62 | 0.231 | 57.9091 | 0 | 0 |

## No-fill cohort by strategy (1-tick)

| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| orb_breakout | 17 | 17 | 0 | 70.6% | $-198.00 | $-223.16 | $-13.13 | 0.349 | 85.9412 | 0 | 0 |
| orb_reclaim | 45 | 45 | 0 | 64.4% | $416.62 | $350.02 | $7.78 | 1.268 | 53.2889 | 0 | 0 |
| orb_rejection | 1 | 1 | 0 | 0.0% | $-26.00 | $-27.48 | $-27.48 | 0.000 | 43.0 | 0 | 0 |
| vwap_reclaim | 5 | 5 | 0 | 80.0% | $26.48 | $19.08 | $3.82 | 1.531 | 55.208 | 0 | 0 |

## Slippage sensitivity (all 165 / no-fill 68, net after commission)

| Slippage | All-165 net | All-165 PF | No-fill-68 net | No-fill-68 PF |
|---|---:|---:|---:|---:|
| 1 tick | $-689.32 | 0.861 | $118.46 | 1.069 |
| 2 tick | $-944.81 | 0.817 | $42.97 | 1.024 |
| 3 tick | $-1,200.32 | 0.775 | $-32.54 | 0.982 |
| 4 tick | $-1,455.81 | 0.737 | $-108.03 | 0.941 |

## Audit and limitations

- Join integrity: 165/165 attempts matched to preserved #346 journal TRADE rows by identity; fill/no-fill split re-verified 97/68; every simulated fill price re-asserted equal to decision close ± slippage.
- Degenerate brackets at entry (fill already past target / past stop): 0 / 0 of 165 — resolved mechanically by the production broker (pessimistic).
- Cross-day resolutions: 7 (post-#339/#342 carry-forward semantics); unresolved at corpus end: 0.
- Isolated per-attempt design measures the SIGNAL, not the account path: no compounding, no breaker, no position blocking. It cannot say what a market-entry SYSTEM would have done H2 (the IOC system halted in H1; a market-entry system would have generated a different attempt set — that requires a full-corpus system replay, out of scope here).
- All 165 attempts are H1 by construction (the #346 breaker halted both instruments before H2), so this pass inherits that censoring and says nothing about H2.
- Entry detachment moves realized R:R away from plan R:R (stop farther, target nearer for late fills); mean adverse entry degradation is 30.2029 ticks (all-165, 1-tick tier).
- Dollar magnitudes are replay-scale. Historical evidence, not live-fill proof.

## Reproduction

```bash
python scripts/market_entry_counterfactual_165.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --logs /private/tmp/corrected_ioc_corpus_logs \
  --raw-attempts scripts/corrected_ioc_corpus_raw_trades.jsonl \
  --out scripts/market_entry_counterfactual_165_results.json \
  --raw scripts/market_entry_counterfactual_165_raw.jsonl \
  --report docs/market-entry-counterfactual-165-2026-07-26.md
```
