# 4HR MNQ One-Gate Ablation

Audit only. The same frozen 81-candidate population was used for every arm.
All arms used one MNQ contract, 32-tick IOC tolerance, one adverse tick, $1.48
commission, and pessimistic same-bar handling.

## Arm semantics (corrected 2026-09-07)

The arms in `scripts/four_hr_mnq_gate_ablation_2026-09-07.json` are keyed
`remove_<gate>`, but their candidate counts identify them as **apply only that
gate** arms, not ablation arms: 43 = 81 − 38 candidates failing trending,
6 = 81 − 75 candidates over the 120-tick stop cap, 20 = 81 − 61 failing R:R,
67 = 81 − 14 detached. The table below is relabelled accordingly. The JSON keys
are unchanged.

| Arm (only this gate applied) | Candidates | Fills | Resolved | Net | PF | Expectancy/fill | Max DD | H1 / H2 net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| All current gates | 1 | 0 | 0 | $0.00 | n/a | n/a | $0.00 | $0.00 / $0.00 |
| Only trending (`remove_trending`) | 43 | 17 | 16 | **+$1,560.82** | **4.993** | +$97.55 | $195.46 | +$44.66 / +$1,516.16 |
| Only R:R (`remove_rr`) | 20 | 9 | 9 | +$330.68 | 2.143 | +$36.74 | $214.92 | -$214.92 / +$545.60 |
| Only stop width (`remove_stop_width`) | 6 | 4 | 4 | -$98.42 | 0.000 | -$24.61 | $98.42 | -$51.46 / -$46.96 |
| Only detached entry (`remove_detached`) | 67 | 44 | 43 | **+$1,731.86** | **2.001** | +$40.28 | $405.36 | +$283.92 / +$1,447.94 |
| Only weak close (`remove_weak_close`) | 77 | 40 | 40 | +$1,289.80 | 1.746 | +$32.25 | $448.38 | +$75.40 / +$1,214.40 |

Read as a per-gate quality ranking: a positive arm means the candidates that
gate *passes* are profitable, i.e. the gate is a good filter on its own.

## True one-gate ablation

Recomputed from the per-candidate ledger in
`scripts/four_hr_mnq_gate_attribution_2026-09-07.json` (all gates applied
except the named one; documented-bracket basis, plan-price fill):

| Gate removed | Admitted | Bracket net |
|---|---:|---:|
| none (current) | 1 | +$102.02 |
| `MARKET_CONDITION_NOT_TRENDING` | 4 | -$6.42 |
| `rr_below_minimum` | 1 | +$102.02 |
| `stop_too_wide` | 12 | **+$1,846.24** |
| `ENTRY_DETACHED_FROM_PRICE` | 1 | +$102.02 |
| `WEAK_BAR_CLOSE` | 1 | +$102.02 |

## Readout

- **Stop-width cap is the binding gate.** It is the only gate whose removal
  admits a materially larger population (12 candidates, +$1,846 bracket), the
  same 12 trades #372 found when lifting the cap, with the same concentration
  caveat (profit concentrated in two months at ~2.7× the policy stop width).
  Its "only stop width" arm being negative means the 6 candidates *inside* the
  cap are poor, not that loosening the cap is unjustified.
- **Trending gate is a good filter in isolation.** Its 38 rejects are
  -$824.74 / PF 0.695 on the documented bracket; its 43 survivors are
  +$3,894 of bracket P&L and +$1,560.82 under IOC. Removing it alone admits
  only 4 candidates because the stop cap sits behind it.
- **Detached-entry and weak-close gates** pass broad positive populations; they
  are not bottlenecks.
- **R:R gate** survivors are positive overall but not walk-forward robust
  (H1 negative); removing it alone admits nothing new.

These are diagnostic runs, not permission to change production gates. The
results do not account for the separate `SHADOW_ONLY` strategy-permission
posture; no permission or risk setting was changed.

Machine-readable output:
`scripts/four_hr_mnq_gate_ablation_2026-09-07.json` (arm keys retain their
original `remove_*` names).
