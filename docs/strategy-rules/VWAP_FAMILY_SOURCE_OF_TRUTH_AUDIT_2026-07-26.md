# VWAP Family Source-of-Truth Audit (2026-07-26)

**Audit only. No runtime, strategy, Pine, or `risk_rules.yaml` changes made.**
Scope: `vwap_hold`, `vwap_rejection`, `vwap_reclaim`. Base: `origin/main@a226a40`
(`12HR Miyagi: canonical detector + honest-fill evidence (PROMISING BUT
UNPROVEN) (#343)`). Does not touch ORB Reclaim, 4HR Re-Trigger, 60M 3-2-2,
12HR Miyagi, strat_2-1-2, or strat_1-2-2.

---

## 1. Files inspected (exact list)

**Runtime / decision path**
- `strategy/signal_engine.py` — `_try_vwap_reclaim` (:2042-2072), `_try_vwap_hold`
  (:2074-2134), `_try_vwap_rejection` (:2136-2197), `_vwap_entry_out_of_range`
  (:2026-2040), strategy-permission gate (:728-774, incl. the MNQ vwap_hold
  proof-lane exception :739-750), trend gate (:1000-1002), NY session-window
  check (:1204-1238), global schedule enforcement (:246, :294, :315, :330),
  `enabled_concepts`/permission wiring (:43), continuation-pullback VWAP
  proximity logic (:2581-2645, distinct feature, not part of the three audited
  strategies).
- `webhook/payload.py` — `AlertPayload` (:33-113), `vwap_reclaimed` (:63),
  `vwap_failed_reclaim` (:76), `signal_strategy` (:111).
- `webhook/state_builder.py` — `build_market_state` (:246-390), VWAP
  derivation (:268-277), `VWAPData` construction (:364-370).
- `webhook/runner.py` — proof-lane market-entry price reference (:2234-2251),
  journal serialization of `state.vwap.reclaimed` (:2727).
- `replay/replay_engine.py` — `_market_state_from_candle` (:1031-1109),
  `vwap_reclaimed`/`vwap_failed_reclaim` derivation (:1038-1063),
  instrument-keyed `prev_candle_by_key`/`prev_prev_candle_by_key` threading
  (:344-353).
- `context/market_context.py` — `VWAPData` dataclass (:49-64).
- `context/mnq_vwap_hold_proof.py` — MNQ+vwap_hold+new_york proof-mode gate
  exception (:1-60, `permission_gate_exception`).
- `context/mnq_vwap_hold_early.py`, `execution/vwap_hold_early_shadow.py` —
  5-minute early-signal shadow lane; confirmed (via its own docstring, :11-19)
  to re-run the real `signal_engine.DecisionEngine.evaluate()` against
  `vwap_hold` rather than reimplementing the predicate — not a second
  definition, out of primary scope, noted for completeness.
- `risk_rules.yaml` — `strategy_permission_gate` (:296-345),
  `enabled_concepts`/`strategy:` block (:364-401), `disabled_concepts_per_instrument`
  (:403-441), `allowed_sessions: [new_york]` (:505), `fill_model` (:347-353).
- `config/settings.py` — `vwap_entry_max_distance_ticks` default (:233),
  `entry_fill_model` default (:216, `"market"`), `exit_mode` default (:207,
  `"static"`), `mnq_vwap_hold_proof_mode` default (:471, :763-764,
  `"observe_only"`), `allowed_sessions` (:67).
- `tradingview/risksentinel_context.pine` — `vwap_val`/`vwap_reclaimed`
  calculation (:105-108), advisory `signal_strategy` waterfall including the
  `vwap_rejection` branch (:394-465), alert JSON `msg` construction incl.
  `vwap`/`vwap_reclaimed`/`signal_strategy` (:507-548). Full-file grep for
  `vwap_failed_reclaim`/`failed_reclaim`: **zero matches**, confirmed also via
  `git log --all -S "vwap_failed_reclaim" -- tradingview/risksentinel_context.pine`
  (zero commits, any branch, any point in history).

**Tests**
- `tests/test_vwap_rejection.py`, `tests/test_vwap_proximity_gate.py`,
  `tests/test_vwap_hold_bar_type_ambiguity.py`, `tests/test_vwap_session_anchor.py`,
  `tests/test_mnq_vwap_hold_early.py`, `tests/test_mnq_vwap_hold_proof.py`.

