# Strategy-Matrix Tranche 1 — Component-Role Study (2026-07-14)

Operator-directed study: test the existing entry strategies, context signals,
and exit models alone and in one-at-a-time combinations, classify each
component's actual role, and rank restoration candidates by evidence. This
document is the research record for the first tranche. **No runtime, config,
or deployment change ships with this PR** — analysis artifacts only.

## Dataset identity

- **Arm set**: `logs/retest_baseline_off/{MES,MNQ}` — real signal-engine TRADE
  decisions (`decision=="TRADE"` and `risk_check.result=="APPROVED"`) from the
  622-day replay batch, spanning 2024-07-02 → 2026-06-25.
- **Arm counts**: MNQ — orb_breakout 63, orb_reclaim 253, pdh_reclaim 68,
  pdl_reclaim 13, vwap_hold 348, vwap_reclaim 260. MES — orb_breakout 217,
  orb_reclaim 305, pdh_reclaim 160, vwap_hold 774.
- **Bars**: `data/replay_polygon_5m/{MES,MNQ}` 5-minute bars for fills and
  resolution.
- **vwap_hold population fingerprint** (for the paired study):
  sha256 `18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880`
  over sorted `(bar_ts, direction, entry, stop, target)` tuples, n=348, all
  SHORT.

## Fill, exit, and cost assumptions

