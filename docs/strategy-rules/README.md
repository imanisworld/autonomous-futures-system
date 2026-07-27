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

## Validation status (as documented, not yet independently reproduced here)

- **4HR:** MNQ 84.4% / MES 78.6% target *touch* over 479 sessions (Jul 2024–Jun 2026).
  The reported edge used **fixed-distance** stops; the documented fixed completed-1H stop P&L
  is **not yet validated** (see `4HR_ReTrigger_Rules.md` §14).
- **Miyagi:** MNQ +$102.35 / MES +$25.78 expectancy at T1 exit, literal stop (n=13/20).
- **3-2-2:** BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS (parity validation, 2026-07-27).
  The honest-fill figure below (MNQ net $1,595.70, PF 10.36, 18W-2L, n=34 candidates,
  20 resolved) never exercised any real runtime risk gate; run through the actual
  wired-in engine, 0/34 reach a fill — `max_stop_ticks` and `min_confluence_grade`
  eliminate the entire population, even with every proven parity defect removed.
  See `Strategy_Inventory.md` and `60M_322_PARITY_VALIDATION_BROKEN_2026-07-27.md`.
  MES marginal, QQQ unconfirmed, IWM rejected.

> Target-touch rates are not P&L. Options profitability (premium/spread/theta) and
> walk-forward stability remain open items per each doc's "not yet validated" section.
