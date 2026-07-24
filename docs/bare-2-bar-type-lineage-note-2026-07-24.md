# Bare "2" Strat bar-type: fail-closed fix + evidence lineage note (2026-07-24)

Follow-up to the unknown-unknown audit (state-scope: #324; effective
window_direction parity: #325). Bounded scope, per operator direction: (1)
confirm the canonical directional-bar contract, (2) fail closed on ambiguous
bare `"2"` wherever direction is required, (3) identify/mark affected
evidence lineage. **No converter rewrite. No corpus regeneration.**

## 1. The bug (now fixed)

`strategy/signal_engine.py::_try_vwap_hold` required Strat confirmation that
the current bar is `two_down` before allowing a `vwap_hold` SHORT setup:

```python
# before
if state.strat and state.strat.current_bar_type not in ("two_down", "2d", "2"):
    return None
```

Bare `"2"` was in the accepted set — a directionally ambiguous bar-type
token (the CSV replay converter can emit it when its source columns lack a
directional flag for a type-2 bar; see §3) silently passed as if it
*confirmed* `two_down`, exactly the reverse of what the check exists to do.

Fixed to route through the existing canonical normalizer instead of an ad
hoc string set:

```python
# after
if state.strat and normalize_bar_type(state.strat.current_bar_type) != TWO_DOWN:
    return None
```

`strategy.strat_classifier.normalize_bar_type` already implements the
correct contract and was already the single source of truth used
everywhere else in the codebase that consumes Strat bar types — this fix
makes `_try_vwap_hold` use it too, rather than duplicating (and getting
wrong) its own comparison.

## 2. Canonical contract (confirmed, not changed)

`strategy/strat_classifier.py` (`_BAR_TYPE_ALIASES`, `normalize_bar_type`)
already enforces the contract this task asked for:

| Input token | Normalizes to | Notes |
|---|---|---|
| `"1"` | `INSIDE_BAR` | |
| `"3"` | `OUTSIDE_BAR` | |
| `"2u"` / `"2U"` | `TWO_UP` | |
| `"2d"` / `"2D"` | `TWO_DOWN` | |
| `"two_up"` / `"two_down"` / `"inside_bar"` / `"outside_bar"` | unchanged | already canonical |
| **`"2"` (bare)** | **unchanged — stays `"2"`** | **deliberately NOT resolved; carries no direction** |

This was already correctly documented in the module (`strat_classifier.py`
lines 36-43: *"Bare `'2'` stays as-is: it carries no direction, so it
cannot be resolved to two_up/two_down here"*). The audit swept every
consumer of `current_bar_type`/`previous_bar_type`/`two_bars_back_type` in
the futures decision path to confirm they all honor this:

| Consumer | Bare-`"2"` handling | Verdict |
|---|---|---|
| `strategy/signal_engine.py::_try_vwap_hold` | accepted bare `"2"` as confirming `two_down` | **BUG — fixed above** |
| `strategy/signal_engine.py::_strat_run_direction` | exact match against `"two_up"`/`"two_down"` only | already safe (bare `"2"` never matches) |
| `strategy/strat_212_122.py::advance_strat_212_122` | calls `normalize_bar_type`, then compares against `TWO_UP`/`TWO_DOWN`/`INSIDE_BAR` exactly | already safe |
| `strategy/regime_classifier.py` (`long_ok`/`short_ok`) | compares against `{"2u","two_up"}` / `{"2d","two_down"}` | already safe (bare `"2"` never matches, falls through to `RESTRICTED`) |
| `alert_ranker/strat.py` (options advisory scanner) | never ingests an external bar-type string — always derives fresh from OHLC via `classify_from_ohlc` | not applicable (no ambiguous-input path exists) |
| `options_manager/*` | separate subsystem, out of scope for this futures-focused pass | not audited here |

No other bug of this class was found. `_try_vwap_hold` was the only
consumer that treated ambiguous evidence as a confirming signal instead of
failing closed.

## 3. Where bare "2" can still be produced (not changed — by design)

`scripts/csv_to_replay.py::bar_type_str()` derives `current_bar_type` from
three Pine-exported boolean flag columns (`bt1`/`bt2`/`bt3` — inside/
directional/outside) with no separate directional (up/down) column for the
"directional" case, so it can still emit bare `"2"` when only that column
shape is available in a given CSV export. This is **unchanged** — fixing it
would mean deriving direction from OHLC in the converter (comparing to
`polygon_to_replay.py`'s approach), which is a converter rewrite and
explicitly out of scope for this pass.

`scripts/polygon_to_replay.py` is **not affected**: it derives Strat bar
types directly from OHLC (`classify_htf_bar`, matching live's Pine
`classify_bar()`) and is documented as "directional and uncollapsed" —
already confirmed clean by the earlier "Polygon 2U/2D collapse" fix
(excluded from this note as a known, already-resolved item).

No committed replay fixture in this repository (`data/replay/**/*.jsonl`)
currently contains a literal bare `"2"` bar-type value (checked via full
grep of `current_bar_type`/`previous_bar_type`/`two_bars_back_type`
across `data/`) — the in-repo fixtures are clean.

## 4. Evidence lineage — what this means for existing conclusions

**Scope of impact:** only `vwap_hold` SHORT-side evidence generated via
`_try_vwap_hold`'s Strat-confirmation gate is affected — specifically, any
replay run over `csv_to_replay.py`-derived data (not Polygon-derived data,
which was never affected) where a bar's Strat classification resolved to
bare `"2"` rather than a directional `2U`/`2D`. Before this fix, such a bar
would have **incorrectly passed** the `two_down` confirmation and been
counted as a qualifying SHORT candidate.

**Live/production impact: none.** `vwap_hold` is `SHADOW_ONLY` in
`risk_rules.yaml` (line 318) — it has never traded live money under this
bug. The exposure is entirely in the **evidence** used to evaluate whether
`vwap_hold` should ever be promoted out of shadow status.

**Mark as version-bound / re-verify before relying on:** any prior
`vwap_hold` win-rate, expectancy, or promotion-readiness conclusion drawn
from a `csv_to_replay.py`-sourced backtest or replay report should be
treated as **stale pending re-verification** — some fraction of its
counted SHORT candidates may have been admitted on ambiguous, not
confirmed, bar-type evidence. This does not necessarily mean those
conclusions are wrong (bare `"2"` may have been rare or absent in the
specific corpus used), only that they were not provably correct under the
gate as written.

**Not affected / no action needed:**
- Any evidence generated from Polygon-derived replay data.
- `strat_212_122`/`strat_4hr_retrigger`/regime-classification evidence —
  those consumers were already fail-closed on bare `"2"` (§2).
- Any `vwap_hold` bar where `state.strat` was absent entirely (`None`) —
  the confirmation check is skipped by design in that case (documented,
  unrelated to this bug), not silently guessed.

**Not done in this pass (explicitly out of scope):** regenerating the
`vwap_hold` evidence corpus, re-running historical `csv_to_replay.py`
backtests, or auditing which specific past reports/result files (outside
this repository — no such artifacts are committed here) were built from
affected data. This note exists so that work can be scoped precisely when
it is undertaken, not to perform it now.
