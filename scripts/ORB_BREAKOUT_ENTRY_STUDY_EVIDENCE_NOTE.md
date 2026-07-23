# orb_breakout_entry_study — evidence note (2026-07-25)

**HISTORICAL RESEARCH EVIDENCE. NOT VALIDATION. NOT RUNTIME. NOT DEPLOYED.**

`orb_breakout_entry_study.py` and its output `orb_breakout_entry_study_results.json`
were found sitting uncommitted in the working tree during the 2026-07-25
repo-hygiene cleanup. Preserved here together because the result is not
otherwise reproducible from anything else in git.

## What this is
A read-only extension of `scripts/orb_market_entry_study.py` (PR #143,
validated 2026-07-02). Reuses the exact same journaled arms, 5-minute bars,
fill mechanism, and resolve harness as that prior study — zero new
assumptions — to break out `orb_breakout` vs `orb_reclaim` separately, and
runner vs static exit mode separately, which the original study only reported
as a combined blend.

## Why it can't be regenerated
The script reads:
- `logs/retest_baseline_off/{MES,MNQ}/journal_*.jsonl` (journaled TRADE/APPROVED
  decisions for orb_breakout/orb_reclaim)
- `data/replay_polygon_5m/{MES,MNQ}/*.jsonl` (5-minute bars)

Both paths are `.gitignore`'d and were never committed. Neither is this
script, until now. A fresh clone of this repo could not re-run this analysis
and reproduce this exact output — this JSON is the only durable record of
what that run found.

## Audit performed before preserving it (read-only, 2026-07-25)
- Confirmed research-only: no CLI flags or code path that places, sizes, or
  schedules a trade.
- Confirmed no runtime import/wiring: not imported anywhere outside itself;
  only imports `context.bar_history._parse_dt` (pure parsing) and
  `execution.broker_interface.BracketOrder` / `execution.paper_broker.{NextBarOHLC,
  PaperBroker}` (in-memory simulated fills only — no live broker/network call).
- Confirmed no env/config mutation and no secrets anywhere in the script.
- Confirmed no other script or module currently calls it.

## Status
This is a research finding, not a validated result and not a deployment
decision. It has not been reviewed for the same rigor as a promotion-gate
or forward-measurement study. Treat it exactly as its own docstring says:
"No deployment. No config change. Analysis only."
