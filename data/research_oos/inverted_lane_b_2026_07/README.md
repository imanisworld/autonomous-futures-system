# Inverted Lane B untouched OOS extension

Research-only MNQ five-minute extension, kept physically separate from
`data/replay_polygon_5m/MNQ` so the frozen 490-trade sample cannot be
silently rewritten.

Fetch command:

```bash
python3 scripts/polygon_to_replay.py \
  --symbol MNQ \
  --start 2026-06-27 \
  --end 2026-07-26 \
  --timeframe 5 \
  --warmup-days 10 \
  --out <temporary-directory>
```

Observed output:

- 24 files and 5,472 bars, ending 2026-07-24.
- File-tree SHA-256:
  `5cd69692cdd9707ec3520d0498a1a666b611dd527c92e9611f8d1b21c6c4585e`.
- 2,136 independently downloaded overlap bars from 2026-06-17 through
  2026-06-26 matched the frozen cache exactly on timestamp and OHLCV
  (zero mismatches).
- A pre-baseline availability probe for 2024-05-15 through 2024-06-30
  returned zero bars; no alternate data was substituted.

The extension produced 19 eligible close-momentum observations from
2026-06-29 through 2026-07-24. Full validation and provenance are in
`scripts/inverted_lane_b_paper_candidate_results.json`.
