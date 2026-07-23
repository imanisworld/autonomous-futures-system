# VWAP-hold isolated fill-model comparison (2026-07-23)

Research only. No production edits, rule-document rewrites, configuration
changes, broker actions, deployments, or automatic strategy reclassification.

Operator-directed isolation test, run against five locked preconditions
(verbatim below) so the "isolated" comparison cannot be contaminated by an
unfrozen population, an underspecified fill model, a mixed proximity-gate
state, or a metric that structurally favors the model with the higher
rejection rate.

## The five locks

1. **Freeze the exact 348-signal population.** Verified: reconstructing the
   arms from `logs/retest_baseline_off/MNQ/journal_*.jsonl` reproduces
   sha256 `18cbbc8427b8afc462b1145347125ae45bb2b6af97f4ef9f374a10565a96d880`
   exactly — byte-identical to the PR #283 fingerprint. The loader only
   reads persisted historical journal rows; it never invokes
   `strategy/signal_engine.py`, so there is no regeneration through current
   strategy code. A manifest recording every row plus the hash is committed:
   `scripts/vwap_hold_isolated_fill_model_manifest.json`.
2. **IOC defined precisely** — extracted from `execution/paper_broker.py`
   source, not paraphrased: limit price = `entry ± 32 ticks` (the live
   `ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ` pin); marketability is a single
   snapshot check against the arrival bar's **open** (the code comment says
   "close" — that mismatch is inherited from the verified PR #283 script and
   stated here, not silently changed); no persistence across bars, no
   later-bar fill, no partial fills; fill price = the better of (market,
   limit), capped at the limit.
3. **Market fill defined precisely** — decision timestamp = signal
   `bar_ts + 15min`; first executable price = the next 5m bar's open;
   gap-through fills immediately at open + 1 adverse tick; otherwise a
   touch-based fill (bar high/low, never intrabar sequencing) within a
   20-minute window at level + 1 adverse tick; no partial fills; a uniform
   $2.24/contract round-turn cost overlay applied identically to both legs
   at settlement (not additional entry slippage — does not double-count the
   embedded 1-tick fill slippage).
4. **Proximity gate frozen disabled.** `config/settings.py:233
   vwap_entry_max_distance_ticks` defaults to `0.0` ("never gates"). The
   field was introduced default-off in PR #92 (2026-06-26); the only attempt
   to enable it, PR #95, was closed unmerged. It has never been ON for any
   day in this population's 2024-07-02..2026-06-25 span, so the primary
   comparison already carries the production-default state with no filter
   applied by this script. No sensitivity variant was run — out of scope
   here per instruction.
5. **Primary metric = net expectancy per armed signal** (net $ ÷ n=348,
   unfilled arms contribute $0). Expectancy-per-fill is reported but is not
   the verdict metric.

## Results

| Leg | Filled | Net after cost | **PRIMARY: exp/armed signal (n=348)** | Secondary: exp/fill | WR | PF |
|---|---|---|---|---|---|---|
| old anchored IOC (32t) | 105/348 (30.2%) | $936.54 | **$2.69** | $8.92 | 0.495 | 1.72 |
| new market entry | 343/348 (98.6%) | $3,583.08 | **$10.30** | $10.51 | 0.501 | 1.52 |

Raw artifacts: `scripts/vwap_hold_isolated_fill_model_manifest.json` (frozen
population + hash), `scripts/vwap_hold_isolated_fill_model_comparison_results.json`
(full per-leg summary).

## Why the primary metric changes the picture

Per-fill, the two legs looked close ($8.92 vs $10.51 — an 18% gap). That
similarity was an artifact of IOC's own rejection rate: expectancy-per-fill
only measures the trades that got through, and IOC let through the
minority. Once every armed signal counts in the denominator — including the
70% IOC self-cancelled — the picture is starkly different: **the market
leg's per-armed-signal expectancy is roughly 3.8x the IOC leg's ($10.30 vs
$2.69)**. This is exactly the distortion lock #5 was written to prevent: a
fill model can look nearly as good as another on a per-fill basis while
producing a fraction of the total value across the actual population of
opportunities the strategy generates.

