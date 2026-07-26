# ORB Reclaim causal sub-15m trigger study

**Verdict: REJECT FASTER-ENTRY HYPOTHESIS — FULL CAUSAL TRIGGER POPULATION NEGATIVE OR NEAR BREAKEVEN**

Pinned code: `bc03eaf015626b439333cec77f6afb3fc6762fbd`
Corpus (gates + proofs): `4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4` (byte-identical to PR #346's corpus)
5m tier: `cd19164862fc22f86dea3e7d69f01d49bd505ada99a86ed10e5ffce36c85789f` (1242 files)
Window: 2025-07-24 → 2026-06-26 (corpus ∩ 5m availability, parity-excluded days removed)

## Question and posture

- PR #355's at-level edge was measured on 15m-confirmed signals only — a population a real sub-15m engine cannot know in advance. This pass builds the FULL causal trigger population at 5m granularity, including every false trigger the completed 15m bar never confirmed.
- Architecture simulated: a resting stop-buy at the frozen plan level (ORB high + 2 ticks), working only while causally-known gates pass (prev 5m close ≤ ORB high; level above last known VWAP; last COMPLETED 15m corpus bar TRENDING; GEX not positive-gamma), filling causally (gap → open ± slip, touch → level ± slip). No completed-15m information is used at or before any fill.
- Frozen bracket (entry/stop/target formulas), $1.48 RT commission, pessimistic fill-bar handling identical to PR #355 LEVEL, production `resolve_position` walk, sequential single-position lane per instrument, 1 contract, no breaker. Evidence orchestration only.

## Proof gates (all passed before simulation)

- Bracket reconstruction: 131/131 #346 orb_reclaim journaled plans reproduced exactly from corpus ORB fields via the frozen formula.
- Tier ORB parity: 478 days compared; 2 excluded for tier disagreement ({"MES": ["2025-09-09"], "MNQ": ["2025-09-09"]}).
- 15m recall: 105/130 (80.8%) of #346 orb_reclaim attempts on included days have a causal 5m trigger at/before their 15m decision bar close (parity metric; differences are real gate-timing effects).

## Pre-registered decision rule (operator's)

- Confirmed-subset positive while full population not material → lookahead artifact / reject.
- Full population material (net > 0 AND PF > 1.10 at both 1 and 2 ticks AND net > 0 at 3 ticks) → PROMISING BUT UNPROVEN, eligible for a separate architecture research lane.
- Otherwise → reject the faster-entry hypothesis.

## The discriminating split (1-tick slippage)

| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FULL causal population | 925 | 925 | 0 | 327 | 3 | 20.4% | $-11,724.63 | $-13,093.63 | $-14.16 | 0.601 |
| Confirmed by 15m (±20 min) | 532 | 532 | 0 | 187 | 1 | 28.0% | $-832.88 | $-1,620.24 | $-3.05 | 0.906 |
| FAILED later 15m (false triggers) | 393 | 393 | 0 | 140 | 2 | 10.2% | $-10,891.75 | $-11,473.39 | $-29.19 | 0.269 |

## By instrument (1 tick)

| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MNQ | 555 | 555 | 0 | 267 | 3 | 17.1% | $-9,341.50 | $-10,162.90 | $-18.31 | 0.476 |
| MES | 370 | 370 | 0 | 60 | 0 | 25.4% | $-2,383.13 | $-2,930.73 | $-7.92 | 0.782 |

## By session (1 tick)

| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| asian | 20 | 20 | 0 | 1 | 0 | 15.0% | $-453.50 | $-483.10 | $-24.16 | 0.378 |
| london | 386 | 386 | 0 | 63 | 2 | 25.6% | $-1,628.63 | $-2,199.91 | $-5.70 | 0.815 |
| new_york | 519 | 519 | 0 | 263 | 1 | 16.8% | $-9,642.50 | $-10,410.62 | $-20.06 | 0.483 |

## By half (1 tick)

| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| H1 | 460 | 460 | 0 | 142 | 1 | 22.8% | $-4,186.38 | $-4,867.18 | $-10.58 | 0.691 |
| H2 | 465 | 465 | 0 | 185 | 2 | 18.1% | $-7,538.25 | $-8,226.45 | $-17.69 | 0.518 |

## By quarter (1 tick)

| Scope | Triggers | Resolved | Open | Imm. stop | Gap fills | WR | Net gross | Net after $1.48 RT | Exp net | PF net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Q1 | 218 | 218 | 0 | 61 | 0 | 24.8% | $-1,346.75 | $-1,669.39 | $-7.66 | 0.769 |
| Q2 | 242 | 242 | 0 | 81 | 1 | 21.1% | $-2,839.63 | $-3,197.79 | $-13.21 | 0.625 |
| Q3 | 267 | 267 | 0 | 112 | 2 | 16.1% | $-5,144.50 | $-5,539.66 | $-20.75 | 0.453 |
| Q4 | 198 | 198 | 0 | 73 | 0 | 20.7% | $-2,393.75 | $-2,686.79 | $-13.57 | 0.614 |

## Slippage sensitivity (net after commission / PF)

| Slippage | Full population | Confirmed ±20min | Failed-later |
|---|---:|---:|---:|
| 1 tick | $-13,093.63 / 0.601 | $-1,620.24 / 0.906 | $-11,473.39 / 0.269 |
| 2 tick | $-14,408.62 / 0.576 | $-2,352.23 / 0.868 | $-12,056.39 / 0.257 |
| 3 tick | $-15,723.63 / 0.553 | $-3,084.24 / 0.832 | $-12,639.39 / 0.247 |
| 4 tick | $-17,038.62 / 0.530 | $-3,816.23 / 0.799 | $-13,222.39 / 0.237 |

## Comparison ladder (orb_reclaim slices, net after commission)

| Pass | Population | Net | PF |
|---|---|---:|---:|
| #346 IOC (system) | 131 attempts, 86 fills | $-588.28 | 0.803 |
| #354 market-at-close | same 131 | $-243.76 | 0.943 |
| #355 LEVEL (15m-confirmed only) | same 131 | $+724.37 | 1.187 |
| THIS PASS (full causal population) | 925 triggers | $-13,093.63 | 0.601 |
| — H1 subset (comparable window) | 460 triggers | $-4,867.18 | 0.691 |

## Audit and limitations

- Gate/arming audit (1 tick): `{"MES": {"armed_no_cross": 8244, "gate_blocked_market_condition": 24513, "gate_blocked_vwap": 1316, "not_armed_prev_close_above_level": 20475, "skipped_london_orb_developing": 451, "triggers_filled": 370}, "MNQ": {"armed_no_cross": 8915, "gate_blocked_market_condition": 24967, "gate_blocked_vwap": 1524, "not_armed_prev_close_above_level": 25324, "skipped_london_orb_developing": 466, "triggers_filled": 555}}`.
- H1 (≤2026-01-23) is the only window comparable to #346/#354/#355 — their populations are censored by the #346 breaker halting both instruments in H1. This pass's H2 rows carry NO breaker censoring and end at the 5m tier's last day; treat cross-pass comparisons as H1-only.
- Trigger-level evidence: the full 15m system's admission machinery (confluence ranking against other strategies, session budgets, one-position-per-account, risk sizing, breaker) is deliberately NOT reproduced beyond a sequential one-position lane per instrument. Population evidence, not account-path P&L.
- The market-condition gate uses the last COMPLETED 15m corpus bar — a real sub-15m engine pays exactly this staleness; the frozen 15m engine instead sees the (not-yet-known) decision bar's own value.
- Confirmation labels derive from the preserved #346 journals (TRADE setups + candidate + blocked-candidate rows, ±20 min).
- 1 contract, replay-scale dollars, historical evidence, not live-fill proof. Nothing here is an implementation recommendation unless the pre-registered material branch fired.

## Reproduction

```bash
python scripts/orb_reclaim_causal_5m_trigger_study.py \
  --corpus data/replay_corpus_v1_market_condition_fixed \
  --m5 data/replay_polygon_5m \
  --logs /private/tmp/corrected_ioc_corpus_logs \
  --out scripts/orb_reclaim_causal_5m_trigger_results.json \
  --raw scripts/orb_reclaim_causal_5m_trigger_raw.jsonl \
  --report docs/orb-reclaim-causal-5m-trigger-study-2026-07-26.md
```
