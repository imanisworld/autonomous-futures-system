# Strategy Rules — Manual Setups (operator source of truth)

Complete, executable trading rules for the operator's manual Strat-based setups.
These are the authoritative rule specs referenced during validation and any future
promotion into `strategy/`. They are **reference material** — not yet wired into
`strategy/signal_engine.py`. See `docs/vp-futures-strategy-snapshot.md` for the
higher-level compilation these expand on.

| File | Strategy | Instruments | Documented stop |
|---|---|---|---|
| `4HR_ReTrigger_Rules.md` | 4HR Re-Trigger (reversal, 4H candles) | QQQ options, MNQ, MES | **1H flip — dynamic/ratcheting**, most-recent completed 1H candle |
| `12HR_Miyagi_Rules.md` | 12HR Miyagi (1-3-1 reversal, 12H candles) | QQQ options, MNQ, MES | **Literal** last-completed 60-min boundary, fixed to T1 (ratchet explicitly rejected) |
| `60M_322_FirstLive_Rules.md` | 60M 3-2-2 First Live (3-bar reversal, 60M) | MNQ only | Fixed opposite-9AM-boundary structural stop |

## Validation status (as documented, not yet independently reproduced here)

- **4HR:** MNQ 84.4% / MES 78.6% target *touch* over 479 sessions (Jul 2024–Jun 2026).
  The reported edge used **fixed-distance** stops; the documented **1H flip** stop P&L
  is **not yet validated** (see `4HR_ReTrigger_Rules.md` §14).
- **Miyagi:** MNQ +$102.35 / MES +$25.78 expectancy at T1 exit, literal stop (n=13/20).
- **3-2-2:** MNQ +$66.50 expectancy (n=31). MES marginal, QQQ unconfirmed, IWM rejected.

> Target-touch rates are not P&L. Options profitability (premium/spread/theta) and
> walk-forward stability remain open items per each doc's "not yet validated" section.
