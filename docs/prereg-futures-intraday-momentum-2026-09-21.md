# Prereg — futures intraday-momentum replication (`fim-v0.1`)

**Status: RESEARCH ONLY. Frozen before the first historical run.**

This study asks whether the published futures intraday-momentum effect is reproducible in the existing MNQ/MES replay corpus with causal 5-minute timing and current research cost assumptions.

Source motivation: Guido Baltussen, Zhi Da, Sten Lammers, and Martin Martens (JFE 2021) report that the return during the rest of the day predicts the final 30-minute futures return in the same direction across more than 60 futures markets. This repository study is a replication/screening exercise, not a claim that the paper's result automatically transfers to MNQ/MES.

Nothing here authorizes strategy promotion, a paper lane, Webull mirroring, risk-rule changes, alerts, or broker execution.

## Population

- Instruments: MNQ and MES, reported separately.
- Data: existing Polygon 5-minute RTH replay corpus under `data/replay_polygon_5m`.
- Session completeness and quarterly-roll exclusions: identical to `research/futures_non_strat_coverage.py`.
- Current session must contain all 78 RTH bars.
- Immediate prior complete session must exist and must not be a roll-excluded session.
- Frozen half split: H1 < 2025-09-01; H2 >= 2025-09-01.

## Causal signal and trade clock

For each eligible session:

1. Prior close = final 5-minute bar close of the immediate prior eligible RTH session.
2. Signal endpoint = close of the 15:25–15:30 ET bar.
3. Rest-of-day return = signal-endpoint close / prior close - 1.
4. If positive, signal LONG; if negative, signal SHORT; exact zero => no trade.
5. Entry decision/fill anchor = next bar open, the 15:30 ET bar open.
6. Exit anchor = final RTH bar close (15:55–16:00 ET).

This deliberately avoids using the 15:30 bar's future path to create the signal.

## Cost cells

Quantity: exactly 1 contract.

- Base: 1 tick adverse entry + 1 tick adverse exit + $1.24 round-turn commission.
- Stress: 2 ticks adverse entry + 2 ticks adverse exit + $1.24 round-turn commission.

No target, no overnight hold, no option/broker assumptions.

## Important safety limitation

The published effect is a time-exit effect and this primary replication has **no protective stop**. Therefore even a strong result can only be classified **PROMISING BUT UNPROVEN / research-only**. It can never be promoted directly into the repository's paper or broker-connected paths because the standing system rule forbids stopless trades.

If and only if this replication survives its gate, a separate preregistered risk-overlay study may test protective-stop designs. No stop may be outcome-fitted inside fim-v0.1.

## Required outputs

For each instrument × cost cell:

- eligible sessions, trades, long/short counts;
- H1/H2 n, net P&L, expectancy, PF, win rate;
- total net P&L, expectancy, PF, max drawdown;
- median and p90 absolute rest-of-day return;
- largest positive month / total positive-month profit;
- deterministic permutation-null p95 for total net P&L.

## Null benchmark

For each instrument/cost cell, preserve the realized final-30-minute outcomes and randomly permute the LONG/SHORT signal labels across eligible sessions using seed 56021 and 5,000 permutations. This destroys signal/outcome association while preserving sample size, direction balance, costs, and realized market paths.

The report records the 95th percentile of permuted total net P&L.

## Pre-registered screening gate

An instrument passes the numeric screen only if:

1. H1 >= 100 trades and H2 >= 100 trades;
2. base net > 0 and PF > 1.10 in both halves;
3. stress net > 0 in both halves;
4. total base net exceeds that instrument's permutation-null p95;
5. top positive month contributes < 60% of total positive-month profit;
6. no timing/session identity defect is found.

The study is cross-instrument promising only if both MNQ and MES pass all numeric conditions.

Even then final classification remains **PROMISING BUT UNPROVEN**, never VALIDATED, and no paper lane is opened without a separately preregistered stop/risk overlay.
