# Saved — ORB / VWAP timeframe × window sweep (2026-10-10)

**Kind:** Exploratory research only. Not forward proof. Not a preregistered trial.

## Method

- **Script:** `scripts/orb_vwap_tf_window_sweep.py`
- **Machine results:** `scripts/orb_vwap_tf_window_sweep_results.json` (same bytes as `results.json` in this folder)
- **Instrument:** MNQ, **NY session only**
- **Bar TFs:** 10m (5m resample), **15m** (`replay_corpus_v1_market_condition_fixed`), 30m (5m resample)
- **Windows (bar close, America/New_York):** morning **09:40–11:00**, afternoon **13:30–16:00**
- **Strategies:** `orb_reclaim`, `orb_breakout`, `vwap_hold` — production `_try_*` predicates
- **Fills:** `ioc_limit` at decision-bar close, 1 adverse tick, 32-tick tolerance; one position at a time
- **`require_trending_condition`:** off for predicate extraction (production gate not re-applied here)

## Headline IOC resolved P&L

### Morning 09:40–11:00

| TF | ORB reclaim | ORB breakout | VWAP hold |
|----|-------------|--------------|-----------|
| 10m | +$18.86, PF 1.02, n=27 | −$608, n=48 | −$127, n=49 |
| 15m | −$493, n=27 | −$292, n=20 | −$227, n=13 |
| 30m | −$318, n=9 | −$8.72, n=14 | −$233, n=16 |

### Afternoon 13:30–16:00

| TF | ORB reclaim | ORB breakout | VWAP hold |
|----|-------------|--------------|-----------|
| 10m | −$98, n=31 | −$67, n=9 | −$466, n=57 |
| 15m | −$98, n=13 | −$65, n=2 | −$269, n=22 |
| 30m | −$34, n=14 | −$117, n=6 | −$119, n=27 |

### Best NY-wide slice (reference)

- **10m ORB reclaim, all NY:** +$352.14, PF 1.103, **132** resolved (252 no-fill)
- **15m ORB reclaim, all NY:** −$170.70, PF 0.921, 65 resolved
- **VWAP:** negative on every TF × window in this run

## Verdict for planning

No demo-ready ORB/VWAP lane from TF + clock window alone under IOC. Only weak signal: **10m ORB reclaim** (especially full NY). **15m** (audit-aligned) stays negative in the requested windows. **VWAP** remains negative with very low fill rates.

**DO NOT REDO** this exact sweep unless corpora paths, script, or window definitions change.
