# Missed-Move / NO_TRADE Gate-Classification Sweep — 622-Day Replay Set

Scope: 9779 large-move windows found across the full 622-day replay set (2024-07-01 to 2026-06-26, both instruments), non-overlapping 4-bar blocks, thresholds MES>=15.0pt / MNQ>=60.0pt (CLI-overridable defaults). For every 15m bar inside a flagged window, this reads the box's own already-computed decision row (`failed_gates`, `shadow_candidates`) — no new signal-detection or setup logic is invented here.

Total classified bars across all move windows: 39112

## Classification breakdown

| classification | count | % |
|---|---:|---:|
| DETECTED_BUT_BLOCKED | 25062 | 64.1% |
| NO_ROW_LOGGED | 4331 | 11.1% |
| NO_GATE_LOGGED | 4230 | 10.8% |
| STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | 3182 | 8.1% |
| DETECTED_AND_TRADED | 1537 | 3.9% |
| NO_COVERED_STRUCTURE_PRESENT | 770 | 2.0% |

## Gate-category breakdown (DETECTED_BUT_BLOCKED rows)

| category | count |
|---|---:|
| trend_condition | 12818 |
| volume | 9396 |
| regime_or_structure | 3952 |
| entry_mechanics | 2848 |

## Individual gate-code breakdown

| gate | count |
|---|---:|
| SIGNAL_BAR_VOLUME_TOO_LOW | 9396 |
| TREND_STRENGTH_BELOW_REQUIRED | 5362 |
| WEAK_BAR_CLOSE | 3952 |
| MARKET_CONDITION_NOT_TRADABLE | 3415 |
| ENTRY_DETACHED_FROM_PRICE | 2848 |
| EMA_STACK_NOT_ALIGNED_SOFT | 2119 |
| EMA_STACK_NOT_ALIGNED | 1316 |
| MARKET_CONDITION_NOT_TRENDING | 606 |

## Examples per classification

| instrument | day | bar_ts | classification | gate | strategy |
|---|---|---|---|---|---|
| MES | 2024-07-01 | 2024-07-01T13:30:00+00:00 | DETECTED_BUT_BLOCKED | MARKET_CONDITION_NOT_TRENDING |  |
| MES | 2024-07-01 | 2024-07-01T13:45:00+00:00 | DETECTED_AND_TRADED |  | vwap_hold |
| MES | 2024-07-01 | 2024-07-01T14:00:00+00:00 | NO_ROW_LOGGED |  |  |
| MES | 2024-07-01 | 2024-07-01T14:15:00+00:00 | STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | WEAK_BAR_CLOSE |  |
| MES | 2024-07-01 | 2024-07-01T14:30:00+00:00 | DETECTED_BUT_BLOCKED | ENTRY_DETACHED_FROM_PRICE | orb_breakout |
| MES | 2024-07-01 | 2024-07-01T14:45:00+00:00 | NO_COVERED_STRUCTURE_PRESENT | WEAK_BAR_CLOSE | orb_breakout |
| MES | 2024-07-01 | 2024-07-01T15:00:00+00:00 | DETECTED_BUT_BLOCKED | EMA_STACK_NOT_ALIGNED |  |
| MES | 2024-07-01 | 2024-07-01T15:15:00+00:00 | DETECTED_BUT_BLOCKED | EMA_STACK_NOT_ALIGNED_SOFT |  |
| MES | 2024-07-01 | 2024-07-01T15:30:00+00:00 | NO_GATE_LOGGED |  |  |
| MES | 2024-07-01 | 2024-07-01T15:45:00+00:00 | DETECTED_BUT_BLOCKED | MARKET_CONDITION_NOT_TRADABLE |  |
| MES | 2024-07-02 | 2024-07-02T14:30:00+00:00 | STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | WEAK_BAR_CLOSE |  |
| MES | 2024-07-02 | 2024-07-02T14:45:00+00:00 | DETECTED_AND_TRADED |  | orb_reclaim |
| MES | 2024-07-02 | 2024-07-02T17:00:00+00:00 | NO_ROW_LOGGED |  |  |
| MES | 2024-07-02 | 2024-07-02T17:15:00+00:00 | NO_ROW_LOGGED |  |  |
| MES | 2024-07-02 | 2024-07-02T17:30:00+00:00 | NO_ROW_LOGGED |  |  |
| MES | 2024-07-02 | 2024-07-02T17:45:00+00:00 | NO_ROW_LOGGED |  |  |
| MES | 2024-07-05 | 2024-07-05T14:15:00+00:00 | NO_GATE_LOGGED |  |  |
| MES | 2024-07-05 | 2024-07-05T14:30:00+00:00 | DETECTED_AND_TRADED |  | orb_reclaim |
| MES | 2024-07-10 | 2024-07-10T16:30:00+00:00 | NO_GATE_LOGGED |  |  |
| MES | 2024-07-10 | 2024-07-10T16:45:00+00:00 | NO_GATE_LOGGED |  |  |
| MES | 2024-07-10 | 2024-07-10T19:00:00+00:00 | STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | WEAK_BAR_CLOSE |  |
| MES | 2024-07-10 | 2024-07-10T19:30:00+00:00 | NO_GATE_LOGGED |  |  |
| MES | 2024-07-11 | 2024-07-11T12:45:00+00:00 | STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | WEAK_BAR_CLOSE |  |
| MES | 2024-07-11 | 2024-07-11T14:15:00+00:00 | DETECTED_AND_TRADED |  | orb_breakout |
| MES | 2024-07-11 | 2024-07-11T15:00:00+00:00 | STRUCTURE_PRESENT_BUT_NOT_QUALIFIED | WEAK_BAR_CLOSE |  |
| MES | 2024-07-11 | 2024-07-11T16:30:00+00:00 | NO_COVERED_STRUCTURE_PRESENT | WEAK_BAR_CLOSE |  |
| MES | 2024-07-12 | 2024-07-12T13:45:00+00:00 | DETECTED_AND_TRADED |  | orb_reclaim |
| MES | 2024-07-16 | 2024-07-16T14:15:00+00:00 | NO_COVERED_STRUCTURE_PRESENT | WEAK_BAR_CLOSE |  |
| MES | 2024-07-18 | 2024-07-18T17:00:00+00:00 | NO_COVERED_STRUCTURE_PRESENT | WEAK_BAR_CLOSE |  |
| MES | 2024-07-18 | 2024-07-18T19:15:00+00:00 | NO_COVERED_STRUCTURE_PRESENT | WEAK_BAR_CLOSE |  |

## Overall verdict: `OVERFILTERED`

Reading: `STRUCTURE_PRESENT_BUT_NOT_QUALIFIED` means the box's own shadow-evaluation layer recognized a structural candidate at that bar even though the live decision was NO_TRADE — this is the closest observable signal to "a real setup existed and a gate blocked it," but it is not proof the shadow candidate would have won; it only says a structure was present. `NO_COVERED_STRUCTURE_PRESENT` means not even the shadow layer saw anything — the weaker, more honest reading of "detection gap" than asserting a specific missing strategy.

## Notes

- `shadow_candidates` presence is used as the structure-presence signal (not `context.orb`/`context.vwap` fields, which the replay journal writer does not populate — confirmed 0/84 in a sampled day; only the live box's journal writer includes the richer `context` block).
- Gate-category taxonomy is a closed, mechanical mapping from the confirmed `failed_gates` vocabulary; an unrecognized future gate code raises loudly instead of silently landing in `other`.
- This is docs/script/tests only — zero changes to execution/, risk/, config/, risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo orders, no strategy promotion or demotion.
