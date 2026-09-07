# 4HR Re-Trigger MNQ — Gate Ablation Closure

_As of 2026-09-07. This file now records the completed gate-ablation/architecture investigation. Do not use the prior ablation-only interpretation to justify a routing change._

## Final verdict

**BROKEN FOR CURRENT EXECUTABLE FORM / HOLD / NO ROUTING FIX.**

The initial 81-candidate gate attribution and single-gate ablation showed positive standalone signal/bracket evidence and made the trend gate look like the primary edge-destroying blocker. That was not sufficient because the ablation did not expose all downstream full-engine gates.

## Completed evidence sequence

### Gate attribution

- population: 81 MNQ candidates;
- documented bracket: **+$3,069.60, PF 1.774**;
- structurally rejected candidates: **+$3,076.02, PF 1.797**;
- 80/81 rejected before execution;
- all-candidate IOC: 44 fills, **+$1,731.86, PF 2.001**.

### One-gate ablation

| Arm | Candidates | Fills | Net | PF | H1 / H2 |
|---|---:|---:|---:|---:|---:|
| Current gates | 1 | 0 | $0.00 | n/a | $0 / $0 |
| Remove trending | 43 | 17 | **+$1,560.82** | **4.993** | +$44.66 / +$1,516.16 |
| Remove R:R | 20 | 9 | +$330.68 | 2.143 | -$214.92 / +$545.60 |
| Remove stop width | 6 | 4 | -$98.42 | 0.000 | -$51.46 / -$46.96 |
| Remove detached entry | 67 | 44 | **+$1,731.86** | **2.001** | +$283.92 / +$1,447.94 |
| Remove weak close | 77 | 40 | +$1,289.80 | 1.746 | +$75.40 / +$1,214.40 |

### Architecture audit

- canonical 4HR rules describe a reversal setup rather than a generic continuation strategy;
- the global market-condition gate applies before setup dispatch;
- MNQ `require_strong_trend=True` remains active downstream;
- detached-entry remains a valid stale-bracket safety check;
- no gate change was authorized from architecture inspection alone.

### Full-engine trend isolation — binding closure

| Arm | Result |
|---|---|
| Current | 1 attempt, 0 fills, `$0` |
| Exempt market-condition only | 37 then fail `TREND_STRENGTH_BELOW_REQUIRED`; 1 attempt, 0 fills |
| Exempt strong-trend only | identical to current; market-condition gate blocks first |
| Exempt both | 28 fail `EMA_STACK_NOT_ALIGNED`; 2 attempts, 1 fill, 1 loss, **-$7.98**, PF 0.00 |

This full-engine result is binding. The positive `remove_trending` ablation was diagnostic but not executable because it did not expose the downstream strong-trend and EMA-stack gates.

## Decision

- Do not exempt 4HR from market-condition gating.
- Do not exempt 4HR from strong-trend gating.
- Do not loosen EMA-stack, R:R, stop-width, detached-entry, or weak-close gates.
- Do not parameter-tune this 81-candidate population.
- Do not reopen this route unless genuinely new evidence changes the executable picture.

## Evidence artifact

Reported local full-engine closure:

- `docs/4hr-mnq-trend-gate-isolation-2026-09-07.md`

Historical gate-attribution and ablation artifacts remain provenance only.

## Stop condition

**Met. 4HR MNQ gate investigation is closed. No code/configuration change is justified.**
