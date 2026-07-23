# vwap_hold vs vwap_rejection overlap audit (2026-07-23)

Research only. No execution or strategy configuration changes. Read-only
code and log analysis — no production, config, strategy, broker, or
rules-doc files modified.

Operator-directed follow-on to the isolated fill-model comparison (PR
#307): determine whether `vwap_hold` and `vwap_rejection` are (a)
genuinely separate market states needing mutually exclusive conditions,
(b) the same setup under two historical names, or (c) one valid setup plus
one redundant implementation.

## Headline finding (updated — see section 2 for the correction to the first pass)

**None of the three cleanly applies.** `vwap_rejection`'s trigger condition
has never fired — not once — in either the 622-day replay dataset or the
live box's recent history, and a full 8-stage provenance trace (section 2)
now proves this is not a data/wiring gap: `vwap_rejection`'s own condition
(`state.vwap.reclaimed == True` **and** `state.vwap.price_vs_vwap ==
"below"`) is **logically self-contradictory**, identically in Pine, live,
and replay, because `reclaimed` can only be `True` on a bar where price is
already `"above"` VWAP — never `"below"`. This is proven, not inferred: a
differential check shows the sibling strategy `vwap_reclaim`, which depends
on the identical `reclaimed` field, HAS fired multiple times recently on
live, ruling out any delivery/parsing failure. The original first-pass
finding of a live-path "collision risk" between the two strategies is
**retracted** — the four-state reachability table (section 4) shows no
state exists where both could be eligible.

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

## 2. Full provenance trace (operator-required, all 8 stages)

**CORRECTION to the first pass of this document**: the original section 2
below called this a "data/wiring gap" and cited `context/market_context.py`
+ a 5-day sample check that found `context.vwap.reclaimed` non-null in
0/337 rows. Re-verification for this pass found that check was **vacuous**
— the `retest_baseline_off` journal schema doesn't carry a `context.vwap`
block at all (confirmed by inspecting a full raw row), so the check would
read 0 regardless of the true underlying state. Worse, the file cited as
"the replay path" (`context/market_context.py`) is **not** what generates
`logs/retest_baseline_off` — that's `replay/replay_engine.py`, a different
module. Tracing the *actual* generator produces a materially different,
more precise conclusion, given in full below and superseding everything in
the original sections 2-4.

**Stage 1 — TradingView/Pine calculation.**
`tradingview/risksentinel_context.pine:108`:
```
vwap_reclaimed = ta.crossover(close, vwap_val)
```
A same-bar, non-latched crossover — computed fresh every bar. `ta.crossover(close, vwap_val)`
is true ONLY on a bar where `close > vwap_val` on that exact bar AND the
prior bar's close was at-or-below vwap. It is never held/persisted across
bars.

**Stage 2 — Alert JSON construction.**
`tradingview/risksentinel_context.pine:519`:
```
msg := msg + "\"vwap_reclaimed\":" + str.tostring(vwap_reclaimed) + ","
```
Sent **unconditionally**, every bar, not gated on any alert type. Not a
"must be sent explicitly" edge case — it's always in the payload.
Pine's own advisory `signal_strategy` waterfall (:401-496, if/elif) checks
`vwap_reclaimed and close < vwap_val and trend_dir == "DOWN"` at line 443
for `vwap_rejection`, before the `vwap_hold` branch at :461 — enforcing
mutual exclusivity by construction in Pine's own recommendation. This is
advisory only; the Python backend re-derives independently (see Stage 6)
and does not consume `signal_strategy` for either of these two.

**Stage 3 — Webhook receipt.**
`webhook/app.py:629`, `receive_alert(payload: AlertPayload, ...)` — FastAPI
validates the raw JSON body directly into `AlertPayload` via dependency
injection, before any handler code runs. No raw-payload audit log exists
separate from this; the field name `vwap_reclaimed` matches Pine's emitted
JSON key exactly (case-sensitive, no alias config found on `AlertPayload`).
No drop point identified in this stage.

**Stage 4 — Payload parsing and defaults.**
`webhook/payload.py:63`: `vwap_reclaimed: bool = False`. A default exists,
but Stage 2 confirms Pine always sends the field, so the default is inert
in normal operation — it only matters for the highly narrow case where a
sender omits the key entirely (not observed).

**Stage 5 — State construction (both live and replay — corrected).**
- **Live** (`webhook/state_builder.py:367-368`):
  `reclaimed=payload.vwap_reclaimed` (passthrough), `holding=price_vs_vwap
  in ("above", "below")` (tautological, derived from `payload.close` vs
  `payload.vwap` at :270-273 — the SAME close/vwap pair Pine used for its
  crossover).
- **Replay — the actual generator, `replay/replay_engine.py:505-529`**
  (NOT `context/market_context.py`, which is a different, unrelated parser
  used elsewhere):
  ```python
  vwap_reclaimed = (
      prev_candle is not None
      and prev_candle.price_vs_vwap != "above"
      and candle.price_vs_vwap == "above"
  )
  ...
  holding=candle.price_vs_vwap in ("above", "below"),
  ```
  This is a genuine cross-bar crossover check — functionally equivalent to
  Pine's `ta.crossover` — **not a missing/always-None field** as the first
  pass of this document incorrectly claimed. `holding` is tautological,
  identically to live. **Live and replay do NOT diverge here — they agree
  with each other and with Pine.** (The `context/market_context.py` code
  read in the first pass is a real function in the repo, but it parses an
  already-built `raw` dict for a different caller; it is not in the
  `retest_baseline_off` generation path and should not have been cited as
  "the replay path.")

**Stage 6 — Strategy evaluation.**
`strategy/signal_engine.py:2086-2143`, `_try_vwap_rejection`: requires
`state.vwap.reclaimed == True` **and** `state.vwap.price_vs_vwap ==
"below"` on the same `MarketState` object (one bar). Since Stage 5's
`reclaimed` computation (in every implementation: Pine, live, replay)
requires `close/candle price above vwap` on that same bar to be `True`,
and `price_vs_vwap` is derived from that identical close/vwap comparison,
**`reclaimed == True` structurally implies `price_vs_vwap == "above"` —
never `"below"` — on the same bar, in every code path.** `vwap_rejection`'s
own trigger condition is **self-contradictory by construction**, not rare.
This is the actual root cause (full statement in section 5 below).

**Stage 7 — Journal serialization.**
`webhook/runner.py:2504`: `"reclaimed": state.vwap.reclaimed` — confirmed
written into the live journal's context block under this exact key. Not a
drop point (checked, not assumed).

**Stage 8 — Replay-row construction.**
Already covered in Stage 5 — `replay/replay_engine.py` builds `MarketState`
directly from candle sequences; there is no separate "replay-row" step
that could lose the field between computation and strategy evaluation
(unlike live, replay doesn't round-trip through a journal write before the
strategy sees it — evaluation happens directly against the constructed
`MarketState`).

**Differential proof this isn't a drop/parsing defect**: `_try_vwap_reclaim`
(signal_engine.py:2006) requires the exact same `state.vwap.reclaimed`
field (`reclaimed and holding and price_vs_vwap=="above"` — note: for
`vwap_reclaim`, requiring `"above"` is CONSISTENT with what `reclaimed`
structurally implies, unlike `vwap_rejection`'s contradictory `"below"`
requirement). Grepped live journals for the literal strategy-name string
`vwap_reclaim"` (trailing quote excludes the `vwap_reclaimed` field-name
substring): `journal_2026-07-20.jsonl`: 2, `journal_2026-07-21.jsonl`: 1,
others 0 — **`vwap_reclaim` has fired multiple times recently.** This
proves `reclaimed` reaches the backend and becomes `True` on real live
bars — Stages 1-4 and the live half of Stage 5 are all confirmed working.
`vwap_rejection`'s zero is not a delivery failure; it's that its own
condition can never be satisfied once `reclaimed` is `True`.

## 3. Predicate comparison table (exact formulas, live vs replay)

| | `vwap_hold` | `vwap_rejection` | `vwap_reclaim` (context) |
|---|---|---|---|
| Live `holding` | `price_vs_vwap in ("above","below")` (`state_builder.py:368`) | same | same |
| Live `reclaimed` | n/a (not checked) | `payload.vwap_reclaimed` passthrough (`:367`) | same |
| Replay `holding` | `candle.price_vs_vwap in ("above","below")` (`replay_engine.py:529`) | same | same |
| Replay `reclaimed` | n/a (not checked) | `prev.price_vs_vwap != "above" and candle.price_vs_vwap == "above"` (`:506-511`) | same |
| Trigger condition | `holding and price_vs_vwap=="below"` | `reclaimed and price_vs_vwap=="below"` | `reclaimed and holding and price_vs_vwap=="above"` |
| Missing-field behavior | `holding` is tautological — can't be "missing" independent of `price_vs_vwap` | `reclaimed` defaults `False` (`payload.py:63`) if absent — closes the gate, doesn't error | same default |
| Live vs replay agreement | **Identical** (both tautological) | **Identical** (both same-bar-crossover, both imply `price_vs_vwap=="above"` when true) | **Identical** |

## 4. Four-state reachability table (`holding` × `reclaimed`)

Since `holding` is tautologically `price_vs_vwap in ("above","below")` in
BOTH live and replay, and `price_vs_vwap` is only ever `"above"`,
`"below"`, or `"at"`, the four nominal combinations collapse given the
`reclaimed → price_vs_vwap=="above"` implication proven in Stage 6:

| `holding` | `reclaimed` | Reachable? | Eligible strategy |
|---:|---:|---|---|
| false | false | Yes — `price_vs_vwap=="at"` (exact VWAP touch, both flags false) | neither |
| true | false | Yes — the overwhelmingly common case (price meaningfully above or below, no crossover this bar) | `vwap_hold` (if below+DOWN+two_down) or `vwap_reclaim` (if above+UP) |
| false | true | **Unreachable** — `reclaimed==True` requires `price_vs_vwap=="above"` (Stage 6), which makes `holding` true, not false, by the same tautology. This row cannot occur in either live or replay. | n/a |
| true | true | Reachable, but only with `price_vs_vwap=="above"` (never `"below"`) — a genuine crossover-up bar | `vwap_reclaim` only (requires `"above"`); **`vwap_rejection` can never be eligible here because it requires `"below"`, which is incompatible with `reclaimed==True`** |

**Conclusion of the table**: there is no reachable state, in either live
or replay, under either code path's actual formulas, where
`vwap_rejection`'s full condition (`reclaimed and price_vs_vwap=="below"`)
evaluates true. Zero occurrences is not a sampling artifact or a wiring
failure — it is the only possible outcome given how the three inputs are
defined. Also: **no row exists where `vwap_hold` and `vwap_rejection` are
BOTH eligible** — the "live-path collision" risk raised in the original
first-pass version of this document (its old section 3, superseded by this
revision) does not actually exist once `reclaimed`'s true derivation is
accounted for, because `reclaimed==True`
forces `price_vs_vwap=="above"`, which independently fails `vwap_hold`'s
own `price_vs_vwap=="below"` requirement too. **Both strategies' collision
risk claim in the original pass was incorrect** — retracted here.

## 5. Root-cause classification

Per the operator's taxonomy, the closest fit is **state-construction
mismatch** — but precisely stated, it is not live disagreeing with replay
(they agree with each other and with Pine in every formula checked). The
mismatch is between:
- how the `reclaimed`/`price_vs_vwap` **state fields** are defined
  (same-bar: `reclaimed` can only be true when the current bar's close is
  above vwap), consistently across Pine, live, and replay, and
- what `vwap_rejection`'s **strategy condition** assumes about their
  relationship (that `reclaimed==True` can coexist with
  `price_vs_vwap=="below"` on the same bar, representing "attempted a
  reclaim, then failed back below") — a **multi-bar** pattern the
  strategy's docstring (:2088 "Price attempted to reclaim VWAP from below,
  failed, and closed back below it") describes correctly in words, but
  which the same-bar boolean check can never express.

None of the other five buckets fit: TradingView calculation is present and
correct (Stage 1); alert serialization is present and unconditional (Stage
2); the parser/schema chain is intact (Stages 3-4, confirmed via the
`vwap_reclaim` differential proof); live and replay do not actually
mismatch each other (Stage 5, corrected). This is a single, precise defect
in one strategy's trigger definition — not a data pipeline problem, and it
is present identically wherever the condition is evaluated (Pine's own
advisory branch has the identical contradiction, so even a Pine-only fix
attempt would need to address the same underlying issue: `reclaimed` would
need to become a **persisted, multi-bar** "recently crossed above, not yet
reverted" flag, not an instantaneous same-bar one, for `vwap_rejection` to
ever be satisfiable as documented).

## 6. What this rules in and out, precisely

- **NOT (b) same setup under two names**: the entry formula is identical,
  but the conditions describe conceptually distinct patterns (holding below
  the whole time vs. a failed reclaim). The self-contradiction found here
  doesn't make them the same setup — it makes `vwap_rejection` currently
  unimplementable as written, independent of `vwap_hold`.
- **NOT (a) genuinely separate with correct mutual exclusivity — but not
  for the reason the first pass of this doc claimed.** They ARE mutually
  exclusive as coded (section 4's table shows no collision state exists) —
  the original "live-path collision" finding is retracted. But (a) still
  doesn't cleanly apply because `vwap_rejection` cannot fire AT ALL, so
  "separate market states" can't be evidenced either.
- **NOT (c) one valid + one redundant**: unchanged from the first pass —
  cannot rank "redundant" against a strategy with zero possible occurrences.
- **Actual finding**: `vwap_rejection`'s trigger is a same-bar logical
  impossibility given how `reclaimed` is defined everywhere it's computed.
  This is a **specific, fixable code/spec defect** (the flag needs to be
  persisted across bars to express "recently reclaimed"), not a data gap,
  not a rarity, and not evidence about `vwap_hold`'s own validity.

## What this does not decide

No retirement, merge, or redesign of `vwap_hold` or `vwap_rejection`.
No conclusion on whether `reclaimed`/`vwap_reclaimed` *should* be wired up
— that is a Pine-source and data-pipeline question outside this repo's
strategy code, and outside this audit's read-only scope. No code change
was made to `state_builder.py`, `market_context.py`, or `signal_engine.py`.

## Evidence reviewed

`tradingview/risksentinel_context.pine` (Pine calc + alert JSON
construction, lines 108, 401-519), `webhook/app.py` (`receive_alert`,
:629), `webhook/payload.py` (`AlertPayload`, :63), `webhook/state_builder.py`
(`:270-273`, `:367-368`), `replay/replay_engine.py` (`:505-529` — the
actual `retest_baseline_off` generator, corrected from the first pass's
incorrect citation of `context/market_context.py`), `strategy/signal_engine.py`
(`_try_vwap_hold`, `_try_vwap_rejection`, `_try_vwap_reclaim`,
`evaluate_setups`, `_score_strategy_candidate`, `_RANK_EXPECTANCY_BONUS`),
`webhook/runner.py:2504` (journal serialization), `risk/risk_engine.py`
(`calculate_rr`), `docs/strategy-matrix-tranche1-2026-07-14.md` (arm
counts), and the live box's `journal_2026-07-{19..23}.jsonl` (5 days,
occurrence checks for both `vwap_rejection` and `vwap_reclaim` via
read-only `grep`, no box state changed).

## Remaining unknowns

- Whether the TradingView Pine source could be changed to make `reclaimed`
  a persisted, multi-bar flag (e.g., "crossed above within the last N
  bars, currently below") that would make `vwap_rejection` satisfiable as
  documented — that is a Pine-script design question and a code change,
  both out of scope for this read-only audit.
- Whether, if `vwap_rejection` were redefined this way, it would fire
  often, rarely, or overlap heavily with `vwap_hold`'s bars in practice —
  cannot be estimated without a real (redefined) implementation to test.
- No further live/replay divergence is expected given Stage 5's finding
  that both agree exactly, but this was checked only for `vwap`-related
  fields relevant to this audit, not exhaustively for all `MarketState`
  fields.