This reproduces and sharpens the PR #283 finding rather than contradicting
it — the market leg's numbers here ($3,583.08 net, 343/348 filled) match
that PR's independently-reproduced headline. What's new is the isolated,
locked comparison confirming the gap holds, and is materially larger, on
the metric that actually answers the strategy question: does the model
that's actually deployable turn the strategy's real opportunity set into
positive value.

## What this does NOT decide

Per operator instruction, this test does not conclude, and this document
does not recommend, that `vwap_hold` or `vwap_rejection` be retired,
merged, or redesigned. It also does not itself constitute proof that either
represents a genuinely separate market state versus a duplicate
implementation. That determination is explicitly deferred to the follow-on
**overlap audit** — the next task, not detector construction — which must
first establish whether the two are (a) genuinely separate conditions
needing mutually exclusive gating, (b) the same setup under two historical
names, or (c) one valid setup plus one redundant implementation. The
previous VWAP narrative ("fillable vwap trades lose") is superseded by this
and the PR #283 finding and should be considered withdrawn, per operator
verdict.

## Verification

- Population hash independently reconstructed and asserted equal to the
  PR #283 frozen fingerprint (hard-fails the script otherwise).
- Both legs reuse the exact PaperBroker call paths already verified in PR
  #283 (`scripts/vwap_hold_paired_fill_comparison.py`); no fill-model code
  was changed, only precisely documented, manifested, and re-summarized
  under the operator-mandated primary metric.
- No file in this PR is imported by the runtime; deploy state unaffected.

---

## HOLD-response evidence package (2026-07-23, second pass)

**Verdict at time of writing: still HOLD.** The section above was ruled
incomplete — it reported one entry-model pair under one exit model with no
cost sensitivity, no chronological split, no drawdown, and no resolution of
the IOC reference-price discrepancy it had only flagged, not traced. This
section adds everything required. It does not change the verdict to
APPROVE; that call belongs to the operator.

Full artifact: `scripts/vwap_hold_evidence_package.py` (script, imports
`load_arms`/`load_bars` from the unchanged #283 module; independently
computes fill determination and reuses the real, unmodified `PaperBroker`
for all exit resolution) → `scripts/vwap_hold_evidence_package_results.json`
(complete matrix + diagnostics).

### 1. IOC reference-price discrepancy — traced, not fixed

- **Comment claims close**: `execution/paper_broker.py:171-172` (docstring)
  and `:213` (the `ValueError` message if `market_price` is omitted) both
  say `market_price (the decision bar's close)`.
- **The comparison script passes open**: `scripts/vwap_hold_paired_fill_comparison.py:158`
  — `broker.execute_bracket(order, market_price=first["open"])`. This line
  is unchanged by PR #307 (it is imported, not reimplemented) and unchanged
  by this evidence package.
- **Production and replay all use close** — three independent, unrelated
  call sites agree with the docstring against the comparison script:
  - `webhook/runner.py:2016-2018` — explicit comment: proof-lane paper
    market entry fills at "the decision bar's close — the same reference
    the entry-sanity guard uses."
  - `execution/mnq_strat_evidence.py:349` — `market = float(state.ohlc.close)`.
  - `replay/replay_engine.py:299` — `market_price=candle.close`. This is
    the engine that generated the arm population's original TRADE/APPROVED
    decisions in `logs/retest_baseline_off` in the first place.
- **Conclusion**: this is a genuine mismatch, not a stale comment. The
  isolated test's IOC leg (both #283's and #307's, since #307 imports it
  unchanged) has never matched how IOC is modeled anywhere else in this
  codebase. The implementation was **not changed** — per instruction, both
  interpretations are run as a labeled sensitivity instead.

### 2. IOC-open vs IOC-close sensitivity (static exit, all cost tiers)

