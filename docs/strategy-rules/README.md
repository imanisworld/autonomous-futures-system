# Strategy Rules — Manual Setups (operator source of truth)

Complete, executable trading rules for the operator's manual Strat-based setups.
These are the authoritative rule specs referenced during validation and promotion
into `strategy/`. The 4HR rule is wired through the canonical persisted state
machine used by `strategy/signal_engine.py`; the other rules remain reference
material. See `docs/vp-futures-strategy-snapshot.md` for the higher-level compilation.

| File | Strategy | Instruments | Documented stop |
|---|---|---|---|
| `4HR_ReTrigger_Rules.md` | 4HR Re-Trigger (reversal, 4H candles) | QQQ options, MNQ, MES | Opposite boundary of last completed 1H candle at actual entry, **fixed** |
| `12HR_Miyagi_Rules.md` | 12HR Miyagi (1-3-1 reversal, 12H candles) | QQQ options, MNQ, MES | **Literal** last-completed 60-min boundary, fixed to T1 (ratchet explicitly rejected) |
| `60M_322_FirstLive_Rules.md` | 60M 3-2-2 First Live (3-bar reversal, 60M) | MNQ only | Fixed opposite-9AM-boundary structural stop |

## Validation status (as documented; see `Strategy_Inventory.md` for the current,
reconciled verdict as of 2026-07-27 — this section keeps the original external/manual
study figures as provenance only)

- **4HR:** MNQ 84.4% / MES 78.6% target *touch* over 479 sessions (Jul 2024–Jun 2026),
  target-touch only, not P&L. The fixed completed-1H stop P&L question this section used
  to flag as unvalidated was resolved 2026-07-26 (#334): **MNQ PROMISING BUT UNPROVEN**,
  n=80, net +$3,069.60, PF 1.774, both walk-forward halves positive, stable 1-3 tick —
  now collecting **paper-forward evidence** (#335). **MES: OVERFIT**, H2 erases H1's edge
  and flips negative at 3-tick slippage — excluded from runtime.
- **Miyagi:** MNQ +$102.35 / MES +$25.78 expectancy at T1 exit (n=13/20) is a
  non-reproducible external manual study, kept as provenance only. Current verdict:
  **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS** (2026-07-27) — causally-corrected stop
  distances fit inside the account's `max_stop_ticks` cap for only 0/8 MNQ and 2/10 MES
  real historical triggers. Never wired to `main`.
- **3-2-2:** MNQ net $1,595.70, PF 10.36, 18W-2L honest-fill (n=34 candidates, 20
  resolved) was produced by a standalone research function with no dependency on any real
  runtime gate. Current verdict: **BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS**
  (2026-07-27) — 0/34 candidates ever reach fill through the real
  `ReplayEngine → DecisionEngine → RiskEngine → PaperBroker` path, including a
  hypothetical pass with every proven parity defect removed (`max_stop_ticks` alone
  eliminates 27/34). Runtime wiring stays deployed/untouched — already fail-closed.

> Target-touch rates are not P&L. Options profitability (premium/spread/theta) remains an
> open item. See `Strategy_Inventory.md` for ORB Reclaim, VWAP family, ORB Breakout
> (including the inverted paper-forward lane, #364), and MES 1-2-2 (#337/#359) — none of
> those are Strat-based manual setups tracked by this doc's table above.
