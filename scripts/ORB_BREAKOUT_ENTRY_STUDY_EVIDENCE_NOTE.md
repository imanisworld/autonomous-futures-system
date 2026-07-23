# orb_breakout_entry_study — evidence note (2026-07-25)

**HISTORICAL RESEARCH EVIDENCE. NOT VALIDATION. NOT RUNTIME. NOT DEPLOYED.**

`orb_breakout_entry_study_results.json` was found sitting uncommitted in the
working tree during the 2026-07-25 repo-hygiene cleanup, alongside the script
that produced it.

**Correction to an earlier claim in this same cleanup pass**: the producing
script, `scripts/orb_breakout_entry_study.py`, is *not* new here — it has
been committed on `main` since PR #261 ("Add MNQ orb_breakout entry study:
market entry needs runner exit"), byte-identical to what's on disk. An
earlier report in this session incorrectly stated the script itself was
uncommitted; it was not. Only this results file is new to git — added here
because the run's *inputs*, not its code, are ephemeral.

## What this is
A read-only extension of `scripts/orb_market_entry_study.py` (PR #143,
validated 2026-07-02). Reuses the exact same journaled arms, 5-minute bars,
fill mechanism, and resolve harness as that prior study — zero new
assumptions — to break out `orb_breakout` vs `orb_reclaim` separately, and
runner vs static exit mode separately, which the original study only reported
as a combined blend.

## Why it can't be regenerated
The (already-committed) script reads:
- `logs/retest_baseline_off/{MES,MNQ}/journal_*.jsonl` (journaled TRADE/APPROVED
  decisions for orb_breakout/orb_reclaim)
- `data/replay_polygon_5m/{MES,MNQ}/*.jsonl` (5-minute bars)

Both paths are `.gitignore`'d and were never committed. A fresh clone of this
repo has the code but not that exact input snapshot, so it could not
reproduce this exact output — this JSON is the only durable record of what
that run found, even though the code itself is safe.

## Audit performed before preserving the results (read-only, 2026-07-25)
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