| Reference | Filled | 1-tick PRIMARY | 2-tick PRIMARY | 3-tick PRIMARY | Both halves+ (all tiers) |
|---|---|---|---|---|---|
| arrival-bar OPEN (the #307 baseline) | 105/348 (30.2%) | $0.03 | **-$0.12** | -$0.27 | **NO** |
| arrival-bar CLOSE (matches production) | 146/348 (42.0%) | $2.97 | **$2.76** | $2.55 | **YES** |

This is material, not cosmetic. Under static exit — the exit mode IOC was
originally live-tested under — the #307 baseline (open) is flat-to-negative
per armed signal at every cost tier and fails the both-halves-positive
check. The production-matching reference (close) fills 39% more signals and
is solidly positive at every tier, passing both halves. **The #307 baseline
understated IOC's own performance by using a reference price nothing else
in the codebase uses.** Under runner exit the ordering is the same
direction but smaller in relative terms: open $2.69-2.84/armed vs close
$3.95-4.37/armed across 1-3 ticks (both pass both-halves at every tier
under runner). Full 9-cell numbers in the matrix below.

### 3. Market-entry mechanics, stated exactly

- **Decision timestamp**: signal `bar_ts + 15 minutes` (`armed_at`).
- **Fill timestamp**: the arrival bar's own ts for a gap-through fill, or
  the specific touch-bar's ts (first bar within a 20-minute window whose
  high/low reaches the level) for a touch fill.
- **Fill-price field**: arrival-bar OPEN + 1 adverse tick (gap-through), or
  level + 1 adverse tick (touch).
- **Signal-bar vs next-bar data**: next-bar only, from `armed_at` forward.
  The signal bar's own subsequent path is never used.
- **Lookahead check**: the gap decision uses only the arrival bar's open —
  no future data. The touch-fill scan looks forward through subsequent
  bars to find *when* an already-placed order would be touched — this
  prices fill *timing*, not the *decision* to trade (the arm was already
  TRADE/APPROVED by the historical replay before this script ever runs), so
  no entry decision depends on information unavailable at decision time.
- **Why 5 of 348 didn't fill**: all five are `SHORT` arms from the very
  first day of the dataset, 2024-07-02, with the market gapping 56-130
  ticks away from the entry level at the open and never touching it within
  the 20-minute window (`gap_at_open_ticks`: -56.3, -89.7, -104.2, -124.9,
  -129.9 — full detail in the results JSON `market_entry_diagnostics`).
  This reads as an understandable small tail (large adverse gaps that ran
  away), not a hidden systematic failure mode.
- **Gap handling**: stated in lock #3 above, unchanged.

### 4. Full entry x exit matrix (2-tick cost baseline; 1/2/3-tick sweep in the results JSON)

| Entry | Exit | Filled | Net (after cost) | **PRIMARY /armed** | /fill | PF | Max DD | Both halves+ |
|---|---|---|---|---|---|---|---|---|
| ioc_open | static | 105/348 | -$41.28 | **-$0.12** | -$0.39 | 0.97 | $425 | NO |
| ioc_open | runner | 105/348 | $936.54 | **$2.69** | $8.92 | 1.72 | $195 | YES |
| ioc_open | partial (2ct approx) | 105/348 | $1,130.46 | **$3.25** | $10.77 | 1.44 | $409 | NO |
| ioc_close | static | 146/348 | $960.92 | **$2.76** | $6.58 | 1.70 | $169 | YES |
| ioc_close | runner | 146/348 | $1,447.61 | **$4.16** | $9.92 | 2.14 | $188 | YES |
| ioc_close | partial (2ct approx) | 146/348 | $2,735.57 | **$7.86** | $18.74 | 2.14 | $327 | YES |
| market | static | 343/348 | -$2,360.90 | **-$6.78** | -$6.88 | 0.56 | $2,447 | NO |
| market | runner | 343/348 | $3,583.08 | **$10.30** | $10.51 | 1.52 | $564 | YES |
| market | partial (2ct approx) | 343/348 | $1,983.46 | **$5.70** | $5.82 | 1.17 | $1,040 | YES |

**Reading this matrix, not just the headline cell:**
- **Static exit is harmful everywhere**, including under market entry
  (-$6.78/armed, PF 0.56, $2,447 max DD, fails both halves) — this
  reproduces the PR #283 tranche-1 component-role finding
  ("Static exit — Harmful — Negative or ~0 on all six MNQ cells") under a
  completely independent entry-model comparison, not just the same study.
