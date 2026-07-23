# Strategy Rules — Manual Setups (operator source of truth)

Complete, executable trading rules for the operator's manual Strat-based setups.
These are the authoritative rule specs referenced during validation and any future
promotion into `strategy/`. They are **reference material** — not yet wired into
`strategy/signal_engine.py`. See `docs/vp-futures-strategy-snapshot.md` for the
higher-level compilation these expand on.

| File | Strategy | Instruments | Documented stop |
|---|---|---|---|
| `4HR_ReTrigger_Rules.md` | 4HR Re-Trigger (reversal, 4H candles) | QQQ options, MNQ, MES | Last completed 1H candle at actual entry, **fixed forever** |
| `12HR_Miyagi_Rules.md` | 12HR Miyagi (1-3-1 reversal, 12H candles) | QQQ options, MNQ, MES | **Literal** last-completed 60-min boundary, fixed to T1 (ratchet explicitly rejected) |
| `60M_322_FirstLive_Rules.md` | 60M 3-2-2 First Live (3-bar reversal, 60M) | MNQ only | Fixed opposite-9AM-boundary structural stop |

## Validation status

- **4HR:** detector reconciled 94/94. IOC-faithful MNQ replay filled 41/94 and
  returned +$1,960.16 at two ticks adverse slippage each side; both chronological
  halves were positive, but H2 produced most of the profit.
- **Miyagi:** detector reconciled 13/13. Six midpoint touches became only three IOC
  fills; H1 was negative and LONG had zero fills. Verdict remains WAIT.
- **3-2-2:** detector reconciled 32/32. IOC-faithful MNQ replay filled 20/32 and
  returned +$1,537.70; both halves and directions remained positive through four
  ticks adverse slippage.

These are research replays, not authorization for live execution. See
`HONEST_FILL_REPLAY_RESULTS.md` for assumptions, splits, and limitations.