- **Fill (baseline legs)**: unbounded market entry — the proof-lane model
  (PR #259/#281/#282). Order arrives at the first 5m bar at/after
  `bar_ts + 15min`; fills at that bar's open + 1 adverse tick, or at a touch
  of the level within 20 minutes when the market hasn't reached it yet.
- **Legacy fill (paired study only)**: the real `PaperBroker`
  `entry_fill_model="ioc_limit"` with the live pin
  `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=32`; `market_price` = the same arrival
  bar's open. Unmarketable → `ENTRY_NOT_FILLED`, as live books it.
- **Resolution**: the real `PaperBroker` (`pessimistic_both_hit=True`), the
  same runtime formulas the box runs. Runner = activation 1.0R, trail 0.5R.
  Partial = 2×1-contract approximation (one static + one runner bracket).
- **Costs**: after-cost cells subtract $1.24 commission round-turn + 2 ticks
  round-turn slippage per contract (MNQ $2.24/RT, MES $4.99/RT total drag).

## Headline results (MNQ, runner exit, after cost)

| Strategy | n resolved | Exp/trade | PF | WF halves | Verdict |
|---|---|---|---|---|---|
| orb_reclaim | 240 | +$23.12 | 1.71 | +30.67 / +16.05 | Strongest; already paper_sim |
| orb_breakout | 60 | +$15.16 | 1.65 | +24.40 / +5.91 | Confirms PR #282 lane |
| vwap_hold | 341 | +$10.51 | 1.52 | +7.19 / +13.73 | **Verdict flipped — see paired study** |
| vwap_reclaim | 260 | +$3.14 | 1.18 | both + | Thin but real |
| pdl_reclaim | 13 | +$45.63 | 4.96 | both + | Promising, under-sampled |
| pdh_reclaim | 67 | −$4.65 | 0.73 | both − | **Reject (negative both instruments)** |

MES: negative or fails walk-forward everywhere except orb_reclaim at 0.5×
stops (+$7.89, halves +11.58/+4.29) — recorded, no posture change.

## vwap_hold paired fill-model comparison (verification)

Required before the vwap_hold restoration lane: prove the newly-positive
result is an execution-model correction, not a different backtest. One arm
population (fingerprint above), two legs, identical arrival bar, stop,
runner, and costs — only the fill mechanism differs:

| Leg | Filled | Net (raw) | Exp/trade | PF | Halves |
|---|---|---|---|---|---|
| old anchored IOC (tol 32t) | 105/348 (30%) | $1,171.74 | +$11.16 | 1.98 | +18.38 / +4.08 |
| new market entry | 343/348 (99%) | $4,346.92 | +$12.75 | 1.67 | +9.27 / +16.20 |

- The market leg **independently reproduces the tranche-1 headline to the
  cent** (net $4,346.92, exp 12.75, WR 0.501, PF 1.668), recomputed from
  journals + bars, not read from the tranche-1 output.
- **The correction is throughput, not subset-rescue**: IOC starves 70% of
  candidates (the documented 2026-06-26 blocker). Under runner exits, even
  the IOC-fillable subset is mildly positive — the historical "fillable
  subset loses" finding was measured under **static exits**, which this
  tranche independently shows is the harmful component. The 2026-06-26
  demotion decomposes into: IOC starvation (confirmed) + static-exit drag
  (the actual per-trade loser).
- NY-only: old +$18.13 (n=35) vs new +$22.72 (n=106).
- Per-arm old/new outcome pairs are recorded in
  `scripts/vwap_hold_paired_fill_comparison_results.json`.

## Component role map

| Component | Best current role | Evidence |
|---|---|---|
| ORB reclaim | Entry strategy | n=240, +$23.12 after cost, halves + |
| ORB breakout | Entry strategy | n=60, +$15.16 after cost, halves + |
| VWAP hold | Entry strategy, NY session | n=341 (+$10.51); NY +$22.72 PF 2.18 |
| VWAP reclaim | Secondary entry, NY session | NY-only +$18.09 (london negative) |
| PDH reclaim | Reject | Negative both instruments, no filter rescues |
| PDL reclaim | Observation until sample grows | n=13, +$45.63, PF 4.96 |
| Strat patterns | Confluence/ranking input | +5.71/+7.27 exp deltas, n=44/36 — too thin to gate |
| EMA-200 alignment | Strategy-specific filter (orb_breakout only) | +$17.40→+$23.67, net dollars UP; hurts orb_reclaim −1.49 |
| Trend gate | Global hard blocker (settled) | Constant ON in arms; live-P&L-validated 2026-06-19 |
| Volume confirmation | Entry-definition component only | As added filter: orb_reclaim −10.31, pdh −23.29 |
| Session | Per-strategy modifier, never global | vwap families NY; orb_breakout degrades NY-only (17.40→9.73) |
| Runner | The exit model | Positive every MNQ strategy; static ≈0/negative everywhere |
| Static exit | Harmful | Negative or ~0 on all six MNQ cells |
| Partial + runner | Redundant at current size | No exp gain over runner, ~2× drawdown |
| 0.5× stops | Future drawdown lever, not a change now | orb_reclaim DD $798→$227, PF 2.10, modest exp cost |
| 2× stops | Harmful | Worse everywhere with adequate n |
| GEX flip/walls/mids | Live context/gate; historical effectiveness unproven | Placeholder NEUTRAL in replay — no historical test possible |
| Signa | Live gate; historically untestable | Same placeholder limitation |
| Supply/demand | Context implementation exists (MarketState zones + gex_gate conflict checks); **no standalone executable entry strategy** | `context/market_context.py:153-177`, `strategy/gex_gate.py:37-55` |
| Volatility triggers | Fields exist (`vol_trigger_up/down`); no validated standalone entry strategy | `context/market_context.py:105-106` |
| Liquidity sweep | Shadow observation only | `strategy/shadow_setups.py`; no entry path |
| orb_rejection | Zero-signal diagnosis needed, not restoration | 0 arms in 622 days |

## Limitations

- Trend, VWAP-alignment, and EMA-9/21 filters are constant across all arms
  (part of the entry definitions or a gate that was ON) — untestable here.
- GEX, Signa, regime, and HTF-direction fields carry placeholder/degraded
  values in replay (feeds did not exist historically). Their only evidence
  path is the live observe-only journals now accumulating. No cell in this
  study tests them.
- pdl_reclaim n=13 and all `strat_confirmed` subsets are too thin to gate on.
- The partial-exit model is a 2×1-contract approximation (no partial-fill
  simulation exists yet).
- Filter tests are post-hoc subsets of one runner pass — interaction effects
  between filters are tranche-2 scope; per the study design, no
  all-signals-combined cell was run.

## Recommended restoration queue (evidence-ranked)

1. **MNQ vwap_hold, NY-only, market entry + runner, proof lane** (operator-
   approved next lane; observe_only first; bounded gate: first clean natural
   candidate or one complete eligible NY session).
2. MNQ vwap_reclaim, NY-only — same pattern, after vwap_hold.
3. pdl_reclaim — observation until the sample grows.
4. orb_rejection — queued for zero-signal diagnosis only.
5. pdh_reclaim — skip (negative).

## Artifacts

- `scripts/strategy_matrix_tranche1.py` + `scripts/strategy_matrix_tranche1_results.json`
- `scripts/vwap_hold_paired_fill_comparison.py` + `scripts/vwap_hold_paired_fill_comparison_results.json`
- No runtime imports; nothing in this PR is loaded by the live system.
