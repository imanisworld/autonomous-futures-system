# Underlying edge screen on Polygon bars, 2026-10-11

Source-only research. No collector change, no trading authority, and no options-profitability claim.

This is a cross-check of the 66-symbol screen in `docs/research-notes/options-underlying-edge-2026-10-11/`. It reuses that screen's frozen entry, invalidation, target, hold, and dedupe rules by loading `run_underlying_edge.py` unchanged. It differs in two ways:

- The bars are adjusted Polygon 5-minute aggregates, not Alpaca SIP.
- It adds exit-R measurement, so each family gets an expectancy rather than only a 1R-before-stop hit rate.

The Alpaca screen had already seen this same 2026-04-01..2026-09-08 window. This run is therefore not an independent sample. The sealed 2026-09-09..15 week is excluded, and the runner exits if the dataset reaches 2026-09-09.

## Result

Per-setup expectancy counts a setup that never triggers as 0R. The same-stop baseline enters at the next open after the setup and uses the strategy's structural stop price. That makes it the comparison the verdict rule uses. The registered baseline, with its tighter pre-entry stop distance, is still recorded in `results.json` and is not the advantage test.

| Family | Eligible | Executed | Strategy R per setup | Same-stop baseline R per setup | Executed-trade R | 2026-08-14..09-08 R per setup | Scorer verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| Higher-timeframe pullback | 7,171 | 1,029 | +0.0034 | −0.2047 | +0.0236 | −0.0114 | TEST FURTHER |
| Failed-break reclaim | 11,850 | 6,950 | −0.0160 | −0.1403 | −0.0273 | −0.0128 | REJECT |
| Compression → expansion | 7,260 | 2,659 | +0.0010 | −0.0448 | +0.0027 | −0.0426 | TEST FURTHER |

The two `TEST FURTHER` labels are mechanical. Each family beat a losing baseline over the full window but was negative in the 2026-08-14..09-08 slice, so the robustness check failed. Their full-sample expectancy is under 0.01R per setup. That is flat, not an edge. Failed-break reclaim lost money per setup on every slice: full, before the scanner overlap, during it, single names, and the ETF cluster.

The 1R hit rates match the Alpaca screen to within about half a point. Pullback scored 46.0% against 46.4%, failed-break 44.4% against 44.3%, and compression 41.3% against 41.3%. The bar source does not explain the gap between hit rate and expectancy.

## Decision status

The uncommitted 2026-10-11 session closeout recorded an operator verdict on this screen:

- pullback **HOLD research** (flat)
- failed-break **REJECT**
- compression **HOLD research** (flat)

It also said that beating the losing baseline is not an edge, that the definitions should not be retuned, and that no options test should follow.

That verdict was not confirmed again for this note.

Like the #1222 screen, this run was scored without a preregistration in the shared trial ledger. Under the 2026-10-18 governance correction (#1228), #1222 is recorded as `UNREGISTERED_ATTEMPT` / `INVALID_EVIDENCE`. This run has the same status:

- Its numbers are historical research notes only.
- They are not an Inventory, handoff or promotion evidence claim.

No ledger row has been added for this run. Recording one is an operator decision.

## Dataset and reproduction

`data/options_research_polygon_5m_20260401_20260908/` is local-only and gitignored. It holds adjusted Polygon 5-minute bars for 66 of 66 symbols over 110 NYSE sessions, with exact regular-session grids and file hashes recorded in its `manifest.json`. The runner checks those hashes before scoring.

Re-run from a checkout that holds that directory:

```bash
.venv/bin/python docs/research-notes/options-polygon-edge-2026-10-11/run_polygon_edge.py
```

On 2026-10-19 this re-run passed the hash check and rewrote `episodes.jsonl` (26,281 rows) and `results.json` byte-for-byte:

- `episodes.jsonl` sha256 `1505a1b3…dc5e2`
- `results.json` sha256 `9b33ff7b…3501a`

Definition version `underlying-edge-v1-2026-10-11`. The 1-2-2 collector is unchanged.
