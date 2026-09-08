# 4HR MNQ Gate Attribution

Status: **AUDIT ONLY — no risk or permission changes**.

The canonical 4HR state machine was replayed over the 5-minute 4HR audit
corpus. The current risk configuration and engine gate ordering were preserved.

## Population and signal

- 81 candidates, 47 short / 34 long
- corpus: `data/replay_corpus_v1_5m_4hr_audit/MNQ`
- dates: 2024-07-09 through 2026-06-19
- median structural stop: 254 ticks
- 75/81 candidates exceed the 120-tick MNQ stop cap
- median R:R: 0.937
- no-stop time controls were positive at 30m, 60m, 120m, and EOD
- EOD control: 79 resolved, PF 2.163, +$7,698.08 net

## Documented bracket

Plan-price fill with the documented fixed stop and day-only exit:

- 81 attempts, 80 resolved
- 49 wins / 31 losses / 2 day-only flatten
- **+$3,069.60**, PF **1.774**, max DD **$908.30**
- H1 **+$1,794.80**, H2 **+$1,274.80**

The structurally rejected candidates alone produced 76 resolved trades,
**+$3,076.02**, PF **1.797**, positive in both halves.

## Ordered first production disposition

| First blocker | Candidates | Historical bracket result |
|---|---:|---:|
| `MARKET_CONDITION_NOT_TRENDING` | 38 | -$824.74, PF 0.695 |
| `RR_BELOW_MINIMUM` | 19 | included in overlapping gate populations |
| `stop_too_wide` | 11 | included in overlapping gate populations |
| `ENTRY_DETACHED_FROM_PRICE` | 8 | included in overlapping gate populations |
| `WEAK_BAR_CLOSE` | 4 | included in overlapping gate populations |
| `IOC_NOT_FILLED` | 1 | no realized result |

The ordered funnel is therefore **38 + 19 + 11 + 8 + 4 = 80 rejected
candidates**, plus **one approved candidate that did not fill by IOC**. The
per-candidate ledger
preserves the exact overlapping gate values, stop ticks, R:R, bracket outcome,
and both fresh engine dispositions.

## Gate overlap and outcome diagnostic

Individual gate counts overlap by design:

- `stop_too_wide`: 75 candidates; 74 resolved; +$3,180.98; PF 1.847
- `rr_below_minimum`: 61 candidates; 60 resolved; +$1,253.70; PF 1.409
- `min_confluence_grade`: 12 candidates; 12 resolved; -$573.76; PF 0.423
- `target_too_close`: 8 candidates; 8 resolved; -$51.34; PF 0.609
- `MARKET_CONDITION_NOT_TRENDING`: 38 candidates; 38 resolved; -$824.74; PF 0.695
- `ENTRY_DETACHED_FROM_PRICE`: 14 candidates; 14 resolved; +$166.28; PF 1.336

These are diagnostic overlapping subsets, not additive P&L partitions.

## Execution funnel

With the current 32-tick MNQ IOC tolerance and one adverse tick:

- all 81 candidates: 44 fills, 43 resolved, +$1,731.86, PF 2.001
- structural survivors: 4 candidates, 2 fills, both losses, -$37.46
- the isolated production engine approved 1 candidate and that IOC did not fill

**Conclusion:** 4HR MNQ has useful signal and documented-bracket evidence, but
the current production architecture removes nearly all of it before execution.
The binding failure is **risk/signal gating**, especially the global stop-width
and R:R architecture, not IOC costs. No configuration or strategy changes were
made.

Machine-readable per-candidate artifact:
`scripts/four_hr_mnq_gate_attribution_2026-09-07.json`.