**Evidence documents**
- `docs/vwap-hold-isolated-fill-model-comparison-2026-07-23.md` (PR #307, full)
- `docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md` (PR #308, full)
- `docs/bare-2-bar-type-lineage-note-2026-07-24.md` (PR #326, full)
- `docs/corpus-v1-clean-baseline-report-2026-07-25.md` (full)
- `docs/mes-mnq-mechanical-research-2026-07-09.md` (VWAP sections, :14-20,
  :87-121, :316-324)
- `docs/strategy-matrix-tranche1-2026-07-14.md` (referenced by #307/#308,
  arm-count provenance)
- `docs/strategy-rules/Strategy_Inventory.md` (full)

**Scripts / raw evidence artifacts**
- `scripts/vwap_hold_paired_fill_comparison.py` (:1-90, arm-loading + fill
  legs), `scripts/vwap_hold_isolated_fill_model_comparison.py`,
  `scripts/vwap_hold_evidence_package.py`, `scripts/vwap_hold_evidence_package_results.json`,
  `scripts/vwap_hold_isolated_fill_model_comparison_results.json`,
  `scripts/vwap_hold_isolated_fill_model_manifest.json`,
  `scripts/vwap_hold_paired_fill_comparison_results.json`
- `scripts/corpus_v1_report.py` (:1-70), `scripts/corpus_v1_results.json`
  (`meta` block), `scripts/validation_vwap_reclaim.json`,
  `scripts/validation_vwap_rejection.json`, `scripts/strategy_validation_report.py`

**Git history**
- `git log --all --oneline --grep="vwap" -i` (full history reviewed, ~55
  commits from `aafded0`/`88a02ab` initial VWAP wiring through `07d2e05`
  #326), plus targeted `git log -S` searches on the `two_down`/BOS-MSS gate
  and the `vwap_failed_reclaim` field, and `gh pr view 321 --json body` for
  the full PR #321 description (not visible from the squashed commit
  message alone).

---

## 2. VWAP HOLD

**Signal (`strategy/signal_engine.py:2074-2134`)**: `state.vwap.holding and
price_vs_vwap == "below"` AND `trend.direction == "DOWN"` AND (if
`state.strat` present) `normalize_bar_type(current_bar_type) == TWO_DOWN`
AND (if any of `bos_direction`/`mss_direction`/`market_structure` present)
bearish structure confirmed AND not `_vwap_entry_out_of_range` (gate default
off, `config/settings.py:233 = 0.0`).

**What "holding" means in code**: `holding = price_vs_vwap in ("above",
"below")` (`webhook/state_builder.py:368`, `replay/replay_engine.py:1095`).
This is **tautological** — it is true whenever price is not exactly at VWAP.
It is not a sustained/multi-bar "held for N bars" concept despite the
strategy name; the only thing giving `vwap_hold` its "sustained" character is
the additional `two_down` Strat-bar-type requirement (current bar's own
range/close relationship, still single-bar) plus the BOS/MSS filter when raw
structure data is present.

**Bar/timeframe**: whatever `payload.timeframe`/`candle.timeframe` is — not
enforced by strategy code. Operationally the deployed Pine indicator and the
Corpus v1 replay both run on 15-minute bars (`docs/corpus-v1-clean-baseline-report-2026-07-25.md:16`
says "canonical 15m replay timeframe"; memory note `project_timeframe_gap`
says the live engine is "15M-only"), but this is an **operational
convention, not a code-enforced contract** — `AlertPayload.timeframe`
defaults to `"5m"` (`webhook/payload.py:56`) if Pine ever sent a different
chart interval.

**Entry/stop/target** (`:2117-2123`): entry = `vwap.value - 2 ticks`; stop =
`vwap.value + 28 ticks` (7 pts on MNQ, `TICK_SIZE["MNQ"]=0.25`,
`signal_engine.py:226`); target = entry − 3.0R. SHORT only.

**Session**: `_try_vwap_hold` itself has **no session check**. "NY session
only" is true today only because (a) `risk_rules.yaml:505` sets
`allowed_sessions: [new_york]` globally (applies to every strategy via
`_enforce_schedule`/`SCHEDULE_MODE=current`, not vwap_hold-specific — see
`signal_engine.py:294`), and (b) the only currently-live path to paper
execution for `vwap_hold` — the `MNQ_VWAP_HOLD_PROOF_MODE` opt-in exception
(`context/mnq_vwap_hold_proof.py:1-20`) — is hard-scoped to
`MNQ + vwap_hold + new_york + paper_sim` specifically. **Contradiction**:
`docs/strategy-rules/Strategy_Inventory.md:174-175` ("NY session only")
reads as if this were a property of the detector's own rule; it is actually
two separate, non-strategy-specific gates stacking to produce that effect.

**Permission status**: `vwap_hold: SHADOW_ONLY` globally
(`risk_rules.yaml:318`) — the only exception is the MNQ proof-lane above,
default mode `observe_only` (`config/settings.py:471`); whether the live box
currently has `MNQ_VWAP_HOLD_PROOF_MODE=paper_sim` set is a box-state
question this repo-only audit cannot answer (out of scope — no box access).

### Is PR #307's evidence testing today's exact predicate?

**Population source**: the 348-signal population (`scripts/vwap_hold_paired_fill_comparison.py:11`,
sha256-fingerprinted, reused unchanged by #307/#308/the evidence package)
comes from `logs/retest_baseline_off/MNQ/journal_*.jsonl` — real historical
`TRADE`/`APPROVED` decisions from a replay run over **Polygon-derived** 5m
candles (`data/replay_polygon_5m`, per the same file's header comment).

**Bare-`"2"` bar-type bug (PR #326, `07d2e05`, merged 2026-07-24) — does it
touch this population?** `docs/bare-2-bar-type-lineage-note-2026-07-24.md:120-121`
states Polygon-derived replay data was **never affected** by the bug
(`scripts/polygon_to_replay.py` was always directional/uncollapsed). Since
the 348-arm population is Polygon-derived, this specific defect (a real,
merged, post-#307 code change to `_try_vwap_hold`'s own predicate) is
verified **not** to invalidate #307/#308's population. This is confirmed by
citation, not assumed — but note the underlying journal files
(`logs/retest_baseline_off/`) are gitignored/untracked, so the exact
`main` SHA that generated them cannot be independently re-derived from git
history; the "when was this population generated" question rests on the
Polygon-vs-CSV data-source distinction (provable) rather than an exact
commit pin (not provable from this repo).

**A real, unresolved discrepancy the isolated test itself flags but does not
fix**: `docs/vwap-hold-isolated-fill-model-comparison-2026-07-23.md:124-146`
(the "second pass" / evidence-package section) proves PR #307's own IOC leg
used the arrival bar's **open** as the marketability reference
(`scripts/vwap_hold_paired_fill_comparison.py:158`), while every production
and replay call site — `webhook/runner.py:2234-2239` ("decision bar's
close — the same reference the entry-sanity guard uses", verified still true
on current `main` at the shifted line number), `execution/mnq_strat_evidence.py:349`,
`replay/replay_engine.py:299` — uses **close**. The evidence package reruns
the comparison both ways (`docs/vwap-hold-isolated-fill-model-comparison-2026-07-23.md:148-164`)
and shows this is material: IOC-open static exit is flat/negative and fails
both-halves at every cost tier; IOC-close static exit is positive and passes
both halves at every tier. **This was never resolved** — the doc's own
"Corrected interpretation" section (:265-286) states the verdict is still
**not** "market entry is validated" and explicitly says the IOC reference
price question remains open pending operator direction. `Strategy_Inventory.md`'s
current verdict cell ("WAIT — isolated fill test pending") predates
knowing this, but the *substance* of "isolated fill test pending" is
actually **stale in the other direction**: the isolated test is done, but
its own second pass concluded HOLD, not approve — so "pending" in the sense
of "not yet run" is wrong, while "pending a resolution before promotion" is
still accurate.

**Verdict on evidence validity**: the market+runner headline number
($10.30/armed signal, PF 1.52, both-halves-positive) is real, reproducible,
and tests a population confirmed unaffected by the one identified
post-hoc predicate change (#326). It is **not** validated under a
production-matching IOC reference price, and the isolated-test authors
themselves call this HOLD, not APPROVE. Treat the market+runner number as
the strongest evidence line for `vwap_hold`, but not as closing the
IOC-viability question.

### Old-studies-mixing-incompatible-definitions concern (operator flag)

`Strategy_Inventory.md:176` ("Positive result (+$22.72/trade) came from
study with different sample, granularity, and exit model vs negative result
— not a clean comparison") refers to `context/mnq_vwap_hold_proof.py:23-26`'s
citation of `docs/strategy-matrix-tranche1-2026-07-14.md` (n=341 raw
+$12.75/trade, NY subset +$22.72/trade) versus the older "VWAP edge is
fiction" IOC-limit finding (memory `project_vwap_edge_fiction`, not a
committed doc reproduced in this pass). **This specific concern is
superseded** by PR #307/#308's five-locked-preconditions methodology, which
reconstructs the *exact same* 348-arm population under both fill models with
a frozen population hash — i.e., the "different sample/granularity" problem
the operator flagged for the *pre-#307* comparisons no longer applies to the
#307/evidence-package comparison, which is same-population-both-legs by
construction. It has **not** been superseded in the sense of "resolved
positive" — see the open IOC-reference-price question above.

---

## 3. VWAP REJECTION

**Predicate today (`strategy/signal_engine.py:2158`)**: `state.vwap and
state.vwap.failed_reclaim` AND `trend.direction == "DOWN"` AND not
`_vwap_entry_out_of_range` AND (if raw structure data present) bearish
confirmation. **No longer the same-bar `reclaimed==True and
price_vs_vwap=="below"` check** that PR #308 proved structurally impossible
— PR #321 (`face9d2`, merged 2026-07-24) replaced it with a distinct
`failed_reclaim` field.

**Is it reachable? Precisely: replay YES, live NO, as of `origin/main`.**

1. **Replay**: `replay/replay_engine.py:1055-1063` derives
   `vwap_failed_reclaim` causally from the actual candle sequence
   (`prev_bar_was_reclaimed and candle.price_vs_vwap == "below"`),
   independent of Pine and independent of `DecisionEngine`/`DailyState`
   (threaded via `prev_candle_by_key`/`prev_prev_candle_by_key`, keyed by
   `(instrument, timeframe)` since the mixed-instrument leak fix, :344-353).
   This is genuinely reachable: **`scripts/validation_vwap_rejection.json`
   proves it fired 8 times** (all resolved, 0 unjoinable) over the 12-month
   Corpus v1 replay run (`main@a5434794e471137af83f6e5886b535fb9e3cfcd5`,
   `docs/corpus-v1-clean-baseline-report-2026-07-25.md:97`, 62.5% WR, PF
   4.432, net $453, all MNQ). **This directly falsifies the current
   `Strategy_Inventory.md:44` verdict text** ("BROKEN — unreachable
   predicate", "0 arms across 622 days") as a description of current `main`
   — that finding was true of the *pre-#321* same-bar predicate and is
   **no longer true post-#321** in replay.
2. **Live**: `AlertPayload.vwap_failed_reclaim` defaults `False`
   (`webhook/payload.py:76`) and `tradingview/risksentinel_context.pine` has
   **zero** occurrences of the string `vwap_failed_reclaim` or
   `failed_reclaim`, confirmed by full-file grep and by
   `git log --all -S "vwap_failed_reclaim" -- tradingview/risksentinel_context.pine`
   returning no commits at all, on any branch, ever. `_try_vwap_rejection`
   therefore **cannot fire live today** — the field is permanently `False`
   until Pine is changed.

**Field-flow trace, payload → state_builder → decision engine → replay
(file:line each hop)**:
- Pine → payload: **gap**. No Pine code path sets/sends `vwap_failed_reclaim`
  anywhere (confirmed above).
- `webhook/payload.py:76` — `AlertPayload.vwap_failed_reclaim: bool = False`.
- `webhook/state_builder.py:369` — `failed_reclaim=payload.vwap_failed_reclaim`
  (pure passthrough, live path).
- `context/market_context.py:64` — `VWAPData.failed_reclaim: bool = False`
  (dataclass field definition).
- `strategy/signal_engine.py:2158` — `_try_vwap_rejection` reads
  `state.vwap.failed_reclaim`.
- `replay/replay_engine.py:1061-1063` and `:1096` — independent derivation
  + `VWAPData(..., failed_reclaim=vwap_failed_reclaim)` (replay path, not
  payload-dependent).

**A second, independent Pine-side gap the brief didn't name, found during
this audit**: Pine's own *advisory* `signal_strategy` waterfall still
encodes the **old, disproven** same-bar contradiction. At
`tradingview/risksentinel_context.pine:443`:
```
else if vwap_reclaimed and close < vwap_val and trend_dir == "DOWN"
    signal_strategy := "vwap_rejection"
```
where `vwap_reclaimed = ta.crossover(close, vwap_val)` (:108) is `true` only
on a bar where `close > vwap_val` *that same bar* — so `close < vwap_val`
on the same line can never simultaneously hold. This is the exact same
logical impossibility PR #308 found in the old backend code, still present
in Pine, unfixed. **Practical impact is limited but real**: this
`signal_strategy` value only feeds `strategy/signal_engine.py:1062`'s
advisory-bracket cross-check (`_apply_advisory_bracket`, :1036-1068), which
only *overrides* the backend's own entry/stop/target when Pine's
`signal_strategy` matches what the backend independently determined
(:1062-1065); since the backend's own `vwap_rejection` predicate cannot
independently fire live either (per the primary gap above), this second bug
currently causes no wrong trade — but it means that **even after** Pine is
updated to send `vwap_failed_reclaim`, unless Pine's `signal_strategy`
branch is *also* changed to reference `vwap_reclaimed[1]` (prior bar) rather
than `vwap_reclaimed` (current bar), Pine's advisory bracket will never
agree with a live-firing backend `vwap_rejection`, and the bracket override
will silently no-op every time (harmless here only because Pine's own
static entry/stop numbers at :446-448 happen to numerically match the
backend's :2181-2182 formula — `vwap - 2t` / `vwap + 20t` — so the no-op
costs nothing today, but it is still a second, distinct staleness in Pine
beyond the field-send gap the brief named).

**Do Pine/runtime/replay have enough information to represent the pattern?**
Runtime + replay: yes, proven (#321 backend fix + the causal replay
derivation + the 8-arm Corpus v1 evidence). Pine: **not a hard gap** —
Pine already computes `vwap_reclaimed` fresh every bar and has full
bar-history access (`close[1]`, etc.), so a one-line change
(`failedReclaim = vwap_reclaimed[1] and close < vwap_val`) would suffice;
it has simply not been written/deployed.

### Smallest unresolved contradiction / pending decision

**This is a product/rule decision, not a code question — stating it
plainly per instruction, not resolving it:**

PR #321's own merged description (`gh pr view 321`, "⚠️ Operational note")
states explicitly: *"`vwap_rejection` is already enabled in `risk_rules.yaml`
on `origin/main` and the deployed demo box. This PR doesn't touch
`risk_rules.yaml`. Merging will make it live-eligible for the first time...
Per operator: the corrected Pine script that would actually populate
`vwap_failed_reclaim=true` is deliberately not deployed yet, so merging this
backend PR alone leaves the demo path inert for VWAP rejection until Pine is
separately finalized and deployed — that's the intended sequencing."*

Searched every branch (`git log --all -S "vwap_failed_reclaim" --
tradingview/risksentinel_context.pine`) for a follow-on Pine deployment: **none
found.** No later commit, PR, or branch updates the Pine script with this
field, and no later commit revisits the sequencing decision. **The decision
that needs making, by the operator (not by an implementing agent): whether
and when to write + deploy the corrected Pine script that sends
`vwap_failed_reclaim`, given the strategy's own reachable-replay evidence is
n=8 (thin) and the "advisory bracket" Pine-side staleness above would also
need addressing at the same time for the two systems to actually agree.**
This is exactly the deferred decision PR #321 flagged; it remains open on
current `main`.

---

## 4. VWAP RECLAIM

**Signal (`strategy/signal_engine.py:2048-2052`)**: `state.vwap.reclaimed
and state.vwap.holding and price_vs_vwap == "above"` AND `trend.direction ==
"UP"` AND not `_vwap_entry_out_of_range`. `reclaimed` = live:
`payload.vwap_reclaimed` passthrough (Pine's `ta.crossover(close, vwap_val)`,
`.pine:108`, sent unconditionally every bar per `.pine:519`); replay:
`replay/replay_engine.py:1039-1043`, `prev_candle.price_vs_vwap != "above"
and candle.price_vs_vwap == "above"` — functionally identical cross-bar
check, confirmed by the overlap audit (`docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`
Stage 5, :104-130) to agree exactly with Pine and with each other. This is
the one VWAP predicate with genuine, independently-confirmed live↔replay↔Pine
formula agreement.

**Entry/stop/target** (`:2056-2061`): entry = `vwap.value + 2 ticks`; stop =
`vwap.value - 28 ticks` (7 pts MNQ); target = entry + 3.0R. LONG only.

**Shared state with Hold/Rejection**: `vwap_reclaim` is the **only** one of
the three that reads `state.vwap.reclaimed`. `vwap_hold` never reads
`reclaimed`. `vwap_rejection` reads the distinct `failed_reclaim` field,
which is *derived from* the same underlying reclaim-sequence logic in both
Pine (:443, current-bar, uncorrected) and replay (:1055-1063, one-bar
lookback, corrected) but is a **structurally separate field**, not a shared
mutable. The overlap audit's four-state reachability table
(:183-209) proves `vwap_hold` and `vwap_reclaim`/`vwap_rejection` never
overlap (disjoint on `price_vs_vwap` above/below).

### Is the n≈29 figure canonical or provenance-only?

`Strategy_Inventory.md:43` cites "n=29 thin" for VWAP Reclaim (MNQ NY).
Traced to `docs/mes-mnq-mechanical-research-2026-07-09.md:120`: `MNQ |
vwap_reclaim | new_york | 29 | 29 | 48.3% | $16.10/tr | $466.96 net` (under
the doc's `ioc_limit_runner` fill-model section, :94-121) — this **is** a
dated, reproducible, session-split figure from a committed, generator-traceable
study (`scripts/mes_mnq_mechanical_research.py`), not an external/unreproduced
number. It passes the same "dated, reproducible" bar the Miyagi/3-2-2 lanes
apply to their own studies. It predates PR #326 (bare-`"2"` fix), but
`vwap_reclaim`'s predicate has **no bar-type/Strat check at all**
(confirmed at :2042-2072 — unlike `vwap_hold`, it never reads
`current_bar_type`), so the #326 fix is categorically inapplicable to this
number; it is not stale on that account.

**A materially newer, larger, currently-uncited number exists and should be
factored in going forward**: Corpus v1 (`docs/corpus-v1-clean-baseline-report-2026-07-25.md:94`,
`main@a5434794e`, 2026-07-25 — one day after `Strategy_Inventory.md`'s last
edit date of 2026-07-23, so its absence from the doc is a timing gap, not an
oversight) reports **n=50** MNQ `vwap_reclaim` (all sessions combined, not
NY-filtered), 56.0% WR, PF 4.309, net $3,759, exp $75/trade — a different,
larger, more recent sample than the n=29 NY-only figure the inventory
currently cites, generated under current production defaults
(`entry_fill_model="market"`, `config/settings.py:216`; the Corpus v1
run's actual `exit_mode`/`entry_fill_model` at generation time is not
recorded in `scripts/corpus_v1_results.json`'s `meta` block or in
`scripts/corpus_v1_report.py`, so whether it exactly matches the repo's
current *default* config or an explicit override used at run time is
**UNKNOWN** — flagged, not assumed). This n=50 number is not walk-forward
split per-strategy in the published doc (only the whole-corpus H1/H2 split
is reported, not `vwap_reclaim`'s own halves) — so while it is larger than
the historical n=29, it does not by itself clear the "both halves positive"
gate row, and should not be read as doing so.

### MES 40% WR figure — sourcing

`risk_rules.yaml:409` comment: `"40% WR on MES vs 100% MNQ — reclaim
conditions fire too loosely"`, part of the `disabled_concepts_per_instrument.MES`
block (:405-408, "Tuned for highest WR on the 74-day MES 5m backtest
(Feb–May 2026)"). **Searched exhaustively** (`grep -rln "74-day\|74 day"
docs/ scripts/`, `git log --all --grep="74-day"`, full-repo grep for
`vwap_reclaim` across every `docs/*.md`): **no committed doc, script, or
JSON artifact backs this specific 40%/100% comparison or the underlying
74-day MES 5m backtest.** It is an **operational comment only** — not
reproducible from anything in this repository. This mirrors the audit
brief's own framing precisely: it is provenance-only, not canonical
evidence, by the same standard the Miyagi/3-2-2 lanes apply to their own
external figures.

---

## 5. Parity matrix

Every cell cites file:line or a document section. `UNKNOWN` = genuinely
unverifiable from this repo, not inferred.

| Field | DOCS | RUNTIME | PINE | REPLAY | EVIDENCE |
|---|---|---|---|---|---|
| **vwap_hold signal** | `Strategy_Inventory.md:171-179` — "entry definition unclear" | `signal_engine.py:2083` `holding and price_vs_vwap=="below"` + trend DOWN + two_down + optional BOS/MSS | `.pine:461-465` `trend_dir=="DOWN" and close<vwap_val and cur_type=="two_down"` | same fields via `replay_engine.py:1095` | `vwap_hold_paired_fill_comparison.py` 348-arm pop |
| **vwap_hold "holding" meaning** | not defined in doc prose | tautological, `price_vs_vwap in ("above","below")` — `state_builder.py:368`, `replay_engine.py:1095` | n/a (Pine doesn't compute a `holding` field, only checks `close<vwap_val` directly, `.pine:461`) | same as runtime | — |
| **vwap_hold timeframe** | not stated as a hard rule | not code-enforced; `payload.timeframe` default `"5m"` (`payload.py:56`) | `timeframe.period`, chart-dependent, operator-set | `candle.timeframe`, whatever conversion script produced | Corpus v1 run used 15m (`corpus-v1...md:16`) |
| **vwap_hold session** | "NY session only" (`Strategy_Inventory.md:175`) | not a `_try_vwap_hold`-specific gate; global `allowed_sessions:[new_york]` (`risk_rules.yaml:505`) applies to all strategies when `SCHEDULE_MODE=current` (`signal_engine.py:246,294`); proof-mode exception hard-scoped to `new_york` (`mnq_vwap_hold_proof.py`) | no session gate in Pine's `vwap_hold` branch itself | replay honors whatever `session` field the candle carries | n=341/348-arm studies drawn from whatever sessions the source data covered, no independent NY isolation inside `_try_vwap_hold` |
| **vwap_hold entry price** | not restated numerically in doc | `vwap.value - 2 ticks` (`:2118`) | `vwap_val - tick*2` (`.pine:464`) | same formula, uses `candle.vwap` | evidence arms carry `setup.entry` field, matches formula (`vwap_hold_paired_fill_comparison.py:63-66`) |
| **vwap_hold stop** | not restated | `vwap.value + 28 ticks` = 7pt MNQ (`:2119`) | `vwap_val + tick*28` (`.pine:465`) | same | same arms |
| **vwap_hold target** | not restated | entry − 3.0R (`:2123`) | `raw_target` not shown for vwap_hold explicitly in the excerpted block (only reclaim/rejection compute `raw_target`; hold's Pine target math not located in `.pine:458-465`) — **UNKNOWN whether Pine sends an advisory target for vwap_hold** | replay uses backend formula only (replay doesn't consume Pine brackets) | n/a |
| **vwap_hold market-condition/trend gate** | not restated | `trend.direction=="DOWN"` required (`:2085`), computed from EMA stack (`state_builder.py:340-346`), plus global `MARKET_CONDITION_NOT_TRADABLE`/`NOT_TRENDING` gates (generic, not vwap-specific) | `trend_dir` sent by Pine, independently overridden backend-side by EMA classify | replay computes trend the same way as live (shared `classify_trend`) | Corpus v1 why-no-trade table shows `vwap_hold` blocked 3922x by `STRATEGY_NOT_PAPER_ELIGIBLE` (`corpus-v1...md:146`) — proves the predicate DOES qualify frequently, just permission-gated |
| **vwap_hold live vs replay parity** | not audited in doc | n/a | n/a | confirmed by overlap-audit Stage 5 that `holding` construction is identical live/replay (`vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md:104-130`) | — |
| **vwap_rejection signal** | `Strategy_Inventory.md:184-202` — describes the OLD same-bar predicate as current | `state.vwap.failed_reclaim` (`:2158`), post-#321 | `.pine:443` still same-bar `vwap_reclaimed and close<vwap_val` — **stale, unfixed** | `replay_engine.py:1061-1063` causal one-bar lookback, correct | 8 arms, Corpus v1 (`validation_vwap_rejection.json`) |
| **vwap_rejection field populated?** | not addressed | live: always `False` default, no Pine sender (`payload.py:76`) | field never sent (confirmed zero-match + zero-commit-history) | derived independently, not payload-dependent — reachable | n=8 only in replay, 0 live (no live occurrences exist to check — field never populated) |
| **vwap_rejection stop** | doc doesn't restate | `vwap.value + 20 ticks` = 5pt MNQ (`:2182`) | `vwap_val + tick*20` (`.pine:447`) | same | 8-arm evidence |
| **vwap_reclaim signal** | `Strategy_Inventory.md:43` n=29 | `reclaimed and holding and price_vs_vwap=="above"` + trend UP (`:2048-2050`) | `vwap_reclaimed and trend_dir=="UP" and close>vwap_val` (`.pine:451`) | `prev.price_vs_vwap!="above" and candle.price_vs_vwap=="above"` (`replay_engine.py:1039-1043`) | n=29 (NY, 2026-07-09) + n=50 (all-session, Corpus v1 2026-07-25, uncited in doc) |
| **vwap_reclaim MES status** | not in Master Table (MNQ-only row) | disabled for MES (`risk_rules.yaml:409`) | n/a (Pine doesn't gate by instrument) | Corpus v1 shows zero MES `vwap_reclaim` rows (`corpus-v1...md:99-103` MES table has only `orb_reclaim`) | 40%WR/74-day figure: **UNKNOWN/unreproducible**, comment-only |
| **VWAP value/session-reset parity** | not addressed in the three audited strategies' docs | consumes whatever `payload.vwap`/`candle.vwap` carries, no independent calc | `ta.vwap(hlc3)`, native session-anchored (`.pine:105`) | PR #314 (`c982ed3`) fixed replay's VWAP accumulator to reset once/day at 18:00 ET matching Pine, not per sub-session — confirmed merged, memory marks this thread CLOSED | `tests/test_vwap_session_anchor.py` exists |
| **Proximity gate (`vwap_entry_max_distance_ticks`)** | PR #92 noted by operator pre-brief | default `0.0` = off, confirmed current (`config/settings.py:233`) | n/a, backend-only | same default applies | locked disabled in #307's five locks |

---

## 6. Contradictions found (docs vs runtime vs Pine vs replay vs evidence)

1. **`Strategy_Inventory.md:44` "BROKEN — unreachable predicate" vs current
   `strategy/signal_engine.py:2158` + `scripts/validation_vwap_rejection.json`**:
   the doc describes the pre-#321 same-bar predicate as if it were still
   current. Post-#321, the predicate is a different field
   (`failed_reclaim`) that fires in replay (n=8, Corpus v1). The doc's
   "0 arms across 622 days" is a true historical fact about the *old*
   predicate, cited as if still describing the *current* one.
2. **`Strategy_Inventory.md:186-191` cites the same-bar contradiction as the
   live predicate**, quoting the exact language later superseded by PR #321
   (`docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`) without
   noting the fix.
3. **`Strategy_Inventory.md:260,281` "VWAP hold isolated fill test... pending"**
   vs PR #307 (`4458eff`, merged 2026-07-23) + the evidence package addendum
   in the same doc file — the test is done, twice over, with a HOLD verdict,
   not "pending."
4. **`Strategy_Inventory.md:178,281` "Entry definition unclear... needs
   `signal_engine.py` review"** vs `strategy/signal_engine.py:2074-2134`,
   which has a fully specified, docstring-explained entry/stop/target
   formula. The definition is not unclear in code; whatever ambiguity
   existed was about which *fill model* validates it, not the formula
   itself.
5. **Pine's advisory `signal_strategy` branch for `vwap_rejection`
   (`.pine:443`) still encodes the exact same-bar impossibility PR #308
   found and PR #321 fixed backend-side** — Pine was never updated to match,
   independent of and in addition to the named `vwap_failed_reclaim`
   field-send gap.
6. **PR #307's IOC leg uses arrival-bar OPEN as the fill reference
   (`vwap_hold_paired_fill_comparison.py:158`) while every production/replay
   call site uses CLOSE** (`webhook/runner.py:2234-2239`,
   `replay/replay_engine.py:299`, `execution/mnq_strat_evidence.py:349`) —
   documented by the evidence-package addendum but never resolved; current
   `Strategy_Inventory.md` doesn't mention this discrepancy at all.
7. **"NY session only" (`Strategy_Inventory.md:175`) reads as a `vwap_hold`
   rule but is actually two unrelated global/proof-mode gates** stacking
   (`risk_rules.yaml:505` `allowed_sessions`, `mnq_vwap_hold_proof.py`'s
   scoped exception) — `_try_vwap_hold` itself has no session logic.
8. **Corpus v1's n=50 `vwap_reclaim` and n=8 `vwap_rejection` reproducible
   replay evidence (2026-07-25) is absent from `Strategy_Inventory.md`**
   (last edited 2026-07-23, one day earlier) — a timing gap, not
   contradiction of fact, but a real staleness now that it exists.

---

## 7. Blockers (need a product/rule decision) vs minor cleanup

### Blockers — need an operator/product decision, not a code fix

- **VWAP Rejection Pine deployment sequencing** (Section 3, "Smallest
  unresolved contradiction"): whether/when to write and deploy a corrected
  Pine script sending `vwap_failed_reclaim` (and fixing the stale
  `signal_strategy` advisory branch at the same time), given the reachable
  evidence so far is n=8. PR #321 explicitly deferred this to the operator
  and no later change addresses it. **Decision needed from: the operator**
  (per PR #321's own text, this was flagged, not decided, at merge time).
- **IOC reference-price convention for `vwap_hold`** (Section 2): whether
  the isolated-fill-model comparison's IOC leg should use OPEN (as #307
  shipped) or CLOSE (as production/replay use everywhere else) as the
  marketability reference — material to the conclusion (fails vs passes
  both-halves at every cost tier). The evidence package recommends CLOSE
  but explicitly did not implement the change pending operator direction.
  **Decision needed from: the operator.**
- **Whether `Strategy_Inventory.md`'s VWAP Reclaim verdict should incorporate
  the newer Corpus v1 n=50 figure** at all, and if so, whether a per-strategy
  walk-forward split should be generated before doing so — this is a
  evidence-standard decision (does thin-but-larger all-session evidence move
  the needle vs the existing session-split n=29), not something this audit
  resolves.

### Minor cleanup — mechanical, no decision needed

- `Strategy_Inventory.md`'s VWAP Hold/Rejection rows and "Pending
  Research"/"Build Queue" prose citing stale states (contradictions #1-4
  above) can be corrected to reference PR #307/#308/#321/#326 by number and
  describe the current predicate and evidence status, without changing the
  Verdict column (see Section 9 for exactly what was changed in this pass).
- Line-number citations inside `docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`
  (e.g. `signal_engine.py:2032-2143`, `replay_engine.py:505-529`) no longer
  match current file line numbers (now ~2074-2197 and ~1031-1109
  respectively) — cosmetic drift from later unrelated refactors (#321, #324
  instrument-keying), not a substantive error; not corrected here since
  editing PR #307/#308's docs is out of this audit's authorized scope
  (only `Strategy_Inventory.md` prose was explicitly authorized for
  correction).

---

## 8. Historical evidence: trustworthy vs superseded

**Trustworthy, with scope stated:**
- PR #307/evidence-package market+runner result ($10.30/armed, PF 1.52,
  both-halves-positive, n=348) — population confirmed unaffected by #326;
  valid **specifically under market-entry+runner-exit**, not as a statement
  about IOC viability (see IOC reference-price blocker above).
- PR #307/evidence-package IOC-close+static/runner results (positive, both
  halves, all cost tiers) — valid but not yet adopted as the reference
  convention; currently a sensitivity analysis, not the shipped baseline.
- `docs/vwap-hold-vs-vwap-rejection-overlap-audit-2026-07-23.md`'s Stage
  1-8 provenance trace and mutual-exclusivity proof — formula-level, still
  accurate for `vwap_hold`/`vwap_reclaim` on current `main`; its
  characterization of `vwap_rejection` as permanently unreachable is
  **superseded by PR #321**.
- `docs/mes-mnq-mechanical-research-2026-07-09.md`'s n=29 MNQ `vwap_reclaim`
  NY figure — dated, reproducible, predicate-unaffected by any later fix;
  valid as one session-specific data point, not walk-forward split.
- Corpus v1 n=50 `vwap_reclaim` / n=8 `vwap_rejection` (2026-07-25) — real,
  reproducible, current-`main`, but n=8 is far below the doc's own 30-trade
  minimum and neither number is walk-forward split per-strategy.
- PR #314's VWAP session-reset fix (once/day at 18:00 ET) — confirmed merged,
  makes replay's VWAP value itself trustworthy as a Pine-parity input to
  all three strategies.

**Superseded:**
- `Strategy_Inventory.md`'s own current VWAP Hold/Rejection prose (Section
  6, items 1-4) — superseded by PR #307/#308/#321, not yet reflected.
- The pre-#307 "VWAP edge is fiction" IOC-limit finding, as a statement
  about the *current* market+runner-testable population — the operator's
  own concern about incompatible old studies is valid for comparisons
  *predating* #307's frozen-population methodology, and superseded for the
  #307-vs-#308-vs-evidence-package comparison specifically (same population,
  by construction).
- The pre-#321 "vwap_rejection is structurally unfireable" finding, as a
  statement about current `main`'s replay behavior (still true for live).

---

## 9. Classification

| Strategy | Classification | One-line reasoning |
|---|---|---|
| **VWAP Hold** | **WAIT** (unchanged from current doc, reasoning updated) | Detector fully specified in code (not "entry definition unclear"); best evidence (market+runner, n=348) is real but rests on an unresolved IOC-reference-price convention question and is SHADOW_ONLY with a single narrow proof-mode exception — not walk-forward-proven under the production-matching fill reference, so no upgrade is warranted. |
| **VWAP Rejection** | **WAIT** (downgrade from doc's stale "BROKEN" framing, but not an upgrade to PROMISING) | No longer structurally unreachable (BROKEN's stated rationale is false on current `main`) — replay evidence exists (n=8) and is positive, but n=8 is far below any adequacy threshold, walk-forward is not evaluated, and the strategy **cannot fire live at all** pending an undecided Pine-deployment call. "BROKEN" overstates today's state (the predicate works); "PROMISING BUT UNPROVEN" would overstate n=8 replay-only evidence for a strategy with zero live-eligible path. WAIT best matches "detector effectively unusable pending an external (Pine) blocker + thin sample," per this repo's taxonomy. |
| **VWAP Reclaim** | **WAIT** (unchanged from current doc) | Predicate has genuine live/replay/Pine formula parity (the cleanest of the three) and two dated, reproducible replay samples exist (n=29 NY-only, n=50 all-session) — but neither is walk-forward split per-strategy, MES's disable rationale (40%WR) is unreproducible/comment-only, and the doc's own 30-minimum sample bar is met by n=50 in count only, not in methodology (no half-split, no slippage sensitivity). No basis to upgrade past WAIT under the "don't credit old-fill-model/old-definition studies" instruction. |

**Explicitly not upgraded**: none of the three moves to PROMISING BUT
UNPROVEN or higher in this pass. The strongest single number found
(`vwap_hold` market+runner, $10.30/armed) is real and current-predicate-valid
but is exactly the kind of single-cell result the repo's own pipeline gates
(Section "Pipeline Gates" in `Strategy_Inventory.md`) say is insufficient
alone (no honest-fill-reference-price resolution, no adequate walk-forward
split reported per-strategy in the two replay sources examined).

---

## 10. Smallest safe next action per strategy

- **VWAP Hold**: resolve the IOC reference-price convention (operator
  decision, Section 7) — this alone would let the existing #307/evidence-package
  data produce a clean walk-forward verdict without any new code or
  detector work. No code change needed to do this; it's a call on which
  already-computed matrix cell (IOC-open vs IOC-close, static vs runner) is
  the strategy's evaluation baseline going forward.
- **VWAP Rejection**: no code action recommended. The smallest safe next
  step is the operator decision already flagged in PR #321 (Pine deployment
  sequencing) — until that is made, the correct posture is "leave as-is,"
  not "build more evidence" (n=8 is too thin to act on either way, and more
  replay-only evidence cannot resolve the live-eligibility question, which
  is entirely Pine-side).
- **VWAP Reclaim**: run a per-strategy walk-forward split (H1/H2) of the
  existing Corpus v1 `vwap_reclaim` journals via
  `scripts/strategy_validation_report.py --strategy vwap_reclaim` (the
  script already supports this; it was simply not surfaced in the
  published `docs/corpus-v1-clean-baseline-report-2026-07-25.md`, which
  only reports the whole-corpus halves, not per-strategy). This produces a
  more decision-useful number from data that already exists, with no new
  replay run and no code change.

---

## 11. Product/rule decisions surfaced (verbatim, not resolved here)

1. Whether and when to write and deploy a corrected Pine script that sends
   `vwap_failed_reclaim=true`/`false` (and fixes the stale same-bar
   `signal_strategy` advisory branch at `.pine:443` in the same pass), given
   `vwap_rejection`'s only current evidence is n=8 replay-only trades.
   **Who decides**: the operator (per PR #321's own merged text, which
   flagged this and explicitly left it undecided).
2. Which arrival-bar price field (`open` vs `close`) is the authoritative
   IOC marketability reference for `vwap_hold` evaluation — `open` is what
   PR #307 shipped, `close` is what every production/replay call site
   actually uses, and the choice flips the static-exit both-halves
   pass/fail result. **Who decides**: the operator (the evidence-package
   addendum recommends `close` but explicitly declined to implement the
   change pending direction).
