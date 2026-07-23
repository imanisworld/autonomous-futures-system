# vwap_hold vs vwap_rejection overlap audit (2026-07-23)

Research only. No execution or strategy configuration changes. Read-only
code and log analysis — no production, config, strategy, broker, or
rules-doc files modified.

Operator-directed follow-on to the isolated fill-model comparison (PR
#307): determine whether `vwap_hold` and `vwap_rejection` are (a)
genuinely separate market states needing mutually exclusive conditions,
(b) the same setup under two historical names, or (c) one valid setup plus
one redundant implementation.

## Headline finding

**None of the three cleanly applies yet.** `vwap_rejection`'s trigger
condition has never fired — not once — in either the 622-day replay
dataset or the live box's recent history. This is not evidence the two
setups are separate, identical, or redundant; it is evidence that
`vwap_rejection` has been **structurally unreachable**, for a traceable,
specific reason, everywhere it has been evaluated so far. The overlap
question cannot be answered empirically until that is fixed or explained.
Separately, a real definitional issue was found in the **live** code path
that would make the two setups non-discriminating from each other the
moment `vwap_rejection` does become reachable — reported below.

## 1. Condition comparison (source: `strategy/signal_engine.py`)

| | `vwap_hold` (:2032-2085) | `vwap_rejection` (:2086-2143) |
|---|---|---|
| Core trigger | `state.vwap.holding` **and** `price_vs_vwap == "below"` | `state.vwap.reclaimed == True` **and** `price_vs_vwap == "below"` |
| Trend | `DOWN` (same) | `DOWN` (same) |
| Proximity gate | `_vwap_entry_out_of_range` (same, disabled by default) | same |
| Strat bar-type | Requires `two_down` **when `state.strat` is present**; skipped if absent | **No bar-type check at all** |
| BOS/MSS/structure gate | Requires bearish structure **when raw data present** (same logic) | same |
| Entry | `vwap.value − 2 ticks` | `vwap.value − 2 ticks` — **identical formula** |
| Stop | `vwap.value + 28 ticks` (7 pts) | `vwap.value + 20 ticks` (5 pts) — narrower |
| Target multiplier | `3.0R` | `3.0R` — same |
| R:R ratio | 3.0 (via `RiskEngine.calculate_rr`) | 3.0 — **identical**, since both use the same multiplier |
| Direction | SHORT only | SHORT only |

Two setups with the same entry price, same direction, same R:R, same
trend/structure gates, differing only in (a) which of two VWAP-state flags
gates them, (b) whether a Strat bar-type confirmation applies, and (c)
stop distance (which only matters once a candidate exists — it doesn't
change *whether* one fires).

## 2. Why `vwap_rejection` has never fired

`vwap_rejection` requires `state.vwap.reclaimed == True`. Traced this flag
end to end:

- **Schema default**: `webhook/payload.py:63` — `vwap_reclaimed: bool = False`.
  If the incoming TradingView alert doesn't explicitly set this field, it
  silently defaults to `False` — no error, no gap flag, just a permanently
  closed gate.
- **Replay path** (`context/market_context.py:359`) reads
  `reclaimed=vwap_raw.get("reclaimed")` from the historical bar/journal
  data. Scanned the first 5 days of the 622-day MNQ replay set (337
  candidate rows): **`reclaimed` is non-null in zero of them.** The field
  is never populated in this dataset at all.
- **Live path** (`webhook/state_builder.py:367`) reads
  `reclaimed=payload.vwap_reclaimed` directly off the live alert payload —
  same default-False field.
- **Live box evidence**: `grep -c vwap_rejection` across the last 5 days of
  `journal_2026-07-{19,20,21,22,23}.jsonl` on the box returns **0** in
  every file — not a single candidate, blocked or approved, mentioning
  `vwap_rejection`.
- **Tranche-1 arm counts** (PR #283,
  `docs/strategy-matrix-tranche1-2026-07-14.md`): the 622-day MNQ arm table
  lists `orb_breakout 63, orb_reclaim 253, pdh_reclaim 68, pdl_reclaim 13,
  vwap_hold 348, vwap_reclaim 260` — **no `vwap_rejection` row at all**,
  meaning zero `TRADE`-approved arms in 622 days.

This is the same pattern tranche-1 already flagged for `orb_rejection`
("0 arms in 622 days → zero-signal diagnosis needed, not restoration") —
but with a specific, traced root cause here: the flag the strategy depends
on is never sent by the alert source (or never survives into the replay
data), not that the market condition itself is rare. **This is a data/
wiring gap, not a strategy-validity finding, and it is outside what a
Pine-source change can be verified or fixed from this codebase.**

## 3. A live-path definitional issue, found while tracing this (latent, not yet triggered)

`state.vwap.holding` is computed differently on the two code paths that
build `MarketState`:

- **Live** (`webhook/state_builder.py:368`):
  `holding=price_vs_vwap in ("above", "below")` — **tautological**. It is
  true whenever price is on either side of VWAP at all, independent of
  `reclaimed`.
- **Replay** (`context/market_context.py:360`):
  `holding=vwap_raw.get("holding")` — an **independent** raw flag read
  from historical data, not derived from `price_vs_vwap`.

Consequence: on the **live** path only, `vwap_hold`'s gating condition
(`holding and price_vs_vwap=="below"`) reduces to just
`price_vs_vwap=="below"`, because `holding` is always true there whenever
that's true. `vwap_hold` does not itself check or exclude `reclaimed`. So
if a live bar ever has `price_vs_vwap=="below"`, `trend==DOWN`, and
`vwap_reclaimed==True` all at once, **both `vwap_hold` and
`vwap_rejection` would fire as candidates on the same bar** — not a
market-state distinction, a live-code coincidence of one field's
tautological definition. `strategy/signal_engine.py:1607-1622`'s
`evaluate_setups` doesn't take a first-match — it `yield`s every strategy
whose condition is met, then a later ranking step
(`_score_strategy_candidate`, :1636-1665) picks one via
`confluence*100 + rr_ratio*10 + expectancy_bonus − priority_index`.
Checked `_RANK_EXPECTANCY_BONUS` (:217-220): **no entry for either
`vwap_hold` or `vwap_rejection`** (both default to a 0.0 bonus), and both
share the identical entry/direction that confluence scoring would see, so
a same-bar collision would likely come down to `priority_index` alone —
`vwap_rejection` is listed before `vwap_hold` in the `strategies` list
(:1613-1614 vs 1615), so it would win by list order, not by any
market-state reasoning.

This has **not been observed** — because `reclaimed` has never been true —
but it means the current code does not yet actually answer the operator's
question (a) "genuinely separate conditions needing mutually exclusive
gating." As written, on live, they are **not** mutually exclusive; only
`vwap_rejection`'s permanent dormancy has hidden that.

## 4. What this rules in and out, precisely

- **NOT (b) same setup under two names**: the entry formula is identical,
  but the trigger conditions are conceptually distinct (price never
  crossing VWAP vs. crossing and failing back) and the Strat bar-type gate
  differs. If `reclaimed` ever populates, these are not simply duplicate
  labels for one condition.
- **NOT yet provably (a) genuinely separate with correct mutual
  exclusivity**: on live, as coded today, they are not mutually exclusive
  — `vwap_hold`'s condition doesn't exclude a `reclaimed` bar. This is
  fixable (e.g., `vwap_hold` could require `not state.vwap.reclaimed`) but
  is a **code change**, out of scope for this read-only audit and not
  authorized by the operator's instruction.
- **NOT yet (c) one valid + one redundant**: cannot rank "redundant"
  against a strategy that has generated zero evidence anywhere. `vwap_hold`
  has 348 replay arms and the PR #307 evidence package; `vwap_rejection`
  has none to compare against.
- **Actual finding**: `vwap_rejection` is dormant by data/wiring gap, not
  by market rarity or design; `vwap_hold`'s live-path condition has a
  latent non-exclusivity with `vwap_rejection` that has simply never been
  exercised. Both are prerequisite facts the operator needs before ranking
  or merging either strategy.

## What this does not decide

No retirement, merge, or redesign of `vwap_hold` or `vwap_rejection`.
No conclusion on whether `reclaimed`/`vwap_reclaimed` *should* be wired up
— that is a Pine-source and data-pipeline question outside this repo's
strategy code, and outside this audit's read-only scope. No code change
was made to `state_builder.py`, `market_context.py`, or `signal_engine.py`.

## Evidence reviewed

`strategy/signal_engine.py` (`_try_vwap_hold`, `_try_vwap_rejection`,
`evaluate_setups`, `_score_strategy_candidate`, `_RANK_EXPECTANCY_BONUS`),
`webhook/state_builder.py`, `context/market_context.py`,
`webhook/payload.py`, `risk/risk_engine.py` (`calculate_rr`),
`docs/strategy-matrix-tranche1-2026-07-14.md` (arm counts), a 5-day sample
of `logs/retest_baseline_off/MNQ/journal_2024-07-*.jsonl` (337 rows, field
presence check), and the live box's `journal_2026-07-{19..23}.jsonl` (5
days, occurrence check via read-only `grep`, no box state changed).

## Remaining unknowns

- Whether the TradingView Pine alert source ever emits `vwap_reclaimed` at
  all, or whether it's absent by design (e.g., that alert type was never
  built) — unverifiable from this codebase; would need the Pine script or
  TradingView-side alert configuration, neither of which lives here.
- Whether, if `reclaimed` were populated, `vwap_rejection`'s condition
  would fire often, rarely, or effectively be a strict subset of
  `vwap_hold`'s bars in practice — cannot be estimated without real data.
- Confluence-score behavior on a genuine same-bar collision was reasoned
  from the ranking formula and the (empty) bonus table, not observed on a
  real collision, since none exists yet.
