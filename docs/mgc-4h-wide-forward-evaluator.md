# MGC 4H wide forward observation — evaluator

Implements [`prereg-mgc-4h-wide-forward-2026-09-23.md`](prereg-mgc-4h-wide-forward-2026-09-23.md)
(#947). Where this note and the prereg disagree, the prereg wins.
**Observation / research only.** Scored offline from a fresh Polygon fetch;
there is no collector, VPS or runtime change.

## Files

| File | Role |
|---|---|
| `research/mgc_4h_wide_forward.py` | Fetch (dated contracts, causal prior-day-volume front, never rolls back), 24h settlement, gap days, 4H session resample, production detectors, wide geometry, blind counts, single look, step-0 parity |
| `scripts/mgc_4h_wide_forward.py` | CLI: `counts` (default, blind), `step0`, `look` |
| `tests/test_mgc_4h_wide_forward.py` | Frozen constants vs the prereg, chain rule, resample, geometry, gap rule, blindness, look refusals, verdicts, CLI guards |

## Runbook (on the Mac: the Polygon key is only in the local `.env`)

```text
python3 scripts/mgc_4h_wide_forward.py counts [--out counts.json]      # any time, blind
# at the look (>=150 trades and >=60 days, or after 2027-06-30):
scp hetzner:/root/autonomous-futures-system/logs/cross_instrument_observation_v1.jsonl obs.jsonl
python3 scripts/mgc_4h_wide_forward.py step0 --live-obs obs.jsonl --out step0.json
python3 scripts/mgc_4h_wide_forward.py look --step0-report step0.json --out look.json --confirm-single-look
```

## Reproduction check (2026-09-23, before the scoring start)

This was a scratch run only: the scoring start was set to 2026-01-01 in
memory, with no file change. It checked that the evaluator reproduces the
#946 MGC 4H WIDE result trade by trade.

| | #946 | Evaluator |
|---|---|---|
| Trades | 438 | 433 kept + 5 `VOID_GAP_DAY` = 438; identical trade keys |
| Net | $23,778.04 | $23,957.04 |
| PF | 1.306 | 1.311 |

Every discrepancy is explained:

- **+$61.00:** the 5 trades on gap days (the prereg's gap rule, which #946
  lacked) are voided. Their net was −$61.
- **+$118.00:** 4 trades from observation day 2026-09-22 expired at the
  day's last close.
  - #946's corpus was fetched at 2026-09-23T03:50Z and was missing that
    day's final hour (20:00–20:45Z).
  - So #946 closed those trades at the 19:45Z close; the settled fresh data
    closes them at 20:45Z.
  - Each trade differs by exactly $59.
  - This is what the 24h settlement rule prevents.
- **The trail-arming rule** (per the prereg, from the next 15m bar) changed
  no trade.
- **The other 434 trades are identical to the cent.**

## Forward start

- The first blind count (fetched 2026-09-23T13:10Z, settled cutoff
  2026-09-21T21:00Z) shows **0 terminal trades**.
- Scoring starts at the first 4H bar ≥ 2026-09-24T22:00:00Z, so no #946 data
  (which ended 2026-09-22) can enter the forward sample.