- **Partial (2×1-contract approximation) never beats runner** — it is
  dragged down by its static-exit half in every row (market: $5.70 vs
  $10.30; ioc_close: $7.86 vs $4.16 is the one exception, because IOC-close
  passes at a materially higher fill rate under static too — still, no row
  shows partial beating runner on drawdown or PF simultaneously).
- **market+runner remains the strongest cell** ($10.30/armed) but
  **ioc_close+runner is not far behind in per-fill terms** ($9.92/fill,
  PF 2.14, the highest PF in the matrix) despite filling only 42% of
  signals — a materially different picture than the #307 baseline
  (ioc_open+runner, $2.69/armed) suggested about IOC's viability once the
  reference-price defect is corrected.

### 5. Same-bar-ambiguous / stop-first diagnostics (static exit only)

Runner exit has no fixed target — `execution/paper_broker.py::_resolve_runner`
exits solely via a trailing stop, so "both stop and target hit in one bar"
does not apply to it the same way and is not computed for it.

| Entry | Resolved (static) | Same-bar ambiguous | Stop-first (clean) | Target-only |
|---|---|---|---|---|
| ioc_open | 105 | 5 (4.8%) | 62 (59.0%) | 38 (36.2%) |
| ioc_close | 146 | 9 (6.2%) | 110 (75.3%) | 27 (18.5%) |
| market | 343 | 15 (4.4%) | 131 (38.2%) | 197 (57.4%) |

Ambiguity rates are similar (4-6%) across all three entry models — the
straddle-bar rate is a property of the bars and stop/target geometry, not
of the entry-fill mechanism. IOC's higher stop-first share (59-75% vs
market's 38%) is consistent with IOC's tolerance-capped fills sitting
closer to the adverse side of the anchored level than market's
touch/gap-adjusted fills.

### 6. Market-only fills subset (per-fill view, explicitly not the verdict metric)

Static exit: -$6.88/fill. Runner exit: $10.51/fill. This isolates that,
for market entry specifically, the entry model was never the problem on a
per-fill basis either — the exit model is what flips the sign.

### 7. PR #307 vs PR #283 — verified, not assumed

`git diff origin/main -- scripts/vwap_hold_paired_fill_comparison.py` on
the #307 branch is **empty** — the file is byte-identical to its PR #283
merged state. #307 imports `load_arms`/`run_leg`/`fingerprint`/`COST_RT`
directly from it rather than duplicating logic, so there is no path for
behavioral drift in the entry-fill mechanics between the two PRs. The only
differences #307 introduces are: (a) a manifest-freeze assertion that
hard-fails if the reconstructed population hash doesn't match the PR #283
fingerprint, and (b) a reporting/summarization layer adding the
per-armed-signal primary metric alongside the existing per-fill metric.
Neither touches fill mechanics. This evidence package (`vwap_hold_evidence_package.py`)
also imports `load_arms`/`load_bars` from the same unchanged #283 module
and resolves every PnL number through the real, unmodified `PaperBroker` —
it independently re-derives entry-fill *timing/price* per model but never
reimplements exit resolution.

### Corrected interpretation

**Supported by this evidence:**
On the frozen 348-signal sample, at 2-tick cost, under runner exit (the
only exit mode that is not harmful), market entry produced the highest net
expectancy per armed signal ($10.30) of the three entry models tested;
IOC's own performance depends materially on which arrival-bar price field
is used as the marketability reference, and the production-matching
reference (close) performs substantially better than the reference the
#307 baseline used (open) — narrowing, but not closing, the gap to market
entry under runner exit, and flipping IOC-close+static from negative/failing
to positive/passing at every cost tier tested.

**Not yet supported:**
That market entry is validated, deployable, or definitively superior to a
correctly-referenced IOC after realistic costs and out-of-sample stability.
That determination needs, at minimum, resolution of which IOC reference
price the isolated test should treat as authoritative (this package
recommends CLOSE, matching all three production/replay call sites, but has
not changed the implementation pending operator direction), and remains
gated on whatever further validation the operator requires beyond this
evidence package.

**The overlap audit stays deferred** until PR #307 is reviewable and this
evidence package is accepted or further revised.
