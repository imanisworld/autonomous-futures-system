# FROZEN RULES R3 — frozen 2026-10-09 23:57:44 EDT, before any R3 data was fetched or tested
Instruments (1 contract each): MNQ ($2/pt, tick .25), MES ($5/pt, .25), M2K ($5/pt, .10), MGC ($10/pt, .10), MCL ($100/pt, .01), MBT ($0.10/pt, tick 5).
Data: Polygon futures daily aggs (resolution=1day) per contract; nothing on/after 2026-06-29 for any instrument. Continuous series: each day use the contract with the highest prior-day volume among listed contracts (roll forward only); prices additively back-adjusted so roll gaps carry no P&L. Holding through a roll costs one extra round trip (commission + slippage).
Signal (time-series momentum, one standard form): s_t = sign(close_t − close_{t−100}) on the adjusted series. Position target = s_t, entered/changed at next day's open.
Exit: on signal reversal (flip at next open), or 3×ATR(20) trailing stop from the highest close (long) / lowest close (short) since entry; ATR(20) = 20-day mean true range, prior bars only. Stop fills at stop or the open if gapped, 2 ticks adverse. After a stop, flat until the signal flips sign.
Costs: $1.48 per side + 2 ticks per side.
Primary = 1 contract per instrument, equal-contract basket. Secondary (not for selection): vol-scaled, contracts = round($500 / (ATR20 × $/pt)), min 0 (skip if 0) — reported only.
Windows: design = all available history through 2025-12-31 (first 100+20 days are warm-up); holdout 2026-01-01..2026-06-26. One evaluation each (tally #12 design, #13 holdout regardless of design result, as requested by the brief).
Benchmarks: buy-and-hold 1 contract of each (rolled, same costs at rolls); random-direction: each trade keeps its instrument, entry date and hold length, side drawn ±1 at random, 500 draws; p = share of draws with net ≥ rule net.
Required account: median per-trade initial risk (3×ATR20 × $/pt) / 2%; and max DD / 25%.
No parameter changes after this timestamp.
